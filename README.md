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
        localc -->|"unix socket"| dproxy["docker-proxy<br/>3 read-only requests"]
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
need published images; see [Publishing the collector images](#publishing-the-collector-images).

## Deploying on a server (behind Traefik)

There are two Compose files:

| File | Use | Differences |
|---|---|---|
| `compose.yml` | Laptop / development (the default when you run `docker compose` without `-f`) | Dashboard on port 5173 on all interfaces; plain HTTP; optional Caddy (`--profile https`). |
| `docker-compose.server.yml` | Server behind an existing Traefik | Traefik labels and network; HTTPS-only session cookie; only the first account can register; port 5173 bound to `127.0.0.1`; no Caddy; **collector endpoints removed from the public side** and served by a separate collector gate on your VPN address. |

### 1. Create `.env` on the server

Nothing in `docker-compose.server.yml` needs editing: everything specific to you comes from
`.env`, so `git pull` can always update the file. Start from the production template:

```bash
cp example.env .env
```

Then set at least these:

| Setting | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | *(required)* | A long random password for the bundled database. |
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

### 2. Use the right Compose file

`COMPOSE_FILE=docker-compose.server.yml` in `.env` matters. Without it, Compose picks
`compose.yml` (the development stack), and an `up` would replace the server containers
with ones that have no Traefik labels and open registration.

**Don't copy the server file under another name** (for example `docker-compose.yml`). If
both `compose.yml` and `docker-compose.yml` exist, Compose silently uses `compose.yml`, and
a copy won't receive updates. If you made a copy of an older version, move your values
into `.env`, delete the copy, and continue with `docker-compose.server.yml`.

### 3. Start it

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

### 4. Let remote servers report over your VPN

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

#### Publishing the collector images

Remote machines install the collector and the Docker proxy from container images,
pinned by digest, so no code is ever downloaded from the dashboard. The
[Publish images](.github/workflows/images.yml) workflow builds them (amd64 and
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

### Updating

```bash
git pull
docker compose -f docker-compose.server.yml up -d --build
```

Always rebuild **all** services (don't name individual ones). The backend,
frontend, collector, Docker proxy and system helper each have their code built
into their image, so a service that isn't rebuilt keeps running the old version.
The database schema is updated automatically when the backend starts.

Collectors on other machines run the image digest from the command you generated.
To update them, publish a new version tag, put the new digests in `.env`, and run a
freshly generated command on each machine (or pull the new digests into its
compose project). Collectors started from `collector-compose.yml` need
`docker compose -f collector-compose.yml up -d --build` after a `git pull`.

<diagram - What each step updates: git pull → source on disk; docker compose up -d --build → backend, frontend, collector, docker-proxy and gate containers rebuilt and recreated; git tag → Publish images workflow → new digests → .env and a fresh Connect a server command for remote machines; show that skipping --build leaves the old image running>

### Without Traefik

- **Caddy (automatic Let's Encrypt):** with `compose.yml`, set
  `DOMAIN`, `SESSION_COOKIE_SECURE=true` and `FRONTEND_BIND=127.0.0.1` in
  `.env`, open ports 80/443, and run `docker compose --profile https up -d --build`.
- **Private, over a VPN:** install Tailscale, WireGuard or OpenVPN on the server
  and your devices, run `docker compose up -d --build`, and use `http://<vpn-ip>:5173`
  (collectors: `http://<vpn-ip>:5173/api`). Nothing is exposed publicly. This uses
  the development stack's single listener; for a public dashboard with collectors
  on a VPN, use the server stack above.

## Adding servers

Adding and removing servers, changing check intervals and managing the
enrollment key are admin-only.

1. On the dashboard, click **Connect a server**. Check the address field: with
   `COLLECTOR_PUBLIC_URL` set it shows your VPN address; otherwise it is prefilled
   with the address you opened the dashboard at and must be reachable from the
   new machine (not `localhost`).
2. Click **Generate command**. This creates a single-use token that expires
   (15 minutes by default) and puts it in the command.
3. Copy the command and run it on the new machine. It needs Docker with the
   compose plugin, and a route to the address above (the VPN).
4. Within a few seconds the machine appears under **New servers detected** with a
   **JOIN TOKEN** badge and a pairing code (also printed in
   `docker logs homelab-collector`). Click **Add**. It starts reporting on its
   next check, without a restart.

<ui - The Connect a server panel: the address field, the Generate command button with its single-use / expires note, and the copyable compose command>

<ui - The New servers detected list with one pending machine showing the JOIN TOKEN badge, its pairing code and the Add / Dismiss buttons>

The machine running the dashboard is added the same way (its collector starts
with the stack, over the gate's internal listener). That collector has
`HOMELAB_DASHBOARD_HOST=true` set in the Compose files, which makes it the machine
shown in the charts at the top of the dashboard and first in the server list.
Collectors installed on other machines don't set it.

### What the command does

It starts a small compose project named `homelab-collector` from the YAML on its
standard input (shortened here):

```bash
docker compose -p homelab-collector -f - up -d <<'EOF'
services:
  collector:
    image: ghcr.io/…/collector@sha256:…
    environment:
      HOMELAB_API_URL: http://10.8.0.1:8081/api
      HOMELAB_JOIN_TOKEN: hlj_…
      HOMELAB_DOCKER_HOST: /run/docker-proxy/docker.sock
    volumes:
      - /proc:/host/proc:ro
      - /:/hostfs:ro
      - collector_state:/var/lib/homelab-collector
      - docker_proxy_socket:/run/docker-proxy:ro
    cap_drop: [ALL]
    read_only: true
  docker-proxy:
    image: ghcr.io/…/docker-proxy@sha256:…
    network_mode: none
    cap_drop: [ALL]
    read_only: true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - docker_proxy_socket:/run/docker-proxy
EOF
```

| Part | Purpose |
|---|---|
| `HOMELAB_API_URL` | Where the collector gate is. |
| `HOMELAB_JOIN_TOKEN` | Proves an admin asked this machine to join. Single use, expires. |
| `image: …@sha256:…` | Signed images pinned by digest. No code is downloaded from the dashboard. |
| `/proc:/host/proc:ro` | Host CPU, memory, load, network, uptime, boot ID, OOM counter and kernel flags. |
| `/:/hostfs:ro` | Disk usage of every mounted filesystem, hostname, OS name and `/etc/machine-id`. |
| `docker-proxy` | The only container with the Docker socket. It answers container list, inspect (without environment variables) and stats, and nothing else. |
| `cap_drop: [ALL]`, `read_only: true` | The collector can't read other users' private files through `/hostfs`, or write outside its state volume. |
| `collector_state` volume | Keeps the token after approval, so restarts don't need approval again. |

To remove a collector completely:

```bash
docker rm -f homelab-collector homelab-docker-proxy
docker volume rm homelab-collector_collector_state homelab-collector_docker_proxy_socket
```

### How registration works

- Over the VPN a collector can't even ask to join without a valid join token. It
  then asks, checking every 5 seconds whether it has been approved. Clicking
  **Add** creates the server and uses up the token; the collector receives its own
  token and saves it. If a token is wrong, expired or used, the collector prints
  why and you copy a fresh command.
- Servers are matched on `/etc/machine-id`. If a new request matches a server you
  already have, the dashboard warns that approving it **replaces that server's
  credentials and signs out its current collector**, and asks you to confirm.
  Only do that when you are reinstalling that machine.
- If you delete a server, its collector can no longer report. On a server install
  it can't ask to join again without a new command (its token was used up). In the
  development stack it asks again and only comes back if you click **Add**.
  **Dismiss** moves a request to a collapsed "Dismissed" list.

<diagram - Sequence diagram of joining: admin clicks Generate command → join token created → command run on the new machine → collector POST /collectors/register with the token over the VPN listener → pending entry with pairing code → collector polls every 5 s → admin clicks Add (token consumed) → poll returns the token → collector saves it and starts POST /metrics/ingest; include the delete → 404 → needs a new command loop>
- Requests from collectors that stopped asking disappear after 2 minutes and are
  deleted after a day.

Alternatives:

- **Enrollment key (no approval step):** for scripted installs, generate a key
  under **Connect a server → Advanced** and add `HOMELAB_ENROLL_KEY: <key>` to the
  collector's environment. Only a hash of the key is stored; rotating or revoking
  it does not affect servers that are already connected.
- **Compose from the repository:** copy the repository to the machine, set
  `HOMELAB_API_URL` (and `HOMELAB_JOIN_TOKEN`) in a `.env` next to
  `collector-compose.yml`, and run
  `docker compose -f collector-compose.yml up -d --build`. This builds the images
  locally instead of pulling them.

### Networking checklist

- Every monitored machine must reach the collector gate (or the dashboard address
  in the development stack); the dashboard never connects to them.
- For machines outside your LAN, put them on a VPN (OpenVPN, WireGuard, Tailscale)
  and publish the gate on the VPN address. Don't expose plain HTTP to the
  internet: collector tokens travel over this connection.
- On a LAN, give the dashboard machine a fixed IP (e.g. a DHCP reservation).

## The dashboard

- **This machine** (top): live CPU, memory and root disk charts for the machine
  running the dashboard, loaded from the last 30 minutes of stored metrics.
- **Fleet**: one card per server, with the dashboard's own machine first. Click a
  server's name to collapse its card to a single line (status, CPU, RAM, fullest
  disk, incident count); **Collapse all** / **Expand all** appear with more than
  one server. Each card shows a **Storage** bar per filesystem, colored by the
  alert thresholds (including any per-mount overrides), a **System** row and a
  **Containers** list. Both lists start closed. A filter box appears for servers
  with more than 10 containers.
- **Network** row: internet reachability, how many ports the server listens on and how
  many are exposed, and the firewall state, opening into the listener list and the last
  7 days of port and firewall changes.
- **System** row: one line of status chips (when it booted, last shutdown, clock,
  OOM kills in the last 24 hours, kernel, pending updates) that opens into the
  last 7 days of reboots, OOM kills and kernel messages. A chip that says
  *unknown* means the collector couldn't read that value on this host, not that
  anything is wrong.
- **Active incidents**: open incidents, most severe first, with the server and
  when they started.

Which cards are collapsed is remembered per browser; the System and Containers
lists always start closed.

<ui - One expanded server card: CPU/RAM/Disk/Load tiles, Storage bars for several mounts (one amber or red), the System row open with a reboot and an OOM kill in its events, and the Containers list open with an unhealthy container and one container's details expanded (stat tiles and CPU/memory charts)>

<ui - Fleet section with every card collapsed to one line (status dot, name, This machine and incident badges, CPU/RAM/fullest disk) and the Collapse all / Expand all button>

## Statistics

**Statistics** in the navigation shows how one server behaved over time. Pick a server and a
range (1 hour, 6 hours, 24 hours, 7 days, 30 days, 90 days, 1 year or all time) and you get
CPU, memory, swap, load and root disk charts. Each chart is the average as a line with a
lighter band up to the highest value, so a short spike stays visible even in a yearly view.
Hover or touch a point for the exact time, average, highest value and number of check-ins, or
use **View as table**. Gaps (a server that was offline) are drawn as gaps. A note under the
cards says what each point is (for example "one per hour, from hourly summaries") and when
the history starts. The selection is kept in the address, so a view can be bookmarked, and
the range is remembered per browser. The short ranges refresh every 30 seconds, except
while the tab is hidden.

Under the charts, **Containers** shows every container of that server as a small card (status
dot, health, restarts, CPU, memory against its limit), with search and sorting (problems first
by default). Select a card for that container's CPU and memory over the chosen range, with its
memory limit drawn as a dashed line, how much of the time it was not running, and how many
times it restarted in the range. A container only reports CPU and memory while it runs.

Further down a server's page: how its CPU and memory use were **distributed** (the share of the
time in each 10% band), **when it is busy** (average CPU or memory by hour of the day and weekday
over the last 30 days, in your time zone), **storage** (a chart per filesystem with an estimate
such as "full in about 3 days" when there is a clear growth trend, and none otherwise), and
**reliability** (how long Docker takes to list containers, and how long the collector takes).

Choose **All servers** for the whole fleet: containers by state and open incidents by severity
(donuts), incidents opened over time by severity (stacked columns), what the incidents were
about, servers by disk usage, the containers using the most memory and CPU right now, and each
server's availability. Availability is the share of expected check-ins that arrived; it is
approximate because it assumes each server's current check interval. Every chart can be shown
as a table.

Short ranges come from the stored check-ins, longer ones from the summaries described under
[Database schema](#database-schema), so a year of history costs about as much to load as an
hour.

What to know about the statistics:

- **Time zones.** Charts for up to a day or two use your clock; day, week (Monday to Sunday) and
  month points are aligned to UTC and labelled in UTC, because that is how they are summarised.
- **Some figures are approximate**: distributions and the days-until-full estimate are worked out
  in the browser from the points you are looking at, availability assumes each server's current
  check interval, and the top containers show values from the latest check-in, not an average
  over the range. The page says so where it matters.
- **Pointer-only details.** Hover and touch tooltips don't work from the keyboard yet; every chart
  has **View as table** as an alternative.
- **Not built yet** (tracked in Milestone 8 on GitHub): CSV and JSON export, reboots per month, a
  state timeline and restarts per day for containers, rankings over the chosen range, network
  traffic charts, event markers on charts, and retention settings for the summaries.

## Alerts and incidents

Rules run on every collector check-in. Each problem opens at most one incident
(per server, per rule and, for disks, per mount); while it lasts the incident is
updated instead of duplicated, and it resolves automatically once the rule
stops firing.

| Rule | Default | Severity |
|---|---|---|
| Disk usage, per mount (overridable per mount) | 85% / 95% | warning / critical |
| Inode usage, per mount | 85% / 95% | warning / critical |
| Memory usage | 90% / 95% | warning / critical |
| Swap usage | 50% | warning |
| 5-minute load per CPU core | 1.5× / 2× | warning / critical |
| Container restart loop | 3 restarts within 30 minutes | critical |
| Container healthcheck `unhealthy` | – | critical |
| Container exited with an error | within 60 minutes of exiting | warning |
| Container killed by the kernel for running out of memory | within 60 minutes | critical |
| Container stopped by a signal (exit 137 or 143, usually `docker stop`) | within 60 minutes | info |
| Container no longer present | stays open 60 minutes | info |
| Container running a different image build | stays open 60 minutes | info |
| Possible memory leak (steady climb over 12 hours) | 10 MB/h and 20% growth | warning |
| Docker unreachable (after it worked before) | 3 check-ins in a row | critical |
| Docker slow to list containers | over 2000 ms, 3 check-ins in a row | warning |
| Collector can't keep up with its check interval | gathering takes over 80% of the interval, 3 check-ins in a row | warning |
| New listener reachable from other machines | stays open 60 minutes | warning |
| Firewall configuration changed | stays open 60 minutes | info |
| Firewall protection reduced (ufw turned off, rules removed, a file gone) | stays open 60 minutes | warning |
| No internet (only if the check is on) | 3 check-ins in a row | warning |
| DNS failing (only if the check is on) | 3 check-ins in a row | warning |
| Server stopped reporting | 3 missed check-ins | critical |
| Host rebooted | stays open 60 minutes | info (critical after an unclean shutdown) |
| OOM killer fired | stays open 60 minutes | warning (critical at 3 or more) |
| Kernel fault flags (machine check, bad page, oops, warning, soft lockup) | – | warning, clears on reboot |
| Clock not synchronised | 3 check-ins in a row | warning |
| Reboot required | – | info |

New listening ports and firewall changes are noticed the same way, by comparing with
what the previous check-in saw. The first report is a silent baseline, an unreadable
or empty report is treated as unknown (never as "everything closed"), and short-lived
programs using random high ports (loopback ones and all UDP ones) are ignored. A new
port bound only to the loopback address is recorded but doesn't open an incident; one
reachable from other machines does. Events are capped like the container ones.

Container exits, removals and image changes are noticed by comparing each check-in
with the previous one, matching containers **by name** (Compose recreates them with
new IDs). Nothing is reported on a server's first check-in or when Docker was
unreachable (an empty list is not proof that everything vanished), and churn is
capped so containers that come and go constantly can't flood the database. A leak is
only flagged when hourly memory averages rise along a straight line without a
restart; caches and warm-up can look similar, so treat it as a prompt to look.

Host rules (disk, inodes, memory, swap, load) open an incident only after
**3 check-ins in a row** breach the threshold, so a brief spike is ignored. An
open incident stays open while the latest value is still over the warning
threshold, and becomes critical only when the critical threshold is breached for
the same number of check-ins.

<diagram - Incident lifecycle for a host rule as a timeline of check-ins: value crosses the warning threshold → after 3 check-ins in a row a WARNING incident opens → stays over critical for 3 check-ins → escalates to CRITICAL → drops under warning → RESOLVED; show a single spike that opens nothing>

Admins can change every threshold, the number of check-ins in a row, how long
event incidents (reboots, OOM kills, container exits, removals and image changes)
stay open, the slow-Docker and slow-collection limits, the memory-leak window and sensitivity, and
**per-mount disk thresholds** under
**Admin → Alerts**. A per-mount override replaces the default disk thresholds for
that mount path on every server. Changes apply from each server's next check-in
and are recorded in the audit log.

<ui - Admin → Alerts: the Incident thresholds form with Warning / Critical fields per rule, Check-ins in a row, and the container restart loop row>

<ui - Active incidents section with a CRITICAL and a WARNING incident, each showing its server and start time>

Incidents are shown on the dashboard only; there are no push notifications yet.
(`ADMIN_ALERT_WEBHOOK_URL` is used by the assistant's `daily_checkup` only.)

## System health

Collectors report what the kernel exposes, without reading any logs:

| Check | Source | Notes |
|---|---|---|
| Reboots | `/proc/sys/kernel/random/boot_id` | A new boot ID between check-ins records a **Reboot** event. |
| OOM kills | `oom_kill` in `/proc/vmstat` | A rising counter records an **OOM kill** event. |
| Kernel faults | `/proc/sys/kernel/tainted` | Only fault bits count (machine check, bad page, oops, warning, soft lockup); proprietary or out-of-tree module flags are ignored. |
| Clock sync | `adjtimex` (read-only), else systemd-timesyncd's marker | Sync state only, not a measured offset. |
| Reboot required | `/run/reboot-required` | Debian and Ubuntu only; other distros show *unknown*. |

### Optional system helper

Process names, kernel error lines and crash-versus-clean shutdown detection need
the kernel log and the login records, which the collector deliberately can't
read. A separate container does:

```bash
docker compose --profile system-events up -d     # add -f docker-compose.server.yml on a server
```

It runs with a single capability (`CAP_SYSLOG`), no network and a read-only
filesystem, sees only `/var/log/wtmp` (read-only), and writes a sanitised JSON
summary that the collector reads from a shared volume. With it you also get:

- the OOM-killed process name on each **OOM kill** event,
- recent kernel error lines in the **System** row (informational, they don't open incidents),
- **Last shutdown: clean / unclean**, and a **critical** incident after an unclean reboot.

Without it those show as *unknown* and everything else keeps working. Read
[SECURITY.md](SECURITY.md) before enabling it; the helper is about 200 lines of
standard-library Python.

<ui - The System row opened on a server with the helper running: status chips on top, then a list with an OOM kill (process name) and kernel error lines>

## Accounts and roles

- The first account created is the **admin**. It needs the install's **setup
  token** (printed in the backend log at start, or `ADMIN_BOOTSTRAP_TOKEN`). Later
  accounts are regular users (and on the server file, registration closes after
  the first account).
- **Passwords must be at least 16 characters** (and at most 72 bytes). A few random
  words make a good passphrase. The rule applies to new accounts; existing
  accounts keep working with their current password.
- **Account page** (sidebar → Account), for every user: change your password (needs
  the current one; signs out your other devices), turn on **two-factor sign-in**,
  and review or end your sessions (**Sign out everywhere else**). See
  [Two-factor sign-in](#two-factor-sign-in).
- Everyone signed in can see all servers, containers and incidents.
- Only admins can add, approve, dismiss or delete servers, change check
  intervals, manage the enrollment key and open the **Admin** page.
- **Admin** page settings that are connected today: alert thresholds (including
  per-mount disk thresholds), how long metrics are kept (default 30 days; older
  check-in samples and system events are deleted hourly, but only after the check-ins have
  been summarised; the summaries behind the Statistics page are not deleted yet, and a
  retention setting for each level is planned), and turning
  chat with the assistant on or off. The other sections on the page show
  placeholder data and aren't saved yet.

### Two-factor sign-in

Two-factor uses a one-time code from any authenticator app (TOTP, the same standard
as Google Authenticator, Aegis or a password manager).

1. **Create a key once.** Secrets are stored encrypted, so the stack needs a key.
   Generate one and put it in `.env` as `SECRETS_KEY` (see `example.env`):

   ```bash
   docker compose run --rm --no-deps backend python -m src.backend.secrets_box
   ```

   Back this key up with your other secrets. **If you lose it, authenticator codes
   stop working** (recovery codes still do, and so does the reset command below).
   To rotate it, put the new key first and keep the old one after a comma.
2. **Turn it on** under Account → Two-factor sign-in. You get a setup key and an
   `otpauth://` link to paste into your authenticator app (no QR code yet), confirm
   with a code, and are shown **10 recovery codes once**. Each works one time.
3. **Signing in** then asks for the code after the password. A recovery code can be
   typed instead.

**Admins must use it by default.** *Admin → Users → Require two-factor for admins* is
**on**. An admin without two-factor can still sign in, but only to the Account page
until they finish setting it up, so nobody is locked out. A member's two-factor is
optional. The setting is only enforced when `SECRETS_KEY` is set; without it the
backend prints a warning at start and the policy does nothing.

**Locked out?** An admin who lost both the authenticator and the recovery codes can
reset an account from the host:

```bash
docker compose exec backend python src/backend/reset_2fa.py someone@example.com
```

It turns two-factor off for that account and signs it out everywhere. With the
policy on, the account is asked to set up two-factor again at its next sign-in.

## Configuration

### Stack (`.env` next to the Compose file)

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | *(required)* | PostgreSQL password. |
| `GEMINI_API_KEY` | empty | Enables the Homie chat assistant. |
| `AGENT_MODEL` | `models/gemini-3.5-flash` | Gemini model for the assistant. |
| `ADMIN_ALERT_WEBHOOK_URL` | empty | Receives JSON alerts for critical `daily_checkup` findings. |
| `SESSION_COOKIE_SECURE` | `false` (dev), `true` (server file) | Send the login cookie over HTTPS only. |
| `SESSION_TTL_SECONDS` | `86400` | Session lifetime. |
| `ALLOW_REGISTRATION` | `true` (dev), `false` (server file) | Let anyone create an account. When `false`, only the first account can be created. |
| `SECRETS_KEY` | empty | Key that encrypts two-factor secrets. Required for two-factor sign-in and for the "require two-factor for admins" policy to be enforced. Generate with `python -m src.backend.secrets_box`; comma-separated keys allow rotation. |
| `ADMIN_BOOTSTRAP_TOKEN` | generated at start | Setup token for the first account. Leave empty to have one generated and printed in the backend log until the first account exists. |
| `FRONTEND_BIND` | `0.0.0.0` (dev), `127.0.0.1` (server file) | Interface for the dashboard's plain-HTTP port. |
| `BACKEND_PORT` | `8060` | Development stack only: the host port (on `127.0.0.1`) where the backend is published for debugging. The server stack publishes no backend port. |
| `FRONTEND_PORT` | `5173` | Dashboard port on the host. |
| `DASHBOARD_DOMAIN` | *(required on a server)* | Domain of the dashboard, without `https://` (server file). |
| `TRAEFIK_ENTRYPOINT` / `TRAEFIK_CERT_RESOLVER` / `TRAEFIK_NETWORK` / `TRAEFIK_ROUTER` | `websecure` / `letsencrypt` / `traefik_default` / `homelab` | Your Traefik names (server file). |
| `CORS_ORIGINS` | `http://localhost:5173` (dev), `https://<DASHBOARD_DOMAIN>` (server file) | Comma-separated origins. Also the allow-list for the cross-site request check: a state-changing request from any other origin is refused unless its `Origin` matches the `Host` the dashboard was reached on. Add your dashboard URL if something in front rewrites `Host`. |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW_SECONDS` | `5` / `60` | Login attempts per IP and per email. |
| `REGISTER_RATE_LIMIT` / `REGISTER_RATE_WINDOW_SECONDS` | `3` / `3600` | Registrations per IP. |
| `COLLECTOR_BIND` / `COLLECTOR_PORT` | `127.0.0.1` / `8081` | Server file: address and port where the collector gate is published. Set the address to your VPN's. |
| `COLLECTOR_PUBLIC_URL` | empty | What **Connect a server** tells new machines to use, e.g. `http://10.8.0.1:8081/api`. |
| `COLLECTOR_IMAGE` / `DOCKER_PROXY_IMAGE` | empty | Digest-pinned images remote machines install. **Connect a server** needs both. |
| `JOIN_TOKEN_TTL_SECONDS` | `900` | How long a generated join token stays valid. |
| `COLLECTOR_ACCESS` | `open` (dev), `strict` (server file) | `strict` makes the backend answer collector endpoints only when they came through the collector gate. |
| `HOMELAB_ENROLL_KEY` | empty | Lets the stack's own collector skip approval. |
| `HOMELAB_SERVER_NAME` | host's hostname | Name shown for the stack's own machine. |
| `DOMAIN` | `localhost` | Domain for the optional Caddy profile. |
| `COMPOSE_FILE` | – | Set to `docker-compose.server.yml` on a server so plain `docker compose` uses it. |
| `LIVE_GRAPH_REFRESH_INTERVAL_SECONDS` | `3` | How often the dashboard refreshes (server file; applied when the frontend image is built). |
| `HOMELAB_CHECK_INTERVAL_SECONDS` | `60` | Initial check interval of the stack's own collector (server file); the dashboard setting overrides it. |

### Collector (environment of the collector container)

| Variable | Default | Description |
|---|---|---|
| `HOMELAB_API_URL` | `http://localhost:8060` | Dashboard API address, e.g. `https://dashboard.example.com/api`. |
| `HOMELAB_JOIN_TOKEN` | empty | Single-use token from **Connect a server**; required over the VPN listener. |
| `HOMELAB_ENROLL_KEY` | empty | Join immediately instead of waiting for approval. |
| `HOMELAB_SERVER_NAME` | host's `/etc/hostname` | Name shown on the dashboard. |
| `HOMELAB_CONNECTIVITY_TARGETS` | empty (check off) | Optional internet check: up to 5 comma-separated `host:port` targets (hostnames or IPv4), e.g. `1.1.1.1:443,9.9.9.9:443`. The collector opens a TCP connection and looks the name up; it sends nothing. Set on each collector; the dashboard cannot set it. |
| `HOMELAB_STATE_DIR` | `/var/lib/homelab-collector` | Where the token is saved (mount a volume here). |
| `HOMELAB_MACHINE_ID` | host's `/etc/machine-id` | Identity used to recognise reinstalled machines. |
| `HOMELAB_DASHBOARD_HOST` | empty | `true` marks this machine as the one running the dashboard (set in the Compose files for the stack's own collector; leave it unset elsewhere). |
| `HOMELAB_SERVER_ID` / `HOMELAB_COLLECTOR_TOKEN` | empty | Explicit credentials (legacy; overrides everything else). |
| `HOST_PROC` / `HOST_ROOT` | `/host/proc` and `/hostfs` if mounted, else `/proc` and `/` | Where the host's `/proc` and filesystem are. |
| `HOMELAB_DOCKER_HOST` | empty | Socket of the read-only Docker proxy, e.g. `/run/docker-proxy/docker.sock`. Preferred over `DOCKER_SOCKET_PATH`. |
| `DOCKER_SOCKET_PATH` | `/var/run/docker.sock` | Direct Docker socket, used only when `HOMELAB_DOCKER_HOST` is unset (not recommended). Without any Docker access only host metrics are sent. |
| `HOMELAB_HELPER_FILE` | `/helper/system.json` | Where the optional system helper's summary is read from. |

The system helper has `HOMELAB_HELPER_INTERVAL_SECONDS` (default `30`, minimum
`10`); the Docker proxy listens on `HOMELAB_DOCKER_PROXY_SOCKET` and forwards to
`DOCKER_SOCKET_PATH`. Both are set in their images and rarely need changing.

The check interval is set per server on the dashboard; collectors pick it up
from the backend's response to each report.

## Security

[SECURITY.md](SECURITY.md) has the full picture. In short:

- **Collector privileges.** In the Compose installs the collector has no Docker
  socket, drops all capabilities and has a read-only filesystem. Only the Docker
  proxy mounts the socket; it has no network, answers four read-only requests and
  strips environment variables, labels, mounts and error text from what it returns.
  Everything a collector reports about containers is validated again by the backend
  (types, ranges, lengths, counts) before it is stored.
- **Network checks are passive.** Listening ports are read from the host's `/proc`
  (protocol, port and scope only, no process names), the firewall check reads known
  configuration files and reports only a hash and a rule count (never the rules, and not
  the live ruleset, which would need `CAP_NET_ADMIN`), and the internet check is off
  unless you configure its targets on that collector. The collector still mounts `/`
  read-only; without capabilities it can't read other users' private files through it.
- **Collector endpoints are not public.** On the server stack the public listener
  returns 404 for register, poll, enroll and ingest; they exist only on the
  collector gate, bound to your VPN address.
- **No code from the dashboard.** Remote machines install signed, digest-pinned
  images. The dashboard no longer serves collector code.
- **Join tokens.** Adding a machine needs a single-use, expiring token minted by an
  admin, then an admin's click. Re-pairing a machine that matches an existing
  server needs explicit confirmation.
- **Fresh installs can't be claimed.** The first account needs a setup token.
- **Accounts.** Passwords must be at least 16 characters. Two-factor sign-in
  (authenticator app plus single-use recovery codes) is available to everyone and
  **required for admins by default**; secrets are encrypted with your `SECRETS_KEY`. Every logged-in account
  can see all monitoring data and use the assistant (your Gemini key) unless an
  admin turns chat off. Only admins can add, approve or remove servers. With
  `ALLOW_REGISTRATION=false` (the default in `docker-compose.server.yml`) only the
  first account can be created; keep it that way on a public domain.
- **Browser protections.** State-changing requests from another site are refused
  (this covers other apps on sibling subdomains), and the frontend sends a strict
  Content-Security-Policy, clickjacking protection and HSTS on the public listener.
- **Secrets at rest.** Collector tokens, join tokens, the setup token and
  enrollment keys are stored only as SHA-256 hashes; passwords are hashed with bcrypt.
- **Client IPs.** nginx accepts `X-Forwarded-For` only from private/Docker
  network addresses (e.g. Traefik), so rate limits see the real client IP and it
  can't be spoofed from outside.
- **Known limits.** The one-line `docker run` install is gone; machines you install
  with `collector-compose.yml` build locally. Anyone on your VPN can reach the
  collector gate (a token and an admin's click are still needed to join).
  `src/backend/seed_user.py` creates an admin straight in the database (from
  `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD`, or an interactive prompt), skipping the
  setup token; it is meant for people who already have database access.

### Homie assistant (monitoring-only)

The assistant has no code path for running shell commands. It may request
exactly one fixed, read-only check at a time: `server_health`,
`container_health`, or `daily_checkup`, implemented with Python system APIs and
Docker's Unix API socket, never the shell or Docker CLI.

The backend container does **not** mount `/var/run/docker.sock`. If you want
the assistant's container checks, point `DOCKER_SOCKET_PATH` at a hardened
Docker API proxy that permits only the required GET endpoints.

`daily_checkup` combines host and container checks. On a critical finding it
POSTs an alert only to `ADMIN_ALERT_WEBHOOK_URL`; without it, the result says
`not_configured` and no request is made. Schedule it from a trusted scheduler
(e.g. a systemd timer) rather than letting the agent create schedules.

## Development

### Backend without Docker

Python 3.12. The backend needs Redis for sessions and rate limits; SQLite works
in place of PostgreSQL.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
docker run -d --name homelab-dev-redis -p 6379:6379 redis:7-alpine   # if Redis isn't running
DATABASE_URL=sqlite:///./dev.db REDIS_URL=redis://localhost:6379/0 \
  uvicorn src.backend.main:app --reload --host 0.0.0.0 --port 8060
```

### Frontend dev server

Node 20 (`.nvmrc`). The Vite dev server proxies `/api` to `localhost:8060` (set `BACKEND_URL` to point it elsewhere),
matching the production nginx setup.

```bash
nvm install && nvm use
cd src/frontend
npm install
npm run dev -- --host 0.0.0.0
```

Vite prints the URL (5173, or the next free port if the Compose stack already
uses it). `./start_frontend.sh` does the same in one step.

Environment variables for the frontend build:

- `VITE_API_BASE_URL` (default `/api`): API base path or URL.
- `VITE_LIVE_GRAPH_REFRESH_INTERVAL_SECONDS` (default `3`, minimum `1`): how
  often the dashboard refreshes.

After changing frontend or collector code, rebuild the containers:
`docker compose up -d --build`. Otherwise the browser keeps getting the old bundle.

### Tests

```bash
source venv/bin/activate
pip install -r requirements-dev.txt   # the app's requirements plus pytest
pytest
```

No Docker, PostgreSQL or Redis is needed; the suite takes about a minute and a half. The chart
helpers have their own tests that need only Node 20 or newer: `cd src/frontend && node --test`. See
[TESTS.md](TESTS.md) for how the tests work, what they don't cover, and what every
test is for.

### Database schema

Tables are created on backend startup (`Base.metadata.create_all`), and new
columns are added with `ALTER TABLE ... IF NOT EXISTS` in the startup hook in
`src/backend/main.py`. Each check-in stores one row in `host_metrics` (which also
carries the boot ID, OOM counter, kernel flags, clock state and last shutdown), one
per filesystem in `mount_metrics` and one per container in `container_metrics` (which
also records the exit code, out-of-memory flag, finish time and image ID), all with the
same timestamp. `system_events` holds reboots, OOM kills, kernel messages, container
changes (appeared, removed, image changed) and network changes (ports opened or closed,
firewall changes); `listener_states` and `firewall_states` hold what was last seen, `join_tokens` the hashed single-use tokens, `recovery_codes` the hashed two-factor
recovery codes (the encrypted two-factor secret lives on the `users` row), and per-mount disk
thresholds live in `app_settings`. `docker/postgres/init/01_create_users.sql` initialises
the `users` table on a fresh volume.

**Summarised history.** A background job (it checks every minute and does nothing when it is up to date) turns the check-in
samples into `host_rollups`, `mount_rollups` and `container_rollups`: one row per server, mount or container
per hour, day, week (Monday to Sunday) and month, all in UTC, with the average and maximum of each metric.
Each tier is built from the finer one when its period ends, a period without check-ins gets no row, and the
purge never deletes samples that have not been summarised yet. Nothing displays these yet; they are the
foundation for the long-range graphs and statistics.

The metrics tables carry only the indexes their queries use: `time` (for the hourly purge) and
`(server_id, time)`, or `(server_id, container_id, time)` for containers. Older databases are
migrated at start: the new indexes are created and the redundant single-column ones are dropped,
which can be repeated safely. Creating an index briefly blocks writes to that table (a few
seconds for a table of millions of rows); on a very large install, create the indexes yourself
first with `CREATE INDEX CONCURRENTLY IF NOT EXISTS <name> ON <table> (<columns>)` using the names
in `src/backend/models.py`, and the start-up migration will skip them.

## Project layout

```text
compose.yml                 development stack (default for `docker compose`)
docker-compose.server.yml   server stack behind Traefik
collector-compose.yml       collector-only stack for monitored machines
SECURITY.md                 what each component can touch, and its limits
TESTS.md                    how to run the tests and what each one checks
requirements-dev.txt        app requirements plus pytest, for running the tests
pytest.ini                  test settings (lets plain `pytest` find the `src` package)
.github/workflows/
  images.yml                builds, signs and publishes the collector images on a version tag
docker/
  backend.Dockerfile        FastAPI backend (uvicorn)
  frontend.Dockerfile       Vite build served by nginx
  collector.Dockerfile      standalone collector image
  docker-proxy.Dockerfile   read-only Docker API proxy image
  system-helper.Dockerfile  optional kernel-log / wtmp helper image
  nginx.conf                development: serves the UI, proxies /api to the backend
  nginx.strict.conf         server stack: public listener, collector endpoints refused
  nginx.collector.conf      server stack: collector gate (VPN and internal listeners)
  Caddyfile                 optional HTTPS front door (dev compose, --profile https)
  postgres/init/            SQL run on a fresh PostgreSQL volume
src/
  backend/                  API: auth, monitoring, enrollment, rate limits, models
    alerts.py               alert engine: one incident per rule, auto-resolve
    rules.py                alert rules and default thresholds
    rollups.py              summarises check-ins into hourly/daily/weekly/monthly rollups
    history.py              range-aware history behind the Statistics page
    stats.py                fleet statistics: incidents over time, availability, busy-hours heatmap
    totp.py                 one-time codes and recovery codes (standard library only)
    secrets_box.py          encrypts stored secrets with SECRETS_KEY; also prints a new key
    reset_2fa.py            command-line two-factor reset for a locked-out account
  agent/
    collector.py            metrics collector (standard library only)
    docker_proxy.py         read-only Docker API proxy (standard library only)
    system_helper.py        optional kernel-log / wtmp helper (standard library only)
    health_checks.py        read-only checks available to the assistant
    service.py, prompts.py  Gemini assistant
  frontend/                 React + Tailwind dashboard (Vite); charts.jsx and chartMath.js are the
                            charts (distributions.jsx: columns, donut, bars, heatmap), StatisticsPage.jsx,
                            FleetStatistics.jsx, ServerInsights.jsx and ContainerStatistics.jsx the page
tests/                      pytest suite
```

## API overview

All paths are relative to `/api` when going through the frontend/nginx.

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /health` | – | Database and Redis status. |
| `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | session cookie | Accounts and sessions. |
| `POST /auth/login/2fa` | pending login token | Second step of sign-in: authenticator or recovery code. |
| `GET /auth/2fa/status`, `POST /auth/2fa/setup`, `POST /auth/2fa/enable` | session (also the limited setup session) | Two-factor status, start and confirm setup. |
| `POST /auth/2fa/disable`, `POST /auth/2fa/recovery-codes` | session + password + code | Turn two-factor off; replace the recovery codes. |
| `POST /auth/password` | session + current password | Change password; signs out other sessions. |
| `GET /auth/sessions`, `POST /auth/sessions/revoke-others`, `DELETE /auth/sessions/{id}` | session | List and end your own sessions. |
| `GET /auth/config` | – | Whether registration is open and whether the first account still needs a setup token. |
| `GET /monitoring/overview` | session | Servers (latest metrics, filesystems, containers from the latest check-in, open incident count), pending servers (admins), active incidents and disk thresholds. |
| `GET /monitoring/servers/{id}/history` | session | A server's metrics over time. With `?range=` (`1h`, `6h`, `24h`, `7d`, `30d`, `90d`, `1y`, `all`) it returns at most about 500 points from the right level of detail, each with average and maximum of the requested `metrics` (`cpu,ram,swap,load,disk,inodes,docker_ms,collect_ms`). Without `range`: CPU, memory and root disk samples (`?minutes=`, default 30, max 1440). |
| `GET /stats/fleet` | session | Incidents opened per bucket and severity, by kind, open now, mean time to resolve, and per-server availability for `?range=`. Cached for a minute. |
| `GET /stats/servers/{id}/heatmap` | session | Average `?metric=cpu\|ram` by hour of the day and weekday over the last 30 days, for `?offset=` minutes from UTC. |
| `GET /monitoring/servers/{id}/mounts/history`, `GET /monitoring/servers/{id}/containers/{container_id}/history` | session | The same for one filesystem (`?mount=/data`) or one container. |
| `GET /monitoring/servers/{id}/containers/{container_id}` | session | One container's latest stats and history (`?limit=`, max 500). |
| `POST /monitoring/servers` | admin | Add a server manually; returns its collector token. |
| `PATCH /monitoring/servers/{id}/interval` | admin | Set check interval (15–86400 s). |
| `DELETE /monitoring/servers/{id}` | admin | Remove a server and its data. |
| `POST /monitoring/pending-collectors/{id}/approve` / `…/dismiss` | admin | Add or dismiss a server asking to join. Approving one that matches an existing server needs `{"replace": true}`. |
| `POST /monitoring/join-tokens` | admin | Create the single-use join token for **Connect a server** (shown once). |
| `GET /monitoring/install-info` | admin | The collector address and images the panel uses. |
| `GET` / `POST` / `DELETE /monitoring/enrollment-key` | admin | Show status / create or rotate / revoke the enrollment key. |
| `GET` / `PUT /admin/settings` | admin | Metrics retention, chat on/off, require two-factor for admins, alert thresholds and per-mount disk thresholds. |
| `GET /agent/config` | session | Whether chat with the assistant is enabled. |
| `POST /monitoring/collectors/register` | join token (rate limited) | Collector asks to join; returns a poll secret and pairing code. |
| `POST /monitoring/collectors/register/poll` | poll secret | Collector checks approval; returns its token once approved. |
| `POST /monitoring/collectors/enroll` | enrollment key | Join without approval. |
| `POST /monitoring/metrics/ingest` | collector token | Collector reports host, container and system metrics. |

The four collector endpoints above exist only on the collector gate in the server
stack (the public listener answers 404); the development stack serves them on the
same address as everything else.
| `GET /conversations`, `POST /conversations`, `DELETE /conversations/{id}`, `POST /agent/chat` | session | Homie assistant chats. |
| `GET /logs`, `POST /logs`, `GET /dashboard/summary` | session | Audit log and summary counts. |

## Troubleshooting

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
| **Connect a server** says no collector images are configured | Set `COLLECTOR_IMAGE` and `DOCKER_PROXY_IMAGE` in `.env` (see [Publishing the collector images](#publishing-the-collector-images)) and rebuild. |
| The register page asks for a setup token | The install has no accounts yet. Use `docker compose logs backend \| grep "setup token"`, or `ADMIN_BOOTSTRAP_TOKEN`. |
| "Password must be at least 16 characters long" | New passwords need 16 or more characters (at most 72 bytes). A few random words work well. |
| "Cross-site request refused" (403) on a button or form | The request came from another origin. Open the dashboard at the address you normally use. If a proxy in front rewrites the `Host` header, add your dashboard URL to `CORS_ORIGINS`. |
| The **System** row shows *unknown* for last shutdown or has no kernel messages | The optional system helper isn't running. Start it with `docker compose --profile system-events up -d`. *Unknown* for reboot-required on non-Debian hosts is expected. |
| `Error response … cannot assign requested address` for the collector gate | `COLLECTOR_BIND` is set to an address that doesn't exist yet (VPN not up). Start the VPN first, or fix the address. |
| The **Network** row says *no config found* for the firewall | None of the known firewall files exist on that host (`/etc/ufw`, `/etc/nftables.conf`, `/etc/iptables/rules.v4`). The check reads configuration, not the live ruleset, so a host that configures its firewall some other way shows this. |
| The **Network** row says *not checked* for the internet | The check is opt-in. Set `HOMELAB_CONNECTIVITY_TARGETS` on that collector (for example `1.1.1.1:443`). |
| Listeners show *unknown* or the Network row is empty | The collector couldn't read the host's network tables (older collector, or a table with more than 500,000 sockets). An unreadable report never raises a "port closed" alert. |
| "Invalid code." although the code looks right | Codes depend on the clock: check the phone's time is automatic. A code also can't be used twice, so wait for the next one. Five wrong codes lock the code step for 5 minutes. |
| Two-factor setup says it is not available | `SECRETS_KEY` is missing or invalid in `.env`. Generate one (see [Two-factor sign-in](#two-factor-sign-in)) and restart the backend. The backend log shows a SECURITY WARNING while the key is missing. |
| Lost the authenticator and the recovery codes | Run `docker compose exec backend python src/backend/reset_2fa.py <email>` on the host, then sign in with the password and set it up again. |
| Authenticator codes stopped working after a restore or `.env` change | `SECRETS_KEY` changed or was lost, so the stored secret can't be decrypted. Restore the old key (rotation: `new,old`), or use a recovery code and the reset command. |
| Server marked offline | It missed 3 check-ins in a row. Check the machine, its collector log and its route to the dashboard. The incident resolves on the next report. |

## License

MIT; see [LICENSE](LICENSE).
