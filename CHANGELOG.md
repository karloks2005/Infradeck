# Changelog

All notable changes to Infradeck are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [1.0.0] - Unreleased

The first public release. Earlier 0.x versions were private pre-releases.

### What's in it

- **Hosts and containers.** CPU, memory, load, swap, network and uptime for every server, disk and inode usage
  for every mounted filesystem, and per-container CPU, memory, I/O, ports, health, exit codes and history.
- **System events.** Reboots, OOM kills, kernel faults, clock sync, pending reboots and package updates. With
  the optional system helper, also clean or unclean shutdowns, which process the OOM killer stopped and who
  listens on each port.
- **Network.** Listening ports (and which ones other machines can reach), firewall changes and an optional
  internet check.
- **Statistics for any range**, from the last hour to all time, per server, per container and for the whole
  fleet, kept fast by hourly, daily, weekly and monthly rollups. Includes the busiest container over time,
  CPU spikes and a "disk full in about N days" estimate.
- **Availability checks** run from the servers you pick: HTTP(S), a keyword, a value in a JSON reply, a TCP
  port, TLS certificate expiry, a DNS record and ping. They have retries, an "all fail" or "any fails" rule,
  upside-down mode, a Test button, and uptime history for 24 hours, 30 days and a year.
- **Incidents.** One incident per problem, opened after a sustained breach and closed when it clears. Admins
  can edit thresholds and switch any rule off; incidents can be acknowledged, muted and resolved.
- **Notifications** to ntfy, Discord, any webhook or email, with per-severity routing and quiet hours.
- **Incident agent (optional).** With an Anthropic, OpenAI or Gemini API key, it diagnoses warning and
  critical incidents from masked evidence, looks things up with read-only tools within a budget, cites the
  data behind each claim, proposes a fix and watches whether it holds. You can also chat about any incident.
  It never runs commands. Monthly budget, per-model prices and who may chat are set in Admin.
- **Accounts.** An admin and read-only users, invitations, two-factor sign-in (required for admins), sessions
  you can review and end, and read-only or write API tokens.
- **Adding servers.** One command and one click: a join token, then approval on the dashboard. Collector
  images are signed with cosign and pinned by digest.

### Security

- Collectors never get the Docker socket. A small proxy allows only a few read requests and strips what the
  collector doesn't need.
- With `docker-compose.server.yml`, collector endpoints exist only on a separate gate on your VPN address, and
  the backend refuses them anywhere else (`COLLECTOR_ACCESS=strict`, now also the backend's default).
- The login cookie is `Secure` by default (`SESSION_COOKIE_SECURE=true`). Sessions are stored hashed, and
  secrets in the database (two-factor keys, the agent's API key, notification targets) are encrypted.
- Turning the agent on, choosing its provider and key, and letting a user chat with it need the password and a
  two-factor code.
- Everything sent to the agent's model provider is masked first, including chat messages.

See [SECURITY.md](SECURITY.md) for the full picture.

### Upgrading from a 0.x pre-release

Back up the database first, then follow [Updating](docs/deployment.md#updating). Changes since 0.10.0
that may need your attention:

- **License.** Infradeck is now licensed under the AGPL-3.0 (it was MIT).
- **Traefik labels** moved to `deploy/traefik.yml`. Set `COMPOSE_FILE=docker-compose.server.yml:deploy/traefik.yml`
  in `.env` before updating, or Traefik stops serving the dashboard.
- **The frontend listens on port 8080** inside its container and runs without root. A reverse proxy you set up
  yourself to reach `frontend:80` must use `frontend:8080`.
- **`TRUSTED_PROXIES`.** `X-Forwarded-For` is trusted only from the addresses you list there. Set it to your
  reverse proxy, or every client shares the proxy's address for rate limits.
- **The incident agent's provider, model and API key** are chosen in Admin → Incident agent. `OPENAI_API_KEY`
  and `AGENT_MODEL` in `.env` are no longer read: enter the key there again, then remove them from `.env`.
- **Backend defaults.** `SESSION_COOKIE_SECURE` now defaults to `true` and `COLLECTOR_ACCESS` to `strict`. The
  compose files set what they need; only a backend run some other way over plain HTTP must set them itself.
- **Metrics ingest** accepts only a server's collector token, not a signed-in session.
- **`GET /dashboard/summary`** was removed.
- **Collectors.** Update them with a freshly generated command to get hard time limits on checks and the refusal
  to follow a redirect from the dashboard's address.

[1.0.0]: https://github.com/karloks2005/infradeck/releases/tag/v1.0.0
