from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from reolink_aio.typings import parse_file_name

from .config import Settings
from .db import connect, set_state

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".h264", ".h265", ".264", ".265"}
COLORS = ["#a2c9ac", "#9db4d3", "#d8b58b", "#b7a2cc", "#8bbfc0"]


def identifier(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def parse_filename(name: str, timezone: str) -> dict:
    result = {
        "start": None,
        "end": None,
        "local_day": None,
        "time_source": "unknown",
        "time_warning": None,
        "triggers": [],
        "triggers_known": False,
        "stream": None,
    }
    tz = ZoneInfo(timezone)
    start = end = None
    try:
        parsed = parse_file_name(name, tz)
        if parsed:
            start = datetime.combine(parsed.date, parsed.start, tz)
            end = datetime.combine(parsed.date, parsed.end, tz)
            if end < start:
                end += timedelta(days=1)
            result["triggers"] = [flag.name.lower() for flag in parsed.triggers if flag.value]
            # NONE can also mean unavailable data; do not assert a known negative.
            result["triggers_known"] = bool(result["triggers"])
            result["stream"] = (
                "main" if name.startswith("RecM") else "sub" if name.startswith("RecS") else None
            )
    except (ValueError, KeyError, IndexError, TypeError):
        pass
    if start is None:
        match = re.search(r"(?<!\d)(20\d{6})[_-]?(\d{6})(?!\d)", name)
        if match:
            try:
                start = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(tzinfo=tz)
            except ValueError:
                pass
    if start:
        ambiguous = start.utcoffset() != start.replace(fold=1).utcoffset()
        result.update(
            start=start.timestamp(),
            end=end.timestamp() if end and end > start else None,
            local_day=start.date().isoformat(),
            time_source="filename",
            time_warning="Ambiguous daylight-saving timestamp; first occurrence assumed"
            if ambiguous
            else None,
        )
    return result


def camera_folder(relative: Path) -> str:
    for part in relative.parts[:-1]:
        if not re.fullmatch(r"[\d_-]+|Mp4Record|recordings?", part, re.I):
            return part
    prefix = re.split(r"(?:_\d{2})?[_-]20\d{6}", relative.stem)[0]
    return prefix if prefix and not prefix.startswith("Rec") else "Unassigned camera"


def walk_recent(root: Path):
    # No writes, follows no links. Directory-local sorting avoids loading the full archive.
    with os.scandir(root) as entries:
        items = sorted(entries, key=lambda e: e.name, reverse=True)
    for entry in items:
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_dir(follow_symlinks=False):
            yield from walk_recent(Path(entry.path))
        elif Path(entry.name).suffix.lower() in VIDEO_EXTENSIONS and entry.is_file(follow_symlinks=False):
            yield Path(entry.path), entry.stat(follow_symlinks=False)


def walk_archive(root: Path):
    """Interleave camera folders and root files, newest names first."""
    with os.scandir(root) as entries:
        items = sorted(entries, key=lambda e: e.name, reverse=True)
    streams = []
    root_files = []
    for entry in items:
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_dir(follow_symlinks=False):
            streams.append(iter(walk_recent(Path(entry.path))))
        elif Path(entry.name).suffix.lower() in VIDEO_EXTENSIONS and entry.is_file(follow_symlinks=False):
            root_files.append(entry)
    if root_files:
        streams.append(((Path(entry.path), entry.stat(follow_symlinks=False)) for entry in root_files))
    while streams:
        for stream in tuple(streams):
            try:
                yield next(stream)
            except StopIteration:
                streams.remove(stream)


def scan(settings: Settings, limit: int = 0) -> dict:
    started = time.time()
    scan_id = uuid.uuid4().hex
    count = added = changed = unstable = 0
    set_state(settings, "scan", {"status": "running", "started_at": started, "seen": 0, "added": 0})
    try:
        # Resolve the input here, inside the worker subprocess, never on API startup.
        root = settings.archive.resolve(strict=True)
        if not root.is_dir():
            raise OSError("Archive source is not a directory")
        if settings.data.resolve().is_relative_to(root) or settings.cache.resolve().is_relative_to(root):
            raise ValueError("Output storage must not be inside archive, including through symlinks")
        batch = []
        last_flush = time.time()
        for path, stat in walk_archive(root):
            if time.time() - stat.st_mtime < settings.stable_seconds or stat.st_size == 0:
                unstable += 1
                continue
            relative = path.relative_to(root)
            folder = camera_folder(relative)
            metadata = parse_filename(path.name, settings.timezone)
            batch.append((relative.as_posix(), path.name, folder, stat, metadata))
            count += 1
            if len(batch) >= 100 or time.time() - last_flush >= 5 or (limit and count >= limit):
                a, c = save_batch(settings, batch, scan_id)
                added += a
                changed += c
                batch.clear()
                set_state(
                    settings,
                    "scan",
                    {
                        "status": "running",
                        "started_at": started,
                        "seen": count,
                        "added": added,
                        "changed": changed,
                        "updated_at": time.time(),
                    },
                )
                last_flush = time.time()
            if limit and count >= limit:
                break
        if batch:
            a, c = save_batch(settings, batch, scan_id)
            added += a
            changed += c
        # Retain records if files disappear. Source availability never deletes metadata.
        result = {
            "status": "complete",
            "started_at": started,
            "finished_at": time.time(),
            "seen": count,
            "added": added,
            "changed": changed,
            "unstable": unstable,
            "limited": bool(limit and count >= limit),
        }
        set_state(settings, "source", {"available": True, "checked_at": time.time()})
    except (OSError, ValueError) as exc:
        result = {
            "status": "error",
            "started_at": started,
            "finished_at": time.time(),
            "seen": count,
            "added": added,
            "error": f"Archive unavailable ({type(exc).__name__})",
        }
        set_state(
            settings, "source", {"available": False, "checked_at": time.time(), "error": result["error"]}
        )
    set_state(settings, "scan", result)
    return result


def save_batch(settings: Settings, batch: list, scan_id: str) -> tuple[int, int]:
    added = changed = 0
    with connect(settings) as conn:
        for relative, filename, folder, stat, info in batch:
            camera = conn.execute("SELECT id FROM cameras WHERE folder=?", (folder,)).fetchone()
            camera_id = camera[0] if camera else identifier("folder:" + folder)
            conn.execute(
                "INSERT OR IGNORE INTO cameras(id,name,folder,color) VALUES(?,?,?,?)",
                (camera_id, folder, folder, COLORS[int(camera_id[:2], 16) % len(COLORS)]),
            )
            rid = identifier(relative)
            previous = conn.execute("SELECT size,mtime FROM recordings WHERE id=?", (rid,)).fetchone()
            if previous and previous[0] == stat.st_size and previous[1] == stat.st_mtime:
                conn.execute("UPDATE recordings SET last_scan=?,available=1 WHERE id=?", (scan_id, rid))
                continue
            if previous:
                changed += 1
            else:
                added += 1
            conn.execute(
                """
                INSERT INTO recordings(id,path,filename,camera_id,size,mtime,start,end,local_day,
                    time_source,time_warning,stream,triggers_known,indexed_at,last_scan)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,
                    start=excluded.start,end=excluded.end,local_day=excluded.local_day,
                    time_source=excluded.time_source,time_warning=excluded.time_warning,
                    stream=excluded.stream,triggers_known=excluded.triggers_known,
                    status='pending',error=NULL,poster=0,proxy=0,sprite_json=NULL,probe_json=NULL,
                    duration=NULL,width=NULL,height=NULL,video_codec=NULL,audio_codec=NULL,
                    fps=NULL,bitrate=NULL,processed_at=NULL,available=1,last_scan=excluded.last_scan
                """,
                (
                    rid,
                    relative,
                    filename,
                    camera_id,
                    stat.st_size,
                    stat.st_mtime,
                    info["start"],
                    info["end"],
                    info["local_day"],
                    info["time_source"],
                    info["time_warning"],
                    info["stream"],
                    info["triggers_known"],
                    time.time(),
                    scan_id,
                ),
            )
            conn.execute("DELETE FROM triggers WHERE recording_id=?", (rid,))
            conn.execute("DELETE FROM recording_event_matches WHERE recording_id=?", (rid,))
            for trigger in info["triggers"]:
                conn.execute("INSERT OR IGNORE INTO triggers VALUES(?,?,?)", (rid, trigger, "filename"))
    return added, changed
