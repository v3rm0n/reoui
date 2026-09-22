# ReoUI

**Your Reolink recordings, organized. Your originals, untouched.**

[![Docker image](https://github.com/v3rm0n/reoui/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/v3rm0n/reoui/actions/workflows/docker-publish.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-a2c9ac.svg)](LICENSE)

ReoUI turns an existing Reolink backup folder into a fast, searchable video library. Browse a day at a glance, filter by camera or detected event, scrub thumbnail previews, and save the moments that matter. Open Live view for fresh camera snapshots and on-demand streams.

Runs in Docker. Keeps recordings on your storage. Uses [reolink_aio](https://github.com/starkillerOG/reolink_aio) to interpret camera recording metadata.

## What you get

| Feature | How it works |
| --- | --- |
| **Recording archive** | Camera and date filters, search, event tags, and a daily coverage timeline |
| **Fast previews** | Background media inspection, posters, and thumbnail strips for scrubbing |
| **Browser playback** | Direct playback where supported; H.264/AAC copies for incompatible originals |
| **Live cameras** | Fresh snapshots on opening, manual refresh, and live players with audio controls |
| **Event recovery** | Historical camera searches with persisted source evidence and conservative matching |
| **Personal annotations** | Bookmarks and notes stored separately from your videos |
| **Device context** | Camera metadata, collection coverage, processing progress, and cache usage |

The archive remains browsable when cameras are offline. A React frontend and FastAPI API read a SQLite catalog while a separate worker handles scanning, FFmpeg processing, and camera reads. The target is three to five cameras and approximately 4 TB of originals; this is a design target, not a completed large-scale benchmark.

## Quick start

You need Docker Compose, an existing backup directory, and local storage for the catalog and generated media. Camera access is optional for archive browsing. Live view requires an already-enabled RTSP stream with H.264 video.

```sh
git clone https://github.com/v3rm0n/reoui.git
cd reoui
mkdir -p .local/data .local/cache .secrets
chmod 700 .secrets
cp .env.example .env
cp cameras.example.json .secrets/cameras.json
chmod 600 .secrets/cameras.json
```

Edit `.env`:

```dotenv
REOUI_ARCHIVE=/absolute/path/to/reolink-backups
REOUI_UID=1000
REOUI_GID=1000
REOUI_TIMEZONE=Europe/Tallinn
```

Use your host user's numeric IDs from `id -u` and `id -g`. Set the timezone used by your camera filenames. Keep data and cache directories **outside** the archive, preferably on an SSD.

For camera metadata and live view, edit `.secrets/cameras.json`:

```json
{
  "cameras": [
    {
      "host": "192.0.2.10",
      "username": "admin",
      "password": "your-camera-password",
      "folder": "garden"
    }
  ]
}
```

`folder` is the top-level camera folder inside the backup directory. Use `{"cameras": []}` for archive-only operation. Unmapped folders can also be connected under **Indexing & storage → Camera connections & metadata**.

Build and start:

```sh
docker compose up -d --build
```

Open **http://localhost:8090**. Discovery and preview generation continue in the background; large imports take time. Follow progress in **Indexing & storage**.

### Use the published image

Every successful `main` push publishes an image to `ghcr.io/v3rm0n/reoui` for **linux/amd64** and **linux/arm64**. Tags include `latest`, `main`, and `sha-<full-commit-sha>`.

Set this in `.env`:

```dotenv
REOUI_IMAGE=ghcr.io/v3rm0n/reoui:latest
```

Then run:

```sh
docker compose pull
docker compose up -d --no-build
```

Private packages require `docker login ghcr.io` with an account that can read the package. Package visibility is managed separately on GitHub. Pin a `sha-…` tag for a reproducible deployment.

## Your originals stay untouched

- Both services mount the archive **read-only**. A missing source directory fails instead of silently creating an empty archive.
- The worker stores its database, previews, playback copies, and temporary live media outside the backup.
- Camera access uses explicit read operations. ReoUI does not enable ports, change recording settings, move cameras, or modify their stored recordings.
- Camera credentials are available only to the worker. The browser receives authenticated application URLs, never camera credentials or RTSP URLs.
- Credentials, local configuration, generated media, and test screenshots are excluded from Git and Docker build contexts.

## Understanding event labels

An FTP backup can outlive the recording on a camera's SD card or NVR. Plain FTP filenames often contain a timestamp **without an event type**. If the camera recording has already been overwritten, those original labels may be unrecoverable.

ReoUI searches historical camera recordings newest first and preserves the returned metadata. A backup receives labels only when it matches one nearby recording from the same camera. The details panel shows the source camera filename and timestamp offset. These are recording-level labels, not exact event timestamps or new AI detections. Ambiguous matches stay **unknown**; the UI explains whether recovery is pending, history is unavailable, or no reliable match was found.

## Live view

Opening Live view requests a current snapshot for each camera. Each card shows when its image was captured and offers a refresh button. A failed refresh retains any previous image with its timestamp and an error message.

Select **Watch live** to start a stream. Live video uses the camera's existing substream, with several seconds of delay. Up to five cameras can stream at once, sharing a reader across viewers. Streams stop when the last viewer leaves; abandoned sessions expire after 45 seconds. Snapshot capture does not leave a live stream running.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `REOUI_ARCHIVE` | Required | Absolute host path to existing recordings |
| `REOUI_IMAGE` | `reoui:local` | Local build or published image tag |
| `REOUI_DATA_DIR` | `./.local/data` | Persistent catalog and application state |
| `REOUI_CACHE_DIR` | `./.local/cache` | Generated previews and playback copies |
| `REOUI_CAMERAS_FILE` | `./.secrets/cameras.json` | Camera connection configuration |
| `REOUI_BIND` / `REOUI_PORT` | `127.0.0.1` / `8090` | Host interface and port |
| `REOUI_AUTH_TOKEN` | Empty | Optional application access token |
| `REOUI_PUBLIC_ORIGIN` | Empty | Browser-facing origin for reverse-proxy deployments, e.g. `https://reoui.example.com` |
| `REOUI_TRUSTED_PROXIES` | `127.0.0.1` | Exact proxy peers trusted for forwarded headers |
| `REOUI_TIMEZONE` | `Europe/Tallinn` | Filename interpretation and display timezone |
| `REOUI_CACHE_BYTES` | `200000000000` | Accounted derivative cache limit, about 200 GB |
| `REOUI_SCAN_INTERVAL` | `900` | Seconds between archive scans |
| `REOUI_CAMERA_INTERVAL` | `300` | Seconds between routine metadata refreshes |
| `REOUI_STABLE_SECONDS` | `60` | Minimum file age before processing |
| `REOUI_SCAN_TIMEOUT` | `180` | Maximum seconds without scan progress |
| `REOUI_AUTO_PROXY_HOURS` | `24` | Automatically prepare recent incompatible clips; `0` disables |

See [deployment and maintenance](docs/deployment.md) for remote access, Tailscale, Colima, backups, and updates.

## Development

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 24+, FFmpeg, and ffprobe.

```sh
uv sync --frozen
npm --prefix frontend ci
npm --prefix frontend run build
```

Set `REOUI_ARCHIVE`, `REOUI_DATA`, `REOUI_CACHE`, and optionally `REOUI_CAMERAS_FILE` to local paths. Run these in separate terminals:

```sh
uv run reoui serve
uv run reoui worker
```

Do not run host and Docker workers against the same database. For frontend development, `npm --prefix frontend run dev` starts Vite.

```sh
uv run ruff check reoui tests
uv run pytest -q
cd frontend
npx playwright install chromium
npm run test:e2e
```

Browser tests create their own synthetic archive. Tests cover source immutability, camera write rejection, event matching, authenticated media access, live viewer cleanup, snapshot publication, and real FFmpeg output. Optional real-camera smoke scripts live in `scripts/` and require an explicitly configured running deployment.

## Current limits

- Historical event detail depends on what the camera retains. Detailed Baichuan event intervals and continuous event subscriptions are not implemented.
- Compatible archive playback copies are re-encoded; hardware acceleration and stream-copy remux optimization are future work.
- Timeline zoom, synchronized archive playback, trimmed exports, and automatic backup ingestion remain future work.
- Renames create new entries. Full-content deduplication and automatic removal of missing files are not implemented.
- Live view uses H.264 substreams; it does not change incompatible camera encoding settings.
- A million-record benchmark and a complete Safari/Firefox codec matrix have not been completed.

See [the implementation plan](PLAN.md) for the broader roadmap.

## License

[MIT](LICENSE). Third-party dependencies retain their own licenses. ReoUI is an independent project, not an official Reolink application.
