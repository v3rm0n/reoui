"""New archive files should reach the catalog without requesting a scan."""

import os
import subprocess
import sys
import time

from fastapi.testclient import TestClient

from reoui.api import create_app
from reoui.db import connect, get_state
from reoui.indexer import scan


def wait_for(predicate, process, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Worker exited early: {process.communicate()[1]}")
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("Worker did not index the new recording in time")


def test_worker_indexes_recording_added_after_startup(settings):
    camera = settings.archive / "Garden"
    camera.mkdir()
    first = camera / "Garden_00_20260918123000.mp4"
    first.write_bytes(b"first recording")
    env = os.environ.copy()
    env.update(
        REOUI_ARCHIVE=str(settings.archive),
        REOUI_DATA=str(settings.data),
        REOUI_CACHE=str(settings.cache),
        REOUI_STABLE_SECONDS="0",
        REOUI_SCAN_INTERVAL="1",
        REOUI_CAMERA_INTERVAL="300",
    )
    env.pop("REOUI_CAMERAS_FILE", None)
    env.pop("REOUI_CONFIG", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "reoui.cli", "worker"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        def first_scan_complete():
            with connect(settings) as conn:
                return (
                    conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 1
                    and get_state(conn, "scan", {}).get("status") == "complete"
                )

        wait_for(first_scan_complete, process)
        second = camera / "Garden_00_20260919123000.mp4"
        second.write_bytes(b"second recording")

        def both_indexed():
            with connect(settings) as conn:
                return conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 2

        wait_for(both_indexed, process)
        with TestClient(create_app(settings)) as client:
            assert client.get("/api/status").json()["counts"]["recordings"] == 2
            assert client.get("/api/dates").json()[0]["day"] == "2026-09-19"
            assert len(client.get("/api/recordings?day=2026-09-19").json()["items"]) == 1
    finally:
        process.terminate()
        process.communicate(timeout=10)


def test_limited_scan_prioritizes_new_root_file(settings):
    (settings.archive / "Garden_00_20260918123000.mp4").write_bytes(b"old")
    (settings.archive / "Garden_00_20260919123000.mp4").write_bytes(b"new")
    result = scan(settings, limit=1)
    assert result["added"] == 1 and result["limited"]
    with connect(settings) as conn:
        assert conn.execute("SELECT filename FROM recordings").fetchone()[0] == "Garden_00_20260919123000.mp4"
