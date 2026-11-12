# Report format
 
Write in the language the user writes in, and leave technical terms, function names and code
as they are. No introduction and no praise. Most important first.
 
## Structure
 
### 1. Summary
 
- The scope that was actually reviewed (one sentence) and the date/commit
  (`git rev-parse --short HEAD`, with a note if there are uncommitted changes).
- One line with the number of findings per level (e.g. "2 high, 3 medium, 4 low"), plus how
  many are `CONFIRMED` and how many `NEEDS VERIFICATION`.
- One sentence about the **most important** finding, or "I found no exploitable
  vulnerabilities in the reviewed scope" if there are none.
### 2. Findings table (mandatory)
 
All findings **must** be shown in a single table, **sorted by risk level, from highest to
lowest**: CRITICAL, HIGH, MEDIUM, LOW, INFO. Within the same level, `CONFIRMED` comes before
`NEEDS VERIFICATION`, then by exploitability (easier to exploit first). Do not group by area
(backend, infra, ...) and do not present findings as prose instead of a table.
 
| ID | Risk | Finding | Location | Attacker | Impact | Status | Fix (one sentence) |
|---|---|---|---|---|---|---|---|
| S-01 | CRITICAL | Short title | [main.py:123](src/backend/main.py#L123) | profile from the threat model | Access | CONFIRMED | What to change |
| S-02 | HIGH | ... | ... | ... | DoS | NEEDS VERIFICATION | ... |
 
Table rules:
 
- The "Impact" column states what the attacker **actually gains**, so it is immediately clear
  whether the finding leads to a breach. Use only these values, and for several of them
  separate with commas (most severe first): `Code execution` (code or commands on the
  backend, host or collector), `Access` (taking over an account, session or admin rights,
  bypassing authentication or 2FA), `Data theft` (reading data or secrets the attacker must
  not see), `Integrity` (planting, altering or silencing data and alerts), `DoS` (crashing or
  slowing down, locking accounts), `Reconnaissance` (reveals the existence of accounts,
  structure or configuration without direct harm). Rate impact in the worst realistic case,
  not the case ideal for the attacker; if it depends on a condition you did not verify, state
  it in the details.
- One row per finding; IDs `S-01`, `S-02`, ... are assigned **after sorting**, so the number
  matches the risk order.
- The "Location" column contains clickable markdown links (at most two; list the rest in the
  details).
- Cells are short (one line). Anything longer goes in the details below.
- If there are no findings at some level, do not add empty rows for it.
- The same table (same columns, same order) applies to the results of parallel agents after
  merging: the report is one table, not four.
### 3. Finding details
 
Same order as the table. Each finding has this shape (no extra headings):
 
```
### [HIGH] S-02 · Short, concrete title (CONFIRMED)
 
**Where:** [file.py:123](path/file.py#L123), called from [other.py:45](path/other.py#L45)
**Attacker:** who (e.g. a logged-in non-admin user / a VPN peer / a compromised collector)
 
**Problem:** what is wrong, in one or two sentences.
 
**Scenario:** step by step, what the attacker sends and what they get.
 
**Why existing protections do not help:** which controls you checked and why this bypasses them.
 
**Fix:** the concrete change (and the test that should cover it), with a short code snippet
if it helps.
 
**Proof:** the test/script you ran and the result, or "not run: <reason>".
```
 
The ID is the same as in the findings table.
For `NEEDS VERIFICATION`, instead of "Proof" write **What needs to be checked** (exactly what
and how).
 
### 4. Documentation claims the code does not support
 
Table: claim (quote + location in `SECURITY.md`/`docs/`) | what the code actually does |
file:line. Omit the section if there are no discrepancies.
 
### 5. Hardening (not a vulnerability)
 
Short bullets (one line each) for things that are not exploitable on their own but would
reduce risk. Do not mix them with findings.
 
### 6. Coverage
 
- **Reviewed:** list of files/areas you read in full.
- **Not reviewed, and why:** (e.g. dependencies without audit tools, live Traefik/OpenVPN
  configuration outside the repository, dynamic testing).
- **Assumptions** about the environment that the findings rely on.
### 7. Next step
 
One sentence: propose a fix order and ask whether the user wants you to start. Change nothing
until they reply.
 
## Style rules
 
- Findings are facts with evidence; mark speculation as `NEEDS VERIFICATION`.
- No duplicates: the same root cause in several places = one finding with a list of
  locations.
- Do not inflate severity for effect, but do not downplay it either (the user explicitly
  wants a strict assessment). When torn, state both levels and justify in one sentence.
- Do not print secret values; give only the location and type.
- No emoji.
 