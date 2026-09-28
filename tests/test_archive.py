import os

from fastapi.testclient import TestClient

from reoui.api import create_app
from reoui.db import connect
from reoui.indexer import parse_filename, scan
from reoui.media import summarize_probe


def test_indexing_is_idempotent_and_keeps_annotations_on_changed_files(settings, archive):
    assert scan(settings)["added"] == 1
    assert scan(settings)["added"] == 0
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM recordings").fetchone()
        assert row["local_day"] == "2026-09-18"
        assert row["triggers_known"] == 0
        conn.execute("UPDATE recordings SET bookmarked=1,note='Important',proxy=1 WHERE id=?", (row["id"],))
    archive.write_bytes(b"updated copy")
    os.utime(archive, (1, 1))
    assert scan(settings)["changed"] == 1
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM recordings").fetchone()
        assert row["bookmarked"] == 1 and row["note"] == "Important"
        assert row["proxy"] == 0 and row["status"] == "pending"


def test_missing_mount_does_not_delete_catalog(settings, archive):
    scan(settings)
    archive.unlink()
    archive.parent.rmdir()
    settings.archive.rmdir()
    assert scan(settings)["status"] == "error"
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 1
        assert conn.execute("SELECT available FROM recordings").fetchone()[0] == 1


def test_full_scan_hides_missing_media_but_preserves_annotations(settings, archive):
    newer = archive.parent / "Garden_00_20260919123000.mp4"
    newer.write_bytes(b"newer recording")
    assert scan(settings)["added"] == 2
    with connect(settings) as conn:
        old_id = conn.execute("SELECT id FROM recordings WHERE filename=?", (archive.name,)).fetchone()[0]
        conn.execute("UPDATE recordings SET bookmarked=1,note='Keep this' WHERE id=?", (old_id,))

    archive.unlink()
    assert scan(settings, limit=1)["missing"] == 0
    with connect(settings) as conn:
        assert conn.execute("SELECT available FROM recordings WHERE id=?", (old_id,)).fetchone()[0] == 1

    assert scan(settings)["missing"] == 1
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT available,bookmarked,note FROM recordings WHERE id=?", (old_id,)
        ).fetchone()
        assert tuple(row) == (0, 1, "Keep this")
    with TestClient(create_app(settings)) as client:
        assert [r["filename"] for r in client.get("/api/recordings").json()["items"]] == [newer.name]
        assert client.get("/api/dates").json()[0]["day"] == "2026-09-19"
        bookmarked = client.get("/api/recordings?bookmarked=true").json()["items"]
        assert len(bookmarked) == 1 and bookmarked[0]["id"] == old_id
        assert bookmarked[0]["available"] is False
        assert client.get("/api/recordings/" + old_id).status_code == 200
        assert client.get("/api/media/" + old_id + "/original").status_code == 404
        assert client.post("/api/recordings/" + old_id + "/prepare").status_code == 409
        assert client.get("/api/status").json()["counts"]["recordings"] == 1

    archive.write_bytes(b"restored recording")
    assert scan(settings)["changed"] == 1
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT available,bookmarked,note FROM recordings WHERE id=?", (old_id,)
        ).fetchone()
        assert tuple(row) == (1, 1, "Keep this")


def test_unknown_and_dst_timestamps_are_explicit():
    assert parse_filename("renamed.mp4", "Europe/Tallinn")["start"] is None
    assert parse_filename("Garden_00_20261025033000.mp4", "Europe/Tallinn")["time_warning"]
    parsed = parse_filename("RecM03_20260918_235930_000130_6D28808_1A468F9.mp4", "Europe/Tallinn")
    assert parsed["end"] > parsed["start"]


def test_api_filters_pagination_and_metadata_unknown(settings, archive):
    other = archive.parent / "Garden_00_20260918120000.mp4"
    other.write_bytes(b"other")
    scan(settings)
    with TestClient(create_app(settings)) as client:
        first = client.get("/api/recordings?limit=1").json()
        assert len(first["items"]) == 1 and first["next_cursor"]
        second = client.get("/api/recordings", params={"limit": 1, "cursor": first["next_cursor"]}).json()
        assert second["items"][0]["id"] != first["items"][0]["id"]
        assert len(client.get("/api/recordings?event=unknown").json()["items"]) == 2
        assert client.get("/api/recordings?event=person").json()["items"] == []
        assert client.get("/api/recordings?day=bad-date").status_code == 422
        assert client.get("/api/recordings?cursor=broken").status_code == 422
        timeline = client.get("/api/timeline?day=2026-09-18").json()
        assert len(timeline["lanes"][0]["bins"]) == 96


def test_probe_handles_missing_audio_and_zero_frame_rate():
    result = summarize_probe(
        {
            "streams": [{"codec_type": "video", "codec_name": "hevc", "avg_frame_rate": "0/0"}],
            "format": {"duration": "30"},
        }
    )
    assert result["duration"] == 30 and result["audio_codec"] is None and result["fps"] is None
