from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from PIL import Image

from .config import Settings
from .media import cache_path


def share_html(template: str, row: sqlite3.Row, token: str, origin: str, settings: Settings) -> str:
    """Render link metadata in the initial HTML, without requiring JavaScript."""
    title = f"{row['camera_name']} · Shared recording · ReoUI"
    description = f"Shared recording from {row['camera_name']}."
    recorded_at = None
    if row["start"] is not None:
        recorded_at = datetime.fromtimestamp(row["start"], ZoneInfo(settings.timezone))
        description += f" Recorded {recorded_at.strftime('%d %B %Y at %H:%M:%S %Z')}."
    if row["duration"]:
        description += f" Duration: {timedelta(seconds=round(row['duration']))}."
    url = f"{origin}/share/{token}"
    properties = {
        "og:type": "video.other",
        "og:site_name": "ReoUI",
        "og:title": title,
        "og:description": description,
        "og:url": url,
    }
    names = {
        "description": description,
        "twitter:card": "summary",
        "twitter:title": title,
        "twitter:description": description,
    }
    if recorded_at:
        properties["video:release_date"] = recorded_at.isoformat()
    if row["duration"] and row["duration"] >= 1:
        properties["video:duration"] = str(round(row["duration"]))
    if row["poster"]:
        try:
            with Image.open(cache_path(settings, row["id"], "poster")) as image:
                width, height = image.size
        except (OSError, ValueError):
            pass
        else:
            image_url = f"{origin}/api/shared/{token}/media/poster"
            alt = f"Preview of the recording from {row['camera_name']}"
            properties.update(
                {
                    "og:image": image_url,
                    "og:image:type": "image/jpeg",
                    "og:image:width": str(width),
                    "og:image:height": str(height),
                    "og:image:alt": alt,
                }
            )
            if image_url.startswith("https://"):
                properties["og:image:secure_url"] = image_url
            names.update(
                {"twitter:card": "summary_large_image", "twitter:image": image_url, "twitter:image:alt": alt}
            )
    tags = [
        f'<meta property="{key}" content="{escape(value, quote=True)}" />'
        for key, value in properties.items()
    ]
    tags += [f'<meta name="{key}" content="{escape(value, quote=True)}" />' for key, value in names.items()]
    tags.append(f'<link rel="canonical" href="{escape(url, quote=True)}" />')
    template = re.sub(
        r"<title>.*?</title>", lambda _: f"<title>{escape(title)}</title>", template, count=1, flags=re.DOTALL
    )
    template = re.sub(r"""<meta\s+name=["']description["'][^>]*>""", "", template, flags=re.IGNORECASE)
    return template.replace("</head>", "\n".join(tags) + "\n</head>", 1)
