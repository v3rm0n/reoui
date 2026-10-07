from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from reoui.api import create_app
from reoui.config import Settings
from reoui.db import connect
from reoui.indexer import scan


def test_public_origin_allows_proxied_login_and_changes(settings, archive):
    scan(settings)
    settings = replace(settings, public_origin="https://archive.example:8443", auth_token="secret")
    with connect(settings) as conn:
        rid = conn.execute("SELECT id FROM recordings").fetchone()[0]
    with TestClient(create_app(settings), base_url="https://backend:8090") as client:
        headers = {"Origin": settings.public_origin}
        assert client.post("/api/login", json={"token": "wrong"}, headers=headers).status_code == 401
        assert client.post("/api/login", json={"token": "secret"}, headers=headers).status_code == 200
        assert (
            client.patch(
                f"/api/recordings/{rid}", json={"note": "Through proxy"}, headers=headers
            ).status_code
            == 200
        )
        for origin in ("https://evil.example", "null", "http://backend:8090"):
            forged = {"Origin": origin, "X-Forwarded-Host": origin, "X-Forwarded-Proto": "https"}
            assert client.post("/api/login", json={"token": "secret"}, headers=forged).status_code == 403
            assert (
                client.patch(f"/api/recordings/{rid}", json={"note": "Rejected"}, headers=forged).status_code
                == 403
            )
        assert client.get(f"/api/recordings/{rid}").json()["note"] == "Through proxy"


def test_default_origin_check(settings):
    settings = replace(settings, auth_token="secret")
    with TestClient(create_app(settings)) as client:
        for origin, expected in (("http://testserver", 200), ("https://evil.example", 403)):
            assert (
                client.post("/api/login", json={"token": "secret"}, headers={"Origin": origin}).status_code
                == expected
            )


def test_public_origin_environment(monkeypatch):
    monkeypatch.setenv("REOUI_PUBLIC_ORIGIN", " https://archive.example/ ")
    assert Settings.from_env().public_origin == "https://archive.example"


@pytest.mark.parametrize(
    "origin",
    ["*", "null", "ftp://archive.example", "https://user@archive.example", "https://archive.example/path"],
)
def test_invalid_public_origin_rejected(settings, origin):
    with pytest.raises(ValueError, match="REOUI_PUBLIC_ORIGIN"):
        replace(settings, public_origin=origin).prepare()


def test_https_public_origin_sets_secure_cookie_on_http_upstream(settings):
    settings = replace(settings, auth_token="secret", public_origin="https://archive.example")
    with TestClient(create_app(settings), base_url="http://backend:8090") as client:
        response = client.post(
            "/api/login", json={"token": "secret"}, headers={"Origin": settings.public_origin}
        )
        assert response.status_code == 200
        assert "; Secure" in response.headers["set-cookie"]
        cookie = response.headers["set-cookie"].split(";", 1)[0]
        # The browser sends its Secure cookie over HTTPS to the proxy;
        # the proxy then forwards it on the internal HTTP request.
        assert client.get("/api/status", headers={"Cookie": cookie}).status_code == 200


def test_additional_origin_allows_login_and_sharing(settings, archive):
    scan(settings)
    settings = replace(
        settings,
        auth_token="secret",
        public_origin="https://public.example",
        allowed_origins=("https://private.example.ts.net",),
    )
    with connect(settings) as conn:
        rid = conn.execute("SELECT id FROM recordings").fetchone()[0]
    with TestClient(create_app(settings), base_url="https://private.example.ts.net") as client:
        headers = {"Origin": "https://private.example.ts.net"}
        assert client.post("/api/login", json={"token": "secret"}, headers=headers).status_code == 200
        assert (
            client.patch(f"/api/recordings/{rid}", json={"note": "Tailnet"}, headers=headers).status_code
            == 200
        )
        share = client.post(f"/api/recordings/{rid}/shares", json={}, headers=headers)
        assert share.status_code == 201
        assert share.json()["url"].startswith("https://public.example/share/")
        for origin in ("https://evil.example", "https://private.example.ts.net.evil.example", "null"):
            assert client.post("/api/logout", headers={"Origin": origin}).status_code == 403
        assert client.post("/api/logout", headers=headers).status_code == 200


def test_allowed_origins_environment(monkeypatch):
    monkeypatch.setenv("REOUI_ALLOWED_ORIGINS", " https://one.example/ , ,https://two.example ")
    assert Settings.from_env().allowed_origins == ("https://one.example", "https://two.example")


@pytest.mark.parametrize("origin", ["*", "null", "ftp://archive.example", "https://archive.example/path"])
def test_invalid_allowed_origin_rejected(settings, origin):
    with pytest.raises(ValueError, match="REOUI_ALLOWED_ORIGINS"):
        replace(settings, allowed_origins=(origin,)).prepare()
