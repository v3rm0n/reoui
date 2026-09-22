"""Recover camera recording flags, preserving evidence for conservative time matches."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta

from reolink_aio.typings import VOD_file

from .db import connect
from .indexer import identifier

MATCH_SOURCE = "device_recording_time_match"


def reconcile(settings, camera_id):
    """Match each backup independently; duplicates are not ambiguous camera events.

    FTP timestamps can lag SD recording starts. Accept one and only one camera
    recording starting 0–12 seconds before the filename time (2s clock tolerance).
    Do not infer labels from general overlap, long recordings, or ambiguous DST.
    Flags describe the matched SD recording, not an exact event inside the FTP clip.
    """
    with connect(settings) as conn:
        conn.execute(
            "DELETE FROM recording_event_matches WHERE recording_id IN (SELECT id FROM recordings WHERE camera_id=?)",
            (camera_id,),
        )
        conn.execute(
            "DELETE FROM triggers WHERE source IN (?, 'device_recording') AND recording_id IN (SELECT id FROM recordings WHERE camera_id=?)",
            (MATCH_SOURCE, camera_id),
        )
        rows = conn.execute(
            "SELECT id,start FROM recordings WHERE camera_id=? AND start IS NOT NULL AND time_warning IS NULL AND available=1",
            (camera_id,),
        ).fetchall()
        for row in rows:
            candidates = conn.execute(
                "SELECT * FROM device_recordings WHERE camera_id=? AND start BETWEEN ? AND ? AND end>?",
                (camera_id, row["start"] - 12, row["start"] + 2, row["start"]),
            ).fetchall()
            if len(candidates) != 1:
                continue
            candidate = candidates[0]
            kinds = json.loads(candidate["triggers"])
            if not kinds:
                continue
            conn.execute(
                "INSERT INTO recording_event_matches VALUES(?,?,?)",
                (row["id"], candidate["id"], row["start"] - candidate["start"]),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO triggers VALUES(?,?,?)",
                [(row["id"], kind, MATCH_SOURCE) for kind in kinds],
            )
        conn.execute(
            "UPDATE recordings SET triggers_known=EXISTS(SELECT 1 FROM triggers WHERE recording_id=recordings.id) WHERE camera_id=?",
            (camera_id,),
        )


async def search_window(client, channel, start, end, budget):
    from .cameras import reolink_time

    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("Recording search limit reached; window will be retried")
    response = await client.read(
        "Search",
        {
            "Search": {
                "channel": channel,
                "onlyStatus": 0,
                "streamType": "main",
                "StartTime": reolink_time(start),
                "EndTime": reolink_time(end),
            }
        },
    )
    if response.get("code") != 0:
        raise ValueError("Camera recording search unavailable")
    result = response.get("value", {}).get("SearchResult")
    if not isinstance(result, dict):
        raise ValueError("Invalid camera recording search response")
    rows = result.get("File", [])
    if not isinstance(rows, list):
        raise ValueError("Invalid camera recording list")
    # Split large responses instead of treating a possible result cap as complete.
    if len(rows) >= 256:
        if (end - start).total_seconds() <= 2:
            raise ValueError("Camera recording search is saturated")
        middle = start + timedelta(seconds=int((end - start).total_seconds() // 2))
        return await search_window(client, channel, start, middle, budget) + await search_window(
            client, channel, middle, end, budget
        )
    await asyncio.sleep(0.1)
    return rows


async def recover_events(settings, client, camera_id, channel, tz, limit=12):
    from .cameras import redact

    now = time.time()
    today = datetime.now(tz).date().isoformat()
    yesterday = (datetime.now(tz).date() - timedelta(days=1)).isoformat()
    with connect(settings) as conn:
        days = conn.execute(
            """SELECT DISTINCT r.local_day FROM recordings r
            LEFT JOIN event_search_days d ON d.camera_id=r.camera_id AND d.day=r.local_day
            WHERE r.camera_id=? AND r.local_day IS NOT NULL AND r.local_day<=?
            AND (d.day IS NULL OR (d.status='error' AND d.checked_at<?)
                 OR (r.local_day>=? AND d.checked_at<?))
            ORDER BY r.local_day DESC LIMIT ?""",
            (camera_id, today, now - 300, yesterday, now - 300, limit),
        ).fetchall()
    for row in days:
        day = row[0]
        start = datetime.fromisoformat(day).replace(tzinfo=tz)
        end = start + timedelta(days=1) - timedelta(seconds=1)
        count = 0
        error = None
        try:
            items = await search_window(client, channel, start, end, [64])
            parsed = []
            for item in items:
                vod = VOD_file(item, tz)
                begin, finish = vod.start_time.timestamp(), vod.end_time.timestamp()
                kinds = [flag.name.lower() for flag in vod.triggers if flag.value]
                vid = identifier(f"{camera_id}:{vod.file_name}:{begin}:{item.get('type')}")
                parsed.append(
                    (vid, camera_id, begin, finish, now, json.dumps(redact(item)), json.dumps(kinds))
                )
            with connect(settings) as conn:
                conn.executemany("INSERT OR REPLACE INTO device_recordings VALUES(?,?,?,?,?,?,?)", parsed)
            count = len({item[0] for item in parsed})
        except Exception as exc:
            error = type(exc).__name__  # Never store transport URLs/tokens.
        with connect(settings) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO event_search_days VALUES(?,?,?,?,?,?)",
                (camera_id, day, time.time(), "error" if error else "complete", count, error),
            )
        if error:
            break  # Avoid hammering an unavailable camera.
    reconcile(settings, camera_id)
    with connect(settings) as conn:
        pending = conn.execute(
            """SELECT COUNT(DISTINCT r.local_day) FROM recordings r
            LEFT JOIN event_search_days d ON d.camera_id=r.camera_id AND d.day=r.local_day
            WHERE r.camera_id=? AND r.local_day<=? AND d.day IS NULL""",
            (camera_id, today),
        ).fetchone()[0]
    return pending
