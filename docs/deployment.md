[← Documentation](README.md)

# Deploying on a server (behind Traefik)

There are two Compose files:

| File | Use | Differences |
|---|---|---|
| `compose.yml` | Laptop / development (the default when you run `docker compose` without `-f`) | Dashboard on port 5173 on all interfaces; plain HTTP; optional Caddy (`--profile https`). |
| `docker-compose.server.yml` | Server behind an existing Traefik | Traefik labels and network; HTTPS-only session cookie; only the first account can register; port 5173 bound to `127.0.0.1`; no Caddy; **collector endpoints removed from the public side** and served by a separate collector gate on your VPN address. |

## 1. Create `.env` on the server

Nothing in `docker-compose.server.yml` needs editing: everything specific to you comes from
`.env`, so `git pull` can always update the file. Start from the production template:

```bash
cp example.env .env
```

Then set at least these:

| Setting | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | *(required)* | A long random password for the bundled database. |
| `REDIS_PASSWORD` | *(required)* | A long random password for Redis, which holds the login sessions. Letters and digits only (`openssl rand -hex 24`). |
| `DASHBOARD_DOMAIN` | *(required)* | Your domain, without `https://`. Used for the Traefik router. |
| `TRAEFIK_ENTRYPOINT` | `websecure` | Your Traefik HTTPS entrypoint name. |
| `TRAEFIK_CERT_RESOLVER` | `letsencrypt` | Your Traefik certificate resolver name. |
| `TRAEFIK_NETWORK` | `traefik_default` | The Docker network Traefik is attached to. |
| `TRAEFIK_ROUTER` | `homelab` | Name of this dashboard's Traefik router and service; change it only if it clashes with another one. |
| `COMPOSE_FILE` | `docker-compose.server.yml` | Makes plain `docker compose ...` commands use the server file (see below). |

Find the network with `docker network ls` or
`docker inspect <traefik-container> --format '{{json .NetworkSettings.Networks}}'`.
The entrypoint and resolver names are in Traefik's static configuration
(`entryPoints:` / `certificatesResolvers:`).

## 2. Use the right Compose file

`COMPOSE_FILE=docker-compose.server.yml` in `.env` matters. Without it, Compose picks
`compose.yml` (the development stack), and an `up` would replace the server containers
with ones that have no Traefik labels and open registration.

**Don't copy the server file under another name** (for example `docker-compose.yml`). If
both `compose.yml` and `docker-compose.yml` exist, Compose silently uses `compose.yml`, and
a copy won't receive updates. If you made a copy of an older version, move your values
into `.env`, delete the copy, and continue with `docker-compose.server.yml`.

## 3. Start it

```bash
docker compose -f docker-compose.server.yml up -d --build
```

Open `https://<your-domain>` and register your account right away. The first
account needs the **setup token** (`docker compose logs backend | grep "setup token"`,
or the value of `ADMIN_BOOTSTRAP_TOKEN` if you set one), so a stranger who finds a
fresh deployment can't claim it; then registration closes. That first account is
the admin. Traefik terminates HTTPS with its own certificates; the stack needs no
certificate configuration.

The public address serves only the dashboard. Collectors don't use it; set up
their route in the next step.

## 4. Let remote servers report over your VPN

Collectors reach the **collector gate**, not the public domain. In `.env`:

| Setting | Example | Meaning |
|---|---|---|
| `COLLECTOR_BIND` | `10.8.0.1` | Your VPN server's address on the dashboard machine (for OpenVPN, the `tun` address). The gate's port is published on this address only. |
| `COLLECTOR_PORT` | `8081` | Port of the gate on that address. |
| `COLLECTOR_PUBLIC_URL` | `http://10.8.0.1:8081/api` | What **Connect a server** tells new machines to use. |
| `COLLECTOR_IMAGE`, `DOCKER_PROXY_IMAGE` | `ghcr.io/…@sha256:…` | The images remote machines install; see below. |

Things to get right:

- **Bind to the VPN address, not `0.0.0.0`.** Docker's published ports bypass
  `ufw` and similar firewalls, so a "deny from outside" rule does not protect them.
- If Docker starts before the VPN is up, the bind fails until the address exists.
  `restart: unless-stopped` retries, or make Docker start after the VPN (for
  example a systemd drop-in with `After=openvpn-server@<name>.service`).
- Traffic inside the VPN is plain HTTP over the encrypted tunnel; collectors
  authenticate with a per-server token that the dashboard stores only as a hash.

### Publishing the collector images

Remote machines install the collector and the Docker proxy from container images,
pinned by digest, so no code is ever downloaded from the dashboard. The
[Publish images](../.github/workflows/images.yml) workflow builds them (amd64 and
arm64), signs them with cosign and prints their digests:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

1. Open the workflow run's summary and copy the `COLLECTOR_IMAGE=…` and
   `DOCKER_PROXY_IMAGE=…` lines into `.env`.
2. If the repository is private, make the three packages **public** (GitHub →
   Packages → *Package settings* → *Change visibility*); otherwise machines can't
   pull them without credentials.
3. Rebuild the stack so the backend knows the images:
   `docker compose -f docker-compose.server.yml up -d --build`.

**Connect a server** won't generate a command until both images are set.

## Updating

```bash
git pull
docker compose -f docker-compose.server.yml up -d --build
```

Always rebuild **all** services (don't name individual ones). The backend,
frontend, collector, Docker proxy and system helper each have their code built
into their image, so a service that isn't rebuilt keeps running the old version.
The database schema is updated automatically when the backend starts.

**Upgrading from a version that had no `REDIS_PASSWORD`:** add one to `.env` first
(`echo "REDIS_PASSWORD=$(openssl rand -hex 24)" >> .env`), then update as above. Compose refuses to start
without it. This version also separates the containers onto three networks and stores login sessions
only as hashes, so **everyone is signed out once** and signs in again; no data is lost. Back the password
up with your other secrets; if you change it later, Redis is simply restarted with the new one.

Collectors on other machines run the image digest from the command you generated.
To update them, publish a new version tag, put the new digests in `.env`, and run a
freshly generated command on each machine (or pull the new digests into its
compose project). Collectors started from `collector-compose.yml` need
`docker compose -f collector-compose.yml up -d --build` after a `git pull`.

<diagram - What each step updates: git pull → source on disk; docker compose up -d --build → backend, frontend, collector, docker-proxy and gate containers rebuilt and recreated; git tag → Publish images workflow → new digests → .env and a fresh Connect a server command for remote machines; show that skipping --build leaves the old image running>

## Without Traefik

- **Another reverse proxy (nginx, Caddy, HAProxy, ...):** use the **server stack** (`docker-compose.server.yml`),
  not the development stack, and point your proxy at its dashboard port (`127.0.0.1:5173` by default; the
  Traefik labels are simply unused). The server stack is the one that keeps the collector endpoints off the
  public side, registration closed and the session cookie HTTPS-only. Terminate HTTPS in your proxy and have it
  send `X-Forwarded-For`.
- **Caddy profile (`--profile https`, development stack): private use only.** It publishes the development
  stack's single listener, which serves the collector endpoints on the same address as the dashboard, so it
  is not suitable for a dashboard that is open to the internet. Use it on a LAN or behind a VPN.
- **Private, over a VPN:** install Tailscale, WireGuard or OpenVPN on the server
  and your devices, run `docker compose up -d --build`, and use `http://<vpn-ip>:5173`
  (collectors: `http://<vpn-ip>:5173/api`). Nothing is exposed publicly. This uses
  the development stack's single listener; for a public dashboard with collectors
  on a VPN, use the server stack above.
