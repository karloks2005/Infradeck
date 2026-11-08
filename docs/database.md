[← Documentation](README.md)

# Database schema

The backend creates and upgrades the schema itself at startup with Alembic migrations
(see [Migrations](#migrations)). Each check-in stores one row in `host_metrics` (which also
carries the boot ID, OOM counter, kernel flags, clock state and last shutdown), one
per filesystem in `mount_metrics` and one per container in `container_metrics` (which
also records the exit code, out-of-memory flag, finish time and image ID), all with the
same timestamp. `system_events` holds reboots, OOM kills, kernel messages, container
changes (appeared, removed, image changed) and network changes (ports opened or closed,
firewall changes); `listener_states` and `firewall_states` hold what was last seen, `join_tokens` the hashed single-use tokens, `recovery_codes` the hashed two-factor
recovery codes (the encrypted two-factor secret lives on the `users` row), and per-mount disk
thresholds live in `app_settings`. Every table is created and changed by the migrations in
`src/backend/migrations/` (see Migrations below).

**The agent's spending.** `agent_runs` has one row per call to the language model: what it was for (`chat`, later
`diagnosis`), whether it worked, the model, input and output tokens, the estimated cost in USD, how long it took, and
who asked about which incident. The month's cost and the budget check add these rows up; a call made before its model
had a price is priced with the current table when it is counted. The prices, the monthly budget and the daily chat
limit are rows in `app_settings` (`agent_prices`, `agent_monthly_cap_usd`, `agent_daily_chat_limit`).

**The agent's diagnoses.** `agent_reports` holds the diagnoses, one row per version per incident: the summary,
how confident the agent is, the checked answer (signals with the evidence they cite, fix steps, how to verify) and
the masked evidence pack it was given, so every report can be traced to its data. A report the model couldn't
produce has status `failed` and the reason. Its body also keeps `investigation`, every tool call the agent made
(tool, masked arguments, its reason, status and masked result); the page only ever gets these described in plain
words ([How the agent investigates](agent-investigation.md#what-is-stored-and-who-can-see-it)). A diagnosis reused from an earlier incident of the same rule has trigger
`reused` and names that incident in its body. Rows are deleted with their incident.

`incident_alerts.reopen_after` is set when the agent resolved an incident because its fix held: until then the same
rule on that server opens no new incident (unless the condition breaks again, which clears it).
`users.agent_chat_enabled` is the per-user chat switch used when chat access is "per user".
`chat_messages.steps` (revision `0003`) holds an agent reply's look-ups, already described in plain words, as JSON;
it is empty for questions and for replies from before it.

**Summarised history.** A background job (it checks every minute and does nothing when it is up to date) turns the check-in
samples into `host_rollups`, `mount_rollups` and `container_rollups`: one row per server, mount or container
per hour, day, week (Monday to Sunday) and month, all in UTC, with the average and maximum of each metric.
Each tier is built from the finer one when its period ends, a period without check-ins gets no row, and the
purge never deletes samples that have not been summarised yet. The Statistics page reads them (see [Statistics](statistics.md)).

**Retention** (`src/backend/retention.py`, hourly): check-in samples and `check_results` are kept 7 days, and
only deleted once summarised. Hourly rollups, `check_rollups` and `system_events` are kept for the
`history_retention_days` setting (365 by default, 730, or 0 for forever); an hourly rollup is only deleted
once its day is summarised. Daily, weekly and monthly rollups are never deleted. Deletes run in chunks of
10,000 rows.

The metrics tables carry only the indexes their queries use: `time` (for the hourly purge) and
`(server_id, time)`, or `(server_id, container_id, time)` for containers. A database from before
migrations gets them from the baseline migration, which also drops the redundant single-column
ones. Creating an index briefly blocks writes to that table (a few seconds for a table of millions
of rows); on a very large install, create the indexes yourself first with
`CREATE INDEX CONCURRENTLY IF NOT EXISTS <name> ON <table> (<columns>)` using the names in
`src/backend/models.py`, and the migration will skip them.

## Migrations

The schema is versioned with [Alembic](https://alembic.sqlalchemy.org/). The revisions live in
`src/backend/migrations/versions/`, and the database records the one it is at in the `alembic_version`
table. At every start the backend runs the revisions the database doesn't have yet, in order, before it
serves anything (`src/backend/migrate.py`):

- On PostgreSQL an advisory lock makes a second process wait instead of migrating at the same time.
- Each revision runs in its own transaction. If one fails, the database stays at the revision before it and
  the backend refuses to start; the log says which revision failed.
- The log has one line per revision applied (`Database migration applied: 0002 (...)`).

The first revision, `0001_baseline`, is the schema as it was when migrations were introduced. A new database
gets every table from it. A database from before migrations (made by the backend's old start-up code) keeps
its tables and data: the baseline creates only the tables it lacks and brings the others up to date, so
upgrading needs no manual step.

How to add a revision is in [Development](development.md#database-migrations).

