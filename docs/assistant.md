[← Documentation](README.md)

# Incident agent (monitoring-only)

## Diagnoses

When a warning or critical incident opens, or gets worse, the agent worker diagnoses it (Admin → Incident agent →
"Diagnose new incidents automatically"; anyone who may act on incidents can also click **Diagnose now** on the
incident). It works in three steps, in `src/agent/`:

1. **Evidence pack** (`evidence.py`): the incident, the machine, metrics from an hour before it opened until now,
   recorded events, image changes of the container, the alert rule's own checks, the containers right now, and
   similar incidents in the last 90 days. Every fact has an ID (`metric:main`, `event:812`, `check:2`,
   `incident:31`). Series are cut to 40 points and lists to their newest entries, and every text is masked for
   secrets (`masking.py`).
2. **The model** (`diagnosis.py`, `DIAGNOSIS_PROMPT.md`) answers in a strict JSON schema: a summary, its confidence,
   signals that each cite fact IDs, up to five fix steps and a condition that shows the incident is over.
3. **Checks** before anyone sees it: signals that cite no fact from the pack are dropped and counted ("1
   unsupported claim left out"), fix commands that could destroy data are flagged and never recommended
   (`safety.py`), and an answer that doesn't parse is asked for once more, then saved as a failed report.

Each diagnosis is a new version in `agent_reports`; the incident page shows the latest, says which model wrote it
and how sure it is, and falls back to the rule-based diagnosis when there is none. The evidence chart, recorded
events and timeline always come from stored data, never from the model. Every model call counts towards the
budget below, and diagnoses stop while the budget is used up.

## Chat


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
