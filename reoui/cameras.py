"""Explicitly read-only Reolink HTTP transport.

Do not use Host.login/get_host_data here: upstream bootstrap can enable ports and
disable manual recording. Only the commands below may cross this transport.
reolink_aio is used for recording/time/trigger interpretation, without its
automatic device setup, protocol fallback, or configuration mutation paths.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

import aiohttp
from reolink_aio.typings import Reolink_timezone

from .config import Settings
from .db import connect, set_state
from .indexer import COLORS, identifier

ALLOWED_COMMANDS = frozenset(
    {
        "Login",
        "Logout",
        "GetDevInfo",
        "GetChannelstatus",
        "GetAbility",
        "GetTime",
        "GetNetPort",
        "GetLocalLink",
        "GetHddInfo",
        "GetWifiSignal",
        "GetChnTypeInfo",
        "GetEnc",
        "GetOsd",
        "GetEvents",
        "GetAiState",
        "GetMdState",
        "GetIsp",
        "GetIrLights",
        "GetPowerLed",
        "GetWhiteLed",
        "GetBatteryInfo",
        "GetPirInfo",
        "GetRecV20",
        "GetRec",
        "GetMask",
        "GetZoomFocus",
        "GetAutoFocus",
        "GetPtzPreset",
        "GetPtzGuard",
        "GetImage",
        "GetAiCfg",
        "GetMdAlarm",
        "GetAiAlarm",
        "GetAudioCfg",
        "Search",
    }
)
CHANNEL_COMMANDS = (
    "GetChnTypeInfo",
    "GetEnc",
    "GetOsd",
    "GetEvents",
    "GetAiState",
    "GetMdState",
    "GetIsp",
    "GetIrLights",
    "GetPowerLed",
    "GetWhiteLed",
    "GetBatteryInfo",
    "GetPirInfo",
    "GetRecV20",
    "GetMask",
    "GetZoomFocus",
    "GetAutoFocus",
    "GetPtzPreset",
    "GetPtzGuard",
    "GetImage",
    "GetAiCfg",
    "GetMdAlarm",
    "GetAudioCfg",
)


class ReadOnlyViolation(ValueError):
    pass


def validate_commands(body: list[dict]) -> None:
    if not body or any(item.get("cmd") not in ALLOWED_COMMANDS for item in body):
        raise ReadOnlyViolation("Camera command rejected by read-only allowlist")
    if any(item.get("action", 0) not in (0, 1) for item in body):
        raise ReadOnlyViolation("Invalid camera read action")


def redact(value):
    if isinstance(value, dict):
        return {
            k: (
                "[redacted]"
                if any(s in k.lower() for s in ("password", "passwd", "token", "secret"))
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class ReadOnlyCamera:
    def __init__(self, config: dict):
        self.config = config
        self.token = ""
        self.session = None
        self.base = ""

    async def request(self, body: list[dict]) -> list[dict]:
        validate_commands(body)  # Before ANY network operation.
        assert self.session is not None
        params = {"cmd": body[0]["cmd"]}
        if self.token:
            params["token"] = self.token
        async with self.session.post(
            self.base, params=params, json=body, ssl=False, allow_redirects=False
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
            if not isinstance(payload, list):
                raise ValueError("Unexpected camera response")
            return payload

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), trust_env=False)
        endpoints = [(True, 443), (False, 80)]
        if "port" in self.config:
            endpoints = [(self.config.get("https", True), self.config["port"])]
        for secure, port in endpoints:
            self.base = f"{'https' if secure else 'http'}://{self.config['host']}:{int(port)}/cgi-bin/api.cgi"
            try:
                response = await self.request(
                    [
                        {
                            "cmd": "Login",
                            "action": 0,
                            "param": {
                                "User": {
                                    "userName": self.config["username"],
                                    "password": self.config["password"],
                                }
                            },
                        }
                    ]
                )
                if response[0].get("code") != 0:
                    raise PermissionError("Camera login denied")
                self.token = response[0]["value"]["Token"]["name"]
                return self
            except PermissionError:
                await self.session.close()
                raise
            except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
                continue
        await self.session.close()
        raise ConnectionError("Camera HTTP API is unavailable; no settings were changed")

    async def __aexit__(self, *_):
        try:
            if self.token:
                await self.request([{"cmd": "Logout", "action": 0, "param": {}}])
        except (aiohttp.ClientError, TimeoutError, ValueError):
            pass
        finally:
            await self.session.close()

    async def read(self, command: str, param: dict | None = None):
        return (await self.request([{"cmd": command, "action": 0, "param": param or {}}]))[0]


def values(results: list[dict]) -> dict:
    return {r["cmd"]: redact(r.get("value")) for r in results if r.get("code") == 0 and "value" in r}


def reolink_time(value: datetime) -> dict:
    return dict(
        year=value.year, mon=value.month, day=value.day, hour=value.hour, min=value.minute, sec=value.second
    )


def load_camera_config(settings: Settings) -> list[dict]:
    if not settings.cameras_file or not settings.cameras_file.is_file():
        return []
    data = json.loads(settings.cameras_file.read_text())
    configs = data if isinstance(data, list) else data.get("cameras", [])
    for config in configs:
        # Host only: credentials never become a URL or a log entry.
        host = config.get("host", "")
        if not host or any(c in host for c in "/@?#:\\"):
            raise ValueError("Camera host must be an IP address or hostname without a port")
        if not config.get("username") or not config.get("password"):
            raise ValueError("Camera credentials are missing")
    return configs


def register_cameras(settings: Settings) -> list[dict]:
    configs = load_camera_config(settings)
    with connect(settings) as conn:
        for index, config in enumerate(configs):
            cid = identifier("device:" + config["host"] + ":0")
            conn.execute(
                """INSERT INTO cameras(id,name,device_host,folder,color,status)
                VALUES(?,?,?,?,?,'pending') ON CONFLICT(id) DO UPDATE SET device_host=excluded.device_host""",
                (
                    cid,
                    config.get("name", "Camera " + config["host"].rsplit(".", 1)[-1]),
                    config["host"],
                    config.get("folder"),
                    COLORS[index % len(COLORS)],
                ),
            )
    return configs


async def collect_one(settings: Settings, config: dict) -> dict:
    host = config["host"]
    captured = time.time()
    event_pending = 0
    event_error = None
    try:
        async with ReadOnlyCamera(config) as client:
            host_results = []
            for command in (
                "GetDevInfo",
                "GetChannelstatus",
                "GetTime",
                "GetAbility",
                "GetNetPort",
                "GetLocalLink",
                "GetHddInfo",
                "GetWifiSignal",
            ):
                param = {"User": {"userName": config["username"]}} if command == "GetAbility" else {}
                host_results.append(await client.read(command, param))
            host_values = values(host_results)
            dev = host_values.get("GetDevInfo", {}).get("DevInfo", {})
            status = host_values.get("GetChannelstatus", {}).get("status", [{"channel": 0}])
            channels = [ch for ch in status if ch.get("online", 1)] or [{"channel": 0}]
            try:
                tz = Reolink_timezone.create_or_get(host_values["GetTime"])
            except (KeyError, ValueError, TypeError):
                from zoneinfo import ZoneInfo

                tz = ZoneInfo(settings.timezone)
            for ch in channels[:32]:
                channel = int(ch.get("channel", 0))
                cid = identifier(f"device:{host}:{channel}")
                results = []
                for command in CHANNEL_COMMANDS:
                    results.append(await client.read(command, {"channel": channel}))
                payload = {
                    "host": host_values,
                    "channel": values(results),
                    "coverage": {
                        r["cmd"]: ("collected" if r.get("code") == 0 else "unavailable")
                        for r in host_results + results
                    },
                    "captured_at": captured,
                    "source": "device_http_readonly",
                    "historical": False,
                    "library_version": "0.21.17",
                }
                name = config.get("name") or ch.get("name") or dev.get("name") or f"Camera {channel + 1}"
                folder = config.get("folder")
                with connect(settings) as conn:
                    # Bind only an exact unique folder/name match. Never guess by recording time alone.
                    if not folder:
                        matches = conn.execute(
                            "SELECT folder FROM cameras WHERE device_host IS NULL AND lower(folder)=lower(?)",
                            (name,),
                        ).fetchall()
                        if len(matches) == 1:
                            folder = matches[0][0]
                    conn.execute(
                        """INSERT INTO cameras(id,name,device_host,channel,folder,metadata,last_seen,status)
                        VALUES(?,?,?,?,?,?,?,'online') ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,metadata=excluded.metadata,last_seen=excluded.last_seen,
                        folder=COALESCE(excluded.folder,cameras.folder),status='online'""",
                        (cid, name, host, channel, folder, json.dumps(payload), captured),
                    )
                    if folder:
                        aliases = conn.execute(
                            "SELECT id FROM cameras WHERE folder=? AND device_host IS NULL", (folder,)
                        ).fetchall()
                        for alias in aliases:
                            conn.execute(
                                "UPDATE recordings SET camera_id=? WHERE camera_id=?", (cid, alias[0])
                            )
                            conn.execute("DELETE FROM cameras WHERE id=?", (alias[0],))
                    conn.execute(
                        "INSERT INTO snapshots(camera_id,captured_at,source,payload) VALUES(?,?,?,?)",
                        (cid, captured, "device_http_readonly", json.dumps(payload)),
                    )
                from .events import recover_events

                try:
                    pending = await recover_events(settings, client, cid, channel, tz)
                    event_pending += pending
                except Exception as exc:
                    event_error = type(exc).__name__
            return {
                "host": host,
                "status": "online",
                "captured_at": captured,
                "event_days_pending": event_pending,
                "event_error": event_error,
            }
    except Exception as exc:
        # Never persist exception strings: HTTP exceptions can include a token in the URL.
        with connect(settings) as conn:
            conn.execute("UPDATE cameras SET status='unreachable' WHERE device_host=?", (host,))
        return {"host": host, "status": "unreachable", "error": type(exc).__name__, "captured_at": captured}


async def collect(settings: Settings) -> list[dict]:
    configs = register_cameras(settings)
    results = []
    for config in configs:
        try:
            result = await asyncio.wait_for(collect_one(settings, config), timeout=75)
        except TimeoutError:
            result = {"host": config["host"], "status": "unreachable", "error": "TimeoutError"}
        results.append(result)
        set_state(settings, "collector", {"updated_at": time.time(), "devices": results})
    return results
