import os
import time

import pytest

from reoui.config import Settings
from reoui.db import initialize


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "archive"
    source.mkdir()
    config = Settings(archive=source, data=tmp_path / "data", cache=tmp_path / "cache", stable_seconds=0)
    initialize(config)
    return config


@pytest.fixture
def archive(settings):
    folder = settings.archive / "Garden"
    folder.mkdir()
    path = folder / "Garden_00_20260918123000.mp4"
    path.write_bytes(b"sample-original-recording" * 100)
    stamp = time.time() - 600
    os.utime(path, (stamp, stamp))
    return path
