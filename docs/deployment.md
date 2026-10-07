# Deployment and maintenance

## Storage

Make the backup available to the Docker host or VM before starting the services. Compose uses `create_host_path: false` and read-only source mounts. Keep `.local/data` and `.local/cache` outside that mount. The runtime user must be able to read the backup and write application storage.

### Colima with a network archive

Mount an SMB or NFS archive directly inside the Colima VM when the source is a NAS. Set `REOUI_ARCHIVE` in `.env` to the **VM path** of the recording directory, then keep the Compose `/recordings` bind mount read-only. This avoids re-exporting a macOS network mount through VirtioFS, which can retain an old view after macOS reconnects the share.

For SMB, install `cifs-utils` in the VM and use a root-owned credentials file with mode `0600`; keep the password out of `.env`, Compose, and mount unit options. A systemd automount can mount the share on demand after VM boot. Check that the VM mount is active and that `docker compose exec worker ls /recordings` lists current files before relying on the worker's scan status. The worker health check alone cannot compare its view with the NAS. On NAS or VM connection errors, inspect the VM mount with `colima ssh -- findmnt /mnt/reoui-archive` and check the worker logs.

The current Colima setup uses `/mnt/reoui-archive/reolink` as `REOUI_ARCHIVE`. Its VM mount and automount units are in `/etc/systemd/system/`, with credentials in `/etc/reoui-smb-credentials`. Those VM files survive normal restarts but must be restored if the Colima profile is deleted and recreated. The host-side `.secrets/nas-smb.credentials` is a private backup of the credentials; the macOS archive share itself does not need to be mounted.

Back up the catalog using SQLite's backup API, or stop both services before copying the data directory. Device observations, bookmarks, and notes can be irreplaceable; derivatives can be rebuilt. Treat the camera configuration as a secret.

## Remote access

Compose publishes the app on `127.0.0.1:8090` by default; `REOUI_BIND` and `REOUI_PORT` can change that host listener. The app also carries Docktail service labels for installations that use Docktail. For ordinary network exposure, configure a strong `REOUI_AUTH_TOKEN` and an HTTPS reverse proxy. The UI exchanges the token for a random HttpOnly session cookie that expires after 30 days. Sign out revokes that session; changing the access token invalidates all existing sessions. Sign-in is limited to ten failed attempts per client address per five minutes. API clients can use `Authorization: Bearer <access-token>`. An empty token disables archive authentication, so do not leave it empty for public access. All camera snapshots and live segments use the same application authentication.

Set `REOUI_PUBLIC_ORIGIN` in `.env` to the exact origin used in your browser, for example `https://reoui.example.com` (include the port if nonstandard; omit paths). Recreate the app container after changing it: `docker compose up -d app`. This origin is also used to generate absolute recording share links and to mark session cookies Secure for HTTPS deployments. This lets POST and PATCH requests pass the same-origin check even when the proxy connects over HTTP or rewrites the Host header. Other origins remain blocked. Without this setting, the app compares the browser Origin against the request scheme and Host.

To use additional browser addresses, set `REOUI_ALLOWED_ORIGINS` to their exact origins, comma-separated (for example a Tailscale HTTPS address). Sign-in and changes are permitted from these addresses as well as `REOUI_PUBLIC_ORIGIN`; generated share links still use `REOUI_PUBLIC_ORIGIN`. Paths and wildcards are not accepted.

Set `REOUI_TRUSTED_PROXIES` to the actual proxy peer address seen inside the app container, and have the proxy send `X-Forwarded-Proto` for the original scheme. Docker forwarding may appear as the Docker network gateway rather than `127.0.0.1`. Avoid wildcard trust. When running outside Compose, Uvicorn uses `FORWARDED_ALLOW_IPS` for this setting.

### Internet access and recording links

Generate a token with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and configure `.env`:

```dotenv
REOUI_AUTH_TOKEN=<generated-token>
REOUI_PUBLIC_ORIGIN=https://reoui.example.com
REOUI_TRUSTED_PROXIES=<proxy-peer-IP>
```

Point the hostname at your HTTPS reverse proxy and proxy the entire site to the app's listener (normally `http://127.0.0.1:8090`). Preserve video Range requests and disable proxy caching for `/api/` and `/share/`. The proxy must be able to reach the app, connect from the configured trusted peer address, and serve a valid TLS certificate. Recreate the app with `docker compose up -d app` after configuration changes.

In a recording's playback toolbar, choose **Share recording**, select an expiry (1, 7, or 30 days), and create a link. Copy the URL while it is displayed: the database stores only a hash of its random 256-bit secret, so the URL cannot be retrieved later. **Active links → Revoke** immediately prevents new requests through that link. Links persist across app restarts, expire on the server, and stop working when a recording is unavailable. Only that recording's original, prepared playback copy, poster, and basic playback details are exposed. Notes, archive paths, other recordings, camera connections, and live streams remain private. Prepare a compatible playback copy before sharing recordings your recipient's browser cannot play.

Share URLs act as credentials: anyone receiving one can watch and download the video until expiry or revocation. Avoid logging full `/share/` and `/api/shared/` URLs in the proxy or analytics. Revocation cannot remove copies already downloaded. The app disables caching on authenticated and shared API responses, sends a no-referrer policy, and tells crawlers not to index share pages. Without `REOUI_PUBLIC_ORIGIN`, link creation uses the incoming request origin; set it explicitly for internet deployments.

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
