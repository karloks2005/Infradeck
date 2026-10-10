[← Documentation](README.md)

# API overview

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
| `GET /conversations`, `POST /conversations`, `DELETE /conversations/{id}`, `POST /agent/chat` | session | Homie assistant chats. |
| `GET /logs`, `POST /logs`, `GET /dashboard/summary` | session | Audit log and summary counts. |

The four collector endpoints above exist only on the collector gate in the server
stack (the public listener answers 404); the development stack serves them on the
same address as everything else.
