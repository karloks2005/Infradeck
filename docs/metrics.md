[← Documentation](README.md)

# Metrics: what every number means

This page explains each value the dashboard and the [Statistics](statistics.md) page show: what it
is, where it comes from, how it is calculated, and what it does **not** tell you. Where two values are
easy to mix up (CPU % and load, memory "used" and "free") the difference is spelled out.

- [How values are collected](#how-values-are-collected)
- [Host metrics](#host-metrics): CPU, load, memory, swap, disk, inodes, network, uptime
- [Container metrics](#container-metrics)
- [System state](#system-state)
- [Network checks](#network-checks)
- [Collector self-metrics](#collector-self-metrics)
- [How history is stored](#how-history-is-stored): what "average" and "highest" mean over a week
- [Derived statistics](#derived-statistics): availability, distributions, busy hours, "full in about N days"
- [Units](#units)

## How values are collected

Each server's collector reads the host's `/proc` and filesystem and the Docker API (through the
read-only proxy) once per **check-in**. The default interval is 60 seconds and it can be set per server
between 15 seconds and 15 minutes. Everything on this page is therefore a **snapshot taken at a
check-in**, not a continuous recording. Between two check-ins nothing is measured.

- The time stored with a check-in is when the dashboard received it (UTC), not the collector's clock.
- A value the collector could not read is stored as *unknown*, never as zero. Unknown values are left
  out of averages, never raise alerts, and show as "–" or *unknown* in the interface.
- The backend validates every number it receives (type, range, length) before storing it.

## Host metrics

| Metric | What it means | How it is measured | Range |
|---|---|---|---|
| **CPU usage** (`cpu_utilization_pct`) | The share of CPU time the processors spent working, across **all cores** together. 100% means every core was fully busy. | Two readings of the aggregate line in `/proc/stat`, **about 0.15 seconds apart**, at each check-in. Time spent idle, and waiting for I/O, counts as not busy. | 0 – 100 % |
| **Load average** (`load_1m`, `load_5m`, `load_15m`) | How many tasks, on average, wanted to run or were stuck waiting (see below). It measures **demand**, not utilisation. | The kernel's own load average, read with `getloadavg()`: averaged by the kernel over the last 1, 5 and 15 minutes. | 0 and up, not capped |
| **Memory used** (`ram_pct`, `ram_used_gb`) | How much RAM programs are actually holding. | `MemTotal − MemAvailable` from `/proc/meminfo`, as a share of `MemTotal`. File caches the kernel can free at once count as **available**, so a Linux machine showing "full" RAM in other tools is usually fine here. | 0 – 100 % |
| **Swap used** (`swap_pct`) | How much of the swap space is in use. | `SwapTotal − SwapFree` as a share of `SwapTotal`. A host without swap reports 0. | 0 – 100 % |
| **Root disk used** (`disk_usage_pct`) | How full the root filesystem is. | `statvfs` of the host root, calculated like `df`: used ÷ (used + space available to non-root users). Reserved blocks are not counted as free. | 0 – 100 % |
| **Inodes used** (`disk_inodes_pct`) | How many files the root filesystem can still hold. A disk can have free space and no free inodes. | `statvfs` file counts: (total − free) ÷ total. Unknown on filesystems that don't report inodes. | 0 – 100 % |
| **Network sent / received** (`net_bytes_sent_mb`, `net_bytes_recv_mb`) | **Running totals** of bytes sent and received since the machine booted. They are not speeds. | Sum over the host's **physical** interfaces from `/proc/1/net/dev`. Virtual interfaces (loopback, Docker bridges, `veth`, VPN tunnels) are left out because their traffic is already counted on the physical one. | MiB, grows until reboot |
| **Uptime** (`system_uptime_seconds`) | Seconds since boot. | `/proc/uptime`. | seconds |

### CPU % and load are different things

| | CPU usage % | Load average |
|---|---|---|
| Question it answers | "How busy are the processors?" | "How many tasks are waiting for them (or for I/O)?" |
| Scale | 0–100% of the whole machine | A count of tasks; compare with the **number of cores** |
| Counts waiting tasks | No | **Yes**: tasks that are runnable, and tasks in uninterruptible sleep (usually waiting for disk or NFS) |
| Time window | A ~0.15 s snapshot per check-in | A kernel-maintained average over 1, 5 or 15 minutes |

How to read them together:

- **Load around the number of cores** is a fully used machine; **well above it** means tasks are queueing.
  Load 4 on a 4-core machine is "full"; the same 4 on a 1-core machine is badly overloaded. That is why
  the alert rule divides by the core count.
- **Low CPU %, high load** usually means tasks are blocked on disk, network storage or something else
  slow. The processors are idle while work waits.
- **High CPU %, load near the core count** is a busy machine that is keeping up.

Which load the interface shows: the **Statistics** chart draws the **1-minute** load as a raw number (not
divided by cores), and the **alert rule** uses the **5-minute** load **per core** (warning at 1.5, critical
at 2.0 by default). The 1-minute figure reacts quickly and is noisy; the 5-minute figure is steadier.

### Reading the CPU figure honestly

Because CPU % is measured over about 0.15 seconds at each check-in, one value is a snapshot of that
moment. With a 60-second interval, each value represents roughly a quarter of a percent of the time.
A busy period that starts and ends between two check-ins can be missed completely, while a spike that
happens to coincide with a check-in is recorded in full. Averages over many check-ins (an hour, a day)
are a good picture of typical usage; a single point is not. The **load average**, which the kernel keeps
continuously, does not have this blind spot, so compare the two when something looks odd. (Measuring
CPU over the whole interval between check-ins would remove the limitation; it is on the list of things
to improve.)

### Filesystems

Disk and inode usage are reported **per filesystem**, in addition to the root figures above
(`mount_metrics`):

| Value | Meaning |
|---|---|
| `mount` | The path where the filesystem is mounted, for example `/` or `/media/data`. |
| `fs_type` | `ext4`, `xfs`, `btrfs`, `zfs`, `nfs`, … |
| `total_gb`, `used_gb` | Size and amount used, in GiB. |
| `used_pct` | Calculated like `df` (used ÷ (used + available to non-root)), the same as the root disk figure. |
| `inodes_pct` | Inodes in use, where the filesystem reports them. |

Included: real block devices and the network or pool filesystems `zfs`, `nfs`, `nfs4`, `cifs`, `smb3`,
`fuse.mergerfs` and `fuse.sshfs`. Left out: `tmpfs`, `devtmpfs`, `overlay`, `squashfs`, and anything under
`/proc`, `/sys`, `/dev`, `/run`, `/snap`, `/var/snap`, `/var/lib/docker`, `/var/lib/containerd` and
`/var/lib/kubelet`. A device mounted in several places is reported once. A disk mounted after the
collector started appears after the collector restarts.

## Container metrics

Taken from Docker's stats and inspect output for every container at each check-in. Only containers that
are **running** report usage figures; for a stopped container they are unknown, not zero.

| Metric | What it means | How it is measured |
|---|---|---|
| **State** (`status`) | `running`, `exited`, `restarting`, `paused`, `created`, `dead`. | Docker inspect. |
| **Health** (`healthcheck_status`) | `healthy`, `unhealthy`, `starting`, or none if the image defines no healthcheck. | Docker inspect. |
| **Restarts** (`restart_count`) | How many times Docker has restarted it. | Docker's counter; it starts again at 0 when the container is recreated. |
| **CPU %** (`cpu_pct`) | How much CPU the container used, where **100% is one full core**. A container using three cores' worth shows about 300%. | The change in the container's CPU time divided by the change in the whole machine's CPU time since the **previous check-in**, times the number of cores. The first check-in after the collector starts has no figure (unknown), and a container that used no CPU in the interval shows none. |
| **Memory used** (`memory_used_mb`) | Memory the container is really holding. | Docker's usage minus its reclaimable page cache (`inactive_file`), the same way `docker stats` does it. |
| **Memory limit / %** (`memory_limit_mb`, `memory_pct`) | The limit set for the container and how much of it is used. | No limit set means no percentage. |
| **Network in / out** (`net_rx_mb`, `net_tx_mb`) | Running totals since the container started. Not speeds. | Sum over the container's interfaces. |
| **Disk read / write** (`block_read_mb`, `block_write_mb`) | Running totals since the container started. | Docker's block I/O counters. |
| **Processes** (`pids`) | How many processes are running inside it. | Docker's pids count. |
| **Exit code, out of memory, finished at** | How a stopped container ended. Exit 137 usually means it was killed (by `docker stop`/`kill`, or by the out-of-memory killer). | Docker inspect. |
| **Image ID** | The exact image build it is running, used to notice a changed image. | Docker inspect. |

## System state

Read from what the kernel exposes, without reading any log files.

| Value | Meaning | Source |
|---|---|---|
| **Boot ID** | A new value between two check-ins means the machine rebooted. | `/proc/sys/kernel/random/boot_id` |
| **OOM kills** | How many processes the kernel's out-of-memory killer has stopped since boot. A rising counter records an event. | `oom_kill` in `/proc/vmstat` |
| **Kernel fault flags** | Only fault bits count (machine check, bad page, oops, warning, soft lockup). Flags for proprietary or out-of-tree modules are ignored. They stay set until reboot. | `/proc/sys/kernel/tainted` |
| **Clock synchronised** | Whether a time service is keeping the clock disciplined. This is the sync state, **not** a measured offset. | `adjtimex` (read-only), or systemd-timesyncd's marker |
| **Reboot required** | Updates are waiting for a restart. Debian and Ubuntu only; other systems show *unknown*. | `/run/reboot-required` |
| **Last shutdown** (clean / unclean) | Whether the previous shutdown was orderly. Needs the optional [system helper](dashboard.md#optional-system-helper). | `wtmp` via the helper |
| **Kernel error lines, OOM-killed process name** | Informational. Need the system helper. | Kernel log via the helper |

## Network checks

Passive, and only what the collector can read without privileges.

| Value | Meaning |
|---|---|
| **Listening ports** | Which TCP/UDP ports the host listens on, and whether each is reachable from other machines or only from the machine itself (**scope**). Protocol, port and scope only, with no process names. Short-lived programs on random high ports are ignored. |
| **Firewall** | A hash and a rule count of the known firewall configuration files, plus whether `ufw` is enabled. Never the rules themselves, and not the live ruleset. A changed hash raises an informational incident. |
| **Internet reachability** | Off unless you set `HOMELAB_CONNECTIVITY_TARGETS` on that collector. `ok` (all targets reachable), `degraded` (some), `dns_failed` (names don't resolve and nothing else is wrong) or `down`; plus the median connection time in milliseconds. |

## Collector self-metrics

| Metric | Meaning |
|---|---|
| **Docker response time** (`docker_latency_ms`) | How long it took to list the containers. A rising value means the Docker daemon is struggling. Unknown when Docker isn't reachable. |
| **Docker status** | `ok`, `unreachable` or `not_configured`, with a coarse reason (timeout, refused, missing, denied, error). Only a host that had Docker working before can raise "Docker unreachable". |
| **Collection time** (`collect_duration_ms`) | How long the collector took to gather everything for one check-in. If it approaches the check interval the collector can't keep up, and a warning opens (80% by default). |

## How history is stored

The check-in snapshots are kept for the retention period (30 days by default). A background job also
summarises them into one row per **hour, day, week (Monday to Sunday) and month**, all in UTC, for
every server, filesystem and container. Each row stores, for every metric:

- the **average** over the check-ins in that period,
- the **highest value** (maximum) seen in a check-in,
- the number of check-ins that reported it (so averages stay exact when some check-ins lack a value).

What that means on the Statistics page:

- A line is the **average of the snapshots** in each bucket. For CPU % that is the average of
  ~0.15-second snapshots, as explained above.
- The lighter band above the line reaches the **highest snapshot** in the bucket, so a spike that a
  check-in caught stays visible even in a yearly view. It is the highest *recorded* value, not
  necessarily the highest value that ever occurred.
- A period with no check-ins has no point: a gap stays a gap and is never filled in.
- Longer ranges use coarser rows, so a point can represent an hour, a day, a week or a month. The note
  under the cards says which. Day, week and month boundaries are in UTC.

See [Database schema](database.md) for the tables and [Statistics](statistics.md) for which range uses which
level.

## Derived statistics

| Figure | How it is worked out | Caveats |
|---|---|---|
| **Availability** | The share of the check-ins a server should have made in the range that actually arrived: check-ins received ÷ (time in range ÷ check interval), capped at 100%. The range starts at the server's first check-in. | Approximate: it assumes the server's *current* check interval for the whole range. A server that never reported is unknown, not 0%. |
| **Incidents opened / open now** | Counted from the incident log by when each incident opened, grouped by severity and by kind of problem. | Counting is capped at 20,000 incidents per request; the answer says when it was cut. |
| **Mean time to resolve** | The average time between an incident opening and resolving, over incidents resolved in the range. | Incidents still open are not included. |
| **Distribution (histogram)** | The share of the time a metric's average was in each 10% band, weighted by the number of check-ins behind each point. | Worked out in the browser from the points shown, so for long ranges it is a distribution of *averages* and looks calmer than the raw data. |
| **Busy hours (heatmap)** | The average of CPU or memory for each hour of the day and weekday over the last 30 days, in your time zone (a fixed offset; daylight-saving changes inside the 30 days are ignored). | Needs a few days of history to fill in. Independent of the selected range. |
| **"Full in about N days"** | A straight line fitted through a filesystem's usage over the range, extended to 100%. | Shown only for a **clear** rising trend: at least 6 points spanning at least a day, rising, and the line must fit the data reasonably (r² ≥ 0.5). Otherwise: "No clear growth trend". It assumes growth continues at the same rate; a cleanup or a one-off copy changes it. |
| **Top containers / servers** | The containers using the most memory or CPU, and servers by disk usage, from the latest check-in. | Current values, not an average over the range. |

## Units

- **GB and GiB.** Sizes labelled "GB" are **GiB** (1 GB = 1024³ bytes), the unit `df -h` uses. "MB" values are MiB (1024² bytes).
- **Percentages** are rounded to two decimals when stored and usually shown with one.
- **Times** are UTC when stored. Charts for up to a day or two use your browser's clock; day, week and month points are labelled in UTC.
