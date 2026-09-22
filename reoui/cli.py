from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from .config import Settings
from .db import connect, initialize, set_state


def main():
    parser = argparse.ArgumentParser(description="ReoUI read-only recording archive")
    parser.add_argument("command", choices=["serve", "worker", "scan", "prepare", "proxy", "collect"])
    parser.add_argument("recording", nargs="?")
    parser.add_argument("--config", default=os.environ.get("REOUI_CONFIG", ".local/config.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = Path(args.config)
    if config.is_file():
        for key, value in json.loads(config.read_text()).items():
            if key.startswith("REOUI_"):
                os.environ.setdefault(key, str(value))
    settings = Settings.from_env()
    initialize(settings)
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
    elif args.command == "worker":
        from .worker import run

        asyncio.run(run(settings, once=args.once))
    elif args.command == "scan":
        from .indexer import scan

        result = scan(settings, args.limit)
        print(json.dumps(result))
        if result["status"] == "error":
            sys.exit(1)
    elif args.command == "collect":
        from .cameras import collect

        print(json.dumps(asyncio.run(collect(settings))))
    elif args.command in ("prepare", "proxy"):
        from .media import prepare

        if not args.recording:
            parser.error("A recording identifier is required")
        try:
            prepare(settings, args.recording, proxy=args.command == "proxy")
        except Exception as exc:
            # Only expose our own controlled errors; arbitrary tool errors may contain filenames.
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            if args.command == "prepare":
                with connect(settings) as conn:
                    conn.execute(
                        "UPDATE recordings SET status='error',error=? WHERE id=?", (message, args.recording)
                    )
            set_state(
                settings,
                "last_media_error",
                {"recording": args.recording, "error": message, "time": time.time()},
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
