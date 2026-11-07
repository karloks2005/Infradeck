[← Documentation](README.md)

# Incident agent

The incident agent diagnoses incidents, watches whether the fix holds, adds its diagnosis to notifications, and
answers questions in a chat. It reads data Infradeck already has; it can't run commands, restart containers or
change files on your servers. Everything about it is in `src/agent/` and in **Admin → Incident agent**.

## Setting it up

1. Make sure `SECRETS_KEY` is set in `.env` (see [Configuration](configuration.md)): the API key is stored
   encrypted with it, so it can't be saved without one.
2. Create an API key with one of the providers: Anthropic (Claude), OpenAI or Google Gemini.
3. In **Admin → Incident agent**, pick the provider, keep or change the model, paste the key and click **Save**.
   Confirm with your password and a two-factor code: this decides where the agent's data goes. Turning the chat
   or automatic diagnosis on, and setting Chat access to "Per user", ask for the same; turning things off doesn't.
   Models used when the field is left empty:

   | Provider | Default model |
   |---|---|
   | Anthropic | `claude-haiku-5-5` |
   | OpenAI | `gpt-6-luna` |
   | Google Gemini | `gemini-3.1-flash-lite` |

   Another model works if it supports structured (JSON schema) output.
4. **Test connection** checks the key and that the model exists, without spending tokens.
5. Enter the model's price per million tokens under **Prices** (from the provider's pricing page), so its calls
   count towards the budget, and set a **Monthly budget** if you want one.

The key never comes back from the backend: the page only shows that one is saved. Each provider keeps its own
key, so switching back and forth doesn't lose them; **Remove key** deletes the chosen provider's key. Every change
is written to the audit log, without the key. The provider addresses are fixed in the code
(`src/agent/providers/`), so the agent's data can only go to one of these three APIs.

Without a key nothing is sent anywhere: incidents keep the rule-based diagnosis (worked out locally from the same
data), notifications go out as before, and the chat is off. If `SECRETS_KEY` changes and the stored key can no
longer be decrypted, the agent behaves as if there were no key until it is saved again.

## Diagnoses

When a warning or critical incident opens, or gets worse, the backend queues a job and the **agent worker**
(its own container, see [The containers](containers.md#infradeck-agent-worker)) diagnoses it in three steps:

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

Each diagnosis is a new version in `agent_reports`. The incident page (Agent) says which model wrote it, when, and
how sure it is, and shows its summary, signals with the facts they rest on, and fix steps; with no diagnosis it
shows the rule-based one and says so. The evidence chart, recorded events and timeline always come from stored
data, never from the model. The Overview shows the first sentence next to the incident.

**Diagnose now / Diagnose again** on an incident asks for a new diagnosis (admins only); the page shows it as soon
as it is ready, without a reload. **Diagnose new incidents automatically** (on by default) can be switched off;
then a diagnosis only happens when someone asks.

**The same problem twice.** When an incident opens for the same rule on the same server (so the same container,
disk or check) as one diagnosed in the last 24 hours, that diagnosis is reused: no model call, no tokens. The
incident says so and links to the earlier one. Getting worse and **Diagnose again** always ask the model.

## Watching the fix

Each diagnosis ends with a condition that shows the incident is over. Two kinds are checked against the data
every minute, without the model (`verify.py`):

- **a container runs N minutes without a restart**;
- **a metric stays under a threshold for N minutes** (memory, swap, load, CPU, a disk).

The incident shows the progress ("Watching: resolves itself once gitlab runs 15 min without a restart. Held for 9
of 15 min."). When the condition holds, the agent resolves the incident with "Resolved by the agent: …", which is
what the resolved list shows. Data older than 5 minutes is never taken as proof.

Most rules resolve their incidents themselves as soon as they stop firing; this matters for the ones that stay
open for a window after the fact (a container exit, an OOM kill, a restart loop counted over 30 minutes). So that
such a rule doesn't open the incident again while its window still covers the old events, the same rule on that
server stays quiet for its window plus 10 minutes (about 70 minutes by default). If the problem comes back in that
time (a new restart, the metric over the threshold), the hold ends at once and the rule opens a new incident.

## Notifications

A notification about a new warning or critical incident waits up to **3 minutes** for the agent's diagnosis and
then carries its first two sentences in a line starting with "Agent:". If the diagnosis isn't ready by then, the
notification goes out without it. It doesn't wait at all when no diagnosis is coming: no key, automatic diagnosis
off, the budget used up, or the worker not running. Resolved notifications never wait.

## Who sees what

- **Show diagnoses to users** (on by default): when off, only owners and admins see the agent's diagnoses, on the
  incident page, on the Overview and in notifications; everyone else sees the rule-based diagnosis. The backend
  leaves the diagnosis out of what it sends them, so it isn't only hidden in the page.
- **Chat access**: **Admins only** (the default), or **Per user**, where an **Agent chat** switch next to each user in
  Admin → Users decides (new users start with it off). Admins always have chat while **Chat** is on.

## Chat

The chat answers questions about an incident or any server. Each message goes to the model with the incident's
facts, and the conversation so far (its latest 40 turns). Every reply is held to a JSON schema with the
provider's structured output: a message, and at most one check from a fixed list. All three providers sit behind
one small interface (`src/agent/providers/`). With Claude, the chat thinks briefly (effort `low`) and a diagnosis
thinks harder (effort `high`); the other providers ignore the setting.

The chat has no code path for running shell commands. It may request exactly one fixed, read-only check at a time:
`server_health`, `container_health`, or `daily_checkup`, implemented with Python system APIs and Docker's Unix API
socket, never the shell or Docker CLI. The backend container does **not** mount `/var/run/docker.sock`; for the
container checks, point `DOCKER_SOCKET_PATH` at a hardened Docker API proxy that permits only the required GET
endpoints. `daily_checkup` combines host and container checks; on a critical finding it POSTs an alert only to
`ADMIN_ALERT_WEBHOOK_URL`, and without it the result says `not_configured` and no request is made.

## What it costs

Every call to the model is recorded with the tokens the provider reports for it (see `agent_runs` in
[Database schema](database.md)). **Admin → Incident agent** shows the month's estimated cost, calls, tokens and cost
per model. The token counts are the provider's own; the cost is an estimate from the prices you enter, and counts
all input at the full price (the provider charges less for cached input), so it errs on the high side. Calls that
fail are recorded with no tokens, since the provider reports none.

With a **monthly budget**, admins get a notification at 80 %, and at 100 % diagnoses and the chat pause until the
1st of next month (incidents and notifications keep working). Each person may send at most a set number of chat
messages in 24 hours (100 by default), on top of 30 an hour. A diagnosis is usually around 10 000 tokens; a chat
message a few hundred to a few thousand.

## The worker card

**Admin → Incident agent → Worker** is a health check, not a history: whether the worker is running, whether any
jobs are queued, running or waiting to retry, the last job, and how many of the latest jobs failed. The folded
list below it shows the last 10 jobs and why each ended as it did (diagnosed, reused, skipped with the reason, or
failed with the error). Redis keeps only the latest 50; the diagnoses themselves live on each incident.
