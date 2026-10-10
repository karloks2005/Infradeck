[← Documentation](README.md)

# Adding servers

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

## What the command does

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

## How registration works

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

## Networking checklist

- Every monitored machine must reach the collector gate (or the dashboard address
  in the development stack); the dashboard never connects to them.
- For machines outside your LAN, put them on a VPN (OpenVPN, WireGuard, Tailscale)
  and publish the gate on the VPN address. Don't expose plain HTTP to the
  internet: collector tokens travel over this connection.
- On a LAN, give the dashboard machine a fixed IP (e.g. a DHCP reservation).
