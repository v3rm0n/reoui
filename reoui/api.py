from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date as Date
from datetime import datetime, timedelta
from datetime import time as Time
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, archive_file
from .db import connect, enqueue, get_state, initialize, serialize_recording
from .live import LEASE_SECONDS, live_file, snapshot_file
from .media import cache_path


class Annotation(BaseModel):
    bookmarked: bool | None = None
    note: str | None = Field(default=None, max_length=5000)


class Login(BaseModel):
    token: str = Field(max_length=256)


class Share(BaseModel):
    expires_in: int = Field(default=7 * 86400, ge=3600, le=30 * 86400)


class Mapping(BaseModel):
    archive_camera_id: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    initialize(settings)

    @asynccontextmanager
    async def lifespan(_app):
        yield

    app = FastAPI(
        title="ReoUI", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    def session_hash(token: str) -> str:
        # Changing the access token invalidates all previously issued sessions.
        return hmac.new(settings.auth_token.encode(), token.encode(), hashlib.sha256).hexdigest()

    def authorized(request: Request) -> bool:
        if not settings.auth_token:
            return True
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        if hmac.compare_digest(bearer.encode(), settings.auth_token.encode()):
            return True
        cookie = request.cookies.get("reoui_session", "")
        if not cookie:
            return False
        with connect(settings) as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM auth_sessions WHERE token_hash=? AND expires_at>?",
                    (session_hash(cookie), time.time()),
                ).fetchone()
                is not None
            )

    @app.middleware("http")
    async def protection(request: Request, call_next):
        public_share = request.method in ("GET", "HEAD") and re.fullmatch(
            r"/api/shared/[A-Za-z0-9_-]{43}(?:/media/(?:original|proxy|poster))?", request.url.path
        )
        response = None
        if (
            request.url.path.startswith("/api/")
            and not public_share
            and request.url.path
            not in (
                "/api/session",
                "/api/login",
                "/api/health",
            )
        ):
            if not authorized(request):
                response = JSONResponse({"detail": "Sign in to view the archive"}, status_code=401)
        origin = request.headers.get("origin")
        expected_origin = settings.public_origin or f"{request.url.scheme}://{request.headers.get('host')}"
        permitted_origins = {expected_origin, *settings.allowed_origins}
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in permitted_origins:
            response = JSONResponse({"detail": "Cross-origin changes are not allowed"}, status_code=403)
        if response is None:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/api/", "/share/")):
            response.headers["Cache-Control"] = "no-store"
        if public_share or request.url.path.startswith("/share/"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    @app.get("/api/health")
    def health():
        with connect(settings) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/api/session")
    def session(request: Request):
        return {"authenticated": authorized(request), "auth_required": bool(settings.auth_token)}

    @app.post("/api/live/{camera_id}")
    def start_live(camera_id: str):
        now = time.time()
        with connect(settings) as conn:
            conn.execute("BEGIN IMMEDIATE")
            camera = conn.execute(
                "SELECT * FROM cameras WHERE id=? AND device_host IS NOT NULL", (camera_id,)
            ).fetchone()
            if not camera:
                raise HTTPException(404, "No live camera connected to this archive")
            ports = json.loads(camera["metadata"]).get("host", {}).get("GetNetPort", {}).get("NetPort", {})
            if ports.get("rtspEnable") != 1:
                raise HTTPException(
                    409, "RTSP is not enabled or has not been discovered. Camera settings remain unchanged."
                )
            worker = get_state(conn, "worker", {})
            if worker.get("stopped") or now - worker.get("heartbeat", 0) > 30:
                raise HTTPException(503, "The live stream worker is offline")
            active = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT camera_id FROM live_viewers WHERE expires_at>?", (now,)
                )
            ]
            if camera_id not in active and len(active) >= 5:
                raise HTTPException(429, "Five cameras are already streaming")
            conn.execute(
                """INSERT INTO live_streams(camera_id) VALUES(?) ON CONFLICT(camera_id)
                DO UPDATE SET status=CASE WHEN status IN ('error','stopped') THEN 'pending' ELSE status END,error=NULL""",
                (camera_id,),
            )
            viewer = uuid.uuid4().hex
            conn.execute("INSERT INTO live_viewers VALUES(?,?,?)", (viewer, camera_id, now + LEASE_SECONDS))
        return {"viewer": viewer}

    @app.post("/api/snapshots/{camera_id}")
    def request_snapshot(camera_id: str):
        now = time.time()
        with connect(settings) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM cameras WHERE id=? AND device_host IS NOT NULL", (camera_id,)
            ).fetchone():
                raise HTTPException(404, "Camera not found")
            worker = get_state(conn, "worker", {})
            if worker.get("stopped") or now - worker.get("heartbeat", 0) > 30:
                raise HTTPException(503, "Snapshot worker is offline")
            conn.execute(
                """INSERT INTO live_snapshots(camera_id,status,requested_at) VALUES(?,'pending',?)
                ON CONFLICT(camera_id) DO UPDATE SET status='pending',requested_at=excluded.requested_at,error=NULL
                WHERE live_snapshots.status NOT IN ('pending','capturing') AND live_snapshots.requested_at<?""",
                (camera_id, now, now - 2),
            )
        return {"ok": True}

    @app.get("/api/snapshots/{camera_id}")
    def snapshot_status(camera_id: str):
        with connect(settings) as conn:
            row = conn.execute(
                "SELECT status,captured_at,error FROM live_snapshots WHERE camera_id=?", (camera_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Snapshot not requested")
            result = dict(row)
            result["url"] = (
                f"/api/snapshots/{camera_id}/image.jpg?t={row['captured_at']}" if row["captured_at"] else None
            )
            return result

    @app.get("/api/snapshots/{camera_id}/image.jpg")
    def snapshot_image(camera_id: str):
        try:
            path = snapshot_file(settings, camera_id)
        except ValueError:
            raise HTTPException(404, "Snapshot not found") from None
        if not path.is_file():
            raise HTTPException(404, "Snapshot not ready")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/live/{camera_id}/viewers/{viewer}")
    def renew_live(camera_id: str, viewer: str):
        with connect(settings) as conn:
            changed = conn.execute(
                "UPDATE live_viewers SET expires_at=? WHERE id=? AND camera_id=? AND expires_at>?",
                (time.time() + LEASE_SECONDS, viewer, camera_id, time.time()),
            ).rowcount
            if not changed:
                raise HTTPException(404, "Live viewing session expired. Reconnect to continue.")
        return {"ok": True}

    @app.delete("/api/live/{camera_id}/viewers/{viewer}")
    def stop_live(camera_id: str, viewer: str):
        with connect(settings) as conn:
            conn.execute("DELETE FROM live_viewers WHERE id=? AND camera_id=?", (viewer, camera_id))
        return {"ok": True}

    @app.get("/api/live/{camera_id}")
    def live_status(camera_id: str):
        with connect(settings) as conn:
            row = conn.execute("SELECT * FROM live_streams WHERE camera_id=?", (camera_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Live stream not started")
            result = dict(row)
            result["playlist"] = (
                f"/api/live/{camera_id}/{row['generation']}/index.m3u8" if row["status"] == "ready" else None
            )
            return result

    @app.get("/api/live/{camera_id}/{generation}/{filename}")
    def live_asset(camera_id: str, generation: str, filename: str):
        with connect(settings) as conn:
            row = conn.execute(
                """SELECT 1 FROM live_streams s WHERE camera_id=? AND generation=?
                AND EXISTS(SELECT 1 FROM live_viewers v WHERE v.camera_id=s.camera_id AND expires_at>?)""",
                (camera_id, generation, time.time()),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Live viewing session ended")
        try:
            path = live_file(settings, camera_id, generation, filename)
        except ValueError:
            raise HTTPException(404, "Live asset not found") from None
        if not path.is_file():
            raise HTTPException(404, "Live asset not ready")
        return FileResponse(
            path,
            media_type="application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/login")
    def login(body: Login, request: Request):
        now = time.time()
        peer = request.client.host if request.client else "unknown"
        valid = bool(settings.auth_token) and hmac.compare_digest(
            body.token.encode(), settings.auth_token.encode()
        )
        with connect(settings) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM login_attempts WHERE reset_at<=?", (now,))
            attempt = conn.execute("SELECT failures FROM login_attempts WHERE peer=?", (peer,)).fetchone()
            if attempt and attempt["failures"] >= 10:
                return JSONResponse(
                    {"detail": "Too many sign-in attempts. Try again in five minutes."},
                    status_code=429,
                    headers={"Retry-After": "300"},
                )
            if not valid:
                conn.execute(
                    """INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(peer)
                    DO UPDATE SET failures=failures+1""",
                    (peer, now + 300),
                )
                return JSONResponse({"detail": "Incorrect access token"}, status_code=401)
            conn.execute("DELETE FROM login_attempts WHERE peer=?", (peer,))
            conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now,))
            conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash=?",
                (session_hash(request.cookies.get("reoui_session", "")),),
            )
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO auth_sessions VALUES(?,?)", (session_hash(token), now + 30 * 86400))
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            "reoui_session",
            token,
            httponly=True,
            samesite="strict",
            secure=settings.public_origin.startswith("https://") or request.url.scheme == "https",
            max_age=30 * 86400,
        )
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        with connect(settings) as conn:
            conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash=?",
                (session_hash(request.cookies.get("reoui_session", "")),),
            )
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("reoui_session", httponly=True, samesite="strict")
        return response

    @app.post("/api/recordings/{rid}/shares", status_code=201)
    def create_share(rid: str, body: Share, request: Request):
        now = time.time()
        token = secrets.token_urlsafe(32)
        share_id = uuid.uuid4().hex
        expires_at = now + body.expires_in
        with connect(settings) as conn:
            if not conn.execute("SELECT 1 FROM recordings WHERE id=? AND available=1", (rid,)).fetchone():
                raise HTTPException(404, "Recording is unavailable")
            conn.execute(
                "INSERT INTO recording_shares VALUES(?,?,?,?,?)",
                (share_id, hashlib.sha256(token.encode()).hexdigest(), rid, now, expires_at),
            )
        origin = settings.public_origin or str(request.base_url).rstrip("/")
        return {"id": share_id, "url": f"{origin}/share/{token}", "expires_at": expires_at}

    @app.get("/api/recordings/{rid}/shares")
    def list_shares(rid: str):
        with connect(settings) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT id,created_at,expires_at FROM recording_shares
                WHERE recording_id=? AND expires_at>? ORDER BY created_at DESC""",
                    (rid, time.time()),
                )
            ]

    @app.delete("/api/recordings/{rid}/shares/{share_id}")
    def revoke_share(rid: str, share_id: str):
        with connect(settings) as conn:
            conn.execute("DELETE FROM recording_shares WHERE id=? AND recording_id=?", (share_id, rid))
        return {"revoked": True}

    def shared_recording(token: str):
        with connect(settings) as conn:
            row = conn.execute(
                """SELECT r.*,c.name AS camera_name,s.expires_at FROM recording_shares s
                JOIN recordings r ON r.id=s.recording_id JOIN cameras c ON c.id=r.camera_id
                WHERE s.token_hash=? AND s.expires_at>? AND r.available=1""",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        if not row:
            raise HTTPException(404, "This share link is invalid, expired, or revoked")
        return row

    @app.get("/api/shared/{token}")
    def shared_details(token: str):
        row = shared_recording(token)
        # Only playback metadata: no archive paths, notes, camera connections, or jobs.
        result = {
            key: row[key] for key in ("camera_name", "start", "end", "duration", "video_codec", "expires_at")
        }
        result.update(poster=bool(row["poster"]), proxy=bool(row["proxy"]), timezone=settings.timezone)
        return result

    @app.api_route("/api/shared/{token}/media/{kind}", methods=["GET", "HEAD"])
    def shared_media(token: str, kind: str, download: bool = False):
        if kind not in ("original", "proxy", "poster"):
            raise HTTPException(404)
        row = shared_recording(token)
        return media(row["id"], kind, download)

    @app.get("/api/status")
    def status():
        with connect(settings) as conn:
            counts = dict(
                conn.execute("""SELECT COUNT(*) AS recordings,COALESCE(SUM(size),0) AS bytes,
                COALESCE(SUM(duration),0) AS seconds,MIN(local_day) AS first_day,MAX(local_day) AS last_day,
                COALESCE(SUM(poster),0) AS previews,COALESCE(SUM(status='error'),0) AS errors,
                COALESCE(SUM(status IN ('pending','processing')),0) AS pending
                FROM recordings WHERE available=1""").fetchone()
            )
            counts["cameras"] = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
            counts["event_tagged"] = conn.execute(
                "SELECT COUNT(*) FROM recordings WHERE available=1 AND triggers_known=1"
            ).fetchone()[0]
            event_recovery = dict(
                conn.execute("""SELECT COUNT(*) AS searched_days,
                COALESCE(SUM(status='error'),0) AS failed_days,
                COALESCE(SUM(status='complete' AND recordings=0),0) AS empty_days
                FROM event_search_days""").fetchone()
            )
            event_recovery["total_days"] = conn.execute("""SELECT COUNT(*) FROM (
                SELECT DISTINCT r.camera_id,r.local_day FROM recordings r JOIN cameras c ON c.id=r.camera_id
                WHERE c.device_host IS NOT NULL AND r.available=1 AND r.local_day IS NOT NULL)""").fetchone()[0]
            cache = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM cache_files").fetchone()[0]
            worker = get_state(conn, "worker", {})
            worker["online"] = bool(
                worker and not worker.get("stopped") and time.time() - worker.get("heartbeat", 0) < 15
            )
            return {
                "archive": str(settings.archive),
                "timezone": settings.timezone,
                "counts": counts,
                "source": get_state(conn, "source", {"available": None}),
                "scan": get_state(conn, "scan", {"status": "idle"}),
                "worker": worker,
                "collector": get_state(conn, "collector", {}),
                "event_recovery": event_recovery,
                "cache": {"bytes": cache, "budget": settings.cache_bytes},
                "read_only": True,
            }

    @app.get("/api/cameras")
    def cameras():
        with connect(settings) as conn:
            rows = conn.execute("""SELECT c.*,COUNT(r.id) AS recordings,MAX(r.start) AS latest,
                COALESCE(SUM(r.size),0) AS bytes FROM cameras c
                LEFT JOIN recordings r ON c.id=r.camera_id AND r.available=1
                GROUP BY c.id ORDER BY c.name""").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item.pop("color", None)  # Legacy catalogs may still have this column.
                item["metadata"] = json.loads(item["metadata"])
                result.append(item)
            return result

    @app.post("/api/cameras/{camera_id}/mapping")
    def mapping(camera_id: str, body: Mapping):
        with connect(settings) as conn:
            device = conn.execute(
                "SELECT * FROM cameras WHERE id=? AND device_host IS NOT NULL", (camera_id,)
            ).fetchone()
            archive = conn.execute(
                "SELECT * FROM cameras WHERE id=? AND device_host IS NULL", (body.archive_camera_id,)
            ).fetchone()
            if not device or not archive:
                raise HTTPException(400, "Choose a device and an unassigned archive folder")
            if device["folder"]:
                raise HTTPException(409, "This camera already has a mapped archive folder")
            conn.execute("UPDATE recordings SET camera_id=? WHERE camera_id=?", (camera_id, archive["id"]))
            conn.execute("UPDATE cameras SET folder=? WHERE id=?", (archive["folder"], camera_id))
            conn.execute("DELETE FROM cameras WHERE id=?", (archive["id"],))
        return {"mapped": True}

    def filters(
        camera: str | None,
        day: str | None,
        event: str | None,
        q: str | None,
        bookmarked: bool,
        time_start: int | None,
        time_end: int | None,
    ):
        clauses = ["r.bookmarked=1" if bookmarked else "r.available=1"]
        params: list = []
        if camera:
            clauses.append("r.camera_id=?")
            params.append(camera)
        if (time_start is None) != (time_end is None):
            raise HTTPException(422, "Both time range boundaries are required")
        if time_start is not None and not day:
            raise HTTPException(422, "Choose a recording day for a time range")
        if day:
            try:
                date = Date.fromisoformat(day)
            except ValueError:
                raise HTTPException(422, "Date must be YYYY-MM-DD") from None
            if time_start is None:
                clauses.append("r.local_day=?")
                params.append(day)
            else:
                tz = ZoneInfo(settings.timezone)
                begin = datetime.combine(date, Time.min, tz).timestamp()
                finish = datetime.combine(date + timedelta(days=1), Time.min, tz).timestamp()
                if not begin <= time_start < time_end <= finish:
                    raise HTTPException(422, "Time range must be within the selected day")
                clauses.append("r.start<? AND COALESCE(r.end,r.start+COALESCE(r.duration,1))>?")
                params.extend((time_end, time_start))
        if event == "unknown":
            clauses.append("r.triggers_known=0")
        elif event:
            clauses.append("EXISTS(SELECT 1 FROM triggers t WHERE t.recording_id=r.id AND t.kind=?)")
            params.append(event)
        if q:
            clauses.append(
                "(r.filename LIKE ? ESCAPE '\\' OR r.note LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\')"
            )
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params += [f"%{escaped}%"] * 3
        return clauses, params

    @app.get("/api/recordings")
    def recordings(
        camera: str | None = None,
        day: str | None = None,
        event: str | None = None,
        q: str | None = Query(None, max_length=200),
        bookmarked: bool = False,
        time_start: int | None = Query(None, ge=0),
        time_end: int | None = Query(None, ge=0),
        cursor: str | None = None,
        limit: int = Query(36, ge=1, le=100),
    ):
        clauses, params = filters(camera, day, event, q, bookmarked, time_start, time_end)
        if cursor:
            try:
                stamp, rid = json.loads(base64.urlsafe_b64decode(cursor))
                if not isinstance(stamp, (int, float)) or not isinstance(rid, str):
                    raise ValueError
            except Exception:
                raise HTTPException(422, "Invalid page cursor") from None
            clauses.append("(COALESCE(r.start,r.mtime),r.id)<(?,?)")
            params += [stamp, rid]
        with connect(settings) as conn:
            rows = conn.execute(
                f"""SELECT r.*,c.name AS camera_name
                FROM recordings r JOIN cameras c ON c.id=r.camera_id WHERE {" AND ".join(clauses)}
                ORDER BY COALESCE(r.start,r.mtime) DESC,r.id DESC LIMIT ?""",
                (*params, limit + 1),
            ).fetchall()
            items = [serialize_recording(conn, row) for row in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps([last["start"] or last["mtime"], last["id"]]).encode()
                ).decode()
            return {"items": items, "next_cursor": next_cursor}

    @app.get("/api/recordings/{rid}")
    def recording(rid: str):
        with connect(settings) as conn:
            row = conn.execute(
                """SELECT r.*,c.name AS camera_name
                FROM recordings r JOIN cameras c ON c.id=r.camera_id WHERE r.id=?""",
                (rid,),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Recording not found")
            data = serialize_recording(conn, row)
            data["probe"] = json.loads(row["probe_json"] or "null")
            data["trigger_sources"] = [
                dict(r) for r in conn.execute("SELECT kind,source FROM triggers WHERE recording_id=?", (rid,))
            ]
            data["event_match"] = None
            history = conn.execute(
                "SELECT status,recordings FROM event_search_days WHERE camera_id=? AND day=?",
                (row["camera_id"], row["local_day"]),
            ).fetchone()
            data["event_recovery_status"] = (
                "recovered"
                if data["triggers_known"]
                else "pending"
                if not history
                else "search_failed"
                if history["status"] == "error"
                else "camera_history_unavailable"
                if history["recordings"] == 0
                else "no_reliable_match"
            )
            match = conn.execute(
                """SELECT d.start,d.end,d.captured_at,d.payload,m.offset_seconds
                FROM recording_event_matches m JOIN device_recordings d ON d.id=m.device_recording_id
                WHERE m.recording_id=?""",
                (rid,),
            ).fetchone()
            if match:
                data["event_match"] = dict(match)
                data["event_match"]["device_filename"] = json.loads(data["event_match"].pop("payload")).get(
                    "name"
                )
            data["jobs"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT kind,status,error FROM jobs WHERE target=? ORDER BY id DESC LIMIT 5", (rid,)
                )
            ]
            return data

    @app.patch("/api/recordings/{rid}")
    def annotate(rid: str, body: Annotation):
        with connect(settings) as conn:
            if not conn.execute("SELECT 1 FROM recordings WHERE id=?", (rid,)).fetchone():
                raise HTTPException(404, "Recording not found")
            if body.bookmarked is not None:
                conn.execute("UPDATE recordings SET bookmarked=? WHERE id=?", (body.bookmarked, rid))
            if body.note is not None:
                conn.execute("UPDATE recordings SET note=? WHERE id=?", (body.note, rid))
        return {"saved": True}

    @app.post("/api/recordings/{rid}/prepare")
    def queue_prepare(rid: str, compatible: bool = True):
        with connect(settings) as conn:
            row = conn.execute("SELECT available FROM recordings WHERE id=?", (rid,)).fetchone()
            if not row:
                raise HTTPException(404, "Recording not found")
            if not row["available"]:
                raise HTTPException(409, "Recording source is unavailable")
        return {"job": enqueue(settings, "proxy" if compatible else "prepare", rid, priority=20)}

    @app.get("/api/dates")
    def dates(camera: str | None = None, bookmarked: bool = False):
        with connect(settings) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT local_day AS day,COUNT(*) AS count
                FROM recordings WHERE local_day IS NOT NULL
                AND (available=1 OR (? AND bookmarked=1)) AND (? IS NULL OR camera_id=?)
                GROUP BY local_day ORDER BY local_day DESC""",
                    (bookmarked, camera, camera),
                )
            ]

    @app.get("/api/timeline")
    def timeline(
        day: str,
        camera: str | None = None,
        event: str | None = None,
        q: str | None = Query(None, max_length=200),
        bookmarked: bool = False,
    ):
        try:
            date = Date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, "Date must be YYYY-MM-DD") from None
        tz = ZoneInfo(settings.timezone)
        begin = datetime.combine(date, Time.min, tz).timestamp()
        finish = datetime.combine(date + timedelta(days=1), Time.min, tz).timestamp()
        step = (finish - begin) / 96
        clauses, params = filters(camera, None, event, q, bookmarked, None, None)
        clauses.append("r.start<? AND COALESCE(r.end,r.start+COALESCE(r.duration,1))>?")
        params.extend((finish, begin))
        with connect(settings) as conn:
            rows = conn.execute(
                f"""SELECT r.camera_id,r.start,r.end,r.duration,r.id,r.triggers_known,
                EXISTS(SELECT 1 FROM triggers t WHERE t.recording_id=r.id AND t.kind!='timer') AS event
                FROM recordings r JOIN cameras c ON c.id=r.camera_id
                WHERE {" AND ".join(clauses)}""",
                params,
            ).fetchall()
        lanes = {}
        for row in rows:
            lane = lanes.setdefault(
                row["camera_id"],
                {
                    "camera": row["camera_id"],
                    "bins": [0] * 96,
                    "events": [0] * 96,
                    "first": [None] * 96,
                },
            )
            lo = max(0, int((row["start"] - begin) / step))
            hi = min(95, int(((row["end"] or row["start"] + (row["duration"] or 1)) - begin - 0.001) / step))
            for index in range(lo, hi + 1):
                lane["bins"][index] += 1
                lane["events"][index] += row["event"]
                lane["first"][index] = lane["first"][index] or row["id"]
        return {"start": begin, "end": finish, "lanes": list(lanes.values()), "bin_seconds": step}

    @app.api_route("/api/media/{rid}/{kind}", methods=["GET", "HEAD"])
    def media(rid: str, kind: str, download: bool = False):
        if kind not in ("original", "poster", "sprite", "proxy"):
            raise HTTPException(404)
        with connect(settings) as conn:
            row = conn.execute("SELECT * FROM recordings WHERE id=?", (rid,)).fetchone()
            if not row:
                raise HTTPException(404, "Recording not found")
            if kind != "original":
                conn.execute(
                    "UPDATE cache_files SET accessed_at=? WHERE recording_id=? AND kind=?",
                    (time.time(), rid, kind),
                )
        try:
            path = (
                archive_file(settings, row["path"]) if kind == "original" else cache_path(settings, rid, kind)
            )
            if not path.is_file():
                raise HTTPException(404, "Media is unavailable or not prepared yet")
        except (OSError, ValueError):
            raise HTTPException(404, "Source is unavailable") from None
        return FileResponse(
            path,
            media_type="image/jpeg" if kind in ("poster", "sprite") else "video/mp4",
            filename=row["filename"] if download else None,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.post("/api/index")
    def index():
        return {"job": enqueue(settings, "scan", priority=10)}

    if settings.web.is_dir():
        app.mount("/assets", StaticFiles(directory=settings.web / "assets"), name="assets")

        @app.get("/{path:path}")
        def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(settings.web / "index.html", headers={"Cache-Control": "no-cache"})

    return app
