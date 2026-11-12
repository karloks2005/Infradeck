# Threat model (Infradeck)
 
A summary of the architecture and trust boundaries, derived from `SECURITY.md` and the code.
If the code and this document disagree, **trust the code** and report it as a documentation
finding.
 
## Components and what they touch
 
| Component | Where | Reaches | Trust |
|---|---|---|---|
| Frontend (nginx + React SPA) | `docker/nginx*.conf`, `src/frontend` | public listener; proxies to the backend | publicly reachable |
| Backend (FastAPI) | `src/backend` | Postgres, internal network segment | processes all input |
| Postgres | `docker/postgres/init` | backend only | holds hashes, encrypted TOTP secrets, host data |
| Collector gate | `docker/nginx.collector.conf` | backend; one port on the VPN address | the only path to collector endpoints |
| Collector | `src/collector/collector.py` | host `/proc`, host FS read-only (`/hostfs`), Docker proxy, backend over HTTP | runs on remote hosts, may be compromised |
| Docker proxy | `src/collector/docker_proxy.py` | Docker socket (uid 0), Unix socket to the collector | holds root-equivalent access; must stay minimal |
| System helper (optional) | `src/collector/system_helper.py` | kernel log, wtmp, `CAP_SYSLOG` | privileged; its output is untrusted |
| Incident agent (LLM) | `src/agent/` (`tools/`, `investigate.py`, `diagnosis.py`, `chat.py`, `masking.py`, `safety.py`, `providers/`, `worker.py`) | read-only tools over the Infradeck database; external LLM provider (Anthropic, OpenAI, Gemini) with a key encrypted in the database | input includes host data; everything sent to the provider goes through `masking.py` |
| CI | `.github/workflows/images.yml` | GHCR, cosign | supply chain for the images that hosts run |
 
Outside the repository (not in scope, but keep them in mind as assumptions): Traefik in front
of the frontend, OpenVPN carrying collector traffic, GHCR packages.
 
## Attacker profiles
 
For every finding, determine which profile can exploit it.
 
1. **Anonymous internet user** on the public listener. Reaches only the dashboard and its
   API. Collector endpoints (register, poll, enroll, ingest) **must not exist** on that side.
2. **Logged-in non-admin user.** By design sees all monitoring data for all servers. The
   question is whether they can *change* anything, see other users' accounts/sessions/
   conversations, or become admin.
3. **Neighbouring web app on the same domain/platform** (a sibling subdomain behind the same
   reverse proxy). Same "site", so `SameSite=Lax` does not protect against state-changing
   requests. The protection is the `Origin` / `Sec-Fetch-Site` check.
4. **VPN peer.** Reaches the collector gate. Without a valid join token it must not even be
   able to request joining; with one it waits for admin approval; ingest requires a server
   token.
5. **Compromised collector.** Controls everything it reports (container names, mounts, ports,
   events). The backend must not trust it: types, lengths, ranges, counts, limits per
   check-in and per hour. It must not be able to affect other servers or the dashboard.
6. **Compromised or malicious container on the host / neighbour on the Docker network.** May
   try to reach the backend bypassing nginx (spoofing `X-Collector-Via`, `X-Forwarded-For`),
   the docker proxy's Unix socket, or a volume it must not share.
7. **Compromised dashboard.** Must not be able to make hosts execute code (the dashboard does
   not serve code), or point probes at arbitrary targets (targets come from the collector's
   environment).
8. **Supply chain.** Dependencies (pip, npm), base images, GitHub Actions, workflow
   permissions, unsigned or unpinned images.
9. **Hostile data from a monitored host.** Container names, mount paths, kernel log lines,
   wtmp, `/etc/os-release`. An attacker with limited access to the host can influence these
   strings. Paths: stored XSS, injection into keys/queries, exceeding limits, breaking
   parsers, **prompt injection** against the assistant.
10. **Physical/local access to the host** (e.g. `seed_user.py`, `reset_2fa.py`) is outside the
    threat model by design: that level of access already grants database access. But the
    scripts must not contain built-in credentials or leak secrets.
## Most valuable assets (in order)
 
1. Access to the Docker API / root on hosts (via the proxy, the helper, mounts).
2. A dashboard admin session and the ability to approve new servers.
3. Server tokens and join tokens (hashed in the database).
4. `SECRETS_KEY` (Fernet) and TOTP secrets, recovery codes.
5. Monitoring data (open ports, firewall hash, container list), because it maps the network.
6. Alert integrity (an attacker who can silence or fake incidents hides an attack).
## Design assumptions to verify, not take for granted
 
- The backend trusts `X-Collector-Via` only if nothing untrusted reaches the backend directly.
- nginx accepts `X-Forwarded-For` only from private/Docker addresses; rate limits depend on
  this.
- Collector endpoints are rejected both on the public side (nginx 404) and in the backend
  (`COLLECTOR_ACCESS=strict`). Check **both** layers, and that neither depends on the other.
- "Single-use" (join tokens, TOTP codes, recovery codes, setup token) must be atomic in the
  database, not "check then mark" (race).
- "Capped" (500 containers, 20 open tokens, 5 attempts/5 min) must hold under concurrent
  requests too.
- The first account becomes admin: check every path to that state (registration, setup
  token, `seed_user.py`, a race between two simultaneous first requests).
## Known accepted limitations (do not report as new, but check they still hold)
 
From `SECURITY.md`: all logged-in accounts see all monitoring data; the 2FA step locks when
codes are guessed; sessions from before 2FA was introduced are not in the list; TOTP only;
VPN traffic is plain HTTP inside the tunnel; history rollups are not deleted yet. If the code
deviates from what is described there, that is a finding.
 
## Open items from the project notes (check whether they are still open)
 
A cap on pending servers per IP; rate limit on `enroll`; `src/backend/seed_user.py`
(built-in admin password); `dev.db` tracked by git; outdated README. If they are still real,
report them with their current state in the code.
 