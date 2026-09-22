"""Serve an isolated synthetic archive for browser tests. Never uses camera secrets."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import uvicorn

from reoui.api import create_app
from reoui.config import Settings
from reoui.db import connect, initialize
from reoui.indexer import scan
from reoui.media import prepare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()
    project = Path(__file__).resolve().parent.parent
    root = project / ".local" / "browser-test"
    archive = root / "recordings"
    archive.mkdir(parents=True, exist_ok=True)
    sample = root / "synthetic.mp4"
    if not sample.is_file():
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=640x360:rate=15",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(sample),
            ],
            check=True,
        )
    for camera in ["Fixture Garden", "Fixture Gate"]:
        folder = archive / camera
        folder.mkdir(exist_ok=True)
        for minute in (0, 10, 20):
            output = folder / f"{camera}_00_2026091812{minute:02}00.mp4"
            if not output.exists():
                shutil.copy2(sample, output)
    settings = Settings(
        archive=archive,
        data=root / "data",
        cache=root / "cache",
        stable_seconds=0,
        web=project / "frontend/dist",
    )
    initialize(settings)
    scan(settings)
    with connect(settings) as conn:
        rows = conn.execute("SELECT id,status FROM recordings").fetchall()
        conn.execute("UPDATE recordings SET bookmarked=0,note=''")
        for index, row in enumerate(rows):
            conn.execute(
                "INSERT OR IGNORE INTO triggers VALUES(?,?,?)",
                (row["id"], "person" if index % 2 else "vehicle", "synthetic_test_fixture"),
            )
        conn.execute("UPDATE recordings SET triggers_known=1")
    for row in rows:
        if row["status"] != "ready":
            prepare(settings, row["id"])
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
