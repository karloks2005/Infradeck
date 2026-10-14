[← Documentation](README.md)

# The containers and why each one exists

Infradeck runs as a handful of small containers rather than one big one. Each has one job and only the
access that job needs. This page goes through them one by one: what it does, how it works, why it is a
separate container, and what happens when it stops.

For the exact permissions of each piece and the limits of each protection, see [SECURITY.md](../SECURITY.md).
For the settings, see [Configuration](configuration.md).

## At a glance

| Container | Image | Runs on | Required? | Job |
|---|---|---|---|---|
| `infradeck-frontend` | built from `docker/frontend.Dockerfile` (nginx) | dashboard machine | yes | Serves the web app and passes `/api` to the backend. |
| `infradeck-backend` | built from `docker/backend.Dockerfile` (Python, FastAPI) | dashboard machine | yes | The API: sign-in, metrics intake, alerts, statistics, notifications, the assistant. |
| `infradeck-postgres` | `postgres:16-alpine` | dashboard machine | yes | Everything that must be kept: accounts, servers, metric history, incidents, settings. |
| `infradeck-redis` | `redis:7-alpine` | dashboard machine | yes | Short-lived state: sign-in sessions, rate-limit counters, pending two-factor logins, a small cache. |
| `infradeck-collector` | built from `docker/collector.Dockerfile` | every monitored machine | yes, one per machine | Reads the machine's CPU, memory, disks, network and containers and sends them to the backend. |
| `infradeck-docker-proxy` | built from `docker/docker-proxy.Dockerfile` | every monitored machine | yes, next to each collector | The only container with the Docker socket; answers six read-only questions. |
| `infradeck-collector-gate` | `nginx:alpine` | dashboard machine, server stack only | yes in `docker-compose.server.yml` | The only door to the collector endpoints, published on your VPN address. |
| `infradeck-system-helper` | built from `docker/system-helper.Dockerfile` | any monitored machine | optional (`--profile system-events`) | Reads the kernel log and wtmp: OOM kills, kernel errors, crash vs clean shutdown. |
| `infradeck-port-owners` | same image as the system helper | any monitored machine | optional (`--profile port-owners`) | Names the process behind each listening port in the Network tab. |
| `infradeck-caddy` | `caddy:2-alpine` | dashboard machine, `compose.yml` only | optional (`--profile https`) | HTTPS with an automatic Let's Encrypt certificate when there is no Traefik. |

A remote server you add with **Connect a server** runs only the collector-side containers: the
collector, the Docker proxy, and the optional helpers ([collector-compose.yml](../collector-compose.yml)).
The dashboard machine runs everything, because it monitors itself as well.

## How they fit together

```
                         browser
                            │ HTTPS (Traefik or Caddy) or plain HTTP on the LAN
                            ▼
                   ┌─────────────────┐
                   │    frontend     │  nginx: web app + /api → backend
                   └────────┬────────┘
                            │ network "front"
  remote collectors         ▼
  over VPN ──► collector-gate ──► ┌─────────────────┐
                                  │     backend     │
  local collector ──► gate :8080 ►│  FastAPI        │
                                  └───┬─────────┬───┘
                                      │         │  network "data" (internal, no route out)
                                      ▼         ▼
                                  postgres     redis


  On every monitored machine:

   /proc, / (read-only) ──► collector ──► HTTPS to the backend (through the gate)
                               ▲   ▲
       Unix socket in a volume │   │ JSON file in a volume
                               │   │
                     docker-proxy  system-helper / port-owners
                         │
                 /var/run/docker.sock
```

Three Docker networks keep each container to what it needs:

- **`data`** is `internal: true`: it has no route to the outside. Only the backend, PostgreSQL and Redis
  are on it, so nothing but the backend can talk to the databases, and the databases can't reach the
  internet.
- **`front`** connects the frontend, the backend and the gate.
- **`collector`** (server stack) holds only the local collector and the gate, so the collector can reach
  the gate and nothing else.

The Docker proxy, the system helper and the port-owners helper have **no network at all**
(`network_mode: none`). They talk to the collector only through files and a Unix socket in shared volumes.

---

## infradeck-frontend

**What it does.** Serves the dashboard (a React app built with Vite) and forwards every request under
`/api/` to the backend.

**How it works.** The image is built in two stages: Node builds the app into static files, then only
those files are copied into a plain `nginx:alpine` image. Node and the source code are not in the
running container. nginx sets the security headers (Content-Security-Policy, Permissions-Policy and
others) and proxies `/api/` to `backend:8000`. On the server stack it uses
[`docker/nginx.strict.conf`](../docker/nginx.strict.conf), which additionally **refuses** the collector
endpoints: on the public side nobody can register a collector or send metrics.

**Why this approach.**
- *One address for the app and the API.* The browser loads the page and calls the API on the same origin,
  so the sign-in cookie can be `SameSite` and `HttpOnly`, and no cross-origin (CORS) exceptions are
  needed.
- *The backend is never exposed directly.* Only nginx is published; the backend's port is bound to
  `127.0.0.1` (development) or not published at all (server).
- *nginx is better at serving files than Python.* Static files, compression and caching are what nginx
  does best, and the backend stays focused on the API.

**If it stops.** The dashboard can't be opened. Collectors are unaffected on the server stack (they go
through the gate); on the development stack the local collector talks to the backend directly.

## infradeck-backend

**What it does.** The whole application logic:

- sign-in, sessions, two-factor codes, roles, invites, API tokens;
- registering collectors and receiving their check-ins (`/monitoring/metrics/ingest`);
- the alert rules, incidents and notification channels (email/webhook, quiet hours);
- statistics, rollups (summarised history) and retention;
- the optional Homie assistant, which talks to Gemini when `GEMINI_API_KEY` is set.

**How it works.** A FastAPI app run by uvicorn on port 8000. At startup it creates any missing tables and
columns in PostgreSQL, moves old Redis keys to the current prefix, and prints the first-run setup token
if no account exists yet. Two background threads run inside it: one marks servers offline when their
collector stops checking in, the other sends notifications for opened and resolved incidents.
`GET /health` reports whether PostgreSQL and Redis answer; Docker's healthcheck uses it, and the other
containers wait for it before they start.

**Why this approach.**
- *Python* because the collector and helpers are Python too, so validation and parsing code is shared and
  understood the same way on both sides.
- *FastAPI* validates every request against a typed model before any code sees it. That matters here,
  because collectors on remote machines are treated as untrusted input.
- *One process, no separate worker container.* The background jobs are small and periodic; a separate
  queue and worker would add two more containers for no gain at homelab scale.
- *Stateless.* Everything the backend keeps lives in PostgreSQL or Redis, so the container can be rebuilt
  or restarted at any time without losing anything.

**If it stops.** The dashboard shows errors, and collectors keep retrying until it is back. Check-ins sent
while it was down are lost (the collector does not queue them), so the history has a gap.

## infradeck-postgres

**What it does.** Stores everything that must survive a restart:

- users, roles, preferences, API tokens, invites, notification channels;
- servers and their system information, pending (not yet approved) collectors;
- every metric sample (host and per-container) and the summarised rollups for long ranges;
- incidents and their history, alert rule settings, global settings.

The full list of tables is in [Database schema](database.md).

**How it works.** The official `postgres:16-alpine` image with its data in the `postgres_data` volume.
On the very first start, [`docker/postgres/init/`](../docker/postgres/init) creates the users table; the
backend creates and upgrades everything else itself at startup. The healthcheck (`pg_isready`) holds the
backend back until the database accepts connections. The container is on the internal `data` network
only and publishes no port.

**Why this approach.**
- *Many writers at once.* Every collector checks in every minute while the background threads and
  users read and write at the same time. PostgreSQL handles concurrent writes properly; SQLite locks the
  whole file for each write. (The tests do use SQLite, because they run in one process.)
- *Time-series queries.* Statistics group, average and take maximums over millions of samples;
  PostgreSQL's indexes and planner do that well.
- *Reliability.* Transactions, crash recovery and standard backup tools (`pg_dump`, see
  [SETUP_DATABASES.md](../SETUP_DATABASES.md)).
- *Why not a dedicated time-series database (InfluxDB, Prometheus).* Accounts, incidents and metrics would
  then live in two databases with two backup routines. With rollups and retention, PostgreSQL is fast
  enough for the scale this project is built for.

**If it stops.** The backend reports `degraded` on `/health`, and almost every page and every check-in
fails until it is back. Nothing already stored is lost: it is all in the volume.

## infradeck-redis

**What it does.** Holds small, short-lived values that expire by themselves:

| Key (prefix `infradeck:`) | What it is | Lives for |
|---|---|---|
| `session:…` | A signed-in browser session. | 24 hours, or 30 days with *Remember me* |
| `user-sessions:…` | Which sessions belong to which user (the *Sessions* list on the Account page, "sign out everywhere"). | as long as the sessions |
| `2fa-pending:…` and its attempt counter | A password check that still waits for the authenticator code. | 5 minutes |
| `rate-limit:…` | Attempt counters for sign-in, registration, two-factor, password change, collector registration, invite checks, the assistant chat, notification tests and more. | the length of each window (e.g. 60 s) |
| `stats:…` | Cached fleet statistics, so the Statistics page doesn't recompute them on every load. | 60 seconds |

Session IDs and the values limited per address or email are stored only as SHA-256 digests, so a copy of
Redis doesn't hand out working sessions or a list of email addresses.

**How it works.** The official `redis:7-alpine` image with a password (`REDIS_PASSWORD`) and append-only
persistence (`--appendonly yes`) into the `redis_data` volume, so people stay signed in across a restart.
It is on the internal `data` network only and publishes no port; the backend connects with `REDIS_URL`.

**Why Redis is a required part, and why not just PostgreSQL.**
- *Sessions are checked on every request.* Each API call looks up its session. Redis answers that from
  memory in well under a millisecond and doesn't add load to the database that is busy taking check-ins.
- *Expiry is built in.* Every key above is written with a lifetime and Redis deletes it on time. In
  PostgreSQL we would need expiry columns, a cleanup job and indexes to do the same.
- *Rate limiting must be exact under load.* A limit like "5 sign-in attempts per minute" needs a counter
  that many requests increase at the same moment. Redis `INCR` + `EXPIRE` in one pipeline is atomic, so
  parallel attempts can't slip past the limit.
- *Server-side sessions instead of tokens in the browser (JWT).* Because the session lives in Redis, signing
  out, "sign out everywhere", a password reset or an admin disabling a user takes effect immediately. A
  self-contained token would stay valid until it expires.
- *It works when the backend restarts or runs several copies.* Counters and sessions kept in the
  backend's memory would vanish on every rebuild and would not be shared between processes.

**If it stops.** It fails **closed**: sign-in, registration and every other rate-limited action answer
`503 Rate limiting service unavailable` instead of running without limits, and existing sessions can't be
checked, so users appear signed out. `/health` shows `redis: down`. Collectors keep checking in (they
authenticate with their own credentials stored in PostgreSQL), so no monitoring data is lost. When Redis
comes back, sessions saved in its volume work again.

## infradeck-collector

**What it does.** Measures the machine it runs on and sends one check-in to the backend every 60 seconds
(`INFRADECK_CHECK_INTERVAL_SECONDS`): CPU, load, memory, swap, disks and inodes, network traffic,
listening ports, uptime and boot ID, system information, and for every Docker container its state,
health, restarts, CPU, memory, swap and volumes. Each figure is explained in [Metrics](metrics.md).

**How it works.**
- It is one Python file using only the standard library: no third-party packages to audit or keep updated.
- It reads the host's `/proc` and its filesystem through **read-only** mounts (`/proc:/host/proc:ro`,
  `/:/hostfs:ro`). It runs with **all capabilities dropped**, a read-only root filesystem and
  `no-new-privileges`, so although it runs as root it can't read other users' private files (SSH keys,
  `.env` files) on the host.
- Container data comes from the Docker proxy through a Unix socket, not from Docker directly; the
  collector has no Docker socket.
- If the optional helpers run, it also reads their summary files from the shared `system_events` volume.
- **Registration:** on first start it registers with the backend and appears under *New servers detected*;
  once an admin approves it (or it was started with a join token or enrollment key), it receives its own
  credentials and saves them in the `collector_state` volume. Later restarts reuse them.

**Why this approach.**
- *Push, not pull.* The collector calls the backend; the backend never connects to your servers. Servers
  behind NAT or a home router need no open port, and the dashboard holds no SSH keys or passwords for them.
- *A container rather than an installed agent.* Installing, updating and removing it is one compose
  command, it runs the same on every distribution, and its permissions are written down in the compose file.
- *Least privilege.* The part that sends data over the network can read only public host information.
  Everything that needs more access is split into the separate containers below.

**If it stops.** After a few missed check-ins the backend marks the server offline and opens a
*server offline* incident.

## infradeck-docker-proxy

**What it does.** Lets the collector read container information without giving it the Docker socket.

**How it works.** It is the only container that mounts `/var/run/docker.sock`. It listens on a Unix
socket inside the `docker_proxy_socket` volume, which only it and the collector share. It answers exactly
six kinds of `GET` request (list containers, inspect one, its stats, one-shot stats, the Docker version,
disk usage) and refuses everything else. It also cuts each answer down to the fields the collector uses:
for example environment variables, which often contain passwords, and all labels except the compose
project name never leave the proxy. It has no network, no capabilities, and a read-only filesystem.

**Why this approach.**
- *The Docker socket is all or nothing.* Anything that can talk to it can start a privileged container,
  which is the same as root on the host, and a `:ro` mount flag doesn't change that. Keeping the socket
  in a tiny container with no network means that the container with network access (the collector) can
  never send a write request to Docker.
- *Why our own proxy instead of an existing one* (such as `tecnativa/docker-socket-proxy`). Those filter
  by URL path but pass Docker's full answers through, including every container's environment variables.
  Ours matches the exact request strings with no decoding and returns only the needed fields. It is about
  200 lines of standard-library Python that can be read in one sitting.

**If it stops.** The Containers tab for that server goes empty and container alerts stop; host metrics
keep coming.

## infradeck-collector-gate (server stack only)

**What it does.** The only way to reach the four collector endpoints (register, poll registration,
enroll, ingest) when the stack runs behind Traefik.

**How it works.** A plain `nginx:alpine` with [`docker/nginx.collector.conf`](../docker/nginx.collector.conf)
and two listeners:

- **`:8081` (VPN).** Publish it on your VPN address only (`COLLECTOR_BIND`). Remote collectors use it, and
  registration there needs a join token from **Connect a server**.
- **`:8080` (internal).** Never published. Only the local collector on the `collector` network uses it.

Everything else answers `404`. The gate marks each request with the listener it came through, and the
backend (with `COLLECTOR_ACCESS=strict`) accepts collector traffic only when that mark is present. The
gate is not on Traefik's network, so other apps behind the same Traefik can't reach the collector
endpoints either.

**Why this approach.** The dashboard itself may be public on the internet, but the endpoints that
create servers and accept data shouldn't be. Separating them into a different container and port means
that the reverse proxy, the firewall and the VPN each decide on their own who may reach them. Docker's
published ports bypass `ufw`, so the bind address, not the firewall, is what protects the port.

**If it stops.** No collector can check in (including the local one), and the servers go offline on the
dashboard. The web app keeps working.

## infradeck-system-helper (optional)

**What it does.** Adds things the collector can't see with its limited access: processes killed by the
out-of-memory killer, kernel errors, and whether the last reboot was a crash or a clean shutdown.

**How it works.** Every 30 seconds it reads the kernel log and the host's `/var/log/wtmp` (read-only),
removes anything that isn't needed, and writes a short JSON summary (`system.json`) into the
`system_events` volume, which the collector reads and sends along. Its only privilege is `CAP_SYSLOG`
(needed for the kernel log). It has no network and a read-only filesystem. Turn it on with
`docker compose --profile system-events up -d`.

**Why a separate, optional container.** Reading the kernel log needs a capability the collector should not
have. Kept apart, the extra access sits in a container that can't send anything anywhere; only the
summary reaches the collector. It is optional because not everyone wants to grant that privilege.

**If it stops or is off.** The System tab shows these events as unavailable; nothing else changes.

## infradeck-port-owners (optional, off by default)

**What it does.** Names the process behind each listening port (for example `5432 → postgres`) in the
Network tab.

**How it works.** The same image as the system helper, started with `INFRADECK_HELPER_MODE=port-owners`.
It reads the host's `/proc`, matches listening sockets to processes, and writes only protocol, port and
process name to `ports.json` in the `system_events` volume. Turn it on with
`docker compose --profile port-owners up -d`.

**Why it is separate and off by default.** Looking into other users' processes needs `CAP_SYS_PTRACE`,
`CAP_DAC_READ_SEARCH`, the host's `/proc`, and no AppArmor confinement. Together those can read any
file on the host, which makes this the most privileged piece of the project. It therefore has no network
and a read-only filesystem, and it is opt-in. Read [SECURITY.md](../SECURITY.md) before enabling it.

**If it stops or is off.** The Network tab lists ports without process names.

## infradeck-caddy (optional, `compose.yml` only)

**What it does.** Puts HTTPS in front of the frontend on a public domain when you don't run Traefik.

**How it works.** The official `caddy:2-alpine` image with [`docker/Caddyfile`](../docker/Caddyfile):
it obtains and renews a Let's Encrypt certificate for `DOMAIN` and proxies everything to `frontend:80`.
Start it with `docker compose --profile https up -d`.

**Why Caddy here.** It handles certificates with no configuration beyond the domain name. The server
stack doesn't include it because it expects an existing Traefik to handle HTTPS (see
[Deploying](deployment.md)).

## Volumes

| Volume | Used by | Holds | Safe to delete? |
|---|---|---|---|
| `postgres_data` | postgres | The database. | **No**: all accounts, servers and history are lost. |
| `redis_data` | redis | Sessions and counters. | Yes, but everyone is signed out. |
| `collector_state` | collector | This machine's collector credentials. | The server has to be approved again and appears as a new server. |
| `docker_proxy_socket` | docker-proxy, collector | The Unix socket between them. | Yes, it is recreated. |
| `system_events` | helpers (write), collector (read) | The helpers' JSON summaries. | Yes, it is recreated. |
| `caddy_data`, `caddy_config` | caddy | Certificates. | Yes, but Caddy requests new certificates (Let's Encrypt has rate limits). |
