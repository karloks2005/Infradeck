[← Documentation](README.md)

# Troubleshooting

| Symptom | Check |
|---|---|
| A new machine never shows up under **New servers detected** | `docker logs homelab-collector` on that machine. "Could not reach …" means the address in the command isn't reachable from it (firewall, `localhost`, wrong IP/domain). |
| Collector log says "Backend no longer accepts server …" | The server was deleted on the dashboard; it is asking to join again, so click **Add** or **Dismiss**. |
| `network traefik_default declared as external, but could not be found` | You're using `docker-compose.server.yml` on a machine without that Traefik network. Use `compose.yml` for development, or set `TRAEFIK_NETWORK` in `.env` to the network Traefik is really on. |
| Dashboard shows old behaviour after a code change or `git pull` | Rebuild **all** services with `docker compose up -d --build` (with `-f docker-compose.server.yml` on a server unless `COMPOSE_FILE` is set) and hard-refresh the browser (Ctrl+Shift+R). |
| "The machine running the dashboard isn't reporting yet" at the top | The stack's own collector hasn't checked in: add it under **New servers detected**, then wait one check interval. If it is reporting, its image is outdated: `docker exec homelab-collector grep -c dashboard_host /app/collector.py` prints `0`. Rebuild it (`docker compose build --no-cache collector && docker compose up -d collector`). |
| A server shows only `/` and no **Storage** bars for other disks | Its collector predates per-mount reporting. Install a newer collector image (run a freshly generated command, or `docker compose -f collector-compose.yml up -d --build`). A disk mounted after the collector started appears after a collector restart. |
| Logged out after opening the dashboard at a new address | Login cookies belong to the address they were set on; log in again. |
| Login fails behind HTTPS with `SESSION_COOKIE_SECURE=true` | The cookie is only sent over HTTPS; open the `https://` address, not `http://…:5173`. |
| Collector log says "Registration refused (HTTP 403): The join token is invalid, expired or already used" | The token in the command has expired, was already used, or is wrong. Click **Generate command** again and run the new command. |
| **Connect a server** says no collector images are configured | Set `COLLECTOR_IMAGE` and `DOCKER_PROXY_IMAGE` in `.env` (see [Publishing the collector images](deployment.md#publishing-the-collector-images)) and rebuild. |
| The register page asks for a setup token | The install has no accounts yet. Use `docker compose logs backend \| grep "setup token"`, or `ADMIN_BOOTSTRAP_TOKEN`. |
| "Password must be at least 16 characters long" | New passwords need 16 or more characters (at most 72 bytes). A few random words work well. |
| "Cross-site request refused" (403) on a button or form | The request came from another origin. Open the dashboard at the address you normally use. If a proxy in front rewrites the `Host` header, add your dashboard URL to `CORS_ORIGINS`. |
| The **System** row shows *unknown* for last shutdown or has no kernel messages | The optional system helper isn't running. Start it with `docker compose --profile system-events up -d`. *Unknown* for reboot-required on non-Debian hosts is expected. |
| `Error response … cannot assign requested address` for the collector gate | `COLLECTOR_BIND` is set to an address that doesn't exist yet (VPN not up). Start the VPN first, or fix the address. |
| The **Network** row says *no config found* for the firewall | None of the known firewall files exist on that host (`/etc/ufw`, `/etc/nftables.conf`, `/etc/iptables/rules.v4`). The check reads configuration, not the live ruleset, so a host that configures its firewall some other way shows this. |
| The **Network** row says *not checked* for the internet | The check is opt-in. Set `HOMELAB_CONNECTIVITY_TARGETS` on that collector (for example `1.1.1.1:443`). |
| Listeners show *unknown* or the Network row is empty | The collector couldn't read the host's network tables (older collector, or a table with more than 500,000 sockets). An unreadable report never raises a "port closed" alert. |
| "Invalid code." although the code looks right | Codes depend on the clock: check the phone's time is automatic. A code also can't be used twice, so wait for the next one. Five wrong codes lock the code step for 5 minutes. |
| Two-factor setup says it is not available | `SECRETS_KEY` is missing or invalid in `.env`. Generate one (see [Two-factor sign-in](accounts.md#two-factor-sign-in)) and restart the backend. The backend log shows a SECURITY WARNING while the key is missing. |
| Lost the authenticator and the recovery codes | Run `docker compose exec backend python src/backend/reset_2fa.py <email>` on the host, then sign in with the password and set it up again. |
| Authenticator codes stopped working after a restore or `.env` change | `SECRETS_KEY` changed or was lost, so the stored secret can't be decrypted. Restore the old key (rotation: `new,old`), or use a recovery code and the reset command. |
| Server marked offline | It missed 3 check-ins in a row. Check the machine, its collector log and its route to the dashboard. The incident resolves on the next report. |
| A collector log says "Check-ins are arriving faster than this server's interval allows" (HTTP 429) | The collector reports more often than a quarter of the server's check interval. It retries by itself; check that only one collector uses that server's token, or adjust `INGEST_MIN_SPACING_RATIO`. |
| "This install already has the most servers it allows" | `MAX_SERVERS` (default 200) is reached. Remove servers you no longer need, or raise it in `.env`. |
| A warning "The credentials of <server> were replaced" | Someone used the enrollment key on a machine whose ID is already known, which signs the previous collector out. Expected after you reinstall that machine with the key; if it wasn't you, rotate the key under *Connect a server → Advanced* and inspect the machine. |
| "Enter your current password to set up two-factor." | Turning on two-factor from a normal session asks for your password again. Type it in the field that appears. |
