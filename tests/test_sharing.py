import hashlib
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from reoui.api import create_app
from reoui.db import connect
from reoui.indexer import scan


@pytest.fixture
def secured(settings, archive):
    scan(settings)
    return replace(settings, auth_token="secret", public_origin="https://archive.example")


def recording_id(settings):
    with connect(settings) as conn:
        return conn.execute("SELECT id FROM recordings").fetchone()[0]


def create_link(client, rid):
    response = client.post(f"/api/recordings/{rid}/shares", json={"expires_in": 3600})
    assert response.status_code == 201
    return response.json()


def test_session_expiry_logout_rotation_and_legacy_cookie(secured):
    app = create_app(secured)
    with TestClient(app, base_url=secured.public_origin) as client:
        assert client.get("/api/session").json() == {"authenticated": False, "auth_required": True}
        client.cookies.set("reoui_session", hashlib.sha256(b"secret").hexdigest())
        assert client.get("/api/status").status_code == 401
        client.cookies.clear()
        response = client.post("/api/login", json={"token": "secret"})
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
        session = client.cookies.get("reoui_session")
        assert client.get("/api/status").status_code == 200
        assert client.post("/api/logout").status_code == 200
        client.cookies.set("reoui_session", session)
        assert client.get("/api/status").status_code == 401
        client.cookies.clear()
        client.post("/api/login", json={"token": "secret"})
        with connect(secured) as conn:
            conn.execute("UPDATE auth_sessions SET expires_at=0")
        assert client.get("/api/status").status_code == 401
        client.post("/api/login", json={"token": "secret"})
        session = client.cookies.get("reoui_session")
    with TestClient(
        create_app(replace(secured, auth_token="rotated")), base_url=secured.public_origin
    ) as client:
        client.cookies.set("reoui_session", session)
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/status", headers={"Authorization": "Bearer rotated"}).status_code == 200


def test_login_throttling_and_unicode(secured):
    with TestClient(create_app(secured)) as client:
        for _ in range(10):
            assert client.post("/api/login", json={"token": "wrong 🔒"}).status_code == 401
        assert client.post("/api/login", json={"token": "secret"}).status_code == 429
        with connect(secured) as conn:
            conn.execute("UPDATE login_attempts SET reset_at=0")
        assert client.post("/api/login", json={"token": "secret"}).status_code == 200


def test_share_scope_ranges_revocation_and_restart(secured, archive):
    rid = recording_id(secured)
    app = create_app(secured)
    with TestClient(app, base_url=secured.public_origin) as owner:
        assert owner.post(f"/api/recordings/{rid}/shares", json={}).status_code == 401
        owner.post("/api/login", json={"token": "secret"})
        link = create_link(owner, rid)
        assert link["url"].startswith(secured.public_origin + "/share/")
        token = link["url"].rsplit("/", 1)[1]
        assert len(token) == 43
        listed = owner.get(f"/api/recordings/{rid}/shares").json()
        assert listed[0]["id"] == link["id"] and "url" not in listed[0]
        with connect(secured) as conn:
            stored = conn.execute("SELECT token_hash FROM recording_shares").fetchone()[0]
            assert stored == hashlib.sha256(token.encode()).hexdigest()
        # Link survives app recreation and does not grant an archive session.
        with TestClient(create_app(secured)) as guest:
            details = guest.get(f"/api/shared/{token}")
            assert details.status_code == 200
            assert set(details.json()) == {
                "camera_name",
                "start",
                "end",
                "duration",
                "video_codec",
                "expires_at",
                "poster",
                "proxy",
                "timezone",
            }
            assert details.headers["cache-control"] == "no-store"
            assert details.headers["referrer-policy"] == "no-referrer"
            response = guest.get(f"/api/shared/{token}/media/original", headers={"Range": "bytes=5-19"})
            assert response.status_code == 206 and response.content == archive.read_bytes()[5:20]
            assert response.headers["cache-control"] == "no-store"
            head = guest.head(f"/api/shared/{token}/media/original")
            assert head.status_code == 200 and head.content == b""
            assert int(head.headers["content-length"]) == archive.stat().st_size
            assert (
                guest.get(f"/api/shared/{token}/media/original?download=true")
                .headers["content-disposition"]
                .startswith("attachment")
            )
            for path in ("/api/recordings", "/api/cameras", "/api/status", f"/api/media/{rid}/original"):
                assert guest.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 401
            assert guest.post(f"/api/shared/{token}").status_code == 401
            assert guest.get(f"/api/shared/{token}/media/sprite").status_code == 401
            assert guest.get(f"/api/shared/{'a' * 43}").status_code == 404
            assert owner.delete(f"/api/recordings/{rid}/shares/{link['id']}").status_code == 200
            assert guest.get(f"/api/shared/{token}").status_code == 404
            assert guest.get(f"/api/shared/{token}/media/original").status_code == 404


def test_expired_missing_recording_and_validation(secured):
    rid = recording_id(secured)
    with TestClient(create_app(secured), base_url=secured.public_origin) as owner:
        owner.post("/api/login", json={"token": "secret"})
        for seconds in (0, 3599, 30 * 86400 + 1):
            assert (
                owner.post(f"/api/recordings/{rid}/shares", json={"expires_in": seconds}).status_code == 422
            )
        assert owner.post("/api/recordings/missing/shares", json={}).status_code == 404
        link = create_link(owner, rid)
        token = link["url"].rsplit("/", 1)[1]
        with connect(secured) as conn:
            conn.execute("UPDATE recording_shares SET expires_at=0")
        assert owner.get(f"/api/shared/{token}/media/original").status_code == 404
        assert owner.get(f"/api/recordings/{rid}/shares").json() == []
        link = create_link(owner, rid)
        token = link["url"].rsplit("/", 1)[1]
        with connect(secured) as conn:
            conn.execute("UPDATE recordings SET available=0 WHERE id=?", (rid,))
        assert owner.get(f"/api/shared/{token}").status_code == 404
        assert owner.post(f"/api/recordings/{rid}/shares", json={}).status_code == 404


def test_share_origin_and_cross_origin_changes(settings, archive):
    scan(settings)
    rid = recording_id(settings)
    with TestClient(create_app(settings), base_url="http://local.example:8090") as client:
        assert create_link(client, rid)["url"].startswith("http://local.example:8090/share/")
        assert (
            client.post(
                f"/api/recordings/{rid}/shares", json={}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.post("/api/logout", headers={"Origin": "https://evil.example"}).status_code == 403
