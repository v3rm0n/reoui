from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from reoui.api import create_app
from reoui.db import connect
from reoui.indexer import scan
from reoui.media import cache_path


class Metadata(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = {}
        self.canonical = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.tags[attrs.get("property") or attrs.get("name")] = attrs.get("content")
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs["href"]


@pytest.fixture
def shared_web(settings, archive, tmp_path):
    scan(settings)
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(Path("frontend/index.html").read_text())
    settings = replace(settings, auth_token="secret", public_origin="https://public.example", web=web)
    with connect(settings) as conn:
        rid = conn.execute("SELECT id FROM recordings").fetchone()[0]
        conn.execute("UPDATE recordings SET duration=98,note='Private note',poster=1 WHERE id=?", (rid,))
        conn.execute("UPDATE cameras SET name=?", ('Garden "<&>"',))
    poster = cache_path(settings, rid, "poster")
    poster.parent.mkdir(parents=True)
    Image.new("RGB", (960, 534), "green").save(poster)
    return settings, rid


def test_share_metadata_is_in_initial_html_and_image_requires_only_share_link(shared_web):
    settings, rid = shared_web
    app = create_app(settings)
    with TestClient(app, base_url=settings.public_origin) as owner:
        owner.post("/api/login", json={"token": "secret"})
        link = owner.post(f"/api/recordings/{rid}/shares", json={}).json()
    with TestClient(app, base_url="https://tailnet.example") as guest:
        path = link["url"].removeprefix(settings.public_origin)
        response = guest.get(path, headers={"User-Agent": "facebookexternalhit/1.1"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
        meta = Metadata(response.text)
        assert meta.tags["og:title"] == 'Garden "<&>" · Shared recording · ReoUI'
        assert meta.tags["og:type"] == "video.other"
        assert meta.tags["og:site_name"] == "ReoUI"
        assert meta.tags["og:url"] == meta.canonical == link["url"]
        assert "18 September 2026 at 12:30:00" in meta.tags["og:description"]
        assert "0:01:38" in meta.tags["description"]
        assert meta.tags["video:duration"] == "98"
        assert meta.tags["video:release_date"].endswith("+03:00")
        assert meta.tags["twitter:card"] == "summary_large_image"
        image_url = meta.tags["og:image"]
        assert image_url.startswith(settings.public_origin + "/api/shared/")
        assert image_url == meta.tags["og:image:secure_url"] == meta.tags["twitter:image"]
        assert meta.tags["og:image:type"] == "image/jpeg"
        assert meta.tags["og:image:width"] == "960" and meta.tags["og:image:height"] == "534"
        image = guest.get(image_url)
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert guest.head(image_url).status_code == 200
        assert guest.head(path).status_code == 200
        assert 'Garden "<&>"' not in response.text
        assert "Private note" not in response.text and archive_path(settings, rid) not in response.text
        assert 'id="root"' in response.text and 'src="/src/main.tsx"' in response.text
        assert guest.get("/").text == (settings.web / "index.html").read_text()


def archive_path(settings, rid):
    with connect(settings) as conn:
        return conn.execute("SELECT path FROM recordings WHERE id=?", (rid,)).fetchone()[0]


@pytest.mark.parametrize("reason", ["expired", "revoked", "missing"])
def test_unavailable_share_does_not_expose_metadata(shared_web, reason):
    settings, rid = shared_web
    app = create_app(settings)
    with TestClient(app, base_url=settings.public_origin) as owner:
        owner.post("/api/login", json={"token": "secret"})
        link = owner.post(f"/api/recordings/{rid}/shares", json={}).json()
        with connect(settings) as conn:
            if reason == "expired":
                conn.execute("UPDATE recording_shares SET expires_at=0")
            elif reason == "revoked":
                conn.execute("DELETE FROM recording_shares")
            else:
                conn.execute("UPDATE recordings SET available=0 WHERE id=?", (rid,))
    with TestClient(app, base_url=settings.public_origin) as guest:
        response = guest.get(link["url"])
        assert response.status_code == 404
        assert "og:image" not in response.text and "og:title" not in response.text
        assert guest.get(link["url"].replace("/share/", "/api/shared/") + "/media/poster").status_code == 404


@pytest.mark.parametrize("poster", [False, True])
def test_share_without_ready_image_has_text_metadata(shared_web, poster):
    settings, rid = shared_web
    cache_path(settings, rid, "poster").unlink()
    settings = replace(settings, public_origin="", auth_token="")
    with connect(settings) as conn:
        conn.execute("UPDATE recordings SET start=NULL,duration=NULL,poster=? WHERE id=?", (poster, rid))
    with TestClient(create_app(settings), base_url="http://local.example:8090") as client:
        link = client.post(f"/api/recordings/{rid}/shares", json={}).json()
        meta = Metadata(client.get(link["url"]).text)
        assert meta.tags["og:url"] == link["url"]
        assert meta.tags["twitter:card"] == "summary"
        assert "og:image" not in meta.tags and "twitter:image" not in meta.tags
        assert "video:release_date" not in meta.tags and "video:duration" not in meta.tags
