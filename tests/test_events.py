import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from reoui.api import create_app
from reoui.cameras import reolink_time
from reoui.db import connect
from reoui.events import reconcile, recover_events, search_window
from reoui.indexer import scan


def seed(settings, archive):
    duplicate = archive.parent / "duplicate" / archive.name
    duplicate.parent.mkdir()
    duplicate.write_bytes(archive.read_bytes())
    scan(settings)
    with connect(settings) as conn:
        r = conn.execute("SELECT * FROM recordings LIMIT 1").fetchone()
        return r["camera_id"], r["start"]


def device(settings, cid, start, key="device", kinds=None):
    with connect(settings) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO device_recordings VALUES(?,?,?,?,?,?,?)",
            (
                key,
                cid,
                start,
                start + 50,
                1,
                json.dumps({"name": "camera-recording.mp4"}),
                json.dumps(kinds if kinds is not None else ["person"]),
            ),
        )


def test_matches_ftp_offset_and_duplicate_backups_with_evidence(settings, archive):
    cid, start = seed(settings, archive)
    device(settings, cid, start - 9)
    reconcile(settings, cid)
    reconcile(settings, cid)
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM triggers").fetchone()[0] == 2
        assert (
            conn.execute("SELECT COUNT(*) FROM recording_event_matches WHERE offset_seconds=9").fetchone()[0]
            == 2
        )
    with TestClient(create_app(settings)) as client:
        rows = client.get("/api/recordings?event=person").json()["items"]
        assert len(rows) == 2
        detail = client.get("/api/recordings/" + rows[0]["id"]).json()
        assert detail["event_match"]["device_filename"] == "camera-recording.mp4"
        assert detail["trigger_sources"][0]["source"] == "device_recording_time_match"
        assert client.get("/api/status").json()["counts"]["event_tagged"] == 2


def test_ambiguous_matches_remove_previous_inferences(settings, archive):
    cid, start = seed(settings, archive)
    device(settings, cid, start - 6)
    reconcile(settings, cid)
    device(settings, cid, start - 3, "second", ["vehicle"])
    reconcile(settings, cid)
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM triggers").fetchone()[0] == 0
        assert conn.execute("SELECT SUM(triggers_known) FROM recordings").fetchone()[0] == 0


def test_general_overlap_and_other_cameras_are_not_event_matches(settings, archive):
    cid, start = seed(settings, archive)
    device(settings, cid, start - 30)
    device(settings, "other-camera", start - 6, "other")
    reconcile(settings, cid)
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM triggers").fetchone()[0] == 0


class FakeCamera:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def read(self, command, param):
        assert command == "Search"
        self.calls += 1
        return self.response


def test_historical_search_persists_flags_and_resumes(settings, archive):
    cid, start = seed(settings, archive)
    tz = ZoneInfo(settings.timezone)
    item = {
        "name": "RecM03_DST20260918_122951_123041_AB288A0_1AA5325.mp4",
        "StartTime": reolink_time(datetime.fromtimestamp(start - 9, tz)),
        "EndTime": reolink_time(datetime.fromtimestamp(start + 41, tz)),
        "type": "main",
        "size": "1",
    }
    cam = FakeCamera({"code": 0, "value": {"SearchResult": {"File": [item]}}})
    asyncio.run(recover_events(settings, cam, cid, 0, tz))
    assert cam.calls == 1
    asyncio.run(recover_events(settings, cam, cid, 0, tz))
    assert cam.calls == 1
    with connect(settings) as conn:
        assert conn.execute("SELECT status FROM event_search_days").fetchone()[0] == "complete"
        assert conn.execute("SELECT COUNT(*) FROM triggers").fetchone()[0] > 0
    # An archive arriving after the camera search can still use preserved metadata.
    late = archive.parent / "late" / archive.name
    late.parent.mkdir()
    late.write_bytes(archive.read_bytes())
    scan(settings)
    asyncio.run(recover_events(settings, cam, cid, 0, tz))
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recordings WHERE triggers_known=1").fetchone()[0] == 3


def test_search_failure_is_not_recorded_as_empty_history(settings, archive):
    cid, _ = seed(settings, archive)
    cam = FakeCamera({"code": 1})
    asyncio.run(recover_events(settings, cam, cid, 0, ZoneInfo(settings.timezone)))
    with connect(settings) as conn:
        assert conn.execute("SELECT status FROM event_search_days").fetchone()[0] == "error"
    asyncio.run(recover_events(settings, cam, cid, 0, ZoneInfo(settings.timezone)))
    assert cam.calls == 1  # Backoff before retry.


def test_large_search_results_are_subdivided():
    class CappedCamera(FakeCamera):
        async def read(self, command, param):
            self.calls += 1
            return {"code": 0, "value": {"SearchResult": {"File": [{}] * 256 if self.calls == 1 else []}}}

    cam = CappedCamera(None)
    assert asyncio.run(search_window(cam, 0, datetime(2026, 9, 18), datetime(2026, 9, 19), [64])) == []
    assert cam.calls == 3
