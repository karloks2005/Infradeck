# Vexion

A self-hosted dashboard for monitoring all your homelab machines in one place.
One machine runs the dashboard; every other machine runs a small collector that
reports host and Docker container metrics back to it. It works the same whether
the machines share a LAN or are spread across sites and VPSes.

It also includes **VertexAI**, an optional monitoring-only AI assistant (Gemini)
that can run a fixed set of read-only health checks and never executes commands.
It's main purpose is to report errors, diagnose errors and propose optimal fixes.

Security comes first: the collectors never get the Docker socket, remote machines
report over a VPN to a listener the public internet can't reach, and nothing
the dashboard serves is ever executed on your hosts. See [SECURITY.md](SECURITY.md)
for what every component can touch and the limits of each protection.

<ui - Full dashboard on desktop: the "This machine" CPU/memory/disk charts at the top, the three stat tiles, and the Fleet section with two or three servers (one expanded, the rest collapsed), plus an open incident in Active incidents>

## Features

- **One dashboard for every machine**: CPU, RAM, load, swap, network totals and
  uptime per server, plus disk and inode usage for **every mounted filesystem**
  (root, data disks, NFS/SMB shares, ZFS pools).
- **This machine at a glance**: live CPU, memory and disk charts for the machine
  running the dashboard, with the last 30 minutes of history.
- **Statistics for any range**: a page with charts from the last hour to everything ever
  recorded, per server (resources, distributions, busy hours, storage with a
  "full in about N days" estimate, reliability), per container (cards and details) and for the
  whole fleet (incidents over time, containers by state, availability, rankings). Long
  ranges stay cheap because history is summarised into hourly, daily, weekly and monthly rows.
- **Container details**: containers sit in a collapsible list per server, sorted by
  name, with problems (unhealthy, restarting, stopped) shown even when it's closed.
  Open a container to see CPU, memory used/limit, processes, network and disk I/O,
  uptime, ports, health status and CPU/memory history. Stopped containers show how
  they ended (exit code, or "out of memory"), and a **Recent changes** list shows
  containers that appeared, disappeared or came up on a different image.
- **System health per server**: reboots, OOM-killer activity, kernel fault flags,
  clock synchronisation and pending reboots, plus (with the optional system helper)
  whether the last shutdown was clean and which process the OOM killer stopped.
- **Network at a glance**: which ports each server listens on (and which are reachable
  from other machines), whether its firewall configuration changed, and, if you turn
  it on, whether it can still reach the internet.
- **Incidents with sensible alert rules**: disk (with per-mount overrides), inodes,
  memory, swap, load per CPU core, container restart loops, failing healthchecks,
  reboots and crashes, OOM kills, kernel faults, unsynchronised clocks, an
  unreachable or slow Docker daemon, containers that crash or are killed for running
  out of memory, containers that vanish or change image, possible memory leaks, new
  ports opened to the network, firewall changes, lost internet or DNS, and servers
  that stop reporting. One incident per problem, opened only after a sustained
  breach where that makes sense and resolved automatically when it clears.
  Thresholds are editable by admins.
- **Add servers with one command and one click**: click **Connect a server**, run
  the generated command on the new machine, then click **Add**. The command carries
  a single-use token, so only an admin can add machines.
- **Built to be exposed carefully**: a public dashboard behind Traefik, with
  collectors reporting over your VPN instead. Collectors run with no Docker
  socket and no capabilities; a separate read-only proxy answers Docker queries.
- **Per-server check interval** (15 s to 15 min), set from the dashboard.
- **Works on phones**: bottom navigation, touch-sized controls and layouts that
  fit from 320 px wide screens up.
- **Accounts with roles**: the first account (created with a setup token) is the
  admin; other accounts get a read-only view. Passwords must be at least 16
  characters. Redis-backed sessions and login/registration rate limiting.
- **Account security**: optional two-factor sign-in (authenticator app and single-use
  recovery codes), **required for admins by default**; change your password, review
  and end your sessions, and reset a locked-out account from the host.
- **Homie chat assistant** (optional) restricted to predefined read-only checks.

<ui - Three phone screenshots side by side (about 390 px wide): the dashboard top with the bottom tab bar, a server card with the Containers list open, and Admin → Alerts thresholds>

## Architecture

```mermaid
flowchart LR
    subgraph main["Dashboard machine (docker compose)"]
        direction TB
        proxy["frontend: nginx<br/>dashboard + /api (public side)"] --> backend["backend: FastAPI"]
        gate["collector-gate: nginx<br/>collector endpoints only"] --> backend
        backend --> pg[(PostgreSQL)]
        backend --> redis[(Redis)]
        localc["collector"] --> gate
        localc -->|"unix socket"| dproxy["docker-proxy<br/>4 read-only requests"]
        helper["system-helper (optional)<br/>CAP_SYSLOG, no network"] -.->|"summary file"| localc
    end
    browser["Browser"] -->|"https://your-domain"| traefik["Traefik"]
    traefik --> proxy
    c1["collector on server A"] -->|"VPN"| gate
    c2["collector on server B"] -->|"VPN"| gate
```

- The **frontend** container (nginx) serves the dashboard and proxies `/api` to
  the backend. On a server install this is the only thing Traefik publishes, and
  it refuses every collector endpoint.
- The **collector gate** (nginx) is the only way to reach the collector endpoints
  (register, poll, enroll, ingest). It publishes one port on your **VPN address**
  and is not attached to Traefik's network.
- The **backend** (FastAPI) stores metrics in PostgreSQL and sessions and rate
  limits in Redis. Its own port is published on `127.0.0.1:8060` only in the
  development stack (change it with `BACKEND_PORT`).
- **Collectors** only make outgoing requests. Monitored machines need no open
  ports, only a route to the collector gate.
- The **Docker proxy** is the only container that mounts `/var/run/docker.sock`.
  It has no network; the collector reaches it through a Unix socket in a shared
  volume and it answers only container list, inspect and stats.
- The optional **system helper** reads the kernel log and `wtmp`, and writes a
  sanitised summary file the collector picks up (profile `system-events`).
- PostgreSQL and Redis have no host ports; they are reachable only inside the
  Compose network.

The development stack (`compose.yml`) keeps a single listener: everything,
including the collector endpoints, is reachable through the frontend. The server
stack splits them as described above.

## Quick start (laptop / development)

Requirements: Docker with Compose v2.

```bash
cp .env.example .env              # then set POSTGRES_PASSWORD to a long random value
docker compose up -d --build
```

1. Find the one-time **setup token** in the backend log:
   `docker compose logs backend | grep "setup token"`. Open http://localhost:5173
   and register an account with it. Use a password of at least 16 characters (a
   few random words work well). The first account is the admin.
2. This machine appears under **New servers detected**. Click **Add**.
3. Metrics start arriving within a few seconds, and the charts at the top of the
   dashboard start following this machine.

Health check: http://localhost:5173/api/health

```bash
docker compose logs -f backend frontend collector   # follow logs
docker compose --profile system-events up -d        # also run the optional system helper
docker compose down                                 # stop (data volumes are kept)
```

To try **Connect a server** on the same machine, set `COLLECTOR_IMAGE` and
`DOCKER_PROXY_IMAGE` in `.env` to `homelab-collector:latest` and
`homelab-docker-proxy:latest` (the images Compose just built). Other machines
need published images; see [Publishing the collector images](docs/deployment.md#publishing-the-collector-images).

## Deploying on a server

The server stack (`docker-compose.server.yml`) publishes the dashboard through an existing **Traefik** and
serves the collector endpoints only on a separate listener bound to your **VPN address**. In short:

1. `cp example.env .env`, then set `POSTGRES_PASSWORD`, `DASHBOARD_DOMAIN`, your Traefik names and
   `COMPOSE_FILE=docker-compose.server.yml` (the last one matters: without it Compose uses the development stack).
2. Generate `SECRETS_KEY` (needed for two-factor sign-in) and set `COLLECTOR_BIND` / `COLLECTOR_PUBLIC_URL` for your VPN.
3. `docker compose -f docker-compose.server.yml up -d --build`, then register the first account with the setup token from the backend log.
4. Click **Connect a server** to add other machines.

Every step, with the reasons and the pitfalls, is in [Deploying on a server](docs/deployment.md) and
[Adding servers](docs/adding-servers.md). To update later: `git pull` and `docker compose -f docker-compose.server.yml up -d --build`.

## Documentation

Full documentation is in the [`docs/`](docs/README.md) folder:

| | |
|---|---|
| [Metrics: what every number means](docs/metrics.md) | How CPU %, load, memory, swap, disk, inodes, network and container figures are measured, and their limits. |
| [Statistics](docs/statistics.md) | History from the last hour to all time, per server, per container and for the whole fleet. |
| [The dashboard and system health](docs/dashboard.md) | Fleet cards, storage, the Network and System rows, the optional system helper. |
| [Alerts and incidents](docs/alerts.md) | Every rule and its default thresholds. |
| [Accounts and two-factor sign-in](docs/accounts.md) | Roles, passwords, authenticator codes, recovery and reset. |
| [Deploying](docs/deployment.md), [Adding servers](docs/adding-servers.md), [Configuration](docs/configuration.md), [Troubleshooting](docs/troubleshooting.md) | Installing, running and fixing it. |
| [Development](docs/development.md), [Database schema](docs/database.md), [API overview](docs/api.md), [Homie assistant](docs/assistant.md) | For contributors. |

## Security

[SECURITY.md](SECURITY.md) has the full picture: what each component can touch, and the limits of each
protection. In short:

- **Collectors are unprivileged.** No Docker socket, no capabilities, a read-only filesystem. Only a separate
  read-only proxy (no network, four allowed requests, stripped answers) touches the socket.
- **Collector endpoints are not public.** On the server stack they exist only on the gate bound to your VPN
  address, and joining needs a single-use token plus an admin's click. Remote machines install signed,
  digest-pinned images; the dashboard never serves code that runs on your hosts.
- **Fresh installs can't be claimed** (the first account needs a setup token), passwords need 16 characters,
  and two-factor sign-in is **required for admins by default**.
- **Browser protections:** cross-site requests are refused, with a strict Content-Security-Policy and HSTS.
- **Secrets at rest:** tokens stored only as hashes, passwords with bcrypt, two-factor secrets encrypted with your `SECRETS_KEY`.

## Development and tests

Backend (Python 3.12) and frontend (Node 20) can run without Docker; see [Development](docs/development.md).

```bash
source venv/bin/activate && pip install -r requirements-dev.txt && pytest     # backend, about two minutes
cd src/frontend && node --test                                                # chart helpers, nothing to install
```

[TESTS.md](TESTS.md) explains how the tests work, what they don't cover and what each one is for.

## License

MIT; see [LICENSE](LICENSE).
