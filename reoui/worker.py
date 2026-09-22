from __future__ import annotations

import asyncio
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time

from .cameras import collect
from .config import Settings
from .db import connect, enqueue, get_state, initialize, set_state
from .live import LiveManager


def claim(settings: Settings, exclude: tuple[str, ...] = ()):
    with connect(settings) as conn:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in exclude)
        clause = f"AND kind NOT IN ({placeholders})" if exclude else ""
        row = conn.execute(
            f"SELECT * FROM jobs WHERE status='queued' {clause} ORDER BY priority DESC,id LIMIT 1", exclude
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1,started_at=?,error=NULL WHERE id=?",
                (time.time(), row["id"]),
            )
            return dict(row)


async def camera_loop(settings: Settings, stop: asyncio.Event):
    while not stop.is_set():
        devices = []
        try:
            devices = await collect(settings)
        except Exception as exc:
            set_state(settings, "collector", {"error": type(exc).__name__, "updated_at": time.time()})
        try:
            delay = (
                min(settings.camera_interval, 60)
                if any(d.get("event_days_pending", 0) for d in devices)
                else settings.camera_interval
            )
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


async def run(settings: Settings, once: bool = False):
    initialize(settings)
    # Single coordinator per data directory; kernel releases the lock on crashes.
    lock = (settings.data / "worker.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("A worker is already running for this data directory") from None
    # Abrupt shutdown may leave unpublished derivative directories. Only our
    # private temporary output is removed, after acquiring the coordinator lock.
    for pattern in ("prepare-*", "playback-*"):
        for temporary in settings.cache.glob(pattern):
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    with connect(settings) as conn:
        conn.execute("UPDATE jobs SET status='queued',started_at=NULL WHERE status='running'")
        conn.execute("UPDATE recordings SET status='pending' WHERE status='processing'")
    collector = asyncio.create_task(camera_loop(settings, stop)) if not once else None
    processes: dict[str, tuple] = {}
    live = LiveManager(settings)
    next_scan = 0
    try:
        while not stop.is_set():
            now = time.time()
            live.tick()
            set_state(settings, "worker", {"heartbeat": now, "active": list(processes)})
            if now >= next_scan:
                enqueue(settings, "scan", priority=5)
                next_scan = now + settings.scan_interval
            for lane, (process, job, start, timeout) in list(processes.items()):
                code = process.poll()
                last_progress = start
                if lane == "scan":
                    with connect(settings) as conn:
                        scan_state = get_state(conn, "scan", {})
                        last_progress = max(start, scan_state.get("updated_at", start))
                if code is None and now - last_progress < timeout:
                    continue
                if code is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    error = "Source or media operation timed out"
                    if lane == "scan":
                        set_state(settings, "source", {"available": False, "error": error, "checked_at": now})
                        set_state(settings, "scan", {"status": "error", "error": error, "finished_at": now})
                else:
                    error = None if code == 0 else "Processing failed; see recording status"
                with connect(settings) as conn:
                    conn.execute(
                        "UPDATE jobs SET status=?,finished_at=?,error=? WHERE id=?",
                        ("error" if error else "done", now, error, job["id"]),
                    )
                    if error and job["kind"] == "prepare":
                        conn.execute(
                            "UPDATE recordings SET status='error',error=COALESCE(error,?) WHERE id=?",
                            (error, job["target"]),
                        )
                processes.pop(lane)
                if not error and job["kind"] == "prepare" and settings.auto_proxy_hours > 0:
                    with connect(settings) as conn:
                        row = conn.execute(
                            "SELECT start,video_codec,audio_codec,proxy FROM recordings WHERE id=?",
                            (job["target"],),
                        ).fetchone()
                    if (
                        row
                        and row["start"]
                        and row["start"] > now - settings.auto_proxy_hours * 3600
                        and not row["proxy"]
                        and (row["video_codec"] != "h264" or row["audio_codec"] not in (None, "aac", "mp3"))
                    ):
                        enqueue(settings, "proxy", job["target"], priority=1)
            if "media" not in processes:
                with connect(settings) as conn:
                    row = conn.execute(
                        "SELECT id FROM recordings WHERE status='pending' ORDER BY COALESCE(start,mtime) DESC LIMIT 1"
                    ).fetchone()
                if row:
                    enqueue(settings, "prepare", row[0])
            exclusions = ("scan",) if "scan" in processes else ()
            if "media" in processes:
                exclusions += ("prepare", "proxy")
            job = claim(settings, exclusions)
            if job:
                kind = job["kind"]
                command = [sys.executable, "-m", "reoui.cli", kind]
                if job["target"]:
                    command += [job["target"]]
                if kind == "prepare":
                    with connect(settings) as conn:
                        conn.execute("UPDATE recordings SET status='processing' WHERE id=?", (job["target"],))
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                timeout = settings.scan_timeout if kind == "scan" else 3900 if kind == "proxy" else 480
                processes["scan" if kind == "scan" else "media"] = (process, job, now, timeout)
            elif once and not processes:
                break
            await asyncio.sleep(0.5)
    finally:
        live.close()
        if collector:
            collector.cancel()
            try:
                await collector
            except asyncio.CancelledError:
                pass
        for process, *_ in processes.values():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        set_state(settings, "worker", {"heartbeat": time.time(), "stopped": True})
        lock.close()
