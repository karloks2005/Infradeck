[← Documentation](README.md)

# Deploying on a server

There are two Compose files:

| File | Use | Differences |
|---|---|---|
| `compose.yml` | Laptop / development (the default when you run `docker compose` without `-f`) | Dashboard on port 5173 on all interfaces; plain HTTP; optional Caddy (`--profile https`). |
| `docker-compose.server.yml` | Server behind your reverse proxy (Traefik, Caddy, nginx, ...) | HTTPS-only session cookie; only the first account can register; port 5173 bound to `127.0.0.1`; no Caddy; **collector endpoints removed from the public side** and served by a separate collector gate on your VPN address. |

The server stack works behind any reverse proxy. For some proxies there is also a ready-made file in
[`deploy/`](../deploy) that you add on top of it, so the proxy picks the dashboard up without any setup of its own:

| Your proxy | Add | What it adds |
|---|---|---|
| Traefik | `deploy/traefik.yml` | The Traefik labels (router, entrypoint, certificate resolver) and Traefik's Docker network. Step-by-step: [Deploy Infradeck behind Traefik](deploy-traefik.md). |
| Anything else | nothing | Point your proxy at the dashboard port, `127.0.0.1:5173` (see [Other reverse proxies](#other-reverse-proxies)). |

The server stack runs the **published, signed images** of one release from GHCR; nothing is built on the server.
To build them from source instead (unreleased changes, a fork), add `deploy/build.yml` as well: see
[Building from source](#building-from-source).

## 0. Get a release

Clone the repository and check out the release you want to run. The compose file names the images of that
version, and the nginx settings it mounts come from the same checkout, so the two always match:

```bash
git clone https://github.com/karloks2005/infradeck.git
cd infradeck
git checkout v<version>
```

The releases are listed on the repository's **Releases** page.

## 1. Create `.env` on the server

Nothing in `docker-compose.server.yml` needs editing: everything specific to you comes from
`.env`, so checking out a newer release can always update the file. Start from the production template:

```bash
cp example.env .env
```

Then set at least these:

| Setting | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | *(required)* | A long random password for the bundled database. |
| `REDIS_PASSWORD` | *(required)* | A long random password for Redis, which holds the login sessions. Letters and digits only (`openssl rand -hex 24`). |
| `DASHBOARD_DOMAIN` | *(required)* | Your domain, without `https://`. Used for the allowed origin (CORS) and the Traefik router. |
| `TRUSTED_PROXIES` | empty | The subnet of your reverse proxy's Docker network, for Traefik `docker network inspect traefik_default --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`. Without it every client shares Traefik's address, so one person's failed sign-ins can lock everyone out for a minute. See [Reverse proxies](#reverse-proxies-and-client-addresses). |
| `TRAEFIK_ENTRYPOINT` | `websecure` | Traefik only: your HTTPS entrypoint name. |
| `TRAEFIK_CERT_RESOLVER` | `letsencrypt` | Traefik only: your certificate resolver name. |
| `TRAEFIK_NETWORK` | `traefik_default` | Traefik only: the Docker network Traefik is attached to. |
| `TRAEFIK_ROUTER` | `infradeck` | Traefik only: name of this dashboard's router and service; change it only if it clashes with another one. |
| `COMPOSE_FILE` | `docker-compose.server.yml`, with Traefik `docker-compose.server.yml:deploy/traefik.yml` | Makes plain `docker compose ...` commands use the server file and your proxy's file (see below). |
| `SECRETS_KEY` | *(strongly recommended)* | Encrypts two-factor secrets, notification addresses and the incident agent's API key. Two-factor is required for admins by default and can't be set up without it. Generate with `docker compose run --rm backend python -m src.backend.secrets_box`. |

With Traefik, find its network with `docker network ls` or
`docker inspect <traefik-container> --format '{{json .NetworkSettings.Networks}}'`.
The entrypoint and resolver names are in Traefik's static configuration
(`entryPoints:` / `certificatesResolvers:`).

## 2. Use the right Compose file

`COMPOSE_FILE` in `.env` matters. Without it, Compose picks `compose.yml` (the development
stack), and an `up` would replace the server containers with ones that have open registration
and serve the collector endpoints publicly. Set it to the server file, followed by your proxy's
file from `deploy/` if you use one, separated by `:`:

```bash
COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml   # with Traefik
COMPOSE_FILE=docker-compose.server.yml                      # any other proxy
```

All commands below are plain `docker compose ...` and rely on it. Don't add
`-f docker-compose.server.yml`: `-f` replaces `COMPOSE_FILE`, so it would leave out your proxy's
file (with Traefik, the dashboard would disappear from your domain until the next plain `up`).

**Don't copy the server file under another name** (for example `docker-compose.yml`). If
both `compose.yml` and `docker-compose.yml` exist, Compose silently uses `compose.yml`, and
a copy won't receive updates. If you made a copy of an older version, move your values
into `.env`, delete the copy, and continue with `docker-compose.server.yml`.

## 3. Start it

```bash
docker compose pull
docker compose up -d
```

`pull` downloads the images of this release (amd64 and arm64); `up` starts them. To check that the images are the
ones the release workflow built and signed, see [Checking the signatures](#checking-the-signatures).

Open `https://<your-domain>` and register your account right away. The first
account needs the **setup token** (`docker compose logs backend | grep "setup token"`,
or the value of `ADMIN_BOOTSTRAP_TOKEN` if you set one), so a stranger who finds a
fresh deployment can't claim it; then registration closes. That first account is
the admin. Your reverse proxy terminates HTTPS with its own certificates; the stack needs no
certificate configuration.

The public address serves only the dashboard. Collectors don't use it; set up
their route in the next step.

The stack also starts **`agent-worker`**, which runs the incident agent's diagnoses. Until an admin saves an API key in
**Admin → Incident agent** (see [Incident agent](assistant.md#setting-it-up)) it only notes that there is no key; with one, enter the model's price in **Admin → Incident agent** so its calls count
towards the budget.

## 4. Let remote servers report over your VPN

Collectors reach the **collector gate**, not the public domain. In `.env`:

| Setting | Example | Meaning |
|---|---|---|
| `COLLECTOR_BIND` | `<vpn-address>` | Your VPN server's address on the dashboard machine (for OpenVPN, the `tun` address). The gate's port is published on this address only. |
| `COLLECTOR_PORT` | `8081` | Port of the gate on that address. |
| `COLLECTOR_PUBLIC_URL` | `http://<vpn-address>:8081/api` | What **Connect a server** tells new machines to use. |
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
[Publish images](../.github/workflows/images.yml) workflow builds every Infradeck image (backend, frontend,
collector, Docker proxy and system helper; amd64 and arm64), signs them with cosign and prints their digests.
The version lives in the `VERSION` file at the repository root; the dashboard and the collector both read it,
and `docker-compose.server.yml` repeats it in its image tags (a test fails if the two differ). Tag the version
it holds:

```bash
git tag "v$(cat VERSION)" && git push origin "v$(cat VERSION)"
```

For a new version, change `VERSION` and the image tags in `docker-compose.server.yml` in one pull request
first, and tag after it is merged. The workflow refuses a
tag that doesn't match `VERSION`, so the images never report a different version than their tag.

Release tags are protected by two repository rulesets (Settings → Rules → Rulesets): only a repository admin can
create a `v*` tag, and nobody can move or delete one. Check the commit before you tag: to retag, you have to
switch the "never moved or deleted" ruleset off first. Prefer a new patch version instead.

1. Open the workflow run's summary and copy the `COLLECTOR_IMAGE=…` and
   `DOCKER_PROXY_IMAGE=…` lines into `.env`.
2. Make the five packages **public** (GitHub → your profile → Packages → each of `infradeck/backend`,
   `infradeck/frontend`, `infradeck/collector`, `infradeck/docker-proxy`, `infradeck/system-helper` → *Package
   settings* → *Change visibility*). New
   packages are private even when the repository is public, and machines can't pull private ones without
   credentials. Check from any machine that isn't signed in to GHCR:
   `docker manifest inspect ghcr.io/<owner>/infradeck/collector:<version>`.
3. Restart the stack so the backend knows the images: `docker compose up -d`.

**Connect a server** won't generate a command until both images are set.

## Updating

Back up the database first (it stays in its Docker volume, but a backup costs nothing):

```bash
docker compose exec -T postgres pg_dump -U infradeck -d infradeck -Fc > infradeck-before-update.dump
```

Then check out the new release and pull its images:

```bash
git fetch --tags
git checkout v<new-version>
docker compose pull
docker compose up -d
```

Your data lives in the stack's volumes, which an update never touches; don't run `docker compose down -v`, which
deletes them. The database schema is updated automatically when the backend starts. Read the
[changelog](../CHANGELOG.md) for anything a release asks you to do.

**Upgrading from a version that built its images on the server** (before 1.0.0): add nothing; the steps above pull
the published images instead. If you had added `--build` to your own scripts, drop it, or add
`deploy/build.yml` to `COMPOSE_FILE` to keep building from source. The old local images (`infradeck-backend`,
`infradeck-frontend` and the collector ones) are no longer used; remove them with `docker image prune` once
everything runs.

**Upgrading from a version where the frontend listened on port 80 inside its container:** it now listens on
8080 and runs without root. The compose files, `deploy/traefik.yml` and the Caddy profile already use the new
port; only a reverse proxy you configured yourself to reach `frontend:80` over a Docker network must change to
`frontend:8080`. A proxy that uses the published port (`127.0.0.1:5173`) needs no change. An old frontend image
under the new settings keeps restarting with `mkdir() "/var/cache/nginx/client_temp" failed (13: Permission
denied)`; `docker compose pull` (or `--build` with `deploy/build.yml`) replaces it.

**Upgrading from a version with the Traefik labels in `docker-compose.server.yml`:** they moved to
`deploy/traefik.yml`. Before you update, change `.env` to
`COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml`. Without it the dashboard starts
without the labels and Traefik stops serving your domain; adding it and running `docker compose up -d` again
fixes that.
The dashboard port's default is `127.0.0.1:5173` again; set `FRONTEND_PORT` if you rely on another one.

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

<diagram - What each step updates: git tag → Publish images workflow → signed images on GHCR and new digests; git checkout of the tag → compose file and nginx settings on disk; docker compose pull → that version's images; docker compose up -d → containers recreated on the same volumes; new digests → .env and a fresh Connect a server command for remote machines>

### Checking the signatures

Every image is signed by the release workflow with cosign, without a key: the signature is tied to the
workflow's identity on GitHub. With [cosign](https://docs.sigstore.dev/cosign/system_config/installation/)
installed, check an image before you run it:

```bash
cosign verify ghcr.io/karloks2005/infradeck/backend:<version> \
  --certificate-identity "https://github.com/karloks2005/Infradeck/.github/workflows/images.yml@refs/tags/v<version>" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

It prints the verified signature as JSON, and fails if the image wasn't signed by that workflow for that tag.
Repeat it for `frontend`, `collector`, `docker-proxy` and `system-helper`.

### Building from source

To run a commit that isn't released, or your own changes, build the images on the server. Add
`deploy/build.yml` to `COMPOSE_FILE`, after the server file and your proxy's file:

```bash
COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml:deploy/build.yml   # with Traefik
COMPOSE_FILE=docker-compose.server.yml:deploy/build.yml                      # any other proxy
```

Then build and start, and repeat after every `git pull`:

```bash
docker compose up -d --build
```

Always build **all** services (don't name individual ones): a service that isn't rebuilt keeps running the old
code. `LIVE_GRAPH_REFRESH_INTERVAL_SECONDS` applies only to a build like this; the published frontend refreshes
every 3 seconds, and each user can change that in **Account → Display**.

## Other reverse proxies

- **Another reverse proxy (nginx, Caddy, HAProxy, a tunnel connector, ...):** use the **server stack**
  (`docker-compose.server.yml`) on its own, not the development stack, and point your proxy at its dashboard
  port (`127.0.0.1:5173` by default). The server stack is the one that keeps the collector endpoints off the
  public side, registration closed and the session cookie HTTPS-only. Terminate HTTPS in your proxy, have it
  send `X-Forwarded-For`, and set `TRUSTED_PROXIES` to its address (see below).
- **A proxy that runs in Docker** can reach `frontend:8080` over a Docker network instead of the published
  port. Attach the **frontend to the proxy's network**, as `deploy/traefik.yml` does. Save this next to it
  (for example as `deploy/proxy.yml`) and add it to `COMPOSE_FILE` after the server file:

  ```yaml
  services:
    frontend:
      networks:
        - proxy

  networks:
    proxy:
      external: true
      name: <your-proxy-network>
  ```

  **Never attach the proxy to the stack's own `front` network.** The backend and the collector gate's internal
  listener are on it, and they trust whatever reaches them there: a proxy on `front` could call the backend
  past nginx, claim any client address, and register a collector without a join token. The proxy is the part
  most exposed to the internet, so it gets to see the frontend and nothing else.
- **Caddy profile (`--profile https`, development stack): private use only.** It publishes the development
  stack's single listener, which serves the collector endpoints on the same address as the dashboard, so it
  is not suitable for a dashboard that is open to the internet. Use it on a LAN or behind a VPN.
- **Private, over a VPN:** install Tailscale, WireGuard or OpenVPN on the server
  and your devices, run `docker compose up -d --build`, and use `http://<vpn-ip>:5173`
  (collectors: `http://<vpn-ip>:5173/api`). Nothing is exposed publicly. This uses
  the development stack's single listener; for a public dashboard with collectors
  on a VPN, use the server stack above.

## Reverse proxies and client addresses

Sign-in, registration, two-factor and enrollment are rate limited per client address. Behind a reverse proxy
every request arrives from the proxy, so the dashboard needs to know which addresses belong to your proxy:
only those may tell it the real client address in `X-Forwarded-For`. Put them in `TRUSTED_PROXIES` in `.env`,
comma-separated, and restart the frontend (`docker compose up -d frontend`). It works the same for any proxy.

| Your proxy runs | Set `TRUSTED_PROXIES` to | Find it with |
|---|---|---|
| In Docker (Traefik, Caddy, nginx, ...) | The subnet of the Docker network it shares with the dashboard | `docker network inspect <network> --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'` |
| On the host itself | The gateway of the dashboard's `front` network, where traffic from the host arrives from | `docker network inspect <project>_front --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'` |
| On another machine | That machine's address as the dashboard sees it | Your network setup |

Not sure? Sign in through the proxy and open **Account › Sessions**: the address shown there is the proxy's.
After setting it, the same page shows your own address instead.

- **Empty (default): nobody is trusted.** Every client behind the proxy shares the proxy's address and limits,
  so one person's failed sign-ins can lock everyone out for a minute. Safe, but set it.
- **Trusting a whole network** also trusts every other container on it. Prefer a network only the proxy and
  the dashboard share.
- **Several proxies in a chain** (for example a CDN in front of Traefik): list each of them; the dashboard
  takes the last address in the chain that isn't one of yours.
- The value is checked when the frontend starts: only IP addresses and CIDR ranges are accepted, and anything
  else stops the container with a message in `docker compose logs frontend`.
