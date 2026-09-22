"""On-demand, leased RTSP readers. Only the worker receives camera credentials."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from urllib.parse import quote

from .cameras import load_camera_config
from .db import connect

LEASE_SECONDS = 45


def snapshot_file(settings, camera_id, temporary=False):
    if not re.fullmatch(r"[a-f0-9]{24}", camera_id):
        raise ValueError("Invalid camera")
    root = settings.cache.resolve()
    path = (root / "snapshots" / (camera_id + (".part.jpg" if temporary else ".jpg"))).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid snapshot path")
    return path


def live_file(settings, camera_id, generation, filename):
    if not re.fullmatch(r"[a-f0-9]{24}", camera_id) or not re.fullmatch(r"[a-f0-9]{16}", generation):
        raise ValueError("Invalid live stream")
    if not re.fullmatch(r"index\.m3u8|segment\d+\.ts", filename):
        raise ValueError("Invalid live asset")
    root = (settings.cache / "live").resolve()
    path = (root / camera_id / generation / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid live asset")
    return path


def live_command(config, camera, output):
    ports = json.loads(camera["metadata"]).get("host", {}).get("GetNetPort", {}).get("NetPort", {})
    if ports.get("rtspEnable") != 1:
        raise ValueError("RTSP is not enabled on this camera; no settings were changed")
    channel = int(camera["channel"]) + 1
    port = int(ports.get("rtspPort", 554))
    url = f"rtsp://{quote(config['username'], safe='')}:{quote(config['password'], safe='')}@{config['host']}:{port}/Preview_{channel:02d}_sub"
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "8000000",
        "-i",
        url,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "5",
        "-hls_delete_threshold",
        "2",
        "-hls_flags",
        "delete_segments+temp_file+omit_endlist",
        "-hls_segment_filename",
        str(output / "segment%09d.ts"),
        str(output / "index.m3u8"),
    ]


class LiveManager:
    def __init__(self, settings):
        self.settings = settings
        self.processes = {}
        self.snapshots = {}
        root = settings.cache / "live"
        if root.exists() and not root.is_symlink():
            shutil.rmtree(root)
        root.mkdir(exist_ok=True)
        with connect(settings) as conn:
            conn.execute("UPDATE live_streams SET status='pending',error=NULL")
            conn.execute(
                "UPDATE live_snapshots SET status='error',error='Capture interrupted. Refresh to retry.' WHERE status IN ('pending','capturing')"
            )

    def snapshot_tick(self):
        now = time.time()
        for cid, (process, output, started) in list(self.snapshots.items()):
            code = process.poll()
            if code is None and now - started < 15:
                continue
            if code is None:
                process.kill()
            process.wait(timeout=3)
            success = code == 0 and output.is_file() and output.stat().st_size > 0
            if success:
                output.replace(snapshot_file(self.settings, cid))
            else:
                output.unlink(missing_ok=True)
            with connect(self.settings) as conn:
                conn.execute(
                    "UPDATE live_snapshots SET status=?,captured_at=CASE WHEN ? THEN ? ELSE captured_at END,error=? WHERE camera_id=?",
                    (
                        "ready" if success else "error",
                        success,
                        now,
                        None if success else "Snapshot unavailable. Try again.",
                        cid,
                    ),
                )
            self.snapshots.pop(cid)
        with connect(self.settings) as conn:
            rows = conn.execute(
                "SELECT c.* FROM cameras c JOIN live_snapshots s ON s.camera_id=c.id WHERE s.status='pending' ORDER BY s.requested_at LIMIT ?",
                (max(0, 3 - len(self.snapshots)),),
            ).fetchall()
        for camera in rows:
            cid = camera["id"]
            try:
                config = next(
                    c for c in load_camera_config(self.settings) if c["host"] == camera["device_host"]
                )
                output = snapshot_file(self.settings, cid, temporary=True)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.unlink(missing_ok=True)
                command = live_command(config, camera, output.parent)
                command = command[: command.index("-map")] + [
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-an",
                    "-vf",
                    "scale=1280:-2",
                    "-q:v",
                    "3",
                    "-threads",
                    "1",
                    "-y",
                    str(output),
                ]
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.snapshots[cid] = (process, output, now)
                with connect(self.settings) as conn:
                    conn.execute("UPDATE live_snapshots SET status='capturing' WHERE camera_id=?", (cid,))
            except Exception:
                with connect(self.settings) as conn:
                    conn.execute(
                        "UPDATE live_snapshots SET status='error',error='Snapshot unavailable. Check the camera connection.' WHERE camera_id=?",
                        (cid,),
                    )

    def state(self, cid, status, error=None, generation=None):
        with connect(self.settings) as conn:
            conn.execute(
                "UPDATE live_streams SET status=?,error=?,generation=COALESCE(?,generation) WHERE camera_id=?",
                (status, error, generation, cid),
            )

    def stop(self, cid, status="stopped", error=None):
        entry = self.processes.pop(cid, None)
        if entry:
            process, output, _ = entry
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            shutil.rmtree(output, ignore_errors=True)
        self.state(cid, status, error)

    def tick(self):
        self.snapshot_tick()
        now = time.time()
        with connect(self.settings) as conn:
            conn.execute("DELETE FROM live_viewers WHERE expires_at<?", (now,))
            rows = conn.execute("""SELECT c.*,s.status AS live_status FROM cameras c
                JOIN live_streams s ON s.camera_id=c.id
                WHERE EXISTS(SELECT 1 FROM live_viewers v WHERE v.camera_id=c.id) LIMIT 5""").fetchall()
        wanted = {r["id"]: r for r in rows}
        for cid in list(self.processes):
            if cid not in wanted:
                self.stop(cid)
                continue
            process, output, started = self.processes[cid]
            playlist = output / "index.m3u8"
            modified = playlist.stat().st_mtime if playlist.exists() else started
            if process.poll() is not None or now - modified > 30:
                self.stop(cid, "error", "Live stream disconnected. Try connecting again.")
            elif playlist.exists():
                self.state(cid, "ready")
        configs = None
        for cid, camera in wanted.items():
            if cid in self.processes or camera["live_status"] not in ("pending", "ready"):
                continue
            # A process that failed in this tick must not immediately restart.
            with connect(self.settings) as conn:
                if (
                    conn.execute("SELECT status FROM live_streams WHERE camera_id=?", (cid,)).fetchone()[0]
                    == "error"
                ):
                    continue
            try:
                if configs is None:
                    configs = {c["host"]: c for c in load_camera_config(self.settings)}
                config = configs.get(camera["device_host"])
                if not config:
                    raise ValueError("Camera connection is not configured")
                generation = uuid.uuid4().hex[:16]
                output = live_file(self.settings, cid, generation, "index.m3u8").parent
                output.mkdir(parents=True, exist_ok=True)
                command = live_command(config, camera, output)
                # Discard stderr: ffmpeg failures can echo the credential-bearing input URL.
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.processes[cid] = (process, output, now)
                self.state(cid, "starting", generation=generation)
            except Exception:
                self.state(
                    cid,
                    "error",
                    "Unable to read this camera stream. Check that RTSP is already enabled and reachable.",
                )

    def close(self):
        for process, output, _ in self.snapshots.values():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            output.unlink(missing_ok=True)
        self.snapshots.clear()
        for cid in list(self.processes):
            self.stop(cid)
