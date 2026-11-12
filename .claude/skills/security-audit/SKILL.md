---
name: security-audit
description: Thorough security review (security audit) of the whole codebase or part of it, reporting every vulnerability found with severity, location, attack scenario and fix. Use when the user asks for a security review, security audit, vulnerability hunt, pentest-style code review, "check the security" / "provjeri sigurnost", "security review" / "sigurnosni pregled", "are there any vulnerabilities" / "ima li propusta", or wants a component (backend, collector, docker proxy, nginx, compose, CI, frontend, assistant) reviewed for security. For a quick check of only the pending diff, the built-in security-review skill is enough.
argument-hint: "[full | backend | collector | infra | frontend | assistant | <path> | diff]"
---
 
# Security audit
 
Goal: find **real, exploitable** security vulnerabilities in this repository and report them
so the owner can fix them right away. The project is an open-source, self-hosted tool with
access to the Docker socket and the host, so the owner wants a **strict** assessment: do not
downplay risk for convenience, and do not round "probably OK" up to "OK".
 
This skill **does not change code** and does not commit. It only reviews and reports. Make
fixes only when the user explicitly asks for them.
 
## Arguments
 
`$ARGUMENTS` sets the scope. No argument = `full`.
 
| Argument | Scope |
|---|---|
| `full` | everything below, in order |
| `backend` | `src/backend/**` |
| `collector` | `src/collector/**` (collector, docker_proxy, system_helper) |
| `infra` | `docker/**`, `*compose*.yml`, `example.env`, `.github/**`, `.gitignore`, `.dockerignore` |
| `frontend` | `src/frontend/**` |
| `assistant` | Incident agent: `src/agent/**` (providers, tools, investigate, diagnosis, chat, masking, safety, jobs, worker, usage), `SYSTEM_PROMPT.md`, `DIAGNOSIS_PROMPT.md`, conversations in the backend |
| `<path>` | that path (file or directory) plus whatever calls it |
| `diff` | only uncommitted changes (`git diff` + `git diff --staged`), read in the context of the full files |
 
If the argument is ambiguous, pick the closest scope and state which one you chose at the
top of the report.
 
## Working rules (read before starting)
 
1. **Read files in full.** `grep` is for finding candidates, never for clearing code of
   suspicion. A function that looks safe often has a hole in its caller or in the order of
   checks. `src/backend/main.py` is ~1800 lines; read all of it, in chunks.
2. **Never print secrets.** `.env` exists locally. If you need to open it, print only the
   variable *names* and whether each value is empty/default, never the values. The same goes
   for tokens, keys and passwords found in code or history: in the report give the location
   and type, not the content (at most the first 4 characters).
3. **Local and harmless only.** Do not send requests to real systems, and do not start Docker
   containers or run scans. To confirm a vulnerability you may run the existing tests
   (`pytest`) and write a short script in the scratchpad directory that calls functions
   directly with hostile input. Nothing you run may touch a real database or the network.
4. **Verify claims made in the documentation.** `SECURITY.md` and `docs/` claim a lot ("rate
   limited", "single-use", "capped", "never follows symlinks"). For every claim in your scope,
   find the code that enforces it and check that it holds on *all* paths. A claim with no
   backing in code is a finding (the documented control does not exist), and so is a control
   that exists on only one path.
5. **Think like an attacker, not a linter.** For every input ask: who can send it, what is the
   worst it can contain, what does the system do with it, and what is the worst that can
   happen as a result. See attacker profiles in
   [references/threat-model.md](references/threat-model.md).
6. **First check whether something is already handled.** Before reporting a finding, find the
   existing control (middleware, dependency, nginx rule, test) and check whether it can be
   bypassed. A finding that ignores an existing protection is noise and erodes the user's
   trust.
7. **Verify every finding** (see below) and classify it as `CONFIRMED` or `NEEDS VERIFICATION`.
   Never report something you could not verify as confirmed.
## Procedure
 
### 1. Preparation (always)
 
- Read [references/threat-model.md](references/threat-model.md) and `SECURITY.md`.
- `git status` and `git log --oneline -15`: what changed recently; new attack surface is most
  often in fresh code.
- For `backend`/`full`: list all routes (`grep -nE "^@(app|router)\.(get|post|put|patch|delete)" src/backend/main.py`)
  and build a **route inventory**: method, path, who may call it (anonymous / any user /
  admin / collector with token), whether it is behind a CSRF/Origin check, whether it has a
  rate limit, what it reads/writes. A route without a clear answer in any column is a
  candidate finding.
- Read the dependency lists (`requirements*.txt`, `src/frontend/package.json`) for later
  review.
### 2. Review by area
 
For each area in scope, read the matching checklist and go through it **item by item**
against the actual code:
 
| Area | Checklist |
|---|---|
| Backend (API, auth, sessions, 2FA, database, rules, alerts) | [references/checklist-backend.md](references/checklist-backend.md) |
| Collector, docker proxy, system helper, LLM assistant | [references/checklist-collector-agent.md](references/checklist-collector-agent.md) |
| Docker, compose, nginx, Postgres, CI, repository, dependencies | [references/checklist-infra.md](references/checklist-infra.md) |
| Frontend | [references/checklist-frontend.md](references/checklist-frontend.md) |
 
The checklists are reminders, **not a boundary**. If you see something suspicious that is
not on the list, investigate it too.
 
**Parallelizing `full`:** the scope is large. If the `Agent` tool is available, launch four
agents in parallel (one per area in the table), give each a self-contained prompt from the
template below, then merge, deduplicate and **re-verify** everything they return yourself
before reporting. A finding reported by an agent is not confirmed until you have read it in
the code yourself. If you cannot use agents, go through the areas one by one yourself.
 
Agent template:
 
> You are doing a security review (read-only: change nothing, do not commit, do not print
> secret values) of the **<area>** area in the repository at <path>. First read
> `.claude/skills/security-audit/references/threat-model.md` and
> `.claude/skills/security-audit/references/<checklist>.md`, then read the **full** files of
> that area and go through the checklist item by item. For each finding return: title,
> file:line, what is wrong, a concrete attack scenario (who, what they send, what they get),
> which existing protection you checked and why it does not help, the proposed fix, and your
> confidence (high/medium/low). Also list which files you read and which you did not. Do not
> report theoretical issues without a path to exploitation.
 
### 3. Verifying each finding
 
For each candidate, work out (at least in your head, and briefly in the report):
 
1. **Source:** who controls the input and at what trust level (anonymous, user, admin,
   collector, VPN peer, data from the host)?
2. **Path:** through which functions does the input reach the dangerous point? Give files and
   lines.
3. **Protections on the path:** which checks, validations, limits and nginx rules exist? Why
   do they not help (or why do they help, in which case there is no finding)?
4. **Impact:** what does the attacker gain (reading data, changing state, account takeover,
   code execution, DoS, escalation to the host)?
5. **Proof:** where possible, confirm with a test or a short local script. If you cannot, the
   finding is `NEEDS VERIFICATION`, with a description of exactly what needs to be checked.
Discard a candidate if: there is no path to exploitation without prior admin access that
already grants the same impact, it is protected by something you have checked, or it is a
purely stylistic recommendation. Such items, if valid, go into the "Hardening" section as a
short bullet, not among the findings.
 
### 4. Severity
 
Rate by impact **in this system** (access to the host and the Docker API is the most
expensive thing to defend), and prefer the stricter rating when torn between two levels:
 
| Level | Meaning | Examples |
|---|---|---|
| **CRITICAL** | Remote, with no or almost no authentication, leads to dashboard takeover, code execution or escape to the host | authentication bypass, hijacking the first admin registration, RCE, access to the Docker socket from the public side |
| **HIGH** | Authenticated or conditional attack with large impact | user→admin escalation, 2FA bypass, IDOR on other users' data, SQL injection, stored XSS in admin context, a compromised collector affecting the dashboard or host |
| **MEDIUM** | Genuinely exploitable, limited impact or requires a special condition | CSRF on a less sensitive action, bypassable rate limit, information leak, single-request DoS |
| **LOW** | Weakness with no clear path to harm on its own | missing header, user enumeration, weak default parameter |
| **INFO** | Observation or hardening | a control that works but is fragile |
 
### 5. Report
 
Format and order: [references/report-format.md](references/report-format.md). In short:
summary (number of findings per level, the most important one in a single line), **a single
table of all findings sorted by risk from highest to lowest** (CRITICAL, HIGH, MEDIUM, LOW,
INFO), finding details in the same order, documentation claims the code does not support,
hardening, and coverage (what was reviewed, what was not, and why).
 
**The table is mandatory.** Never present findings only as prose or grouped by area. Merge
the results of parallel agents into one table and sort them together.
 
Reference files as markdown links (`[main.py:412](src/backend/main.py#L412)`) so they are
clickable in the editor.
 
Print the report in the conversation. **Do not save it to a file** (e.g. `docs/`) unless the
user asks, because it may contain undisclosed vulnerabilities.
 
At the end, offer the next step in one sentence (e.g. fix the findings from most to least
severe, with a test for each) and **wait for a reply**.
 
## What this skill is not
 
- It is not dynamic testing: it does not scan a live system, fuzz, or run exploits.
- It is not a replacement for `pip-audit` / `npm audit` or an image scanner; if they are
  available and the user approves network access, run them, and if not, **explicitly list**
  dependencies as not reviewed in the coverage section (dependencies are not assessed "from
  memory", because version numbers alone are not a reliable source).
- It does not certify that the system is secure. A "no findings" report may only say "I found
  no exploitable vulnerabilities in the reviewed scope", together with a list of what was
  reviewed.