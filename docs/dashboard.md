[← Documentation](README.md)

# The dashboard and system health

For what each value means and how it is measured, see [Metrics](metrics.md).

- **This machine** (top): live CPU, memory and root disk charts for the machine
  running the dashboard, loaded from the last 30 minutes of stored metrics.
- **Fleet**: one card per server, with the dashboard's own machine first. Click a
  server's name to collapse its card to a single line (status, CPU, RAM, fullest
  disk, incident count); **Collapse all** / **Expand all** appear with more than
  one server. Each card shows a **Storage** bar per filesystem, colored by the
  alert thresholds (including any per-mount overrides), a **System** row and a
  **Containers** list. Both lists start closed. A filter box appears for servers
  with more than 10 containers.
- **Network** row: internet reachability, how many ports the server listens on and how
  many are exposed, and the firewall state, opening into the listener list and the last
  7 days of port and firewall changes.
- **System** row: one line of status chips (when it booted, last shutdown, clock,
  OOM kills in the last 24 hours, kernel, pending updates) that opens into the
  last 7 days of reboots, OOM kills and kernel messages. A chip that says
  *unknown* means the collector couldn't read that value on this host, not that
  anything is wrong.
- **Active incidents**: open incidents, most severe first, with the server and
  when they started.

Which cards are collapsed is remembered per browser; the System and Containers
lists always start closed.

<ui - One expanded server card: CPU/RAM/Disk/Load tiles, Storage bars for several mounts (one amber or red), the System row open with a reboot and an OOM kill in its events, and the Containers list open with an unhealthy container and one container's details expanded (stat tiles and CPU/memory charts)>

<ui - Fleet section with every card collapsed to one line (status dot, name, This machine and incident badges, CPU/RAM/fullest disk) and the Collapse all / Expand all button>

## System health

Collectors report what the kernel exposes, without reading any logs:

| Check | Source | Notes |
|---|---|---|
| Reboots | `/proc/sys/kernel/random/boot_id` | A new boot ID between check-ins records a **Reboot** event. |
| OOM kills | `oom_kill` in `/proc/vmstat` | A rising counter records an **OOM kill** event. |
| Kernel faults | `/proc/sys/kernel/tainted` | Only fault bits count (machine check, bad page, oops, warning, soft lockup); proprietary or out-of-tree module flags are ignored. |
| Clock sync | `adjtimex` (read-only), else systemd-timesyncd's marker | Sync state only, not a measured offset. |
| Reboot required | `/run/reboot-required` | Debian and Ubuntu only; other distros show *unknown*. |

### Optional system helper

Process names, kernel error lines and crash-versus-clean shutdown detection need
the kernel log and the login records, which the collector deliberately can't
read. A separate container does:

```bash
docker compose --profile system-events up -d     # add -f docker-compose.server.yml on a server
```

It runs with a single capability (`CAP_SYSLOG`), no network and a read-only
filesystem, sees only `/var/log/wtmp` (read-only), and writes a sanitised JSON
summary that the collector reads from a shared volume. With it you also get:

- the OOM-killed process name on each **OOM kill** event,
- recent kernel error lines in the **System** row (informational, they don't open incidents),
- **Last shutdown: clean / unclean**, and a **critical** incident after an unclean reboot.

Without it those show as *unknown* and everything else keeps working. Read
[SECURITY.md](../SECURITY.md) before enabling it; the helper is about 200 lines of
standard-library Python.

<ui - The System row opened on a server with the helper running: status chips on top, then a list with an OOM kill (process name) and kernel error lines>
