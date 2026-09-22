import hashlib
import shutil
import subprocess

import pytest

from reoui.db import connect
from reoui.indexer import scan
from reoui.media import cache_path, prepare, probe, summarize_probe


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_media_pipeline_keeps_original_and_publishes_playable_assets(settings):
    folder = settings.archive / "Sample"
    folder.mkdir()
    original = folder / "Sample_00_20260918123000.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "1.2",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(original),
        ],
        check=True,
    )
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    source_mtime = original.stat().st_mtime_ns
    scan(settings)
    with connect(settings) as conn:
        rid = conn.execute("SELECT id FROM recordings").fetchone()[0]
    # A user can request compatible playback before background inspection finishes.
    prepare(settings, rid, proxy=True)
    prepare(settings, rid)
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id=?", (rid,)).fetchone()
        assert row["status"] == "ready" and row["poster"] == 1 and row["proxy"] == 1
        assert row["video_codec"] == "mpeg4"  # Original metadata, not proxy metadata.
        assert row["sprite_json"] is not None
    result = summarize_probe(probe(cache_path(settings, rid, "proxy")))
    assert result["video_codec"] == "h264" and result["audio_codec"] == "aac"
    assert abs(result["duration"] - row["duration"]) < 0.3
    assert cache_path(settings, rid, "poster").is_file()
    assert cache_path(settings, rid, "sprite").is_file()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    assert original.stat().st_mtime_ns == source_mtime
