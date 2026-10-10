[← Documentation](README.md)

# Database schema

Tables are created on backend startup (`Base.metadata.create_all`), and new
columns are added with `ALTER TABLE ... IF NOT EXISTS` in the startup hook in
`src/backend/main.py`. Each check-in stores one row in `host_metrics` (which also
carries the boot ID, OOM counter, kernel flags, clock state and last shutdown), one
per filesystem in `mount_metrics` and one per container in `container_metrics` (which
also records the exit code, out-of-memory flag, finish time and image ID), all with the
same timestamp. `system_events` holds reboots, OOM kills, kernel messages, container
changes (appeared, removed, image changed) and network changes (ports opened or closed,
firewall changes); `listener_states` and `firewall_states` hold what was last seen, `join_tokens` the hashed single-use tokens, `recovery_codes` the hashed two-factor
recovery codes (the encrypted two-factor secret lives on the `users` row), and per-mount disk
thresholds live in `app_settings`. `docker/postgres/init/01_create_users.sql` initialises
the `users` table on a fresh volume.

**Summarised history.** A background job (it checks every minute and does nothing when it is up to date) turns the check-in
samples into `host_rollups`, `mount_rollups` and `container_rollups`: one row per server, mount or container
per hour, day, week (Monday to Sunday) and month, all in UTC, with the average and maximum of each metric.
Each tier is built from the finer one when its period ends, a period without check-ins gets no row, and the
purge never deletes samples that have not been summarised yet. The Statistics page reads them (see [Statistics](statistics.md)). They are
not purged yet; a retention setting for each level is planned.

The metrics tables carry only the indexes their queries use: `time` (for the hourly purge) and
`(server_id, time)`, or `(server_id, container_id, time)` for containers. Older databases are
migrated at start: the new indexes are created and the redundant single-column ones are dropped,
which can be repeated safely. Creating an index briefly blocks writes to that table (a few
seconds for a table of millions of rows); on a very large install, create the indexes yourself
first with `CREATE INDEX CONCURRENTLY IF NOT EXISTS <name> ON <table> (<columns>)` using the names
in `src/backend/models.py`, and the start-up migration will skip them.
