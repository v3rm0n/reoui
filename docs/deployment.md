# Deployment and maintenance

## Storage

Make the backup available to the Docker host or VM before starting the services. Compose uses `create_host_path: false` and read-only source mounts. Keep `.local/data` and `.local/cache` outside that mount. The runtime user must be able to read the backup and write application storage.

### Colima with a network archive

Mount an SMB or NFS archive directly inside the Colima VM when the source is a NAS. Set `REOUI_ARCHIVE` in `.env` to the **VM path** of the recording directory, then keep the Compose `/recordings` bind mount read-only. This avoids re-exporting a macOS network mount through VirtioFS, which can retain an old view after macOS reconnects the share.

For SMB, install `cifs-utils` in the VM and use a root-owned credentials file with mode `0600`; keep the password out of `.env`, Compose, and mount unit options. A systemd automount can mount the share on demand after VM boot. Check that the VM mount is active and that `docker compose exec worker ls /recordings` lists current files before relying on the worker's scan status. The worker health check alone cannot compare its view with the NAS. On NAS or VM connection errors, inspect the VM mount with `colima ssh -- findmnt /mnt/reoui-archive` and check the worker logs.

The current Colima setup uses `/mnt/reoui-archive/reolink` as `REOUI_ARCHIVE`. Its VM mount and automount units are in `/etc/systemd/system/`, with credentials in `/etc/reoui-smb-credentials`. Those VM files survive normal restarts but must be restored if the Colima profile is deleted and recreated. The host-side `.secrets/nas-smb.credentials` is a private backup of the credentials; the macOS archive share itself does not need to be mounted.

Back up the catalog using SQLite's backup API, or stop both services before copying the data directory. Device observations, bookmarks, and notes can be irreplaceable; derivatives can be rebuilt. Treat the camera configuration as a secret.

## Remote access

Compose publishes the app on `127.0.0.1:8090` by default; `REOUI_BIND` and `REOUI_PORT` can change that host listener. The app also carries Docktail service labels for installations that use Docktail. For ordinary network exposure, configure a strong `REOUI_AUTH_TOKEN` and an HTTPS reverse proxy. The UI exchanges the token for an HttpOnly session cookie. All camera snapshots and live segments use the same application authentication.

Set `REOUI_PUBLIC_ORIGIN` in `.env` to the exact origin used in your browser, for example `https://reoui.example.com` (include the port if nonstandard; omit paths). Recreate the app container after changing it: `docker compose up -d app`. This lets POST and PATCH requests pass the same-origin check even when the proxy connects over HTTP or rewrites the Host header. Other origins remain blocked. Without this setting, the app compares the browser Origin against the request scheme and Host.

Set `REOUI_TRUSTED_PROXIES` to the actual proxy peer address seen inside the app container, and have the proxy send `X-Forwarded-Proto` for the original scheme. Docker forwarding may appear as the Docker network gateway rather than `127.0.0.1`. Avoid wildcard trust. When running outside Compose, Uvicorn uses `FORWARDED_ALLOW_IPS` for this setting.

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
