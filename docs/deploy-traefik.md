[← Documentation](README.md)

# Deploy Infradeck behind Traefik

When you're done, the dashboard runs on your server at `https://<your-domain>` behind your existing Traefik, and
remote machines can report to it over your VPN.

This guide covers one setup: a server where Traefik already runs in Docker. It uses the server stack
(`docker-compose.server.yml`) plus the ready-made [`deploy/traefik.yml`](../deploy/traefik.yml). You don't edit
either file; your settings go into `.env`. For another reverse proxy, see
[Deploying on a server](deployment.md#other-reverse-proxies). Already running Infradeck behind Traefik? Go to
[Upgrade an existing install](#upgrade-an-existing-install).

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

```bash
git clone https://github.com/karloks2005/Infradeck.git infradeck
cd infradeck
cp example.env .env
```

`.env` holds your passwords and keys. Keep it on the server and never commit it.

## 2. Fill in `.env`

Find the subnet of Traefik's network. Replace `traefik_default` if your network has another name:

```bash
docker network inspect traefik_default --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
```

It prints one range, for example:

```text
172.18.0.0/16
```

Open `.env` and set these lines. Leave every other line empty.

```bash
COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml
POSTGRES_PASSWORD=<output of: openssl rand -hex 32>
REDIS_PASSWORD=<output of: openssl rand -hex 24>
DASHBOARD_DOMAIN=<your-domain, without https://>
TRUSTED_PROXIES=<the subnet printed above>
TRAEFIK_ENTRYPOINT=<your HTTPS entrypoint, or empty for websecure>
TRAEFIK_CERT_RESOLVER=<your certificate resolver, or empty for letsencrypt>
TRAEFIK_NETWORK=<Traefik's network, or empty for traefik_default>
```

- `COMPOSE_FILE` makes every plain `docker compose ...` command use both files.
- `TRUSTED_PROXIES` lets Traefik tell Infradeck each visitor's real address. Without it all visitors share
  Traefik's address, and one person's failed sign-ins lock everyone out for a minute. Every container on that
  network is trusted too, so keep only containers you trust on Traefik's network.

## 3. Start the stack

Run `docker compose` without `-f` in this step and every later one. `-f` replaces `COMPOSE_FILE`, which leaves
out `deploy/traefik.yml`, and Traefik stops serving the dashboard.

```bash
docker compose up -d --build
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

Back up the database first. The backend updates the schema when it starts:

```bash
docker compose exec -T postgres pg_dump -U infradeck -d infradeck -Fc > infradeck-before-upgrade.dump
```

Use your `POSTGRES_USER` and `POSTGRES_DB` instead of `infradeck` if you set them.

**From a version with the Traefik labels in `docker-compose.server.yml`:** the labels moved to
`deploy/traefik.yml`. Set these in `.env` before you pull, or Traefik stops serving the dashboard:

```bash
COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml
TRUSTED_PROXIES=<subnet of Traefik's network, see step 2>
```

The dashboard's local port is `127.0.0.1:5173` again. Set `FRONTEND_PORT` if something on the server relies on
another one.

Update:

```bash
git pull
docker compose up -d --build
```

Rebuild everything; a service you don't rebuild keeps running the old code. Check the result as in
[Verify](#verify).

### Roll back

Go back to the commit you ran before, and rebuild:

```bash
git log --oneline -5
git checkout <previous-commit>
docker compose up -d --build
```

If that version still had the Traefik labels in `docker-compose.server.yml`, set
`COMPOSE_FILE=docker-compose.server.yml` in `.env` first.

If the older backend doesn't start on the upgraded database, restore the backup:

<!-- TODO: verify this restore on a test install -->
```bash
docker compose stop backend agent-worker collector
docker compose exec -T postgres pg_restore -U infradeck -d infradeck --clean --if-exists < infradeck-before-upgrade.dump
docker compose up -d
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `network traefik_default declared as external, but could not be found` | `TRAEFIK_NETWORK` doesn't match Traefik's network. | Set it to the name from [Before you start](#before-you-start), then `docker compose up -d`. |
| Traefik answers `404 page not found` | `deploy/traefik.yml` isn't loaded, so the frontend has no labels. | Check `COMPOSE_FILE` in `.env`, then `docker compose up -d` without `-f`. |
| Traefik answers `Bad Gateway` or `Gateway Timeout` | Traefik and the frontend aren't on the same network. | Set `TRAEFIK_NETWORK` to the network Traefik is attached to, then `docker compose up -d`. |
| Certificate warning in the browser | `TRAEFIK_CERT_RESOLVER` doesn't match a resolver in Traefik, so Traefik serves its default certificate. | Fix the name, then `docker compose up -d`. Traefik's log shows any ACME error. |
| Signed out right after signing in | The dashboard was opened over `http://`, or `SESSION_COOKIE_SECURE` was set to `false`. | Use `https://<your-domain>` and leave `SESSION_COOKIE_SECURE` empty. |
| Everyone is locked out after a few failed sign-ins | `TRUSTED_PROXIES` is empty or wrong, so all visitors share Traefik's address. | Set it as in step 2, then `docker compose up -d`. |
| The frontend restarts over and over | `TRUSTED_PROXIES` contains something other than IP addresses and CIDR ranges. | `docker compose logs frontend` names the bad value. Fix it in `.env`. |

More symptoms: [Troubleshooting](troubleshooting.md).

## Next

- [Adding servers](adding-servers.md): put collectors on your other machines.
- [Deploying on a server](deployment.md): why the server stack is built this way, and other reverse proxies.
