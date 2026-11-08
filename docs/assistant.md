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
   or automatic diagnosis on, setting Chat access to "Per user" and raising a look-up budget from 0 ask for the
   same; turning things off doesn't.
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
(its own container, see [The containers](containers.md#infradeck-agent-worker)) diagnoses it in four steps:

1. **Evidence pack** (`evidence.py`): the incident, the machine, metrics from an hour before it opened until now,
   recorded events, image changes of the container, the alert rule's own checks, the containers right now, and
   similar incidents in the last 90 days. Every fact has an ID (`metric:main`, `event:812`, `check:2`,
   `incident:31`). Series are cut to 40 points and lists to their newest entries, and every text is masked for
   secrets (`masking.py`).
2. **Looking further** (`investigate.py`): the model may look things up with the [read-only
   tools](#what-the-agent-can-look-up), up to 8 calls by default, for example the week before the pack's window, or
   whether other servers are affected. See [Look-up budget](#look-up-budget).
3. **The answer** (`diagnosis.py`, `DIAGNOSIS_PROMPT.md`) in a strict JSON schema: a summary, its confidence,
   signals that each cite fact IDs, up to five fix steps and a condition that shows the incident is over.
4. **Checks** before anyone sees it: signals that cite no fact from the pack or from a tool result are dropped and
   counted ("1 unsupported claim left out"), fix commands that could destroy data are flagged and never recommended
   (`safety.py`), and an answer that doesn't parse is asked for once more, in the same conversation and without
   tools (so the look-ups aren't paid for twice), then saved as a failed report.

Each diagnosis is a new version in `agent_reports`, with the evidence pack and every look-up it made (tool,
arguments, result) kept next to it. The incident page (Agent) says which model wrote it, when, and
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
- **a metric stays under a threshold for N minutes** (memory, swap, load, CPU, a disk). Only a metric from the
  evidence pack can be watched; a condition on a metric the agent found with a tool becomes one a person confirms.

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
facts and the conversation so far (its latest 40 turns), and the model may look things up with the same
[read-only tools](#what-the-agent-can-look-up) as a diagnosis, up to 4 calls per message by default. Only the
questions and replies stay in the conversation; the look-ups of an earlier message aren't sent again. Every reply
is held to a JSON schema with the provider's structured output: one message. All three providers sit behind one
small interface (`src/agent/providers/`).

With Claude, an answer that may use tools thinks harder (effort `high`); a chat message without tools thinks
briefly (`low`). The other providers ignore the setting.

A message that is still looking things up after 45 seconds is told to answer with what it has, so the reply comes
back before the web server stops waiting (120 seconds). The chat has no code path for running commands: the tools
only read Infradeck's database, and the reply is text.

## What the agent can look up

Besides the evidence pack, the agent has eleven **read-only tools** (`src/agent/tools/`): questions it can ask
Infradeck's database while it investigates, such as "how did CPU change on 3 November between 10:00 and 12:00" or
"has this disk filled up before". Diagnoses and the chat use them within a [budget](#look-up-budget) of calls.

| Tool | What it answers |
|---|---|
| `server_info` | What the machine is and how it is now: OS, kernel, CPU, memory, collector and Docker versions, last check-in, online, pending updates, uptime, reboot needed, clock sync, Docker and internet reachability. |
| `host_metrics` | CPU, memory, swap, load, root disk, inodes, Docker API and collector timing over a window: min, max, average, first, last and up to 40 points per metric. |
| `list_containers` | Every container at one check-in (last 7 days): status, health, CPU, memory and limit, restarts, exit code, OOM kill, image. Not running and largest first. |
| `container_history` | One container over a window: CPU, memory and restart count, how much of the time it was not running, its memory limit, and (last 7 days) each change of status, health, exit code, OOM kill or image. |
| `disk_usage` | Every filesystem (or one): used space and inodes now, usage over the last N days, growth in GB per day and in about how many days it fills up. |
| `events` | What the machine recorded: reboots, OOM kills, kernel faults, image changes, containers added or removed, ports opened or closed, firewall changes, package updates. The newest 30. |
| `compare_to_baseline` | A metric's average over one hour next to the same hour yesterday and that hour of the day over the previous 4 weeks, with the difference. |
| `find_incidents` | Earlier incidents on one server or all of them, of one kind or any: how long they lasted and how they were resolved. |
| `fleet_snapshot` | Every server at one moment: online, open incidents and the worst of them, failing availability checks. Tells one machine's problem from everyone's. |
| `check_results` | Availability checks over a window: uptime, when they were down, the errors they got, response times. |
| `network_state` | Listening ports (and whether other machines can reach them), the firewall, and port and firewall changes in the last 7 days. |

**What they may read.** Only what Infradeck already stores. No tool writes anything, contacts a collector or runs a
command, and the list is closed: a call to any other name, or with arguments that don't match the tool's schema
(an unknown field, a wrong type, a value outside its list), is refused before anything is read. The agent names a
server by its hostname and a container or check by its name; an unknown one is answered with the names it may use.

**How far back.** Any window in the last **90 days**, given as `from` and `to` (UTC); without them, the last 24
hours. The last 7 days come from every check-in; older windows from the hourly summaries, the same data as the
charts on Statistics, so two hours from last month are two points. The agent is told to start wide and narrow down.

**How much comes back.** Each series is cut to 40 points, each the highest value of its stretch, so a short peak
is never averaged away; lists keep their newest 30 entries; text is clipped; a whole result stays under about
12 KB. If it would be larger, the longest list is halved (a series by joining neighbouring points, keeping the
higher one) and the result says `"truncated": true`.

**What never comes back.** Every result is masked for secrets like the evidence pack (`masking.py`), and the
interface masks it again before it is sent (`add_tool_results`). A check's target is always shown as non-admins
see it, without credentials or query string, because it goes to the model provider. IP addresses, users, the audit
log and enrolment are not readable by any tool. So a tool gives the same answer whoever asked, and a user who
may chat learns nothing through it that the dashboard doesn't show them.

**Fact IDs.** Every fact a tool returns has an ID that a diagnosis cites, as with the evidence pack. The ID says
what the fact is, not when it was read, so the same question always gives the same ID; the window is in the label
shown next to it ("CPU · web1 · 2026-11-03 10:00–12:00 UTC"). When the agent reads the same series twice with
different windows, the label keeps both.

| Fact | ID |
|---|---|
| a recorded event, an incident | `event:812`, `incident:31` (as in the evidence pack) |
| a host metric, a filesystem | `metric:web1.cpu`, `metric:web1.disk:/var` |
| a container, its metrics | `container:web1/nginx`, `metric:web1/nginx.memory_mb` |
| a machine, its ports and firewall | `server:web1`, `network:web1` |
| an hour against its usual level | `baseline:web1.cpu` |
| an availability check | `uptime:7` (`check:N` in the evidence pack means the alert rule's own checks) |
| a server in the fleet snapshot | `fleet:web1` |

**Adding a tool.** Write the function in the module for its area (`servers.py`, `containers.py`, `activity.py`):
it gets the database session, the checked arguments, the time and a `Facts` collector, and returns a dict whose
facts each carry an `id` added with `facts.add`. Register it in `tools/__init__.py` with a description and a
strict schema (every property required, `additionalProperties: false`, null for "not given"), and add tests to
`tests/assistant/test_agent_tools.py`: the shared tests then check that it stays bounded, masked, labelled and
read-only.

### Look-up budget

**Admin → Incident agent → Look-ups** sets how many tool calls one answer may make: **8 per diagnosis** (0 to 20)
and **4 per chat message** (0 to 10) by default. At 0 the agent gets no tools and answers from what it was given in
a single call, as before the tools existed. Raising a budget from 0 is confirmed with the password and a two-factor
code, because look-ups send more of a server's data (its history, ports, firewall) to the provider. Each change is
written to the audit log.

How the budget holds (`investigate.py`):

- A call past the budget isn't run; the model gets "Tool budget used up; give your final answer now." for it.
- Once the budget is used up, the month's [budget](#what-it-costs) is reached, or a chat message's 45 seconds are
  over, the next call to the model is made with tools not allowed, so it has to answer. The tools stay listed (a
  changed tool list would make the provider's cache and Claude's earlier thinking invalid).
- A model that still asks for tools after that is stopped and the answer fails, so a loop can't run on.
- A call with wrong arguments, or for an unknown server, goes back to the model with the reason so it can correct
  it; that counts as a call. Any other failure is logged on the server and the model only hears "The look-up
  failed".

**Audit log.** Every tool call, run or refused, is an `agent_tool_call` entry: which tool, its arguments (masked),
and whether it worked, under the incident ("Diagnosis of INC-0042") or the chat question and conversation.
Deleting a conversation keeps these entries and only removes their link to it. No one can write this event type
through `POST /logs`.

## What it costs

Every call to the model is recorded with the tokens the provider reports for it (see `agent_runs` in
[Database schema](database.md)). **Admin → Incident agent** shows the month's estimated cost, calls, tokens and cost
per model. The token counts are the provider's own; the cost is an estimate from the prices you enter, and counts
all input at the full price (the provider charges less for cached input), so it errs on the high side. Calls that
fail are recorded with no tokens, since the provider reports none.

With a **monthly budget**, admins get a notification at 80 %, and at 100 % diagnoses and the chat pause until the
1st of next month (incidents and notifications keep working). Each person may send at most a set number of chat
messages in 24 hours (100 by default), on top of 30 an hour. A diagnosis without look-ups is usually around 10 000
tokens; a chat message a few hundred to a few thousand. Each look-up adds its result (up to about 12 KB, a few
thousand tokens) to every later call of the same answer, and every call is recorded on its own, so a diagnosis that
uses all 8 look-ups can cost several times as much. Lower the [look-up budget](#look-up-budget) to spend less.

## The worker card

**Admin → Incident agent → Worker** is a health check, not a history: whether the worker is running, whether any
jobs are queued, running or waiting to retry, the last job, and how many of the latest jobs failed. The folded
list below it shows the last 10 jobs and why each ended as it did (diagnosed, reused, skipped with the reason, or
failed with the error). Redis keeps only the latest 50; the diagnoses themselves live on each incident.
