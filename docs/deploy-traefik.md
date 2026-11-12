[← Documentation](README.md)

# Deploy Infradeck behind Traefik

When you're done, the dashboard runs on your server at `https://<your-domain>` behind your existing Traefik, and
remote machines can report to it over your VPN.

This guide covers one setup: a server where Traefik already runs in Docker. It uses the server stack
(`docker-compose.server.yml`) plus the ready-made [`deploy/traefik.yml`](../deploy/traefik.yml). You don't edit
either file; your settings go into `.env`. For another reverse proxy, see
[Deploying on a server](deployment.md#other-reverse-proxies). Already running Infradeck behind Traefik? Go to
[Upgrade an existing install](#upgrade-an-existing-install).

Upgrading an existing install with this guide has been tested on a real server. A fresh install has not yet been
followed end to end; if a step doesn't work as written, please open an issue.

## Before you start

- **Docker with Compose v2** on the server. Check with `docker compose version`.
- **Traefik running in Docker** with the Docker provider, an HTTPS entrypoint and a certificate resolver.
  Infradeck labels its frontend with `traefik.enable=true`, so `exposedByDefault=false` is fine.
- **A DNS record** for `<your-domain>` pointing at the server.
- **Optional, for remote machines:** a VPN (WireGuard, OpenVPN, Tailscale) between the server and those machines.

Look up three names from your Traefik setup. Step 2 needs them.

| Name | Where to find it | Infradeck's default |
|---|---|---|
| HTTPS entrypoint | Traefik's static configuration, under `entryPoints:` | `websecure` |
| Certificate resolver | Traefik's static configuration, under `certificatesResolvers:` | `letsencrypt` |
| Traefik's Docker network | `docker inspect <traefik-container> --format '{{json .NetworkSettings.Networks}}'` | `traefik_default` |

All commands below run on the server, in the `infradeck` folder from step 1.

## 1. Get the code

Replace `<version>` with the release you want, for example `1.0.0` (see the repository's **Releases** page):

```bash
git clone https://github.com/karloks2005/Infradeck.git infradeck
cd infradeck
git checkout v<version>
cp example.env .env
```

The stack runs that release's published, signed images; the checkout supplies the compose files and the nginx
settings of the same version.

`.env` holds your passwords and keys. Keep it on the server and never commit it.

## 2. Fill in `.env`

Find the subnet of Traefik's network. Replace `traefik_default` if your network has another name:

```bash
docker network inspect traefik_default --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
```

It prints one range, an address followed by `/` and a number. That is `<traefik-subnet>` below.

Open `.env` and set these lines. Leave every other line empty.

```bash
COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml
POSTGRES_PASSWORD=<output of: openssl rand -hex 32>
REDIS_PASSWORD=<output of: openssl rand -hex 24>
DASHBOARD_DOMAIN=<your-domain, without https://>
TRUSTED_PROXIES=<traefik-subnet>
TRAEFIK_ENTRYPOINT=<your HTTPS entrypoint, or empty for websecure>
TRAEFIK_CERT_RESOLVER=<your certificate resolver, or empty for letsencrypt>
TRAEFIK_NETWORK=<Traefik's network, or empty for traefik_default>
```

- `COMPOSE_FILE` makes every plain `docker compose ...` command use both files.
- `TRUSTED_PROXIES` is the range printed above, not the network's name: a name stops the
  frontend from starting.
- `TRUSTED_PROXIES` lets Traefik tell Infradeck each visitor's real address. Without it all visitors share
  Traefik's address, and one person's failed sign-ins lock everyone out for a minute. Every container on that
  network is trusted too, so keep only containers you trust on Traefik's network.

## 3. Start the stack

Run `docker compose` without `-f` in this step and every later one. `-f` replaces `COMPOSE_FILE`, which leaves
out `deploy/traefik.yml`, and Traefik stops serving the dashboard.

```bash
docker compose pull
docker compose up -d
docker compose ps
```

The first build takes a few minutes. It worked when every service shows `running` and `backend` shows
`healthy`. Traefik's dashboard lists a router named `infradeck`.

## 4. Add the secrets key

The key encrypts two-factor secrets and notification addresses. Two-factor sign-in is required for admins and
can't be set up without it.

```bash
docker compose run --rm backend python -m src.backend.secrets_box
```

It prints one key:

```text
<a 44-character key ending in =>
```

Put it in `.env` as `SECRETS_KEY=<that key>`, and keep a copy with your other secrets, outside database backups.
Then apply it:

```bash
docker compose up -d
```

## 5. Create the admin account

Print the one-time setup token:

```bash
docker compose logs backend | grep "setup token"
```

```text
infradeck-backend  | First-run setup token: <token>  (needed to create the first account; shown again at every start until it exists)
```

Open `https://<your-domain>`, register, and enter the token. This first account is the admin, and registration
closes after it. Set up two-factor sign-in when the dashboard asks.

## 6. Let remote machines report (optional)

Collectors never use the public domain. They report to the collector gate, published on your VPN address.

Bind the gate to the VPN address, never `0.0.0.0`: Docker's published ports bypass `ufw` and similar firewalls,
so `0.0.0.0` puts the collector endpoints on the internet. Add to `.env`, with `<vpn-address>` the server's own
address on the VPN:

```bash
COLLECTOR_BIND=<vpn-address>
COLLECTOR_PUBLIC_URL=http://<vpn-address>:8081/api
COLLECTOR_IMAGE=<collector image digest>
DOCKER_PROXY_IMAGE=<Docker proxy image digest>
```

The two image lines come from the Publish images workflow; see
[Publishing the collector images](deployment.md#publishing-the-collector-images). Apply the change:

```bash
docker compose up -d
```

Then open **Connect a server** in the dashboard and follow [Adding servers](adding-servers.md).

## Verify

1. Check the response from outside the server:
   ```bash
   curl -sI https://<your-domain> | grep -i strict-transport-security
   ```
   ```text
   strict-transport-security: max-age=15552000
   ```
   That header comes from Infradeck, so the request went through Traefik to the dashboard over HTTPS.
2. Sign in and open **Account › Sessions**. Your session shows your own public address. An address from
   Traefik's subnet means `TRUSTED_PROXIES` is missing or wrong.
3. If you did step 6: the new machine appears on the dashboard within a minute of running its command.

## Upgrade an existing install

Find the folder the running stack was started from:

```bash
docker compose ls
```

```text
NAME        STATUS       CONFIG FILES
infradeck   running(8)   /home/<user>/infradeck/docker-compose.server.yml,/home/<user>/infradeck/deploy/traefik.yml
```

Run every command below in that folder. Run from another clone, `docker compose` starts a second project with
empty volumes instead of upgrading yours. To move the install to another folder, see
[Move an install to another folder](#move-an-install-to-another-folder).

Back up the database. The backend updates the schema when it starts:

```bash
docker compose exec -T postgres pg_dump -U infradeck -d infradeck -Fc > infradeck-before-upgrade.dump
docker compose exec -T postgres pg_restore --list < infradeck-before-upgrade.dump | head -3
```

The second command reads the backup back; it prints `; Archive created at ...`. Use your `POSTGRES_USER` and
`POSTGRES_DB` instead of `infradeck` if you set them. Keep the `-T`: with a terminal attached (`docker exec -t`)
the dump is corrupted.

**From a version with the Traefik labels in `docker-compose.server.yml`:** the labels moved to
`deploy/traefik.yml`. Set these in `.env` before you pull, or Traefik stops serving the dashboard:

```bash
COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml
TRUSTED_PROXIES=<traefik-subnet, see step 2>
```

The dashboard's local port is `127.0.0.1:5173` again. Set `FRONTEND_PORT` if something on the server relies on
another one.

Update to the new release, with `<new-version>` its number:

```bash
git fetch --tags
git checkout v<new-version>
docker compose pull
docker compose up -d
```

Check the result as in [Verify](#verify). An install from before 1.0.0 built its images on the server; this
pulls the published ones instead and keeps your volumes.

### Roll back

Go back to the release you ran before:

```bash
git checkout v<previous-version>
docker compose pull
docker compose up -d
```

A version from before 1.0.0 has no published dashboard images: check out the commit you ran (`git log --oneline`)
and run `docker compose up -d --build` instead.

If that version still had the Traefik labels in `docker-compose.server.yml`, set
`COMPOSE_FILE=docker-compose.server.yml` in `.env` first.

If the older backend doesn't start on the upgraded database, restore the backup.

> **Not tested yet.** This restore hasn't been tried end to end. Keep the dump file until the restored dashboard
> works, and if you can, try the restore on a copy first.

```bash
docker compose stop backend agent-worker collector
docker compose exec -T postgres pg_restore -U infradeck -d infradeck --clean --if-exists < infradeck-before-upgrade.dump
docker compose up -d
```

### Move an install to another folder

The volumes of a Compose project are named after it, for example `homelab-agent_postgres_data` for a stack
started from a folder named `homelab-agent`. Docker can't rename volumes, so copy them into volumes for the new
folder. The old volumes stay as they are, for rolling back.

1. In the new clone, make `.env` match the old one: the same `POSTGRES_*`, `REDIS_PASSWORD` and `SECRETS_KEY`,
   or the database refuses the backend and two-factor sign-in stops working. Add the project name, so it no
   longer depends on the folder's name, and the two Traefik settings from the upgrade above:
   ```bash
   COMPOSE_PROJECT_NAME=infradeck
   COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml
   TRUSTED_PROXIES=<traefik-subnet, see step 2>
   ```
2. Back up the database in the old folder, as in [Upgrade an existing install](#upgrade-an-existing-install).
3. Stop the old stack. The dashboard is down until step 5. Don't add `-v`, which deletes the volumes:
   ```bash
   cd <old-folder>
   docker compose down
   ```
4. Copy the four data volumes, with `<old-project>` the name `docker compose ls` showed. The Docker proxy's
   socket volume needs no copy.
   ```bash
   for v in postgres_data redis_data collector_state system_events; do
     docker volume create --label com.docker.compose.project=infradeck --label com.docker.compose.volume=$v infradeck_$v
     docker run --rm -v <old-project>_$v:/from:ro -v infradeck_$v:/to --entrypoint sh postgres:16-alpine -c 'cp -a /from/. /to/'
   done
   ```
   The labels mark the new volumes as the `infradeck` project's own, so Compose uses them without a warning.
5. In the new folder, start the stack:
   ```bash
   docker compose pull
   docker compose up -d
   ```
   Check it as in [Verify](#verify). Accounts, history and the dashboard host's server are all still there.

To go back, run `docker compose down` in the new folder and `docker compose up -d` in the old one. Delete the
old volumes (`docker volume rm <old-project>_postgres_data ...`) only once the new install has run for a while.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `network traefik_default declared as external, but could not be found` | `TRAEFIK_NETWORK` doesn't match Traefik's network. | Set it to the name from [Before you start](#before-you-start), then `docker compose up -d`. |
| Traefik answers `404 page not found` | `deploy/traefik.yml` isn't loaded, so the frontend has no labels. | Check `COMPOSE_FILE` in `.env`, then `docker compose up -d` without `-f`. |
| Traefik answers `Bad Gateway` or `Gateway Timeout` | Traefik and the frontend aren't on the same network. | Set `TRAEFIK_NETWORK` to the network Traefik is attached to, then `docker compose up -d`. |
| Certificate warning in the browser | `TRAEFIK_CERT_RESOLVER` doesn't match a resolver in Traefik, so Traefik serves its default certificate. | Fix the name, then `docker compose up -d`. Traefik's log shows any ACME error. |
| Signed out right after signing in | The dashboard was opened over `http://`, or `SESSION_COOKIE_SECURE` was set to `false`. | Use `https://<your-domain>` and leave `SESSION_COOKIE_SECURE` empty. |
| Everyone is locked out after a few failed sign-ins | `TRUSTED_PROXIES` is empty or wrong, so all visitors share Traefik's address. | Set it as in step 2, then `docker compose up -d`. |
| The frontend restarts over and over | `TRUSTED_PROXIES` contains something other than IP addresses and CIDR ranges, such as the network's name. | `docker compose logs frontend` names the bad value. Put the subnet from step 2 in `.env`, then `docker compose up -d`. |
| `service "postgres" is not running`, although the dashboard works | The stack runs from another folder, so this folder is a different Compose project. | Find the folder with `docker compose ls` and run the command there. |
| The frontend restarts over and over with `mkdir() "/var/cache/nginx/client_temp" failed (13: Permission denied)` | An old frontend image runs under the new settings. | `docker compose pull && docker compose up -d`. |

More symptoms: [Troubleshooting](troubleshooting.md).

## Next

- [Adding servers](adding-servers.md): put collectors on your other machines.
- [Deploying on a server](deployment.md): why the server stack is built this way, and other reverse proxies.
