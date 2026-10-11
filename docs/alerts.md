[← Documentation](README.md)

# Alerts and incidents

The values the rules look at are explained in [Metrics](metrics.md). Note that the load rule uses the 5-minute load per CPU core.

Rules run on every collector check-in. Each problem opens at most one incident
(per server, per rule and, for disks, per mount); while it lasts the incident is
updated instead of duplicated, and it resolves automatically once the rule
stops firing.

| Rule | Default | Severity |
|---|---|---|
| Disk usage, per mount (overridable per mount) | 85% / 95% | warning / critical |
| Inode usage, per mount | 85% / 95% | warning / critical |
| Memory usage | 90% / 95% | warning / critical |
| Swap usage | 50% | warning |
| 5-minute load per CPU core | 1.5× / 2× | warning / critical |
| Container restart loop | 3 restarts within 30 minutes | critical |
| Container healthcheck `unhealthy` | – | critical |
| Container exited with an error | within 60 minutes of exiting | warning |
| Container killed by the kernel for running out of memory | within 60 minutes | critical |
| Container stopped by a signal (exit 137 or 143, usually `docker stop`) | within 60 minutes | info |
| Container no longer present | stays open 60 minutes | info |
| Container running a different image build | stays open 60 minutes | info |
| Possible memory leak (steady climb over 12 hours) | 10 MB/h and 20% growth | warning |
| Docker unreachable (after it worked before) | 3 check-ins in a row | critical |
| Docker slow to list containers | over 2000 ms, 3 check-ins in a row | warning |
| Collector can't keep up with its check interval | gathering takes over 80% of the interval, 3 check-ins in a row | warning |
| New listener reachable from other machines | stays open 60 minutes | warning |
| Firewall configuration changed | stays open 60 minutes | info |
| Firewall protection reduced (ufw turned off, rules removed, a file gone) | stays open 60 minutes | warning |
| No internet (only if the check is on) | 3 check-ins in a row | warning |
| DNS failing (only if the check is on) | 3 check-ins in a row | warning |
| Server stopped reporting | 3 missed check-ins | critical |
| Host rebooted | stays open 60 minutes | info (critical after an unclean shutdown) |
| OOM killer fired | stays open 60 minutes | warning (critical at 3 or more) |
| Kernel fault flags (machine check, bad page, oops, warning, soft lockup) | – | warning, clears on reboot |
| Clock not synchronised | 3 check-ins in a row | warning |
| Reboot required | – | info |
| Server credentials replaced through the enrollment key | stays open 60 minutes | warning |

New listening ports and firewall changes are noticed the same way, by comparing with
what the previous check-in saw. The first report is a silent baseline, an unreadable
or empty report is treated as unknown (never as "everything closed"), and short-lived
programs using random high ports (loopback ones and all UDP ones) are ignored. A new
port bound only to the loopback address is recorded but doesn't open an incident; one
reachable from other machines does. Events are capped like the container ones.

Container exits, removals and image changes are noticed by comparing each check-in
with the previous one, matching containers **by name** (Compose recreates them with
new IDs). Nothing is reported on a server's first check-in or when Docker was
unreachable (an empty list is not proof that everything vanished), and churn is
capped so containers that come and go constantly can't flood the database. A leak is
only flagged when hourly memory averages rise along a straight line without a
restart; caches and warm-up can look similar, so treat it as a prompt to look.

Host rules (disk, inodes, memory, swap, load) open an incident only after
**3 check-ins in a row** breach the threshold, so a brief spike is ignored. An
open incident stays open while the latest value is still over the warning
threshold, and becomes critical only when the critical threshold is breached for
the same number of check-ins.

<diagram - Incident lifecycle for a host rule as a timeline of check-ins: value crosses the warning threshold → after 3 check-ins in a row a WARNING incident opens → stays over critical for 3 check-ins → escalates to CRITICAL → drops under warning → RESOLVED; show a single spike that opens nothing>

Admins can change every threshold, the number of check-ins in a row, how long
event incidents (reboots, OOM kills, container exits, removals and image changes)
stay open, the slow-Docker and slow-collection limits, the memory-leak window and sensitivity, and
**per-mount disk thresholds** under
**Admin → Alerts**. A per-mount override replaces the default disk thresholds for
that mount path on every server. Changes apply from each server's next check-in
and are recorded in the audit log.

<ui - Admin → Alerts: the Incident thresholds form with Warning / Critical fields per rule, Check-ins in a row, and the container restart loop row>

<ui - Active incidents section with a CRITICAL and a WARNING incident, each showing its server and start time>

Incidents are shown on the dashboard only; there are no push notifications yet.
(`ADMIN_ALERT_WEBHOOK_URL` is used by the assistant's `daily_checkup` only.)
