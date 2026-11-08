[← Documentation](README.md) · [Incident agent](assistant.md)

# How the agent investigates

This page explains, end to end, what happens between an incident (or a chat question) and the agent's answer: what
the model is given, what it may look up, who writes each part of what you see, what is checked, what is stored, and
where the limits are. [Incident agent](assistant.md) is the short version for people using it; this page is for
anyone who needs to trust it, change it or review it.

The code is in `src/agent/`: `investigate.py` (the loop), `tools/` (the look-ups), `steps.py` (the steps shown to
people), `diagnosis.py` and `chat.py` (the two ways in), `masking.py` and `providers/` (what leaves the machine).

## Contents

- [The short version](#the-short-version)
- [One investigation, step by step](#one-investigation-step-by-step)
- [Who writes what](#who-writes-what)
- [What the model sees and what people see](#what-the-model-sees-and-what-people-see)
- [The budget, and when the model has to answer](#the-budget-and-when-the-model-has-to-answer)
- [What is checked](#what-is-checked)
- [What is stored, and who can see it](#what-is-stored-and-who-can-see-it)
- [The tools, and how each step is described](#the-tools-and-how-each-step-is-described)
- [Limits](#limits)
- [Changing it](#changing-it)

## The short version

1. The model gets the incident's evidence pack (a diagnosis) or the question and the conversation (the chat).
2. It may call read-only **tools** that look things up in Infradeck's database, a few times, within a **budget**.
   Each call names the question it should answer (its **reason**).
3. It writes its answer: a diagnosis that must cite the facts it rests on, or a chat reply.
4. Infradeck checks the answer, saves it with the look-ups behind it, and writes every look-up to the audit log.
5. The page shows the answer and, folded away underneath, each look-up: the agent's reason, what it looked up, and
   one line of facts from the result, written by Infradeck's code, not by the model.

The model never acts: no tool writes anything, runs a command or contacts a monitored machine.

## One investigation, step by step

```
 incident opens                     someone asks in the chat
       │                                       │
       ▼                                       ▼
 evidence pack (evidence.py)         question + incident facts + earlier turns
       │                                       │
       └──────────────┬────────────────────────┘
                      ▼
          investigate.py: start the conversation
          (system prompt, the message, the 11 tools)
                      │
        ┌────────────►▼
        │     the model takes a step ── answer ──────────────┐
        │             │                                       │
        │        tool calls                                   │
        │             ▼                                       │
        │   within budget? ── no ──► "Tool budget used up"    │
        │             │ yes                                   │
        │             ▼                                       │
        │   tools.run: check the arguments, read the DB,      │
        │   bound and mask the result                         │
        │             │                                       │
        │   audit log entry · trail entry                     │
        │             │                                       │
        └── results back to the model (masked again)          │
                                                              ▼
                                   diagnosis: check the answer and save a report
                                   chat: save the reply with its steps
```

In more detail:

1. **The way in.** A diagnosis starts from the evidence pack (`evidence.py`): the incident, the machine, metrics
   around it, events, the rule's own checks, similar incidents. Every fact in it has an ID such as `metric:main`.
   A chat message starts from the question, the facts of the incident it is about (if any), and the conversation's
   latest 40 turns. Earlier look-ups are not sent again: only questions and replies stay in the conversation.
2. **The conversation starts** (`investigate.py`). The provider gets the system prompt (`DIAGNOSIS_PROMPT.md` or
   `SYSTEM_PROMPT.md`), the message, the list of tools, and the JSON schema the final answer must follow. With
   tools offered the model thinks harder (effort `high`; Claude and OpenAI use it). Optional arguments are plain
   strings where empty means "not given", because providers limit nullable parameters in strict schemas (#196);
   **Test connection** in Admin sends one request of exactly this shape (calls allowed), so a provider that refuses
   it says so at once (#199).
3. **The model takes a step.** It either answers, or asks for one or more tool calls. Each call is a tool name and
   arguments that follow the tool's schema, including `reason`: one sentence saying what the call should find out,
   written before any result exists.
4. **Each call is checked and run** (`tools/__init__.py`). The name must be one of the 11 tools, and the arguments
   must match the schema exactly (no extra or missing fields, right types, values from the allowed lists). The tool
   reads stored data only. Its result is cut to fit (about 12 KB), masked for secrets, and carries fact IDs.
5. **Each call is recorded**: in the trail kept with the answer, and in the audit log (`agent_tool_call`).
6. **The results go back** to the model, masked once more on the way (`add_tool_results`), and it takes the next
   step. This repeats until it answers or has to (see [the budget](#the-budget-and-when-the-model-has-to-answer)).
7. **The answer is checked and saved.** A diagnosis goes through `check_answer` (see [What is checked](#what-is-checked))
   and becomes a new version in `agent_reports`, with the evidence pack and the trail. A chat reply is saved in
   `chat_messages`, with its steps already described (`steps.py`).
8. **The page shows it.** The incident page and the chat show the answer, and under it "How the agent got there ·
   N look-ups" or "Looked up · N look-ups", folded away until opened.

## Who writes what

Everything you see comes from one of two places, and the page keeps them apart.

| Part | Written by | When | Can it be wrong about the data? |
|---|---|---|---|
| The diagnosis: summary, signals, fix steps, verify condition | the model | after its look-ups | Yes, so it is checked: every signal must cite facts that exist, and commands are checked for danger. |
| A chat reply | the model | after its look-ups | Yes. Chat replies are not checked against the data; treat them as advice. |
| A step's **reason** ("Did memory grow before the restarts?") | the model | when it makes the call, before the result exists | It states an intention, not a finding, so it can be vague but not a wrong claim about the result. |
| A step's **what it looked up** ("Memory on web1 · 1 Nov 00:00 – now") | Infradeck's code (`steps.py`), from the call's arguments | when the page is shown | No: it restates the arguments. |
| A step's **what it found** ("memory 41–96 %, average 63 %") | Infradeck's code (`steps.py`), from the stored result | when the page is shown | No: it copies numbers and names the tool returned, and says "Done." when the result isn't in the expected shape. |
| The evidence chart, events, timeline | Infradeck's code, from stored data | always | No. |

So the model's words are the diagnosis, the reply and the reasons; the facts next to them are Infradeck's. A step
never says what its numbers mean ("memory is the cause"): that is the diagnosis's job, and the diagnosis has to
cite the facts it rests on.

**Why the summaries are not written by the model.** It could summarise each result, but that would cost tokens on
every call and could not be checked against the data. The tools return structured data with the figures already
worked out (minimum, maximum, average, counts, growth), so restating them is plain formatting.

## What the model sees and what people see

| | The model | People on the page |
|---|---|---|
| Evidence pack | all of it, masked | the incident, chart, events and timeline (from stored data, not from the pack) |
| A tool result | all of it: statistics, up to 40 points per series, lists, labels | one line of facts (`found`) |
| A call's arguments | its own | in plain words (`asked`) and the window in their own clock |
| The reason | its own | in quotes, as the agent's |
| Errors | the reason a call was refused, or "The look-up failed" | the same |

Nothing reaches the browser that the dashboard doesn't already show: the raw results stay in the database
(`agent_reports.body`, `investigation`) and are never sent to the page. Check targets inside results are always
without credentials or query string.

**Masking.** Every text that goes to the provider is masked for tokens, keys, passwords and credentials in URLs
(`masking.py`): the evidence pack, each tool result (twice: by the tool and by the provider interface), each
tool error, and each call's arguments and reason as they are stored. The reason is the model's own text, but it
can repeat something it saw, so it is masked too.

## The budget, and when the model has to answer

The budget is a number of tool calls per answer, set in **Admin › Incident agent › Look-ups**:

| | Default | Range |
|---|---|---|
| per diagnosis | 8 | 0–20 |
| per chat message | 4 | 0–10 |

- **0** offers no tools at all: the model answers in a single call from what it was given.
- A call with wrong arguments counts as a call (it went to the model and back).
- A call past the budget, even in the same turn, isn't run. The model gets "Tool budget used up; give your final
  answer now." for it, and the step shows "Not run: the look-up budget was used up."
- The model then gets one more step with tools not allowed (`allow_tools=False`), so it has to answer. The tool list
  itself stays the same for the whole conversation, because changing it would make the provider's cache and Claude's
  earlier thinking invalid.
- The same happens when the month's spending cap is reached during an investigation, or (chat only) after 45
  seconds, so the reply comes back before the web server gives up waiting (120 seconds).
- A model that still asks for tools after being told to answer is stopped and the answer fails, so a loop can't run
  on. In the worst case a diagnosis makes its budget plus two model calls (plus one for an answer that has to be
  asked for again).

Raising a budget from 0 is confirmed with the password and a two-factor code, because tools send more of a
server's data (its history, ports, firewall) to the provider. Every change is in the audit log.

## What is checked

**Before a call runs** (`tools.run`): the tool exists; the arguments match its schema exactly (an optional
argument left out counts as empty, since Claude gets the tools without `strict`, see [Incident agent](assistant.md#chat)); strings are
non-empty and at most 200 characters, lists at most 12 items; the server, container or check exists (otherwise the
model is told the names it may use); windows lie within the last 90 days.

**When a call fails**: a mistake the model can correct (a `ToolError`) goes back with the reason. Anything else is
logged on the server with the details, and the model only hears "The look-up failed", so no internal detail leaks
into the conversation.

**The diagnosis** (`check_answer` in `diagnosis.py`):

- A signal must cite at least one fact ID from the evidence pack or from a tool result in this investigation.
  Signals that don't are dropped and counted ("1 unsupported claim left out"); with none left, confidence is "low".
- A "metric stays below a threshold" condition must name a metric from the evidence pack, because only those can be
  watched by `verify.py`. A condition on a metric the agent found with a tool becomes one a person confirms.
- Fix commands are checked for destructive patterns (`safety.py`); a flagged step gets a warning and is never
  recommended.
- An answer that doesn't parse is asked for once more in the same conversation, without tools, so the look-ups
  aren't paid for twice; after that the report is saved as failed.

**The chat reply** must be a JSON object with a non-empty message. Its content is not checked against the data.

## What is stored, and who can see it

| Where | What | Who sees it |
|---|---|---|
| `agent_reports.body` → `investigation` | each call: tool, arguments (masked), reason, status, the masked result | nobody through the page; it stays in the database so a report can be traced |
| `GET /incidents/{id}` → `agent.report.steps` | each call described: `tool`, `status`, `reason`, `asked`, `found`, `from`, `to`, `at` | whoever sees the diagnosis ("Show diagnoses to users") |
| `chat_messages.steps` | the reply's steps, already described | the person whose conversation it is |
| `audit_logs`, event type `agent_tool_call` | each call: tool, reason, arguments (masked), whether it worked, under the incident ("Diagnosis of INC-0042") or the chat question | chat look-ups: the asker, through `GET /logs`; diagnosis look-ups belong to no user and are only in the database. Nobody can write this event type, and deleting a conversation keeps these entries |

The steps of a report are described when the page asks for them, from the stored trail, so better wording later
applies to old reports too. A chat reply's steps are described once, when it is saved, so the database doesn't keep
the raw results of chat look-ups. Reports and replies from before this feature have no steps, and the page shows
none.

## The tools, and how each step is described

What every tool returns is described in [Incident agent](assistant.md#what-the-agent-can-look-up). This table is
how `steps.py` turns each call into a step. `from`, `to` and `at` are sent apart, and the page shows them in the
viewer's clock ("3 Nov 10:00–12:00", "1 Nov 00:00 – now", "at 5 Nov 09:30").

| Tool | What it looked up | What it found (from these fields) | Example |
|---|---|---|---|
| `server_info` | Machine facts for *server* | `online`, `os`, `cpu_cores`, `memory_gb`, `updates_pending`, `reboot_required` | online, Debian 12, 4 cores, 16 GB memory, 4 updates pending |
| `host_metrics` | *metrics* on *server* | each series' `stats` (min, max, average), at most three | CPU 10–97 %, average 11.6 %; memory 50 % throughout |
| `list_containers` | Every container on *server* | count, running, not running (names), OOM kills | 2 containers, 1 running, not running: worker, 1 killed for running out of memory |
| `container_history` | Container *name* on *server* | peak memory against the limit, peak CPU, restarts in the window, time not running, recreated, changes | memory up to 110 MB of 256 MB, CPU up to 2 %, restarted 1× |
| `disk_usage` | Filesystems (or one) on *server*, last *n* days | `used_pct`, `growth_gb_per_day` ("not growing" under 0.05 GB a day), `full_in_days`, at most two | / 60 % used, +1 GB a day, full in about 40 days |
| `events` | Events on *server* (kinds) | `total`, and counts by kind among the events returned | 42 events; of the newest 30: 28 packages updated, 1 port opened, 1 OOM kill |
| `compare_to_baseline` | *metric* on *server* against its usual level for that hour | `value`, `usual_for_this_hour`, `difference_pct` | 10 % that hour, usually 5 % (+100 %) |
| `find_incidents` | Earlier incidents on *server* or every server, of kind | `total`, the newest incident | 1 incident, the newest INC-0032: Container nginx restarted |
| `fleet_snapshot` | Every server at one moment | count, offline, with open incidents, with failing checks | 2 servers, offline: db1, open incidents on db1, web1 |
| `check_results` | Availability check *name*, or checks run from *server* | `uptime_pct`, `down`, the commonest error, at most two | Shop 95.8 % up, down 5 times (HTTP 503) |
| `network_state` | Open ports and firewall on *server* | ports listening, reachable from other machines, enabled firewalls, changes in 7 days | 2 ports listening, 1 reachable from other machines, firewall: ufw-conf, 1 change in the last 7 days |

**When a step can't be described.** A failed call shows the reason it failed; a refused one "Not run: the look-up
budget was used up." A result that was cut to fit adds "(the result was shortened to fit)". A field that isn't a
number where a number belongs, or any other unexpected shape, gives "Done." instead of a guess. An entry whose
arguments can't be read shows the tool's name. Describing a step never fails the page.

## Limits

- **A step's line says little.** It picks the headline figures: the peak and average, not the shape of a series;
  three metrics of five; two filesystems; two checks. The model saw everything; the line is a reminder, not the data.
- **Event counts by kind** are of the events returned (the newest 30), which the line says when there are more.
- **The reason is the model's word.** It is shown in quotes and labelled as the agent's. It can be vague ("Checking
  memory"); the prompts ask for a specific question.
- **Chat replies aren't checked** against the data the way diagnoses are. They cite no fact IDs.
- **Look-ups cost tokens.** Each result (up to about 12 KB) goes with every later call of the same answer; a diagnosis
  using all 8 look-ups can cost several times one without (see [What it costs](assistant.md#what-it-costs)).
- **The window is shown in the browser's clock**, as elsewhere in the app; the agent itself works in UTC.

## Changing it

**A new tool.** Follow [Adding a tool](assistant.md#what-the-agent-can-look-up) for the tool itself; `reason` is
added to its schema automatically. Then add two functions to `steps.py`: one in `ASKED` (what it looked up, from
the arguments) and one in `FOUND` (one line from the result's fields). `test_every_tool_has_a_way_to_say_what_it_asked_and_found`
fails until both exist. Add a case to `test_each_tool_is_told_in_plain_words` in `tests/assistant/test_agent_steps.py`
with a real result from the tools' test database.

**Wording.** Change the functions in `steps.py`; reports pick it up at once, chat replies saved before keep their
old wording. Keep each line to facts the tool returned, never an interpretation.

**The budget, the deadline, the extra steps.** `settings.TOOL_BUDGETS`, `chat.DEADLINE_SECONDS` and
`investigate.EXTRA_STEPS`. The deadline must leave room for one more model call inside nginx's 120 seconds.

**The prompts.** `DIAGNOSIS_PROMPT.md` and `SYSTEM_PROMPT.md`, sections `[TOOLS]`. They tell the model to start
wide, cite tool IDs, give a specific reason, and never claim a look-up it didn't make.

The tests for all of this are listed in [TESTS.md](../TESTS.md): `test_agent_investigation.py` (the loop, the
budget, the audit log, the diagnosis and the chat), `test_agent_steps.py` (the steps), `test_agent_tools.py` (the
tools) and `src/frontend/tests/stepsLogic.test.js` (the window as the page shows it).
