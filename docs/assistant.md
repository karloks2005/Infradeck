[← Documentation](README.md)

# Incident agent chat (monitoring-only)

The chat runs on the OpenAI API. Set `OPENAI_API_KEY` in `.env` (and optionally `AGENT_MODEL`, default
`gpt-5-mini`), then restart the backend. **Admin → Incident agent → Test connection** confirms the key works and
the model exists without spending tokens. Without a key the chat is off; incidents, notifications and the
rule-based diagnosis work the same.

**What it costs.** Every call to the model is recorded with its tokens (see `agent_runs` in
[Database schema](database.md)). In **Admin → Incident agent** you enter each model's price per million tokens (copy it
from the provider's pricing page; prices aren't fetched automatically), and the page shows the month's estimated cost,
calls, tokens and cost per model. With a **monthly budget** set, admins get a notification on their channels at 80 %,
and at 100 % the chat pauses until the 1st of next month (incidents and notifications keep working). Each person may
also send at most a set number of chat messages in 24 hours (100 by default), on top of 30 an hour.

Every reply is held to a JSON schema with OpenAI's Structured Outputs: a message, and at most one check from
the fixed list below. The provider sits behind a small interface (`src/agent/providers.py`), so other providers
can be added later.

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
