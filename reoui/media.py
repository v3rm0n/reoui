from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from PIL import Image

from .config import Settings, archive_file
from .db import connect

SUFFIX = {"poster": "poster.jpg", "sprite": "sprite.jpg", "proxy": "playback.mp4"}


def cache_path(settings: Settings, rid: str, kind: str) -> Path:
    if len(rid) != 24 or any(c not in "0123456789abcdef" for c in rid):
        raise ValueError("Invalid recording identifier")
    return settings.cache / rid[:2] / rid / SUFFIX[kind]


def run_media(arguments: list[str], timeout: int = 90) -> bytes:
    result = subprocess.run(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        # Diagnostics are local; avoid copying paths or arbitrary embedded tags into errors.
        raise RuntimeError(f"{arguments[0]} could not decode this recording")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(
        run_media(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=45
        )
    )


def summarize_probe(info: dict) -> dict:
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    if not video:
        raise ValueError("No video stream found")
    fmt = info.get("format", {})
    try:
        fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        fps = None
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    return {
        "duration": duration if math.isfinite(duration) and duration > 0 else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "fps": fps,
        "bitrate": int(fmt.get("bit_rate") or 0) or None,
    }


def reserve_cache(settings: Settings, needed: int) -> None:
    if needed > settings.cache_bytes:
        raise ValueError("Playback copy exceeds the configured cache budget")
    with connect(settings) as conn:
        used = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM cache_files").fetchone()[0]
        candidates = conn.execute(
            """SELECT recording_id,kind,bytes FROM cache_files
            WHERE kind='proxy' AND accessed_at<? ORDER BY accessed_at""",
            (time.time() - 3600,),
        ).fetchall()
        for row in candidates:
            if used + needed <= settings.cache_bytes:
                break
            cache_path(settings, row[0], row[1]).unlink(missing_ok=True)
            conn.execute("DELETE FROM cache_files WHERE recording_id=? AND kind=?", (row[0], row[1]))
            conn.execute("UPDATE recordings SET proxy=0 WHERE id=?", (row[0],))
            used -= row[2]
        if used + needed > settings.cache_bytes:
            raise ValueError(
                "Cache budget reached; increase the quota or wait for older playback copies to expire"
            )
    if shutil.disk_usage(settings.cache).free < needed + 500_000_000:
        raise ValueError("Not enough free space for media processing")


def publish(settings: Settings, rid: str, kind: str, temporary: Path) -> None:
    target = cache_path(settings, rid, kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    size = temporary.stat().st_size
    reserve_cache(settings, size)
    os.replace(temporary, target)
    with connect(settings) as conn:
        conn.execute("INSERT OR REPLACE INTO cache_files VALUES(?,?,?,?)", (rid, kind, size, time.time()))


def assert_unchanged(path: Path, row) -> None:
    stat = path.stat()
    if stat.st_size != row["size"] or stat.st_mtime != row["mtime"]:
        raise ValueError("Recording changed during processing; rescan after the copy finishes")


def prepare(settings: Settings, rid: str, proxy: bool = False) -> None:
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id=?", (rid,)).fetchone()
    if not row:
        raise ValueError("Recording no longer exists")
    source = archive_file(settings, row["path"])
    assert_unchanged(source, row)
    if proxy:
        return prepare_proxy(settings, row, source)
    reserve_cache(settings, 2_000_000)
    info = probe(source)
    summary = summarize_probe(info)
    # A source duration wins over a filename's rough end time, with provenance retained.
    end = row["start"] + summary["duration"] if row["start"] and summary["duration"] else row["end"]
    duration = summary["duration"] or 1
    with tempfile.TemporaryDirectory(dir=settings.cache, prefix="prepare-") as temporary:
        work = Path(temporary)
        poster = work / "poster.jpg"
        run_media(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "2",
                "-ss",
                str(min(duration * 0.25, 10)),
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(960,iw)':-2",
                "-q:v",
                "3",
                "-y",
                str(poster),
            ]
        )
        tail_margin = max(0.25, 2 / (summary["fps"] or 10))
        timestamps = [round(i * max(0, duration - tail_margin) / 11, 3) for i in range(12)]
        canvas = Image.new("RGB", (240 * 6, 135 * 2), "#101719")
        for i, stamp in enumerate(timestamps):
            frame = work / "frame.jpg"
            frame.unlink(missing_ok=True)
            run_media(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-threads",
                    "2",
                    "-ss",
                    str(stamp),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=240:135:force_original_aspect_ratio=decrease,format=yuv444p,pad=240:135:(ow-iw)/2:(oh-ih)/2",
                    "-q:v",
                    "4",
                    "-y",
                    str(frame),
                ],
                timeout=30,
            )
            with Image.open(frame) as image:
                canvas.paste(image, ((i % 6) * 240, (i // 6) * 135))
        sprite = work / "sprite.jpg"
        canvas.save(sprite, quality=76)
        assert_unchanged(source, row)
        publish(settings, rid, "poster", poster)
        publish(settings, rid, "sprite", sprite)
    manifest = {"width": 240, "height": 135, "columns": 6, "rows": 2, "timestamps": timestamps}
    with connect(settings) as conn:
        conn.execute(
            """UPDATE recordings SET duration=?,width=?,height=?,video_codec=?,audio_codec=?,
            fps=?,bitrate=?,end=?,probe_json=?,poster=1,sprite_json=?,status='ready',error=NULL,
            processed_at=? WHERE id=?""",
            (*summary.values(), end, json.dumps(info), json.dumps(manifest), time.time(), rid),
        )


def prepare_proxy(settings: Settings, row, source: Path) -> None:
    rid = row["id"]
    duration = row["duration"] or summarize_probe(probe(source))["duration"]
    if not duration:
        raise ValueError("Cannot validate playback completeness without a source duration")
    budget = max(5_000_000, int(duration * 350_000))
    reserve_cache(settings, budget)
    with tempfile.TemporaryDirectory(dir=settings.cache, prefix="playback-") as temporary:
        target = Path(temporary) / "playback.mp4"
        run_media(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "2",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                "scale='min(1280,iw)':-2,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "25",
                "-maxrate",
                "2400k",
                "-bufsize",
                "4800k",
                "-threads",
                "2",
                "-force_key_frames",
                "expr:gte(t,n_forced*2)",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                "-fs",
                str(budget),
                "-y",
                str(target),
            ],
            timeout=min(3600, max(120, int(duration * 4))),
        )
        info = summarize_probe(probe(target))
        if not info["duration"] or info["duration"] < duration - 1:
            raise ValueError("Playback preparation did not produce the complete recording")
        assert_unchanged(source, row)
        publish(settings, rid, "proxy", target)
    with connect(settings) as conn:
        conn.execute("UPDATE recordings SET proxy=1 WHERE id=?", (rid,))
