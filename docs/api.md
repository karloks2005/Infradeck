[← Documentation](README.md)

# API overview

All paths are relative to `/api` when going through the frontend/nginx.

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /health` | – | Database and Redis status. |
| `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | session cookie | Accounts and sessions. |
| `POST /auth/login/2fa` | pending login token | Second step of sign-in: authenticator or recovery code. |
| `GET /auth/2fa/status`, `POST /auth/2fa/setup`, `POST /auth/2fa/enable` | session (also the limited setup session) | Two-factor status, start and confirm setup. Starting setup from a normal session needs `{"password": ...}`; the forced first enrolment doesn't. |
| `POST /auth/2fa/disable`, `POST /auth/2fa/recovery-codes` | session + password + code | Turn two-factor off; replace the recovery codes. |
| `POST /auth/reauth` | session with two-factor (rate limited) | Step 1 of confirming a change that sends data to the AI provider: checks the password and returns a `reauth_token` (5 minutes, this session only, 5 codes, single use). Step 2 sends `reauth_token` and `code` with the change. |
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
| `GET` / `PUT /admin/settings` | admin | History retention (`history_retention_days`: 365, 730 or 0 for forever), chat on/off, require two-factor for admins, alert thresholds and per-mount disk thresholds; the agent's model prices, monthly budget, daily chat limit, automatic diagnosis, whether users see diagnoses, chat access (`admins_only` or `per_user`), and the look-up budgets (`agent_tool_budget_diagnosis` 0–20, `agent_tool_budget_chat` 0–10). Turning the chat or automatic diagnosis on, chat access to `per_user`, and a look-up budget up from 0 also need `reauth_token` and `code` (see `POST /auth/reauth`). |
| `GET /admin/users`, `PATCH /admin/users/{id}` | admin | Users and invitations; change someone's role, switch them off, or (with per-user chat access) allow them the agent chat (`agent_chat_enabled`). |
| `GET /agent/config` | session | Whether chat with the agent can be used: switched on, allowed for you (`may_chat`), a key configured, and the monthly budget not used up. |
| `GET /incidents`, `GET /incidents/{id}` | session | The incident inbox; one incident with its rule-based diagnosis and, under `agent`, the agent's latest report (with `watch` progress while it is open, `reused_from` when it was reused, and `steps`, its look-ups in plain words: `tool`, `status`, `reason`, `asked`, `found`, `from`, `to`, `at`; never the raw results). The report is left out (`hidden: true`) for users who aren't admins while diagnoses aren't shown to users. |
| `POST /incidents/{id}/acknowledge`, `…/resolve`, `…/mute`, `…/unmute` | admin | Act on an incident. |
| `POST /incidents/{id}/diagnose` | admin | Queue a new diagnosis by the agent (409 without a key, 429 while the budget is used up). |
| `GET /admin/agent/usage` | admin | This month's model calls, tokens and estimated cost against the budget, per model and per day, with the prices and limits. |
| `GET /admin/agent/worker` | admin | Whether the agent worker is running, how many jobs wait, are running or wait to be retried, and the latest results. |
| `PUT /admin/agent/provider` | admin (rate limited) + `reauth_token` and `code` | Choose the agent's provider (`anthropic`, `openai`, `gemini`) and model, and optionally save a new API key, stored encrypted (409 without `SECRETS_KEY`). Returns the choice and whether a key is saved, never the key. |
| `DELETE /admin/agent/key` | admin | Remove the chosen provider's API key; the agent stops until a new one is saved. |
| `POST /admin/agent/test` | admin | Checks the saved key, that the configured model exists, and that the provider accepts a request shaped like a diagnosis (every tool and the answer schema, tools not allowed). That request costs a few thousand tokens and is counted in the usage as kind `test`; the reply adds `tokens`. At most 10 in 10 minutes. |
| `POST /monitoring/collectors/register` | join token (rate limited) | Collector asks to join; returns a poll secret and pairing code. |
| `POST /monitoring/collectors/register/poll` | poll secret | Collector checks approval; returns its token once approved. |
| `POST /monitoring/collectors/enroll` | enrollment key | Join without approval. |
| `POST /monitoring/metrics/ingest` | collector token | Collector reports host, container and system metrics. |
| `GET /conversations`, `POST /conversations`, `DELETE /conversations/{id}`, `POST /agent/chat` | session | Chats with the incident agent (403 for users chat isn't allowed for). `POST /agent/chat` returns `message` and `steps` (as in a report); each message in `GET /conversations` carries its `steps`. |
| `GET /logs`, `POST /logs`, `GET /dashboard/summary` | session | Audit log and summary counts (`agent_lookups`: the agent's tool calls). `POST /logs` adds an entry to your own log (level from a fixed list, message up to 2000 characters, at most 120 an hour); its source is always `dashboard`, and system event types such as `agent_tool_call` are refused. |

The four collector endpoints above exist only on the collector gate in the server
stack (the public listener answers 404); the development stack serves them on the
same address as everything else.
