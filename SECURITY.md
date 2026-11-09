# Security

Infradeck is meant to be self-hosted by people who care about the machines it watches.
This page says what each component can reach, so you can decide what to run.

## Components and what they can touch

| Component | Runs as | Reaches | Notes |
|---|---|---|---|
| Collector | container | host `/proc`, the host filesystem (read-only), the Docker proxy, the backend over HTTP, and the targets of availability checks an admin defines | No Docker socket in the compose installs. All capabilities dropped; one namespaced `net.ipv4.ping_group_range` sysctl lets ping checks use an unprivileged ICMP socket (not a capability, no raw sockets). |
| Docker proxy | container, uid 0, no capabilities | the Docker socket; a Unix socket shared only with the collector; no network | Allows six read-only requests. See below. |
| System helper (optional) | container, uid 0 with **one** capability | kernel log, `/var/log/wtmp` (read-only), one output volume | Off by default. Details below. |
| Agent worker | container, no capabilities, read-only filesystem | PostgreSQL, Redis, the model provider on the internet | Runs the agent's jobs from a queue in Redis; no published port. |
| Collector gate (server stack) | nginx container, no capabilities | the backend; publishes one port on your VPN address | The only way to reach collector endpoints. See below. |
| Backend / frontend / database / Redis | containers | each other, over separate networks | Only the frontend port is published. Redis and PostgreSQL are on a network with no route out and are reachable only by the backend. Redis needs a password. |

## What the sign-in page shows before anyone signs in

The sign-in page says which install it belongs to, so `GET /auth/config` answers without a session.
Anyone who can reach the dashboard can read: the install's name (by default "<first admin's first
name>'s homelab"; change it with `instance_name` in the admin settings), how many servers it watches,
the Infradeck version, and whether registration is open. If the dashboard faces the internet, that is
what a stranger learns; keep it behind a VPN if that is too much.

"Keep me signed in for 30 days" makes that one session, and its cookie, last 30 days instead of
`SESSION_TTL_SECONDS` (a day by default). The choice is made at the password step and kept on the
server, so the two-factor step cannot lengthen a session. The limited session an admin gets before
setting up two-factor is always 30 minutes. Long sessions are listed with the rest under Account and
end with "sign out everywhere".

## API tokens, notification channels and invitations

**API tokens** (Account › API tokens) let a script read what a signed-in user can read.

- A token is shown once and stored only as a SHA-256 hash. It is sent as `Authorization: Bearer idk_…`.
- A *read* token may only `GET`. A *write* token may also acknowledge, resolve, mute and unmute
  incidents, and only an admin can create one. If the owner's role or account changes, the token follows.
- No token, whatever its scope, reaches `/account`, `/auth`, `/admin`, the chat or anything that
  changes servers. A leaked token can't create another token, change a password, read the pending
  servers' pairing codes or spend the model provider's quota.
- Tokens can expire; revoking one works at once.

**Notification channels** (Account › Notifications) make the backend connect to an address a user
typed, so that address is treated as hostile:

- Only `http` and `https`, no user name or password in the address, no redirects followed, a 5 second
  timeout, and the answer is never shown to the user, only its status code.
- Every address the name resolves to is checked, when the channel is saved and again each time
  something is sent. Loopback, link-local (which includes cloud metadata addresses), multicast and
  reserved ranges are always refused. The connection then goes to the exact address that was checked,
  not to the name again, so a DNS answer that changes between the check and the connection (DNS
  rebinding) can't send it somewhere else; no proxy from the environment is used either.
- Private LAN ranges are allowed **only for admins**, because a homelab's ntfy usually lives there.
  That means an admin can make the backend send a small POST to any host on the networks the backend
  container can reach, including the stack's own services. Admins can already do far more; other
  users can only reach public addresses.
- Addresses are stored encrypted with `SECRETS_KEY` and shown afterwards only as the host and the last
  few characters. Without `SECRETS_KEY`, channels can't be saved at all.
- Email is sent only to the account's own address, and only if `SMTP_HOST` and `SMTP_FROM` are set.
- What is sent: the incident's title, its description, the server's name and the incident number.
  If those must not leave your network, use a channel inside it.

**Invitations** (Admin › Users) are single-use links for one email address that work while registration
is closed. Infradeck sends no email, so the admin passes the link on; whoever holds it and knows the
address can register, so send it over a channel you trust. It expires after 7 days and is stored hashed.

**Incident actions** (acknowledge, resolve, mute) are for admins only and each is written to the audit
log. Muting hides an incident from the Overview and stops notifications for it; it does not stop the
rule. An admin can also switch a rule off entirely in Admin › Alert rules, which closes its incidents.

**The chat about an incident** sends your question together with that incident's title, description
and the facts Infradeck worked out (signals and recorded events, which contain container and host
names) to the model provider. Before it leaves, the whole message is masked for secrets like the
agent's evidence pack (below; `src/agent/masking.py`). Turn Chat off in Admin › Incident agent if that
must not happen.

**The agent's diagnosis** (when an API key is saved in Admin › Incident agent) sends an evidence pack to the model provider: the
incident, the server's name, OS, cores and memory, metrics around the incident, recorded events, image changes,
the containers' names, images and figures, and similar incidents before it. Before it leaves, every text in it is
masked for secrets (tokens, keys, passwords, credentials in URLs, private keys; `src/agent/masking.py`), and the
model is told the pack is untrusted data. Its answer is checked before anyone sees it: claims that cite no fact
from the pack are dropped, and fix commands that could destroy data or open the machine up are flagged and never
marked recommended (`src/agent/safety.py`). So is a command with a line break or hidden characters, which could
paste as something other than what the page shows; the page shows every command exactly as it is copied. Any
other command that removes something (`rm`, `prune`, `-delete`, `DROP` …) is offered with a note but never marked
recommended either, because a list of dangerous patterns is never complete. Nothing the model writes is run. Requests to OpenAI are sent with
`store: false`, so OpenAI keeps no copy of the conversation for later retrieval (its own abuse-monitoring retention
still applies). Turn off "Diagnose new incidents
automatically" in Admin › Incident agent to send nothing unless someone asks; without a key nothing is sent
and incidents keep the rule-based diagnosis, which is worked out locally.

**The agent's tools** (`src/agent/tools/`) let a diagnosis or a chat message look up more of the same stored data: metrics,
containers, filesystems, events, earlier incidents, availability results, listening ports and the firewall, from
the last 90 days. The list is closed and every call is checked against the tool's schema before anything is read.
No tool writes, contacts a collector or runs a command. Results are bounded and masked like the evidence pack; a
check's target is sent without its credentials and query string; IP addresses, users, the audit log and enrolment
are never returned, so a tool answers everyone the same and shows nothing the dashboard doesn't. The model may make
a set number of calls per answer (Admin › Incident agent › Look-ups, 8 per diagnosis and 4 per chat message by
default, 0 for none); a call past it isn't run and the model then has to answer (`src/agent/investigate.py`). Every
call, run or refused, is written to the audit log with its masked arguments, and users can't forge or delete these
entries. The page shows each look-up only as a line of facts Infradeck writes from the result, never the raw result,
next to the reason the model gave for the call (masked like everything else it writes); see
[How the agent investigates](docs/agent-investigation.md).

**Who sees the agent.** With "Show diagnoses to users" off, the backend leaves the agent's report out of every
incident, Overview line and notification for users who aren't admins. Chat access is "Admins only" by default; with
"Per user", only users an admin switched on in Admin › Users may chat, and the backend answers 403 to everyone
else. **The agent resolving incidents**: it only resolves an incident when the condition from its own diagnosis
holds in fresh data (a sample from the last 5 minutes), and the rule stays quiet only for its event window, ending
at once if the problem comes back. A wrong condition can close an incident early, but not hide a new one for long.

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
  `src/collector/system_helper.py` (about 200 lines, standard library only) before enabling it.

How its output is treated as untrusted:

- Kernel log lines and `wtmp` content can be influenced by an attacker (a process can name itself
  anything). The helper matches fixed patterns, reduces text to printable characters, truncates it,
  and caps the number of events. Nothing is executed or put into a command.
- The collector opens the helper's file with `O_NOFOLLOW`, caps its size, requires it to be fresh and
  from the current boot, and forwards only the expected fields.
- The backend validates everything again (event kinds, key format, timestamps, lengths, counts).

## The optional port-owners helper

The Network tab can name the process behind each listening port (`sshd`, `mariadbd`), but only if
something looks into other users' processes. That is a much bigger privilege than anything else here,
so it is a **separate container, off by default**, and enabling the kernel-log helper above never
grants it. Enable it with `docker compose --profile port-owners up -d`.

Be clear about what you are granting:

- **The host's `/proc`, `CAP_SYS_PTRACE`, `CAP_DAC_READ_SEARCH` and no AppArmor confinement.** All four
  are needed to read which sockets a process holds, and together they are enough to read any file on
  the host (through `/proc/<pid>/root`) and the memory of any process. Treat this container as able
  to read everything on the machine. If that is not acceptable, leave it off: ports published by
  containers are still named, and the rest show as "not published by a container".
- **No network, read-only filesystem, no new privileges.** It cannot send anything anywhere; its only
  way out is the file it writes to the volume it shares with the collector.
- **What it actually reads:** the host's socket tables, the link target of each process's file
  descriptors (never the files behind them) and each matching process's short name. No arguments,
  paths, users or environment.
- **What leaves it:** protocol, port and process name, at most 512 entries. A process can name itself
  anything, so the name is reduced to printable characters and 64 characters; the collector reads the
  file with `O_NOFOLLOW`, a size cap and a freshness check, and the backend validates it again.
- It is the same single file as the system helper (`src/collector/system_helper.py`), in a different mode.

## The Docker socket and the read-only proxy

A Docker socket mount is root on the host: the `:ro` flag only protects the socket file, not the
API behind it, and anything that can reach the API can start a privileged container. So in the
compose files the collector **does not mount the socket**. A separate `docker-proxy` container does:

- It answers exactly six requests: `GET /containers/json?all=1`, `GET /containers/<id>/json`,
  `GET /containers/<id>/stats?stream=false`, the same stats request with `&one-shot=true` (which makes
  Docker answer at once instead of waiting a second for a second CPU reading; the collector works out CPU %
  from the change since its previous check-in) `GET /version`, of which only the daemon's version numbers pass, and `GET /system/df`, which is cut
  down to six numbers (how much disk images and the build cache take, and how much of that nothing
  uses; no image, volume or container names). Everything else (other endpoints, other methods, extra
  query parameters, encoded or padded paths) gets a 403 and is logged. Matching is literal against the
  whole request target, with no decoding.
- It cuts the answers down to a fixed list of fields. For container inspect that is the ID, name,
  restart count, image name and ID, state, health, exit code, out-of-memory flag, start and finish
  times, published ports, one label and where its mounts come from. Container inspect output also
  includes each container's environment variables (often secrets), every other label and Docker's
  free-text error message; none of them leave the proxy. Adding a field means changing the proxy and
  its tests on purpose.
- The one label that passes is `com.docker.compose.project`, so the dashboard can group containers by
  compose project. Of a mount, only its type, its source (a host path or a volume name) and its
  destination pass: never its contents, options or driver settings. That means **host paths that
  containers mount, and volume names, are visible to everyone who can sign in**, like container names
  and images already are. If a path itself is sensitive, don't put it in a container's mounts.
- It runs with all capabilities dropped, a read-only filesystem and `no-new-privileges`, with no
  network at all. It listens on a Unix socket (mode 0660) in a volume that only the collector shares.
- It is one standard-library file, `src/collector/docker_proxy.py`, built from this repository. Review it
  before trusting it: it is the one piece that holds the socket.

A compromised collector can therefore read what it already reports (container names, states,
stats, compose project, mount sources) and nothing more through Docker.

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
- **Registration stays off.** `ALLOW_REGISTRATION` defaults to `false` everywhere (in the backend and in
  both compose files), so after the first account nobody else can sign up. If you turn it on, the backend
  prints a SECURITY WARNING at every start, because anyone who can reach the dashboard can then create an
  account and read every server, container and incident.

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
- **Every endpoint requires a session by default.** A single app-wide dependency runs before every handler and
  rejects the request with 401 unless the path is on a short, explicit exempt list — the sign-in and registration
  steps, the token-authenticated collector endpoints, and (when enabled) the API docs. Each handler still makes its
  own role and ownership checks; this baseline just means a new endpoint that forgets its check **fails closed**
  (unreachable) instead of silently becoming public. A test (`tests/security/test_endpoints_require_auth.py`) walks
  every registered route and fails the build if a non-exempt one answers an unauthenticated request.

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
`docker-compose.server.yml` the collector endpoints (register, poll, enroll, ingest metrics and report
check results) don't exist on the public side at all.

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
- **The enrollment key is the exception, and it is watched.** A collector installed with the enrollment key
  (for scripted installs) gets its existing server back, with a new token, when its machine ID is already
  known, which is what makes reinstalling easy. Because that key is long-lived, using it is rate limited
  (per address and per key), the number of servers is capped (`MAX_SERVERS`, default 200), and replacing an
  existing server's credentials is recorded: it appears in the server's System row, in the audit log, and
  opens a **warning** for an hour, with advice to rotate the key if you didn't do it. That warning and the
  System-row entry are exempt from the "few per server" limits on the dashboard, so whoever holds the new
  credentials can't crowd them out with what the server reports. Treat the key like a
  password and rotate it under *Connect a server → Advanced* if a host that holds it is compromised.
- **No code comes from the dashboard.** The dashboard no longer serves `collector.py`. Remote
  hosts install the collector and the Docker proxy from container images you publish (the "Publish
  images" workflow builds them for a version tag, signs them with cosign and prints their digests).
  Put the digests in `COLLECTOR_IMAGE` and `DOCKER_PROXY_IMAGE`; the panel won't generate a command
  without them. The GHCR packages must be public for hosts to pull them without credentials.
- **The VPN is yours to run.** Collector traffic over the VPN listener is plain HTTP inside the
  tunnel. Collectors authenticate with a per-server token that is stored hashed on the dashboard.
  A collector never follows a redirect from the dashboard's address, so a sign-in page or another proxy in
  front of the API can't make it send its token somewhere else; it logs the address it was sent to and
  waits for `INFRADECK_API_URL` to be fixed. It reads at most 1 MiB of any answer.

What this still depends on:

- The backend trusts the `X-Collector-Via` header nginx sets. That is safe only while nothing
  untrusted can reach the backend directly (the backend's port is not published in the server stack; don't
  add other containers to its network).
- Anyone on your VPN can reach the gate. With a valid join token they can request to join, and an
  admin still has to approve them; ingest additionally needs a server's token.
- Rotating a collector token means re-adding that server.

## Data from collectors is untrusted

A compromised collector controls everything it reports, so the backend never trusts any of it. For the host
itself, every number is range-checked (percentages 0 to 100, loads, sizes and counters within sane limits; NaN,
infinity, text and booleans become "unknown"), so one bad value can't make pages fail for everyone. Server
descriptions (hostname, address, operating system, core count, memory) are checked too. A hostname is
compared with other servers' names ignoring case, accents and width, a name that mixes Latin letters with a
lookalike alphabet (Cyrillic or Greek, the usual way to impersonate another server) is refused, a
rename to a name another server uses is ignored, and a new server whose name is taken gets a number
(`raspberrypi (2)`), so two machines can always be told apart. Only the stack's own collector,
which reaches the backend through the gate's internal listener, can claim to be the dashboard's machine. At most
64 filesystems are accepted per check-in (names up to 200 characters), kernel-log events from the system helper at most 20 per check-in and 500 per server per week,  check-ins must be spaced by a quarter of the check interval
(refused with 429 otherwise, and checked only after the token is verified, so nobody else can use up a
server's slot), and the alerts list on the dashboard shows at most ten per server and the System row ten events per server
(chosen by the database, so thousands of stored events cost no more to show), so one noisy server can't
push everyone else's off. A date a little in the future is treated as now.

For containers, each record is reduced to known fields of the right type (text made printable and capped, numbers
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
  network layout, plus one more thing: for ports the host is already reported to listen on, whether one of
  those files has a rule accepting that port (and which file), so the Network tab can say "no rule" next to
  an exposed port. Rules about anything else are never named. This sees configuration, not the live ruleset: reading that needs `CAP_NET_ADMIN`,
  which can also change the firewall, so the collector doesn't have it.
- **Machine facts** for the System tab (kernel release, CPU model, time zone, interface names with their
  network and traffic, temperatures from sysfs, and on Debian and Ubuntu the pending-update counts apt
  leaves in world-readable files) are read the same passive way. Nothing is executed on the host, and the
  backend keeps only known fields of the right type and size.
- **Swap per container** is read from each container's cgroup file under the host's `/sys/fs/cgroup`
  (world-readable), because Docker's stats don't report it. Only containers Docker already listed are
  looked up, by their full ID.
- **The internet check is off by default.** When enabled it only opens a TCP connection and looks the
  name up, sending nothing. Its targets come from the collector's own environment
  (`INFRADECK_CONNECTIVITY_TARGETS`), never from the dashboard, so a compromised dashboard can't point
  probes at anything. Targets are limited to five hostnames or IPv4 addresses, and a probe cannot delay
  a check-in beyond a fixed deadline.
- **Unreadable never means closed.** If any network table can't be read, or is too large to read
  completely, the listener report is unknown rather than partial, so it can't make ports look closed.
  Reports are validated again by the backend and capped, and events are capped per check-in and per hour.
- **Visibility.** Like all monitoring data, the list of listening ports and firewall state is visible to
  every signed-in account. Keep registration closed on a public dashboard.

## Availability checks

Availability checks (the Availability page) are services the collectors try on a schedule — an HTTP(s)
URL, a keyword or a value in a JSON reply, a TCP port, a TLS certificate, a DNS record, or an ICMP ping.

- **Only admins define checks.** Creating, editing, pausing, deleting and the **Test** button all need
  an admin session; an API token of any scope is refused. A read-only user or a leaked token cannot make
  a collector connect anywhere.
- **An admin's check is an outbound request from the collector's host**, to any address the admin types.
  Like a private-LAN notification channel, it is a way for an admin to have a collector reach hosts on
  its own networks — the same reach an admin already has. Members and tokens can't.
- **The collector runs the check itself, with no shell and only the standard library.** It validates
  again what the dashboard sent (scheme, host, port, ranges) and refuses the rest (no `file://`, no
  non-HTTP schemes). HTTP checks are **GET or HEAD only**, with no custom headers, body or credentials,
  follow at most 10 redirects (only to http/https) and read at most **1 MiB** of a response. The body
  never leaves the collector: a result is only up/down, the status code, the response time, the days
  until a TLS certificate expires, and a short reason (≤200 characters; for a JSON check that reason can
  include up to ~80 characters of the value that failed the comparison).
- **A check's timeout is a hard limit for the whole run.** When it is up, the collector shuts down the
  check's connections, so a target that answers a byte at a time can't hold a check, or the collector's
  few check workers, past it. (A name lookup ends when the system resolver gives up.) A check whose
  results stop arriving turns **stale** on the Availability page within a few intervals instead of
  keeping its last state; an incident it had stays open until results come back.
- **Ping uses an unprivileged ICMP datagram socket**, allowed for the container's own group by a
  namespaced `net.ipv4.ping_group_range` sysctl. It grants no capability and no raw socket (no packet
  crafting or sniffing); without the sysctl a ping check simply reports that ICMP isn't permitted. DNS
  checks send a UDP query to the resolver IP the admin chose (or the system resolver), nothing more.
- **Results come in on their own endpoint.** `POST /monitoring/metrics/checks` accepts only a server's
  collector token, exists only on the gate (404 on the public listener), is rate limited, and its
  results are range-checked and bounded like every other collector report. A collector is given, and
  answers for, only the checks assigned to its server.
- **Test is ephemeral and bounded.** A test is queued in Redis for the chosen servers, runs once, and is
  forgotten after two minutes; an admin may run at most 20 a minute.
- **Visibility.** Every signed-in account can read the checks, so a check's address is handled with its
  secret-bearing parts removed for anyone who can't edit it: credentials in a URL are **refused** when a
  check is saved, and for non-admins (and in the text of the incident a failing check opens) the query
  string and any userinfo are stripped, leaving the scheme, host, port and path. Admins, who can edit the
  check, see the full target. The expected values a check compares against (a keyword, a JSON value) are
  shown as typed, so don't put a secret there either.

## Client addresses and collector installs

- **Client IPs.** nginx accepts `X-Forwarded-For` only from the addresses in `TRUSTED_PROXIES`: the reverse
  proxy in front of the dashboard, whichever one it is (Traefik, Caddy, nginx, HAProxy, ...). Anyone else's
  header is ignored and the address that connected counts, so another container, a LAN or a VPN client can't
  appear to come from a new address on every request and get around the sign-in, registration, two-factor
  and enrollment limits. With `TRUSTED_PROXIES` empty (the default) nobody is trusted; behind a proxy every
  client then shares the proxy's address and limits, which is safe but strict, so set it. The value is checked
  when the container starts (IP addresses and CIDR ranges only); anything else stops the container.
- **No one-line installer.** The old `docker run` install is gone. Remote machines run the compose
  command **Connect a server** generates (signed, digest-pinned images), and machines set up from
  `collector-compose.yml` build the images locally.

## Network separation, Redis and login sessions

- **Three networks.** `data` holds PostgreSQL, Redis and the backend and has **no route to the internet**.
  `front` holds the frontend, the collector gate and the backend. `collector` holds the collector and the gate
  only. So the collector, the container that talks to remote hosts and parses their data, can reach the gate
  and nothing else: it can't resolve or connect to Redis or PostgreSQL, and a neighbouring container can't
  either. (Checked on a running stack.) The development stack has the same split, except that its collector
  talks to the backend directly.
- **Redis has a password** (`REDIS_PASSWORD`, required). Without it, anything that could reach Redis could write
  a session for any user, administrator included, with no password or two-factor.
- **Limits on what a signed-in user can run up.** Messages to the assistant (30 an hour per user), new
  conversations (30 an hour, 100 in total) and audit-log entries (120 an hour) are limited, since each can cost
  the administrator's AI provider quota or database space. Setting up two-factor from a normal session needs the
  password again, and every try counts toward the lockout, the right password included. A limited
  (set-up-only) session lasts 30 minutes, not a day.
- **Turning the agent on is confirmed with the password and a two-factor code.** Choosing the agent's provider,
  model or API key, switching the chat or automatic diagnosis on, opening the chat to other users and raising a
  look-up budget from 0 all start
  sending data from the servers to an outside model, so someone with a stolen admin session can't do it alone.
  The password is checked first (`POST /auth/reauth`) and gives a token that lasts five minutes, belongs to that
  session, allows five codes and is used up by the change; every try counts toward the two-factor lockout, and an
  admin without two-factor can't make these changes. Turning things off and removing the key need no
  confirmation, so stopping the agent is never harder than starting it (`src/backend/reauth.py`).
- **Sessions are stored hashed.** Redis holds only the SHA-256 of the cookie value, in the session key, the
  per-user index and the pending-login token. Reading Redis (a backup, a leaked dump) doesn't hand out working
  sessions. Note what this does and doesn't do: hashing protects against *reading* Redis. It does not stop
  someone who can *write* to it, because they can pick a secret and store its hash; the password and the
  network separation are what stop that.
- **Upgrade.** Sessions written in the old format no longer work (everyone signs in once) and are deleted from
  Redis at start, so the old session IDs don't linger there.
- **The backend** trusts `X-Forwarded-For` from any address that can reach it (`--forwarded-allow-ips *`). That
  is deliberate: every request from outside reaches it through nginx, which replaces the header with the address
  it settled on, and only the stack's own containers can connect to it directly. Narrowing it to the nginx
  containers would need fixed addresses on the Docker networks, which can clash with networks already on the host.

## Statistics and history

- **Who can see it.** Every signed-in account sees the same statistics as the dashboard, for every
  server and container. As with the rest of the monitoring data, keep registration closed on a public
  dashboard.
- **Nothing new is collected.** The history is summarised from check-ins already stored (averages and
  maximums per hour, day, week and month); deleting a server deletes its summaries too.
- **Bounded requests.** The history and statistics endpoints take only a fixed list of ranges and a
  whitelist of metric names, cap the number of points and rows they read, and cache fleet answers for a
  minute under keys made only from validated values, so a request cannot make them do unbounded work or
  fill the cache. Text from the server (container names, mount paths) is shown as text, never as HTML.
- **Retention.** Raw check-in samples (host, container and mount rows) and availability results are
  deleted after 7 days. Hourly summaries and events are kept for the admin setting under *Admin → Data &
  retention* (1 year by default, 2 years or forever; nothing shorter can be chosen); daily, weekly and
  monthly summaries are kept forever. Nothing is deleted before the coarser level that replaces it exists,
  so long-range history stays available while the bulky raw rows don't grow without bound.

## Container privileges

Every container the stack builds or runs runs with `no-new-privileges` and a read-only filesystem, and drops
all Linux capabilities, adding back only the few listed here. The only exception is the optional Caddy of the
development stack's `https` profile, which uses the official image's defaults.

| Container | Runs as | Capabilities | Filesystem |
|---|---|---|---|
| backend, agent-worker | UID 10001 | none | read-only, `/tmp` in memory |
| frontend | `nginx` (port 8080 inside the container, so it needs no capability to listen) | none | read-only; cache, pid file and the `TRUSTED_PROXIES` file in memory |
| redis | `redis` | none while running | read-only, data in its volume |
| postgres | `postgres` | none while running | read-only, data in its volume, socket and `/tmp` in memory |
| collector | root | none, so it can't read other users' files (see below) | read-only |
| collector-gate | root, workers as `nginx` | `CHOWN`, `SETUID`, `SETGID` | read-only, cache and pid in memory |
| docker-proxy | root (to open the Docker socket) | none | read-only, no network |
| system-helper, port-owners (optional) | root | `SYSLOG`; `SYS_PTRACE` and `DAC_READ_SEARCH` (see their sections above) | read-only, no network |

**Exceptions, and why.** The official Redis and PostgreSQL images start as root to make their data folder
belong to their own user (and, for PostgreSQL, give it mode `0700`), then switch to that user. That start needs
`CHOWN`, `DAC_READ_SEARCH` (to walk a data folder only its owner can read), `SETUID` and `SETGID`, and
PostgreSQL also `FOWNER` (to set the mode). The database process keeps none of them: switching away from root
clears them. Without the hand-over, an existing `redis_data` volume written by an older version (when Redis
ran as root) would no longer be writable.

## Other exposure to be aware of

- The collector mounts the host filesystem read-only at `/hostfs` to read disk usage and a few
  files such as `/etc/os-release`.
- The collector runs with all capabilities dropped, a read-only filesystem and `no-new-privileges`.
  Without `CAP_DAC_OVERRIDE`, even as root it can't read other users' private files (SSH keys,
  `.env`) through the `/hostfs` mount; only files readable by "other" or owned by root are visible.
  Its one kernel-level allowance is a namespaced `net.ipv4.ping_group_range` sysctl, so availability
  ping checks can use an unprivileged ICMP socket; it is not a capability and grants no raw-socket access.
- The Docker proxy is reached through a Unix socket in a volume shared only with the collector. It
  has no network at all, so there is no port to attack and nothing to authenticate. Don't mount that
  volume into other containers.
- The system helper and the Docker proxy are built from this repository or from signed images on
  purpose and are never fetched from the dashboard, because they hold privileges.

## Reporting a vulnerability

Please open a private security advisory on the repository, or contact the maintainer directly,
rather than filing a public issue.
