from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    archive: Path
    data: Path
    cache: Path
    cameras_file: Path | None = None
    timezone: str = "Europe/Tallinn"
    cache_bytes: int = 200_000_000_000
    stable_seconds: int = 30
    scan_interval: int = 30
    full_scan_interval: int = 900
    camera_interval: int = 300
    scan_timeout: int = 180
    auto_proxy_hours: int = 24
    web: Path = Path("frontend/dist")
    auth_token: str = ""
    public_origin: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            archive=Path(os.environ.get("REOUI_ARCHIVE", "/recordings")).absolute(),
            data=Path(os.environ.get("REOUI_DATA", ".local/data")).absolute(),
            cache=Path(os.environ.get("REOUI_CACHE", ".local/cache")).absolute(),
            cameras_file=Path(os.environ["REOUI_CAMERAS_FILE"])
            if os.environ.get("REOUI_CAMERAS_FILE")
            else None,
            timezone=os.environ.get("REOUI_TIMEZONE", "Europe/Tallinn"),
            cache_bytes=int(os.environ.get("REOUI_CACHE_BYTES", "200000000000")),
            stable_seconds=int(os.environ.get("REOUI_STABLE_SECONDS", "30")),
            scan_interval=int(os.environ.get("REOUI_SCAN_INTERVAL", "30")),
            full_scan_interval=int(os.environ.get("REOUI_FULL_SCAN_INTERVAL", "900")),
            camera_interval=int(os.environ.get("REOUI_CAMERA_INTERVAL", "300")),
            scan_timeout=int(os.environ.get("REOUI_SCAN_TIMEOUT", "180")),
            auto_proxy_hours=int(os.environ.get("REOUI_AUTO_PROXY_HOURS", "24")),
            web=Path(os.environ.get("REOUI_WEB", "frontend/dist")),
            auth_token=os.environ.get("REOUI_AUTH_TOKEN", ""),
            public_origin=os.environ.get("REOUI_PUBLIC_ORIGIN", "").strip().rstrip("/"),
        )

    def prepare(self) -> None:
        ZoneInfo(self.timezone)
        if self.public_origin:
            origin = urlsplit(self.public_origin)
            if (
                origin.scheme not in ("http", "https")
                or not origin.hostname
                or origin.username is not None
                or origin.password is not None
                or origin.path
                or origin.query
                or origin.fragment
            ):
                raise ValueError("REOUI_PUBLIC_ORIGIN must be an HTTP(S) origin without a path")
        if self.cache_bytes < 0:
            raise ValueError("Cache budget must be nonnegative")
        # Never stat a possibly disconnected archive from the web process.
        # Resolve output paths to catch aliases into the protected source.
        archive = Path(os.path.abspath(self.archive))
        for output in (self.data, self.cache):
            resolved = output.resolve()
            if resolved == archive or archive in resolved.parents or resolved in archive.parents:
                raise ValueError("Application storage must be separate from the recording source")
            resolved.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data / "archive.sqlite3"


def archive_file(settings: Settings, relative: str) -> Path:
    """Resolve a catalog path for reading; reject escapes, including symlinks."""
    root = settings.archive.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Path is outside recording source")
    return path
