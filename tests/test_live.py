import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from reoui.api import create_app
from reoui.db import connect, set_state
from reoui.live import LiveManager, live_command, live_file

CID = "a" * 24


def camera(settings):
    meta = {"host": {"GetNetPort": {"NetPort": {"rtspEnable": 1, "rtspPort": 554}}}}
    with connect(settings) as c:
        c.execute(
            "INSERT INTO cameras(id,name,device_host,metadata) VALUES(?,?,?,?)",
            (CID, "Test", "192.0.2.1", json.dumps(meta)),
        )
    set_state(settings, "worker", {"heartbeat": time.time()})


def test_live_leases_multiple_viewers_and_authenticated_assets(settings):
    camera(settings)
    config = replace(settings, auth_token="test-token")
    with TestClient(create_app(config)) as client:
        assert client.post("/api/live/" + CID).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        first = client.post("/api/live/" + CID).json()["viewer"]
        second = client.post("/api/live/" + CID).json()["viewer"]
        assert client.post(f"/api/live/{CID}/viewers/{first}").status_code == 200
        generation = "b" * 16
        path = live_file(settings, CID, generation, "index.m3u8")
        path.parent.mkdir(parents=True)
        path.write_text("#EXTM3U\n")
        with connect(settings) as c:
            c.execute("UPDATE live_streams SET generation=?,status='ready'", (generation,))
        url = f"/api/live/{CID}/{generation}/index.m3u8"
        response = client.get(url)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        client.delete(f"/api/live/{CID}/viewers/{first}")
        assert client.get(url).status_code == 200
        client.delete(f"/api/live/{CID}/viewers/{second}")
        assert client.get(url).status_code == 404
        assert client.get(f"/api/live/{CID}/{generation}/secrets.json").status_code == 404
        assert client.post("/api/live/not-a-camera").status_code == 404


def test_live_does_not_enable_disabled_rtsp(settings):
    camera(settings)
    with connect(settings) as c:
        c.execute("UPDATE cameras SET metadata='{}'")
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/live/" + CID).status_code == 409
    with pytest.raises(ValueError):
        live_command(
            {"host": "192.0.2.1", "username": "user", "password": "secret"},
            {"metadata": "{}", "channel": 0},
            settings.cache,
        )


def test_live_rejects_escapes(settings):
    with pytest.raises(ValueError):
        live_file(settings, CID, "b" * 16, "../../secret")
    root = settings.cache / "live" / CID
    root.parent.mkdir(parents=True)
    root.symlink_to(settings.data, target_is_directory=True)
    with pytest.raises(ValueError):
        live_file(settings, CID, "b" * 16, "index.m3u8")


def test_expired_leases_stop_process_and_remove_temporary_live_data(settings):
    camera(settings)
    manager = LiveManager(settings)
    output = live_file(settings, CID, "b" * 16, "index.m3u8").parent
    output.mkdir(parents=True)
    (output / "index.m3u8").write_text("live")

    class Process:
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            return 0

    process = Process()
    manager.processes[CID] = (process, output, time.time())
    with connect(settings) as c:
        c.execute("INSERT INTO live_streams(camera_id) VALUES(?)", (CID,))
        c.execute("INSERT INTO live_viewers VALUES(?,?,?)", ("expired", CID, time.time() - 1))
    manager.tick()
    assert process.killed and not output.exists()
    with connect(settings) as c:
        assert c.execute("SELECT status FROM live_streams").fetchone()[0] == "stopped"
    manager.close()


def test_snapshot_request_is_authenticated_coalesced_and_does_not_start_live(settings):
    camera(settings)
    with TestClient(create_app(replace(settings, auth_token="snapshot-token"))) as client:
        assert client.post("/api/snapshots/" + CID).status_code == 401
        client.headers["Authorization"] = "Bearer snapshot-token"
        assert client.post("/api/snapshots/" + CID).status_code == 200
        assert client.post("/api/snapshots/" + CID).status_code == 200
        assert client.get("/api/snapshots/" + CID).json()["status"] == "pending"
        assert client.get("/api/snapshots/" + CID + "/image.jpg").status_code == 404
        assert client.post("/api/snapshots/missing").status_code == 404
    with connect(settings) as c:
        assert c.execute("SELECT COUNT(*) FROM live_snapshots").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM live_viewers").fetchone()[0] == 0


def test_snapshot_is_published_atomically_with_capture_time(settings):
    from reoui.live import snapshot_file

    camera(settings)
    manager = LiveManager(settings)
    output = snapshot_file(settings, CID, temporary=True)
    output.parent.mkdir(parents=True)
    output.write_bytes(b"new snapshot")

    class Process:
        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    with connect(settings) as c:
        c.execute(
            "INSERT INTO live_snapshots(camera_id,status,requested_at) VALUES(?,'capturing',?)",
            (CID, time.time()),
        )
    manager.snapshots[CID] = (Process(), output, time.time())
    manager.snapshot_tick()
    assert not output.exists()
    assert snapshot_file(settings, CID).read_bytes() == b"new snapshot"
    with TestClient(create_app(settings)) as client:
        result = client.get("/api/snapshots/" + CID).json()
        assert result["status"] == "ready" and result["captured_at"] > 0
        assert client.get(result["url"]).headers["cache-control"] == "no-store"
    assert not manager.snapshots
    manager.close()


def test_snapshot_timeout_keeps_previous_image_and_reports_error(settings):
    from reoui.live import snapshot_file

    camera(settings)
    manager = LiveManager(settings)
    old = snapshot_file(settings, CID)
    old.parent.mkdir(parents=True)
    old.write_bytes(b"previous")
    output = snapshot_file(settings, CID, temporary=True)
    output.write_bytes(b"incomplete")

    class Process:
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            return 0

    process = Process()
    with connect(settings) as c:
        c.execute("INSERT INTO live_snapshots VALUES(?,'capturing',?,1,NULL)", (CID, time.time() - 20))
    manager.snapshots[CID] = (process, output, time.time() - 20)
    manager.snapshot_tick()
    assert process.killed and not output.exists() and old.read_bytes() == b"previous"
    with connect(settings) as c:
        row = c.execute("SELECT * FROM live_snapshots").fetchone()
        assert row["status"] == "error" and row["captured_at"] == 1
    manager.close()
