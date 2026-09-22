from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from .config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS cameras(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT, device_host TEXT,
 channel INTEGER NOT NULL DEFAULT 0, color TEXT NOT NULL DEFAULT '#8fbd9d',
 metadata TEXT NOT NULL DEFAULT '{}', last_seen REAL, status TEXT NOT NULL DEFAULT 'archive'
);
CREATE TABLE IF NOT EXISTS recordings(
 id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
 camera_id TEXT NOT NULL REFERENCES cameras(id), size INTEGER NOT NULL, mtime REAL NOT NULL,
 start REAL, end REAL, local_day TEXT, time_source TEXT NOT NULL DEFAULT 'unknown',
 time_warning TEXT, stream TEXT, duration REAL, width INTEGER, height INTEGER,
 video_codec TEXT, audio_codec TEXT, fps REAL, bitrate INTEGER,
 triggers_known INTEGER NOT NULL DEFAULT 0, probe_json TEXT,
 poster INTEGER NOT NULL DEFAULT 0, sprite_json TEXT, proxy INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pending', error TEXT, available INTEGER NOT NULL DEFAULT 1,
 bookmarked INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
 indexed_at REAL NOT NULL, processed_at REAL, last_scan TEXT
);
CREATE INDEX IF NOT EXISTS recordings_time ON recordings(start DESC, id DESC);
CREATE INDEX IF NOT EXISTS recordings_camera_time ON recordings(camera_id,start DESC,id DESC);
CREATE INDEX IF NOT EXISTS recordings_day ON recordings(local_day,start DESC,id DESC);
CREATE INDEX IF NOT EXISTS recordings_status ON recordings(status);
CREATE TABLE IF NOT EXISTS triggers(
 recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(recording_id,kind,source)
);
CREATE INDEX IF NOT EXISTS trigger_kind ON triggers(kind,recording_id);
CREATE TABLE IF NOT EXISTS snapshots(
 id INTEGER PRIMARY KEY, camera_id TEXT NOT NULL, captured_at REAL NOT NULL,
 source TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_camera_time ON snapshots(camera_id,captured_at DESC);
CREATE TABLE IF NOT EXISTS device_recordings(
 id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, start REAL, end REAL,
 captured_at REAL NOT NULL, payload TEXT NOT NULL, triggers TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS jobs(
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, started_at REAL,
 finished_at REAL, error TEXT
);
CREATE INDEX IF NOT EXISTS device_recordings_camera_time ON device_recordings(camera_id,start);
CREATE TABLE IF NOT EXISTS event_search_days(
 camera_id TEXT NOT NULL, day TEXT NOT NULL, checked_at REAL NOT NULL,
 status TEXT NOT NULL, recordings INTEGER NOT NULL, error TEXT,
 PRIMARY KEY(camera_id,day)
);
CREATE TABLE IF NOT EXISTS recording_event_matches(
 recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
 device_recording_id TEXT NOT NULL, offset_seconds REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS active_job ON jobs(kind,target)
 WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS queue_order ON jobs(status,priority DESC,id);
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS live_streams(
 camera_id TEXT PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'pending', generation TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS live_snapshots(
 camera_id TEXT PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
 status TEXT NOT NULL, requested_at REAL NOT NULL, captured_at REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS live_viewers(
 id TEXT PRIMARY KEY, camera_id TEXT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
 expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS live_viewers_camera ON live_viewers(camera_id,expires_at);
CREATE TABLE IF NOT EXISTS cache_files(
 recording_id TEXT NOT NULL, kind TEXT NOT NULL, bytes INTEGER NOT NULL,
 accessed_at REAL NOT NULL, PRIMARY KEY(recording_id,kind)
);
CREATE INDEX IF NOT EXISTS cache_lru ON cache_files(kind,accessed_at);
"""


@contextmanager
def connect(settings: Settings):
    conn = sqlite3.connect(settings.db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(settings: Settings) -> None:
    settings.prepare()
    with connect(settings) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        if version != 1:
            raise RuntimeError("Unsupported database version; restore or migrate before starting")


def set_state(settings: Settings, key: str, value: Any) -> None:
    with connect(settings) as conn:
        conn.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, json.dumps(value)))


def get_state(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def enqueue(settings: Settings, kind: str, target: str = "", priority: int = 0) -> int:
    with connect(settings) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO jobs(kind,target,priority,created_at) VALUES(?,?,?,?)",
            (kind, target, priority, time.time()),
        )
        row = conn.execute(
            "SELECT id FROM jobs WHERE kind=? AND target=? AND status IN ('queued','running')",
            (kind, target),
        ).fetchone()
        return row[0]


def serialize_recording(conn: sqlite3.Connection, row) -> dict:
    data = dict(row)
    data.pop("probe_json", None)
    data["triggers"] = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT kind FROM triggers WHERE recording_id=? ORDER BY kind", (data["id"],)
        )
    ]
    for key in ("poster", "proxy", "bookmarked", "triggers_known", "available"):
        data[key] = bool(data[key])
    data["sprite"] = json.loads(data.pop("sprite_json") or "null")
    return data
