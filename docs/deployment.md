# Deployment and maintenance

## Storage

Mount the backup before starting Docker. Compose uses `create_host_path: false` and read-only source mounts. Keep `.local/data` and `.local/cache` outside that mount. The runtime user must be able to read the backup and write application storage.

Back up the catalog using SQLite's backup API, or stop both services before copying the data directory. Device observations, bookmarks, and notes can be irreplaceable; derivatives can be rebuilt. Treat the camera configuration as a secret.

## Remote access

The default listener is localhost. For ordinary network exposure, configure a strong `REOUI_AUTH_TOKEN` and an HTTPS reverse proxy. The UI exchanges the token for an HttpOnly session cookie. All camera snapshots and live segments use the same application authentication.

Set `REOUI_TRUSTED_PROXIES` to the actual proxy peer address seen inside the app container. Docker forwarding may appear as the Docker network gateway rather than `127.0.0.1`. Keep proxy Host/Origin headers consistent so same-origin edits work. Avoid wildcard trust.

### Tailscale

For private tailnet access, leave Docker bound to localhost and run on the Docker host:

```sh
tailscale serve --bg --https=443 http://127.0.0.1:8090
tailscale serve status
```

Use the HTTPS address printed by Tailscale. Access follows your tailnet policy; a separate application token is optional for a trusted tailnet. Check existing Serve routes before replacing one. To disable this route:

```sh
tailscale serve --https=443 off
```

The host, Tailscale, Docker, and archive mount must remain available. See [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).

## macOS with Colima

An isolated profile keeps the application separate from other Docker projects:

```sh
colima start reoui --cpus 2 --memory 3 --disk 30 \
  --mount /path/to/recordings \
  --mount /absolute/path/to/reoui:w \
  --activate=false --ssh-config=false
docker --context colima-reoui compose up -d --build
```

A Colima mount without `:w` is read-only. Keep the Compose mount read-only too. Application data uses the configured host directories; the VM disk holds images and runtime data. Use the explicit context for subsequent commands.

## Updating

For published images, set `REOUI_IMAGE=ghcr.io/v3rm0n/reoui:latest` in `.env`, then:

```sh
docker compose pull
docker compose up -d --no-build
docker compose ps
docker compose logs --tail=80
```

For local builds:

```sh
git pull --ff-only
docker compose build app
docker compose up -d --no-build
```

Both services use the same image. Back up the catalog before an upgrade. The image workflow tests every push to `main`, then publishes amd64 and arm64 images to GHCR using the repository's `GITHUB_TOKEN`; no personal registry token is stored in the repository. Pull requests run tests without publishing. A manual run can publish from `main` as well.
