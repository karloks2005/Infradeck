# Security

Homelab Agent is meant to be self-hosted by people who care about the machines it watches.
This page says what each component can reach, so you can decide what to run.

## Components and what they can touch

| Component | Runs as | Reaches | Notes |
|---|---|---|---|
| Collector | container | host `/proc`, the host filesystem (read-only), the Docker proxy, the backend over HTTP | No Docker socket in the compose installs. |
| Docker proxy | container, uid 0, no capabilities | the Docker socket; a Unix socket shared only with the collector; no network | Allows three read-only requests. See below. |
| System helper (optional) | container, uid 0 with **one** capability | kernel log, `/var/log/wtmp` (read-only), one output volume | Off by default. Details below. |
| Collector gate (server stack) | nginx container, no capabilities | the backend; publishes one port on your VPN address | The only way to reach collector endpoints. See below. |
| Backend / frontend / database | containers | each other | Only the frontend port is published. |

## The optional system helper

Reading the kernel log (`dmesg`) needs a privilege, and crash-vs-clean-shutdown detection needs
the login records. The collector doesn't get either: a separate helper does the privileged reads
and the collector only receives its sanitized output.

Enable it with `docker compose --profile system-events up -d` (use the same `-f` file you use for
the collector). Without it, the dashboard still reports reboots, OOM kills (as a count), kernel
fault flags, clock sync and pending reboots from `/proc`; the rest shows as "unknown".

What it can and cannot do:

- **Capabilities:** all dropped except `CAP_SYSLOG`, the minimum for reading the kernel log when
  `kernel.dmesg_restrict=1` (Ubuntu's default). It runs as uid 0 inside the container because Docker
  does not grant capabilities to non-root users; with everything else dropped that is a small surface.
- **Network:** none (`network_mode: none`). It cannot send anything anywhere.
- **Filesystem:** read-only root. The only writable path is its output volume. The only host file it
  sees is `/var/log/wtmp`, read-only, and it reads only boot and shutdown records from it
  (not logins, users or addresses).
- **Reads, never writes, the kernel log:** it uses the non-destructive read-all call.
- **No code is fetched from the dashboard.** The helper is built from this repository. Review
  `src/agent/system_helper.py` (about 200 lines, standard library only) before enabling it.

How its output is treated as untrusted:

- Kernel log lines and `wtmp` content can be influenced by an attacker (a process can name itself
  anything). The helper matches fixed patterns, reduces text to printable characters, truncates it,
  and caps the number of events. Nothing is executed or put into a command.
- The collector opens the helper's file with `O_NOFOLLOW`, caps its size, requires it to be fresh and
  from the current boot, and forwards only the expected fields.
- The backend validates everything again (event kinds, key format, timestamps, lengths, counts).

## The Docker socket and the read-only proxy

A Docker socket mount is root on the host: the `:ro` flag only protects the socket file, not the
API behind it, and anything that can reach the API can start a privileged container. So in the
compose files the collector **does not mount the socket**. A separate `docker-proxy` container does:

- It answers exactly three requests: `GET /containers/json?all=1`, `GET /containers/<id>/json` and
  `GET /containers/<id>/stats?stream=false`. Everything else (other endpoints, other methods, extra
  query parameters, encoded or padded paths) gets a 403 and is logged. Matching is literal against the
  whole request target, with no decoding.
- It cuts the answers down to a fixed list of fields. For container inspect that is the ID, name,
  restart count, image name and ID, state, health, exit code, out-of-memory flag, start and finish
  times, and published ports. Container inspect output includes each container's environment
  variables (often secrets), labels, mounts and Docker's free-text error message; none of them leave
  the proxy. Adding a field means changing the proxy and its tests on purpose.
- It runs with all capabilities dropped, a read-only filesystem and `no-new-privileges`, with no
  network at all. It listens on a Unix socket (mode 0660) in a volume that only the collector shares.
- It is one standard-library file, `src/agent/docker_proxy.py`, built from this repository. Review it
  before trusting it: it is the one piece that holds the socket.

A compromised collector can therefore read what it already reports (container names, states,
stats) and nothing more through Docker.

## Protecting a fresh install

- **Setup token.** While an install has no accounts, creating the first one (which becomes the admin)
  needs a setup token. Without it, whoever finds a freshly deployed dashboard first (certificate
  transparency logs publish new HTTPS hostnames within minutes) could claim it. The backend generates
  a token at every start until the first account exists and prints it in its log
  (`docker compose logs backend | grep "setup token"`); only its hash is stored. To choose your own,
  set `ADMIN_BOOTSTRAP_TOKEN`. Once an account exists the token is deleted and nothing more is needed.
- **`src/backend/seed_user.py` creates an admin without the setup token**, so it is for people who
  already have access to the database (local development, recovering a lost login). It reads the
  email and password from `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` or prompts for them, never prints
  the password, and applies the same 16-character rule. It has no built-in account or password.
- **Registration stays off.** `ALLOW_REGISTRATION` defaults to `false` in `docker-compose.server.yml`,
  so after the first account nobody else can sign up.

## Passwords and cross-site requests

- **Passwords must be at least 16 characters** (and at most 72 bytes, bcrypt's limit; longer ones are
  refused instead of being silently truncated). A few random words make a good passphrase. The rule
  applies when an account is created; existing accounts keep working with their current password, so
  change any short ones. Failed sign-ins are rate limited per address and per email.
- **Cross-site requests are refused.** Session cookies are `SameSite=Lax`, which still lets a page on a
  sibling subdomain (every other app behind your reverse proxy counts as the same site) send
  state-changing requests with your cookie. The backend therefore rejects any `POST`, `PUT`, `PATCH` or
  `DELETE` whose `Origin` isn't this dashboard (same host and port it was reached on, or an origin in
  `CORS_ORIGINS`), including `Origin: null`, and any request the browser marks cross-site
  (`Sec-Fetch-Site`) when there's no `Origin`. Collectors and `curl` send neither header and
  authenticate with tokens, so they're unaffected. Reads aren't covered; they don't change anything,
  and CORS stops other sites from reading the answers.
  If something between the browser and nginx rewrites the `Host` header, add your dashboard's URL to
  `CORS_ORIGINS` (for example `https://dashboard.example.com`).

## Two-factor sign-in and sessions

- **TOTP and recovery codes.** Admins must use two-factor by default (*Admin → Users*). Codes are
  standard 6-digit TOTP (RFC 6238) and accepted one 30-second period either side. A code can be used
  once; replaying it, even inside its window, is refused. Ten recovery codes (80 bits each) are shown
  once, stored only as SHA-256 hashes, and each works a single time.
- **No session before the second step.** After a correct password the server returns only a short-lived
  (5 minute) single-use token. A session and cookie exist only after a correct code. Wrong codes get one
  generic answer and are counted per account (5 per 5 minutes, shared by every attempt, so starting the
  password step again doesn't reset it) and per address.
- **Secrets are encrypted.** The TOTP secret is stored encrypted (Fernet) with `SECRETS_KEY` from `.env`.
  Without a valid key nothing is ever stored in plain text, two-factor can't be set up, and the
  "require for admins" policy is *not enforced* (the backend prints a warning at start), so a missing
  key can't lock anyone out. Losing the key makes authenticator codes unusable; recovery codes and the
  reset command still work.
- **Enrolment can't lock you out.** An admin without two-factor, while the policy is on, gets a limited
  session that can reach only their profile, the two-factor setup and logout. Every other endpoint
  refuses it by default.
- **Sessions.** Session IDs are random and never shown to the browser's page; the Account page lists
  sessions under separate public IDs. Changing the password or turning two-factor on or off signs out
  the other sessions and issues a new session ID for the current one. A session ID isn't reused after a
  privilege change.
- **Reset.** `python src/backend/reset_2fa.py <email>`, run in the backend container, turns two-factor
  off for an account and signs it out everywhere. It needs access to the host, which is the point.

Known limits:

- Anyone who knows an account's password can make five wrong code guesses per five minutes, which locks
  that account's code step for the rest of the window (the same trade-off as the email lockout).
- Sessions that existed before this feature was installed aren't in the per-user list; they expire at
  the normal 24 hours (or on logout) and can't be ended from the Account page.
- TOTP only. There are no passkeys or hardware keys yet, and the setup shows a key and link, not a QR code.
- Recovery codes protect against a lost phone, not a lost `SECRETS_KEY` combined with a lost phone and
  lost recovery codes; the host-side reset covers that.

## HTTP security headers

The frontend's nginx sends these on every response: a Content-Security-Policy that allows only the
app's own scripts and connections (`script-src 'self'`; styles also allow inline style attributes,
which React uses), `frame-ancestors 'none'` and `X-Frame-Options: DENY` against clickjacking,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`,
`Cross-Origin-Opener-Policy: same-origin`, and no server version. The public configuration
(`docker/nginx.strict.conf`) also sends HSTS (180 days, no `includeSubDomains`, no preload).

If you add a feature that loads anything from another origin, extend the policy in the nginx
configuration on purpose; the browser will otherwise refuse it.

## Connecting remote servers

Internet-facing dashboards shouldn't accept unauthenticated collector traffic, so with
`docker-compose.server.yml` the collector endpoints (register, poll, enroll, ingest) don't exist on
the public side at all.

- **Two doors.** The public listener (behind Traefik or another reverse proxy) serves only the
  dashboard and its API and answers 404 for every collector endpoint. Collectors use the
  `collector-gate` container instead, which publishes one port on your **VPN address** (set
  `COLLECTOR_BIND`, e.g. your OpenVPN server's tun address) and is not attached to your reverse
  proxy's network, so other apps can't reach it. The backend refuses collector endpoints unless they
  arrived through the gate (`COLLECTOR_ACCESS=strict`), as a second layer.
- **Bind to the VPN address, not `0.0.0.0` plus a firewall rule.** Docker's published ports bypass
  `ufw` and similar firewalls, so a "deny from outside" rule does not protect them.
- **Join tokens.** The Connect a server button creates a random single-use token that expires
  (15 minutes by default) and embeds it in the install command. Over the VPN listener a collector
  can't even ask to join without a valid token, and you still click Add. A token is consumed when
  its server is approved. Tokens are stored only as SHA-256 hashes, and at most 20 can be open.
- **Re-pairing an existing machine needs explicit confirmation.** If a new request matches the
  machine ID of a server you already have, approving it replaces that server's credentials and signs
  its collector out. The dashboard warns and asks, and the API refuses without `replace: true`.
- **No code comes from the dashboard.** The dashboard no longer serves `collector.py`. Remote
  hosts install the collector and the Docker proxy from container images you publish (the "Publish
  images" workflow builds them for a version tag, signs them with cosign and prints their digests).
  Put the digests in `COLLECTOR_IMAGE` and `DOCKER_PROXY_IMAGE`; the panel won't generate a command
  without them. The GHCR packages must be public for hosts to pull them without credentials.
- **The VPN is yours to run.** Collector traffic over the VPN listener is plain HTTP inside the
  tunnel. Collectors authenticate with a per-server token that is stored hashed on the dashboard.

What this still depends on:

- The backend trusts the `X-Collector-Via` header nginx sets. That is safe only while nothing
  untrusted can reach the backend directly (port 8000 is not published in the server stack; don't
  add other containers to its network).
- Anyone on your VPN can reach the gate. With a valid join token they can request to join, and an
  admin still has to approve them; ingest additionally needs a server's token.
- Rotating a collector token means re-adding that server.

## Container data is untrusted

A compromised collector controls everything it reports about containers, so the backend never trusts
it: each record is reduced to known fields of the right type (text made printable and capped, numbers
range-checked, timestamps parsed strictly, image IDs matched against `sha256:` plus 64 hex characters),
and anything unexpected becomes "unknown", which never raises an incident. At most 500 containers are
accepted per check-in, names are capped at 128 characters so they can't break incident keys, and
container changes (appeared, removed, image changed) are capped per check-in and per hour with a single
notice, so churn can't flood the database. The hourly memory-trend analysis is bounded too (100
containers and 300,000 samples per server). Container changes are shown in their own list so a burst of
them can't push reboots or OOM kills out of the System row.

What the collector deliberately does not ask Docker for: `/info`, `/events`, logs, images, exec or any
write. Checking registries for newer images would need outbound network access and is tracked
separately (#38) as an opt-in, isolated component rather than part of the collector or proxy.

## Network checks

The collector's network checks are passive or opt-in, and add no privileges:

- **Listening ports** are read from the host's own network tables (`/proc/1/net`, the host's view; a
  container's own `/proc/net` would show only the container's sockets). Only protocol, port and scope
  (loopback, all interfaces, one address) are collected. Process names are not, because matching sockets
  to other users' processes needs privileges the collector doesn't have. Short-lived programs using
  random high ports (loopback ones and all UDP ones) are ignored.
- **Firewall configuration** is read from a fixed list of files (`/etc/ufw/*.rules`, `ufw.conf`,
  `nftables.conf`, `iptables/rules.v4` and `rules.v6`), never following symlinks and never reading more than
  1 MB. Only a short hash and a rule count leave the host, because the rules themselves reveal your
  network layout. This sees configuration, not the live ruleset: reading that needs `CAP_NET_ADMIN`,
  which can also change the firewall, so the collector doesn't have it.
- **The internet check is off by default.** When enabled it only opens a TCP connection and looks the
  name up, sending nothing. Its targets come from the collector's own environment
  (`HOMELAB_CONNECTIVITY_TARGETS`), never from the dashboard, so a compromised dashboard can't point
  probes at anything. Targets are limited to five hostnames or IPv4 addresses, and a probe cannot delay
  a check-in beyond a fixed deadline.
- **Unreadable never means closed.** If any network table can't be read, or is too large to read
  completely, the listener report is unknown rather than partial, so it can't make ports look closed.
  Reports are validated again by the backend and capped, and events are capped per check-in and per hour.
- **Visibility.** Like all monitoring data, the list of listening ports and firewall state is visible to
  every signed-in account. Keep registration closed on a public dashboard.

## Other exposure to be aware of

- The collector mounts the host filesystem read-only at `/hostfs` to read disk usage and a few
  files such as `/etc/os-release`.
- The collector runs with all capabilities dropped, a read-only filesystem and `no-new-privileges`.
  Without `CAP_DAC_OVERRIDE`, even as root it can't read other users' private files (SSH keys,
  `.env`) through the `/hostfs` mount; only files readable by "other" or owned by root are visible.
- The Docker proxy is reached through a Unix socket in a volume shared only with the collector. It
  has no network at all, so there is no port to attack and nothing to authenticate. Don't mount that
  volume into other containers.
- The system helper and the Docker proxy are built from this repository or from signed images on
  purpose and are never fetched from the dashboard, because they hold privileges.

## Reporting a vulnerability

Please open a private security advisory on the repository, or contact the maintainer directly,
rather than filing a public issue.
