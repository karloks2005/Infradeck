[← Documentation](README.md)

# Configuration

## Stack (`.env` next to the Compose file)

Every setting below is listed, with no values, in [`example.env`](../example.env): copy it to `.env` and fill in what you need. An empty value means the default shown here.

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | *(required)* | PostgreSQL password. |
| `POSTGRES_DB` / `POSTGRES_USER` | `infradeck` / `infradeck` | Name of the database and its user. Installs from before the rename can keep `homelab` (see [SETUP_DATABASES.md](../SETUP_DATABASES.md)). |
| `REDIS_PASSWORD` | *(required)* | Redis password. Redis holds the login sessions, so nothing that can merely reach it may use it. Use letters and digits only (`openssl rand -hex 24`) because it is put into a URL. |
| `OPENAI_API_KEY` | empty | OpenAI API key for the incident agent: its diagnoses and the chat. Empty: both are off, and incidents keep the rule-based diagnosis. **Admin → Incident agent → Test connection** checks it. |
| `AGENT_MODEL` | `gpt-5-mini` | OpenAI model for the agent. It must support Structured Outputs. |
| `ADMIN_ALERT_WEBHOOK_URL` | empty | Receives JSON alerts for critical `daily_checkup` findings. |
| `SESSION_COOKIE_SECURE` | `false` (dev), `true` (server file) | Send the login cookie over HTTPS only. |
| `SESSION_TTL_SECONDS` | `86400` | Session lifetime without *Remember me*. |
| `SESSION_COOKIE_NAME` | `homie_session` | Name of the sign-in cookie. Change it only if it clashes with another app on the same domain. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_STARTTLS` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | empty / `587` / `true` / empty / empty / empty | Email for incident notifications. Email is off unless `SMTP_HOST` and `SMTP_FROM` are set. |
| `ALLOW_REGISTRATION` | `false` | Let anyone create an account. When `false` (the default), only the first account can be created. Turning it on prints a security warning at every start. |
| `MAX_SERVERS` | `200` | The most servers the install holds, however they are added (enrollment key, approval or by hand). |
| `INGEST_MIN_SPACING_RATIO` | `0.25` | A check-in must arrive at least this share of the server's check interval (and never sooner than 5 seconds) after the previous one, or it is refused with 429. `0` switches the limit off. |
| `ENROLL_RATE_LIMIT` | `30` | Enrollments with the enrollment key allowed per address per hour (twice that per key). Raise it if many collectors reach the dashboard from one address (for example behind NAT). |
| `ENABLE_API_DOCS` | `false` | Serves the interactive API documentation at `/api/docs`. Leave off on a public install. |
| `TRUSTED_PROXIES` | empty | The address or network of the reverse proxy in front of the dashboard, comma-separated, so rate limits count each client by its real address. Only these may set `X-Forwarded-For`. Empty trusts nobody: behind a proxy all clients then share its address. See `example.env` for how to find the value. |
| `SECRETS_KEY` | empty | Key that encrypts two-factor secrets. Required for two-factor sign-in and for the "require two-factor for admins" policy to be enforced. Generate with `python -m src.backend.secrets_box`; comma-separated keys allow rotation. |
| `ADMIN_BOOTSTRAP_TOKEN` | generated at start | Setup token for the first account. Leave empty to have one generated and printed in the backend log until the first account exists. |
| `FRONTEND_BIND` | `0.0.0.0` (dev), `127.0.0.1` (server file) | Interface for the dashboard's plain-HTTP port. |
| `BACKEND_PORT` | `8060` | Development stack only: the host port (on `127.0.0.1`) where the backend is published for debugging. The server stack publishes no backend port. |
| `FRONTEND_PORT` | `5173` | Dashboard port on the host. |
| `DASHBOARD_DOMAIN` | *(required on a server)* | Domain of the dashboard, without `https://` (server file). |
| `TRAEFIK_ENTRYPOINT` / `TRAEFIK_CERT_RESOLVER` / `TRAEFIK_NETWORK` / `TRAEFIK_ROUTER` | `websecure` / `letsencrypt` / `traefik_default` / `infradeck` | Your Traefik names (`deploy/traefik.yml`). |
| `CORS_ORIGINS` | `http://localhost:5173` (dev), `https://<DASHBOARD_DOMAIN>` (server file) | Comma-separated origins. Also the allow-list for the cross-site request check: a state-changing request from any other origin is refused unless its `Origin` matches the `Host` the dashboard was reached on. Add your dashboard URL if something in front rewrites `Host`. |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW_SECONDS` | `5` / `60` | Login attempts per IP and per email. |
| `REGISTER_RATE_LIMIT` / `REGISTER_RATE_WINDOW_SECONDS` | `3` / `3600` | Registrations per IP. |
| `COLLECTOR_BIND` / `COLLECTOR_PORT` | `127.0.0.1` / `8081` | Server file: address and port where the collector gate is published. Set the address to your VPN's. |
| `COLLECTOR_PUBLIC_URL` | empty | What **Connect a server** tells new machines to use, e.g. `http://10.8.0.1:8081/api`. |
| `COLLECTOR_IMAGE` / `DOCKER_PROXY_IMAGE` | empty | Digest-pinned images remote machines install. **Connect a server** needs both. |
| `JOIN_TOKEN_TTL_SECONDS` | `900` | How long a generated join token stays valid. |
| `COLLECTOR_ACCESS` | `open` (dev), `strict` (server file) | `strict` makes the backend answer collector endpoints only when they came through the collector gate. |
| `INFRADECK_ENROLL_KEY` | empty | Lets the stack's own collector skip approval. |
| `INFRADECK_SERVER_NAME` | host's hostname | Name shown for the stack's own machine. |
| `DOMAIN` | `localhost` | Domain for the optional Caddy profile. |
| `COMPOSE_FILE` | – | On a server: `docker-compose.server.yml`, with Traefik `docker-compose.server.yml:deploy/traefik.yml`, so plain `docker compose` uses them. |
| `LIVE_GRAPH_REFRESH_INTERVAL_SECONDS` | `3` | How often the dashboard refreshes (server file; applied when the frontend image is built). |
| `INFRADECK_CHECK_INTERVAL_SECONDS` | `60` | Initial check interval of the stack's own collector (server file); the dashboard setting overrides it. |

## Collector (environment of the collector container)

| Variable | Default | Description |
|---|---|---|
| `INFRADECK_API_URL` | `http://localhost:8060` | Dashboard API address, e.g. `https://dashboard.example.com/api`. |
| `INFRADECK_JOIN_TOKEN` | empty | Single-use token from **Connect a server**; required over the VPN listener. |
| `INFRADECK_ENROLL_KEY` | empty | Join immediately instead of waiting for approval. |
| `INFRADECK_SERVER_NAME` | host's `/etc/hostname` | Name shown on the dashboard. |
| `INFRADECK_CONNECTIVITY_TARGETS` | empty (check off) | Optional internet check: up to 5 comma-separated `host:port` targets (hostnames or IPv4), e.g. `1.1.1.1:443,9.9.9.9:443`. The collector opens a TCP connection and looks the name up; it sends nothing. Set on each collector; the dashboard cannot set it. |
| `INFRADECK_STATE_DIR` | `/var/lib/infradeck-collector` | Where the token is saved (mount a volume here). |
| `INFRADECK_MACHINE_ID` | host's `/etc/machine-id` | Identity used to recognise reinstalled machines. |
| `INFRADECK_DASHBOARD_HOST` | empty | `true` marks this machine as the one running the dashboard (set in the Compose files for the stack's own collector; leave it unset elsewhere). |
| `INFRADECK_SERVER_ID` / `INFRADECK_COLLECTOR_TOKEN` | empty | Explicit credentials (legacy; overrides everything else). |
| `HOST_PROC` / `HOST_ROOT` | `/host/proc` and `/hostfs` if mounted, else `/proc` and `/` | Where the host's `/proc` and filesystem are. |
| `INFRADECK_DOCKER_HOST` | empty | Socket of the read-only Docker proxy, e.g. `/run/docker-proxy/docker.sock`. Preferred over `DOCKER_SOCKET_PATH`. |
| `DOCKER_SOCKET_PATH` | `/var/run/docker.sock` | Direct Docker socket, used only when `INFRADECK_DOCKER_HOST` is unset (not recommended). Without any Docker access only host metrics are sent. |
| `INFRADECK_HELPER_FILE` | `/helper/system.json` | Where the optional system helper's summary is read from. |

The system helper has `INFRADECK_HELPER_INTERVAL_SECONDS` (default `30`, minimum
`10`); the Docker proxy listens on `INFRADECK_DOCKER_PROXY_SOCKET` and forwards to
`DOCKER_SOCKET_PATH`. Both are set in their images and rarely need changing.

The check interval is set per server on the dashboard; collectors pick it up
from the backend's response to each report.

## Frontend build variables

Environment variables for the frontend build (set when the image is built):

- `VITE_API_BASE_URL` (default `/api`): API base path or URL.
- `VITE_LIVE_GRAPH_REFRESH_INTERVAL_SECONDS` (default `3`, minimum `1`): how
  often the dashboard refreshes.
