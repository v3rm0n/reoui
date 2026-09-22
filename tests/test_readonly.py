import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from reoui.api import create_app
from reoui.cameras import ALLOWED_COMMANDS, ReadOnlyCamera, ReadOnlyViolation, redact
from reoui.config import archive_file
from reoui.db import connect
from reoui.indexer import scan


@pytest.mark.parametrize(
    "command",
    [
        "SetNetPort",
        "SetRec",
        "SetManualRec",
        "Reboot",
        "Upgrade",
        "PtzCtrl",
        "SetTime",
        "Snap",
        "Search;SetRec",
        "login",
        None,
    ],
)
def test_camera_mutation_rejected_before_network(command):
    client = ReadOnlyCamera({"host": "192.0.2.1", "username": "test", "password": "secret"})
    # No session exists. Validation must reject before attempting transport access.
    with pytest.raises(ReadOnlyViolation):
        asyncio.run(client.request([{"cmd": command}]))


def test_camera_allowlist_has_only_known_reads_and_session_operations():
    assert all(cmd.startswith("Get") or cmd in {"Login", "Logout", "Search"} for cmd in ALLOWED_COMMANDS)
    assert redact({"Token": {"name": "hidden"}, "password": "hidden", "DevInfo": {"model": "camera"}}) == {
        "Token": "[redacted]",
        "password": "[redacted]",
        "DevInfo": {"model": "camera"},
    }


def test_output_directories_cannot_overlap_archive(settings):
    with pytest.raises(ValueError):
        replace(settings, cache=settings.archive / "cache").prepare()
    with pytest.raises(ValueError):
        replace(settings, data=settings.archive.parent).prepare()


def test_source_traversal_and_symlinks_rejected(settings, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"private")
    (settings.archive / "alias.mp4").symlink_to(outside)
    for name in ("../outside.mp4", "alias.mp4", str(outside)):
        with pytest.raises(ValueError):
            archive_file(settings, name)


def test_authenticated_ranges_and_bookmarks_do_not_change_source(settings, archive):
    before = archive.read_bytes()
    stat = archive.stat()
    scan(settings)
    secured = replace(settings, auth_token="test-access-token")
    with connect(settings) as conn:
        rid = conn.execute("SELECT id FROM recordings").fetchone()[0]
    with TestClient(create_app(secured)) as client:
        assert client.get("/api/recordings").status_code == 401
        assert client.get(f"/api/media/{rid}/original").status_code == 401
        assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"token": "test-access-token"}).status_code == 200
        response = client.get(f"/api/media/{rid}/original", headers={"Range": "bytes=5-19"})
        assert response.status_code == 206
        assert response.content == before[5:20]
        assert (
            client.patch(f"/api/recordings/{rid}", json={"bookmarked": True, "note": "Keep this"}).status_code
            == 200
        )
        assert client.get(f"/api/recordings/{rid}").json()["note"] == "Keep this"
        assert (
            client.patch(
                f"/api/recordings/{rid}", json={"note": "x"}, headers={"Origin": "https://untrusted.test"}
            ).status_code
            == 403
        )
    assert archive.read_bytes() == before
    assert archive.stat().st_mtime_ns == stat.st_mtime_ns
