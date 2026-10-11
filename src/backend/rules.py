"""Alert rules evaluated on every collector check-in, and the thresholds they use.

Rules only decide *what is firing*; ``alerts.sync_alerts`` turns that into
opened, refreshed and resolved alerts.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from .alerts import CONTAINER_RULE_PREFIX, CRITICAL, INFO, WARNING
from .models import ContainerMetric, FirewallState, HostMetric, ListenerState, MountMetric, SystemEvent

HOST_RULE_PREFIX = "host:"
SYSTEM_RULE_PREFIX = "system:"
TREND_RULE_PREFIX = "trend:"
NETWORK_RULE_PREFIX = "network:"

DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "disk_warning_pct": 85, "disk_critical_pct": 95,
    "inodes_warning_pct": 85, "inodes_critical_pct": 95,
    "memory_warning_pct": 90, "memory_critical_pct": 95,
    "swap_warning_pct": 50,
    "load_warning_per_core": 1.5, "load_critical_per_core": 2.0,
    # How many check-ins in a row a host metric must breach before an alert opens.
    "sustained_checks": 3,
    "restart_loop_count": 3, "restart_loop_window_minutes": 30,
    # How long a reboot, OOM kill, container exit, removal or image change keeps its incident open after it happened.
    "event_window_minutes": 60,
    # Listing containers slower than this (sustained) means the Docker daemon is struggling.
    "docker_slow_ms": 2000,
    # Collecting a check-in taking more than this share of the server's check interval (sustained) means it is falling behind.
    "collect_slow_pct": 80,
    # A container whose memory climbs steadily is flagged as a possible leak; see memory_trend_rules.
    "leak_window_hours": 12, "leak_min_mb_per_hour": 10, "leak_min_growth_pct": 20,
}

_LIMITS: dict[str, tuple[float, float]] = {
    **{key: (1, 100) for key in DEFAULT_THRESHOLDS if key.endswith("_pct")},
    "load_warning_per_core": (0.1, 50), "load_critical_per_core": (0.1, 50),
    "sustained_checks": (1, 20),
    "restart_loop_count": (1, 100), "restart_loop_window_minutes": (5, 1440),
    "event_window_minutes": (5, 1440),
    "docker_slow_ms": (100, 60000), "leak_window_hours": (6, 72), "leak_min_mb_per_hour": (1, 10000),
}
_INTEGER_KEYS = {"sustained_checks", "restart_loop_count", "restart_loop_window_minutes", "event_window_minutes", "docker_slow_ms", "leak_window_hours"}


def validate_thresholds(values: dict, current: dict) -> dict:
    """Merge ``values`` over ``current`` and validate the result. Raises ValueError with a readable message."""
    unknown = set(values) - DEFAULT_THRESHOLDS.keys()
    if unknown:
        raise ValueError(f"Unknown threshold: {', '.join(sorted(unknown))}.")
    merged = {**current, **values}
    for key, value in merged.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number.")
        if key in _INTEGER_KEYS and value != int(value):
            raise ValueError(f"{key} must be a whole number.")
        low, high = _LIMITS[key]
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low:g} and {high:g}.")
        merged[key] = int(value) if key in _INTEGER_KEYS else value
    for metric in ("disk", "inodes", "memory"):
        if merged[f"{metric}_warning_pct"] >= merged[f"{metric}_critical_pct"]:
            raise ValueError(f"The {metric} warning threshold must be below its critical threshold.")
    if merged["load_warning_per_core"] >= merged["load_critical_per_core"]:
        raise ValueError("The load warning threshold must be below its critical threshold.")
    return merged


def validate_mount_thresholds(value) -> dict[str, dict[str, float]]:
    """Per-mount disk thresholds as {mount: {"disk_warning_pct", "disk_critical_pct"}}. Raises ValueError."""
    if not isinstance(value, dict):
        raise ValueError("Mount thresholds must be an object keyed by mount path.")
    cleaned: dict[str, dict[str, float]] = {}
    for mount, pair in value.items():
        if not isinstance(mount, str) or not mount.startswith("/") or len(mount) > 255:
            raise ValueError("Each mount must be an absolute path such as /media/data.")
        if not isinstance(pair, dict) or set(pair) != {"disk_warning_pct", "disk_critical_pct"}:
            raise ValueError(f"{mount} needs a warning and a critical threshold.")
        for key, number in pair.items():
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not 1 <= number <= 100:
                raise ValueError(f"{mount}: {key} must be a number between 1 and 100.")
        if pair["disk_warning_pct"] >= pair["disk_critical_pct"]:
            raise ValueError(f"{mount}: the warning threshold must be below its critical threshold.")
        cleaned[mount] = {key: pair[key] for key in ("disk_warning_pct", "disk_critical_pct")}
    return cleaned


def _level(value: float | None, warning: float, critical: float | None) -> int:
    """0 = fine, 1 = warning, 2 = critical."""
    if value is None:
        return 0
    if critical is not None and value >= critical:
        return 2
    return 1 if value >= warning else 0


def _fire_level(values: list, warning: float, critical: float | None, sustained: int, active: bool) -> int:
    """Alert level for a metric given its recent values, newest first."""
    levels = [_level(value, warning, critical) for value in values]
    # Opening an alert, or escalating it to critical, needs every recent check-in to breach;
    # an open alert stays open while the latest check-in still breaches.
    sustained_level = min(levels) if len(levels) >= sustained else 0
    return max(sustained_level, min(levels[0], 1)) if active and levels else sustained_level


def _escape(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


def _gb(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value / 1024:.1f} TB" if value >= 1024 else f"{value:.0f} GB"


def host_rules(db, server, thresholds: dict, active_keys: set[str], mount_overrides: dict | None = None) -> dict[str, tuple[str, str, str]]:
    """Evaluate the host rules against the server's most recent check-ins."""
    sustained = thresholds["sustained_checks"]
    samples = db.query(HostMetric).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc(), HostMetric.id.desc()).limit(sustained).all()
    if not samples:
        return {}
    # Titles are format strings for the latest value, so escape braces in names we put into them.
    host = _escape(server.hostname)
    cores = server.cpu_cores or None

    def load_per_core(sample):
        return sample.load_5m / cores if cores and sample.load_5m is not None else None

    # (rule key, values newest first, warning, critical, title, description), with {value} filled in from the latest value.
    checks = [
        (f"{HOST_RULE_PREFIX}memory", [s.ram_pct for s in samples], thresholds["memory_warning_pct"], thresholds["memory_critical_pct"],
         f"Memory usage on {host} is {{value:.0f}}%", "{value:.1f}% of RAM is in use. Check the containers and processes using the most memory."),
        (f"{HOST_RULE_PREFIX}swap", [s.swap_pct for s in samples], thresholds["swap_warning_pct"], None,
         f"Swap usage on {host} is {{value:.0f}}%", "{value:.1f}% of swap has been in use for several check-ins, an early sign of memory pressure."),
        (f"{HOST_RULE_PREFIX}load", [load_per_core(s) for s in samples], thresholds["load_warning_per_core"], thresholds["load_critical_per_core"],
         f"Load on {host} is {{value:.1f}}× its cores", f"The 5-minute load average is {{value:.2f}} per CPU core across {cores or '?'} cores. Work is queuing for the CPU."),
    ]

    times = [sample.time for sample in samples]
    by_mount: dict[str, dict] = {}
    for row in db.query(MountMetric).filter(MountMetric.server_id == server.id, MountMetric.time.in_(times)):
        by_mount.setdefault(row.mount, {})[row.time] = row
    current = {mount: rows[times[0]] for mount, rows in by_mount.items() if times[0] in rows}
    if current:
        for raw_mount, row in sorted(current.items()):
            mount = _escape(raw_mount)
            history = [by_mount[raw_mount].get(time) for time in times]
            size = f"{_gb(row.used_gb)} of {_gb(row.total_gb)} used"
            disk = {"disk_warning_pct": thresholds["disk_warning_pct"], "disk_critical_pct": thresholds["disk_critical_pct"], **(mount_overrides or {}).get(raw_mount, {})}
            checks.append((f"{HOST_RULE_PREFIX}disk:{raw_mount}", [r.used_pct if r else None for r in history], disk["disk_warning_pct"], disk["disk_critical_pct"],
                           f"Disk usage on {host}:{mount} is {{value:.0f}}%", f"{mount} is {{value:.1f}}% full ({size}). Free up space before it fills completely."))
            checks.append((f"{HOST_RULE_PREFIX}inodes:{raw_mount}", [r.inodes_pct if r else None for r in history], thresholds["inodes_warning_pct"], thresholds["inodes_critical_pct"],
                           f"Inode usage on {host}:{mount} is {{value:.0f}}%", f"{{value:.1f}}% of inodes on {mount} are used. Many small files (caches, logs, mail queues) are the usual cause."))
    else:
        # Collectors from before per-mount reporting only send the root filesystem.
        root = {"disk_warning_pct": thresholds["disk_warning_pct"], "disk_critical_pct": thresholds["disk_critical_pct"], **(mount_overrides or {}).get("/", {})}
        checks.append((f"{HOST_RULE_PREFIX}disk:/", [s.disk_usage_pct for s in samples], root["disk_warning_pct"], root["disk_critical_pct"],
                       f"Disk usage on {host}:/ is {{value:.0f}}%", "/ is {value:.1f}% full. Free up space before it fills completely."))
        checks.append((f"{HOST_RULE_PREFIX}inodes:/", [s.disk_inodes_pct for s in samples], thresholds["inodes_warning_pct"], thresholds["inodes_critical_pct"],
                       f"Inode usage on {host}:/ is {{value:.0f}}%", "{value:.1f}% of inodes on / are used. Many small files (caches, logs, mail queues) are the usual cause."))

    firing: dict[str, tuple[str, str, str]] = {}
    for key, values, warning, critical, title, description in checks:
        level = _fire_level(values, warning, critical, sustained, key in active_keys)
        if level:
            firing[key] = (CRITICAL if level == 2 else WARNING, title.format(value=values[0]), description.format(value=values[0]))
    return firing


# Bits of /proc/sys/kernel/tainted that mean the kernel hit a fault; the rest (proprietary or
# out-of-tree modules, livepatch) are normal on many healthy machines.
TAINT_FAULTS = {4: "machine check", 5: "bad page", 7: "kernel oops", 9: "kernel warning", 14: "soft lockup"}


def taint_faults(value: int | None) -> list[str]:
    return [name for bit, name in TAINT_FAULTS.items() if value and value >> bit & 1]


REBOOT_DETAIL = "The host rebooted"
CLEAN_REBOOT_DETAIL = "The host rebooted after a clean shutdown"
UNCLEAN_REBOOT_DETAIL = "The host restarted after an unclean shutdown (crash, power loss or forced reset)"
REBOOT_DETAILS = {"clean": CLEAN_REBOOT_DETAIL, "unclean": UNCLEAN_REBOOT_DETAIL}
REBOOT_KEY_PREFIX = "reboot:"


def detect_system_events(previous: HostMetric | None, host: dict, helper_active: bool = False) -> list[tuple[str, str, str | None]]:
    """(kind, detail, event key) for what changed between the previous check-in and this one's host fields.

    With the system helper running, OOM kills come from its kernel log events instead of the counter.
    """
    if previous is None:
        return []
    events: list[tuple[str, str, str | None]] = []
    boot_id = host.get("boot_id")
    rebooted = bool(boot_id and previous.boot_id and boot_id != previous.boot_id)
    if rebooted:
        events.append(("reboot", REBOOT_DETAILS.get(host.get("last_shutdown"), REBOOT_DETAIL), f"{REBOOT_KEY_PREFIX}{boot_id}"))
    # Kernel counters restart with each boot, so compare them only within one boot.
    oom_total = host.get("oom_kill_total")
    if not helper_active and not rebooted and oom_total is not None and previous.oom_kill_total is not None and oom_total > previous.oom_kill_total:
        killed = oom_total - previous.oom_kill_total
        events.append(("oom_kill", f"The kernel OOM killer stopped {killed} {'process' if killed == 1 else 'processes'}", None))
    faults = taint_faults(host.get("kernel_tainted"))
    new_faults = [fault for fault in faults if rebooted or fault not in taint_faults(previous.kernel_tainted)]
    if new_faults:
        events.append(("kernel_fault", f"The kernel reported: {', '.join(new_faults)}", None))
    return events


HELPER_EVENT_KINDS = {"oom_kill", "kernel_error"}
HELPER_EVENTS_MAX = 50
HELPER_EVENT_MAX_AGE = timedelta(days=7)
_EVENT_KEY = re.compile(r"[A-Za-z0-9:._-]{1,128}")


def validate_helper_events(raw, now: datetime) -> list[tuple[str, datetime, str, str]]:
    """(kind, time, detail, key) for the well-formed, recent events a collector forwarded from the helper.

    The collector reads them from a file, so each field is checked again here and anything odd is dropped.
    """
    if not isinstance(raw, list):
        return []
    events = []
    for item in raw[:HELPER_EVENTS_MAX]:
        if not isinstance(item, dict) or item.get("kind") not in HELPER_EVENT_KINDS:
            continue
        detail, key, when = item.get("detail"), item.get("key"), item.get("time")
        if not all(isinstance(value, str) for value in (detail, key, when)) or not _EVENT_KEY.fullmatch(key):
            continue
        try:
            time = datetime.fromisoformat(when)
        except ValueError:
            continue
        if time.tzinfo is None:
            time = time.replace(tzinfo=timezone.utc)
        if not now - HELPER_EVENT_MAX_AGE <= time <= now + timedelta(minutes=5):
            continue
        events.append((item["kind"], time, "".join(char if char.isprintable() else "?" for char in detail).strip()[:300], key))
    return events


DOCKER_REASONS = {
    "timeout": "the Docker API did not answer in time",
    "refused": "the connection to the Docker API was refused",
    "missing": "the Docker API socket does not exist",
    "denied": "access to the Docker API was denied",
    "error": "the Docker API returned an error",
}


def system_rules(db, server, thresholds: dict, now: datetime, active_keys: frozenset | set = frozenset()) -> dict[str, tuple[str, str, str]]:
    """Evaluate the system-state rules (reboots, OOM kills, kernel faults, clock sync, pending reboot)."""
    sustained = thresholds["sustained_checks"]
    samples = db.query(HostMetric).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc(), HostMetric.id.desc()).limit(sustained).all()
    if not samples:
        return {}
    latest, host = samples[0], server.hostname
    since = now - timedelta(minutes=thresholds["event_window_minutes"])
    events = db.query(SystemEvent).filter(SystemEvent.server_id == server.id, SystemEvent.time >= since).order_by(SystemEvent.time.desc()).all()
    firing: dict[str, tuple[str, str, str]] = {}

    if reboots := [event for event in events if event.kind == "reboot"]:
        unclean = reboots[0].detail == UNCLEAN_REBOOT_DETAIL
        firing[f"{SYSTEM_RULE_PREFIX}reboot"] = (
            CRITICAL if unclean else INFO, f"{host} restarted after an unclean shutdown" if unclean else f"{host} rebooted",
            f"The host restarted at {reboots[0].time:%Y-%m-%d %H:%M} UTC. " + (
                "The previous shutdown was not recorded as clean, which points to a crash, power loss or forced reset." if unclean
                else "If you didn't expect this, check for a crash or power loss."),
        )
    if oom := [event for event in events if event.kind == "oom_kill"]:
        firing[f"{SYSTEM_RULE_PREFIX}oom"] = (
            CRITICAL if len(oom) >= 3 else WARNING, f"{host} is killing processes to free memory",
            f"The kernel OOM killer fired {len(oom)} time(s) in the last {thresholds['event_window_minutes']} minutes ({'; '.join(dict.fromkeys(event.detail for event in oom[:3]))}). Check which containers or processes are using the most memory.",
        )
    if replaced := [event for event in events if event.kind == "credentials_replaced"]:
        firing[f"{SYSTEM_RULE_PREFIX}credentials"] = (
            WARNING, f"The credentials of {host} were replaced",
            f"At {replaced[0].time:%Y-%m-%d %H:%M} UTC the enrollment key was used to give this server new credentials, which signed its previous collector out. "
            "That is expected when you reinstalled it with the key. If you did not, treat the enrollment key as compromised: rotate it under Connect a server → Advanced, and check the machine.",
        )
    if faults := taint_faults(latest.kernel_tainted):
        # The taint flags stay set until the next boot, so this alert clears on reboot.
        firing[f"{SYSTEM_RULE_PREFIX}kernel"] = (WARNING, f"Kernel on {host} reported faults", f"The kernel has flagged: {', '.join(faults)}. Check dmesg on the host for details; the flag clears after a reboot.")
    if len(samples) >= sustained and all(sample.ntp_synced is False for sample in samples):
        firing[f"{SYSTEM_RULE_PREFIX}ntp"] = (WARNING, f"Clock on {host} is not synchronized", "The system clock is not synced to a time source. Drifting clocks break TLS, logs and scheduled jobs. Check that chrony, systemd-timesyncd or ntpd is running.")
    if len(samples) >= sustained and all(sample.docker_status == "unreachable" for sample in samples):
        key = f"{SYSTEM_RULE_PREFIX}docker_unreachable"
        # A host that never had Docker working (or has none) isn't an incident; one that did and lost it is.
        # An already-open alert stays open even after the samples that proved it worked have been purged.
        if key in active_keys or db.query(HostMetric.id).filter(HostMetric.server_id == server.id, HostMetric.docker_status == "ok").first():
            firing[key] = (CRITICAL, f"Docker is unreachable on {host}", f"Container data has stopped updating because {DOCKER_REASONS.get(latest.docker_reason, DOCKER_REASONS['error'])}. Check that the Docker daemon (and the docker-proxy container) is running.")
    elif len(samples) >= sustained and all(sample.docker_status == "ok" and sample.docker_latency_ms is not None and sample.docker_latency_ms > thresholds["docker_slow_ms"] for sample in samples):
        firing[f"{SYSTEM_RULE_PREFIX}docker_slow"] = (WARNING, f"Docker is responding slowly on {host}", f"Listing containers took {latest.docker_latency_ms} ms (limit {thresholds['docker_slow_ms']} ms) for several check-ins in a row. The daemon may be overloaded or the host starved of resources.")
    budget_ms = server.check_interval_seconds * 1000 * thresholds["collect_slow_pct"] / 100
    if len(samples) >= sustained and all(sample.collect_duration_ms is not None and sample.collect_duration_ms > budget_ms for sample in samples):
        firing[f"{SYSTEM_RULE_PREFIX}collect_slow"] = (
            WARNING, f"The collector on {host} can't keep up with its check interval",
            f"Gathering data took {latest.collect_duration_ms / 1000:.1f} s of the {server.check_interval_seconds} s check interval (limit {thresholds['collect_slow_pct']:g}%) for several check-ins in a row, so data arrives late. Raise the check interval or reduce the number of containers on this machine.")
    if latest.reboot_required:
        firing[f"{SYSTEM_RULE_PREFIX}reboot_required"] = (INFO, f"{host} needs a reboot", "Installed updates (often a new kernel) only take effect after a restart.")
    return firing


CONTAINER_EVENT_KINDS = ("container_added", "container_removed", "image_changed", "container_notice")
MAX_CONTAINER_EVENTS_PER_CHECKIN = 10
MAX_CONTAINER_EVENTS_PER_HOUR = 40
# What a stopped container's exit status usually means; 137 and 143 are what `docker stop` / `docker kill` produce.
EXIT_HINTS = {1: "general error", 2: "misuse of a shell command", 126: "command not executable", 127: "command not found",
              134: "aborted (SIGABRT)", 137: "killed (SIGKILL)", 139: "segmentation fault", 143: "terminated (SIGTERM)"}
STOP_SIGNAL_CODES = {137, 143}


def _short_image(image_id: str) -> str:
    return image_id.removeprefix("sha256:")[:12]


def detect_container_events(previous: list, containers: list[dict]) -> list[tuple[str, str, str]]:
    """(kind, detail, container name) for what changed since the previous check-in.

    Containers are matched by name, because Compose recreates them with new IDs. Nothing is reported on
    the first check-in or when either list is empty: an empty list means Docker was unreachable, and
    treating that as "everything vanished" would be a false alarm.
    """
    if not previous or not containers:
        return []
    before = {container.name: container for container in previous}
    after = {container["name"]: container for container in containers}
    events: list[tuple[str, str, str]] = []
    for name in sorted(after.keys() - before.keys()):
        image = (after[name].get("image") or "")[:60]
        events.append(("container_added", f"Container {name} appeared" + (f" ({image})" if image else ""), name))
    for name in sorted(before.keys() - after.keys()):
        events.append(("container_removed", f"Container {name} is no longer present", name))
    for name in sorted(after.keys() & before.keys()):
        old, new = before[name].image_id, after[name].get("image_id")
        if old and new and old != new:
            events.append(("image_changed", f"Container {name} now runs a different image build ({_short_image(old)} to {_short_image(new)})", name))
    return events


def exit_rule(container: dict, thresholds: dict, now: datetime) -> tuple[str, str, str] | None:
    """Classify a container that has stopped. Unknown values (old collectors, no timestamp) never alert."""
    code, finished = container.get("exit_code"), container.get("finished_at")
    if container.get("status") not in ("exited", "dead") or code is None or finished is None:
        return None
    oom = container.get("oom_killed") is True
    if (code == 0 and not oom) or now - finished > timedelta(minutes=thresholds["event_window_minutes"]):
        return None
    name = container["name"]
    hint = EXIT_HINTS.get(code)
    detail = f"exit code {code}" + (f" ({hint})" if hint else "")
    if oom:
        return (CRITICAL, f"Container {name} was killed for running out of memory",
                f"{name} stopped ({detail}) after the kernel killed it for exceeding its memory. Raise its memory limit or look for a leak.")
    if code in STOP_SIGNAL_CODES:
        return (INFO, f"Container {name} was stopped by a signal",
                f"{name} ended with {detail}, which is what `docker stop` or `docker kill` produce, so this is usually deliberate. If it wasn't, check what stopped it.")
    return (WARNING, f"Container {name} exited with an error", f"{name} stopped with {detail}. Check its logs for the reason.")


def container_rules(db, server_id: int, containers: list[dict], thresholds: dict, now: datetime) -> dict[str, tuple[str, str, str]]:
    """Evaluate the container rules for one check-in's container records."""
    window = thresholds["restart_loop_window_minutes"]
    # Lowest restart count seen per container within the window; the rise since then is the number of restarts.
    # Keyed by container ID because a recreated container starts counting from zero again.
    ids = [container["container_id"] for container in containers]
    earliest = dict(
        db.query(ContainerMetric.container_id, func.min(ContainerMetric.restart_count))
        .filter(ContainerMetric.server_id == server_id, ContainerMetric.container_id.in_(ids), ContainerMetric.time >= now - timedelta(minutes=window))
        .group_by(ContainerMetric.container_id)
        .all()
    ) if ids else {}

    firing: dict[str, tuple[str, str, str]] = {}
    for container in containers:
        # Compose recreates containers with new IDs but the same name, so key alerts by name.
        name = container.get("name") or container["container_id"]
        if exit_alert := exit_rule({**container, "name": name}, thresholds, now):
            firing[f"{CONTAINER_RULE_PREFIX}{name}:exit"] = exit_alert
        restarts = int(container.get("restart_count") or 0) - earliest.get(container["container_id"], int(container.get("restart_count") or 0))
        if restarts >= thresholds["restart_loop_count"]:
            firing[f"{CONTAINER_RULE_PREFIX}{name}:restart_loop"] = (
                CRITICAL, f"Container {name} is in a restart loop",
                f"{name} restarted {restarts} times in the last {window} minutes (restart count {container.get('restart_count', 0)}). Check its logs for the reason it exits.",
            )
        if str(container.get("healthcheck_status") or "").lower() == "unhealthy":
            firing[f"{CONTAINER_RULE_PREFIX}{name}:unhealthy"] = (
                CRITICAL, f"Container {name} is unhealthy",
                f"The Docker HEALTHCHECK for {name} is failing. Inspect it with: docker inspect --format '{{{{json .State.Health}}}}' {name}",
            )
    # Removals and image changes are noticed by comparing check-ins (see detect_container_events); the incident
    # stays open for the event window and clears on its own.
    present = {container.get("name") for container in containers}
    since = now - timedelta(minutes=thresholds["event_window_minutes"])
    for event in db.query(SystemEvent).filter(SystemEvent.server_id == server_id, SystemEvent.kind.in_(("container_removed", "image_changed")), SystemEvent.time >= since).order_by(SystemEvent.time.desc()):
        name = event.event_key
        if not name:
            continue
        if event.kind == "container_removed" and name not in present:
            firing[f"{CONTAINER_RULE_PREFIX}{name}:removed"] = (INFO, f"Container {name} is no longer present", f"{name} disappeared from this server. If you removed it on purpose this clears by itself.")
        elif event.kind == "image_changed" and name in present:
            firing[f"{CONTAINER_RULE_PREFIX}{name}:image"] = (INFO, f"Container {name} is running a different image", f"{event.detail}. Expected after an update or rebuild; investigate if you didn't make one.")
    return firing


NETWORK_EVENT_KINDS = ("port_opened", "port_closed", "firewall_changed", "firewall_weakened", "network_notice")
MAX_NETWORK_EVENTS_PER_CHECKIN = 10
MAX_NETWORK_EVENTS_PER_HOUR = 40
MAX_LISTENERS = 512
EPHEMERAL_PORT_START = 32768
LISTENER_PROTOCOLS = ("tcp", "udp")
LISTENER_SCOPES = {"loopback": "loopback only", "any": "all interfaces", "specific": "a specific address"}
FIREWALL_LABELS = {"ufw": "ufw IPv4 rules", "ufw6": "ufw IPv6 rules", "ufw-conf": "ufw settings", "nftables": "nftables.conf",
                   "iptables": "iptables rules.v4", "ip6tables": "ip6tables rules.v6"}
_SHA16 = re.compile(r"[0-9a-f]{16}")
INTERNET_STATUSES = ("ok", "degraded", "dns_failed", "down")


def listener_tracked(proto: str, port: int, scope: str) -> bool:
    """Ephemeral ports are skipped: loopback ones and all UDP ones are programs talking out, not services."""
    return not (port >= EPHEMERAL_PORT_START and (scope == "loopback" or proto == "udp"))


def validate_listeners(raw) -> list[tuple[str, int, str]] | None:
    """The tracked, well-formed listeners a collector reported (deduplicated, capped), or None if it isn't a list."""
    if not isinstance(raw, list):
        return None
    found: set[tuple[str, int, str]] = set()
    for item in raw[:MAX_LISTENERS + 100]:
        if not isinstance(item, dict):
            continue
        proto, port, scope = item.get("proto"), item.get("port"), item.get("scope")
        if proto in LISTENER_PROTOCOLS and scope in LISTENER_SCOPES and isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535 and listener_tracked(proto, port, scope):
            found.add((proto, port, scope))
    return sorted(found)[:MAX_LISTENERS]


def validate_firewall(raw) -> dict | None:
    """{"files": {name: (sha, rules)}, "ufw_enabled": bool | None} from a collector's report, or None if unusable.

    A report of None means the collector found no firewall files at all, which is a real (empty) state.
    """
    if raw is None:
        return {"files": {}, "ufw_enabled": None}
    if not isinstance(raw, dict) or not isinstance(raw.get("files"), list):
        return None
    files: dict[str, tuple[str, int]] = {}
    for item in raw["files"][:len(FIREWALL_LABELS) * 2]:
        if not isinstance(item, dict):
            continue
        name, sha, count = item.get("name"), item.get("sha"), item.get("rules")
        if name in FIREWALL_LABELS and name not in files and isinstance(sha, str) and _SHA16.fullmatch(sha) and isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 1_000_000:
            files[name] = (sha, count)
    enabled = raw.get("ufw_enabled")
    return {"files": files, "ufw_enabled": enabled if isinstance(enabled, bool) else None}


def _listener_text(proto: str, port: int, scope: str) -> str:
    return f"{proto}/{port} ({LISTENER_SCOPES[scope]})"


def detect_listener_changes(known: dict[tuple[str, int, str], object], current: set[tuple[str, int, str]]) -> list[tuple[str, str, str]]:
    """(kind, detail, key) for ports that opened or closed. Nothing is reported without a baseline or a current list."""
    if not known or not current:
        return []
    events = [("port_opened", f"New listener: {_listener_text(*key)}", "/".join(map(str, key))) for key in sorted(current - known.keys())]
    events += [("port_closed", f"Listener closed: {_listener_text(*key)}", "/".join(map(str, key))) for key in sorted(known.keys() - current)]
    return events


def detect_firewall_changes(known: dict[str, FirewallState], firewall: dict) -> list[tuple[str, str, str]]:
    """(kind, detail, name) for changes since the last report; a report that lowers protection is "weakened"."""
    if not known:
        return []  # first report for this server is only a baseline
    events: list[tuple[str, str, str]] = []
    for name, (sha, count) in sorted(firewall["files"].items()):
        label, row = FIREWALL_LABELS[name], known.get(name)
        if row is None:
            events.append(("firewall_changed", f"{label} appeared", name))
        elif row.sha != sha:
            if name == "ufw-conf" and row.enabled is True and firewall["ufw_enabled"] is False:
                events.append(("firewall_weakened", "ufw was enabled and is now disabled", name))
            elif name == "ufw-conf" and row.enabled is False and firewall["ufw_enabled"] is True:
                events.append(("firewall_changed", "ufw was disabled and is now enabled", name))
            elif name == "ufw-conf":
                events.append(("firewall_changed", f"{label} changed", name))
            elif count < row.rules:
                events.append(("firewall_weakened", f"{label} lost rules ({row.rules} to {count})", name))
            else:
                events.append(("firewall_changed", f"{label} changed ({row.rules} to {count} rules)", name))
    events += [("firewall_weakened", f"{FIREWALL_LABELS[name]} is no longer present", name) for name in sorted(known.keys() - firewall["files"].keys())]
    return events


def network_rules(db, server, thresholds: dict, now: datetime) -> dict[str, tuple[str, str, str]]:
    """Incidents for the internet check, newly exposed listeners and firewall changes."""
    host, sustained = server.hostname, thresholds["sustained_checks"]
    firing: dict[str, tuple[str, str, str]] = {}
    samples = db.query(HostMetric).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc(), HostMetric.id.desc()).limit(sustained).all()
    if len(samples) >= sustained:
        if all(sample.internet_status == "down" for sample in samples):
            firing[f"{NETWORK_RULE_PREFIX}internet"] = (WARNING, f"{host} cannot reach the internet", "Every connectivity target failed for several check-ins in a row. Check the uplink, the router and the host's network settings.")
        elif all(sample.internet_status == "dns_failed" for sample in samples):
            firing[f"{NETWORK_RULE_PREFIX}dns"] = (WARNING, f"DNS is failing on {host}", "The connectivity targets can't be resolved by name, for several check-ins in a row. Check the DNS servers the host uses.")
    since = now - timedelta(minutes=thresholds["event_window_minutes"])
    events = db.query(SystemEvent).filter(SystemEvent.server_id == server.id, SystemEvent.kind.in_(NETWORK_EVENT_KINDS), SystemEvent.time >= since).order_by(SystemEvent.time.desc()).all()
    open_ports = {(row.proto, row.port, row.scope) for row in db.query(ListenerState).filter(ListenerState.server_id == server.id)}
    for event in events:
        if event.kind == "port_opened" and event.event_key:
            proto, _, rest = event.event_key.partition("/")
            port, _, scope = rest.partition("/")
            if scope != "loopback" and port.isdigit() and (proto, int(port), scope) in open_ports:
                firing[f"{NETWORK_RULE_PREFIX}port:{proto}/{port}"] = (WARNING, f"New listener on {proto}/{port} on {host}", f"{event.detail}. Something started listening on a port that other machines can reach. Expected after starting a service; investigate if you didn't.")
    for kind, severity, title in (("firewall_weakened", WARNING, f"Firewall protection was reduced on {host}"), ("firewall_changed", INFO, f"Firewall configuration changed on {host}")):
        details = list(dict.fromkeys(event.detail for event in events if event.kind == kind))[:3]
        if details:
            firing[f"{NETWORK_RULE_PREFIX}firewall"] = (severity, title, "; ".join(details) + ". This reads configuration files, not the live ruleset. If you didn't make the change, check the host.")
            break
    return firing


MAX_TREND_CONTAINERS = 100
MAX_TREND_ROWS = 300_000
MIN_SAMPLES_PER_BUCKET = 5
MIN_TREND_FIT = 0.9


def _linear_fit(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """Least-squares (slope, intercept, R squared) for (x, y) points, or None if they don't define a line."""
    n = len(points)
    mean_x, mean_y = sum(x for x, _ in points) / n, sum(y for _, y in points) / n
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    if not spread:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / spread
    intercept = mean_y - slope * mean_x
    total = sum((y - mean_y) ** 2 for _, y in points)
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in points)
    return slope, intercept, (1 - residual / total) if total else 0.0


def memory_trend_rules(db, server_id: int, thresholds: dict, now: datetime) -> dict[str, tuple[str, str, str]]:
    """Flag running containers whose memory climbs steadily as a possible leak.

    Hourly averages over the window must be nearly complete, rise at least ``leak_min_mb_per_hour`` and
    ``leak_min_growth_pct`` along a straight line (R squared at least 0.9), and the container must not
    have restarted or been recreated in the window, since either resets its memory. Work is bounded:
    at most 100 containers and 300,000 samples per server.
    """
    window = thresholds["leak_window_hours"]
    since = now - timedelta(hours=window)
    host_time = db.query(func.max(HostMetric.time)).filter(HostMetric.server_id == server_id).scalar()
    if host_time is None:
        return {}
    current = {
        row.name: row for row in db.query(ContainerMetric)
        .filter(ContainerMetric.server_id == server_id, ContainerMetric.time == host_time, ContainerMetric.status == "running")
        .order_by(ContainerMetric.name).limit(MAX_TREND_CONTAINERS)
    }
    if not current:
        return {}
    samples: dict[str, list] = {}
    for name, time, used, restarts, container_id in (
        db.query(ContainerMetric.name, ContainerMetric.time, ContainerMetric.memory_used_mb, ContainerMetric.restart_count, ContainerMetric.container_id)
        .filter(ContainerMetric.server_id == server_id, ContainerMetric.time >= since, ContainerMetric.name.in_(list(current)), ContainerMetric.memory_used_mb.isnot(None))
        .order_by(ContainerMetric.time).limit(MAX_TREND_ROWS)
    ):
        samples.setdefault(name, []).append((time if time.tzinfo else time.replace(tzinfo=timezone.utc), used, restarts, container_id))

    firing: dict[str, tuple[str, str, str]] = {}
    for name, rows in samples.items():
        if len({row[2] for row in rows}) > 1 or len({row[3] for row in rows}) > 1:
            continue  # restarted or recreated: memory was reset, so the trend means nothing
        buckets: dict[int, list[float]] = {}
        for time, used, _restarts, _id in rows:
            buckets.setdefault(int((time - since).total_seconds() // 3600), []).append(used)
        points = [(float(bucket), sum(values) / len(values)) for bucket, values in sorted(buckets.items()) if len(values) >= MIN_SAMPLES_PER_BUCKET]
        if len(points) < max(4, round(window * 0.75)):
            continue
        fit = _linear_fit(points)
        if not fit:
            continue
        slope, intercept, r_squared = fit
        start, end = intercept + slope * points[0][0], intercept + slope * points[-1][0]
        growth = (end - start) / max(start, 1.0) * 100
        if slope < thresholds["leak_min_mb_per_hour"] or growth < thresholds["leak_min_growth_pct"] or r_squared < MIN_TREND_FIT:
            continue
        limit = current[name].memory_limit_mb
        eta = f" At this rate it reaches its {limit:.0f} MB limit in about {(limit - end) / slope:.0f} hours." if limit and limit > end else ""
        firing[f"{TREND_RULE_PREFIX}{name}:memory"] = (
            WARNING, f"Container {name} may have a memory leak",
            f"Memory use rose steadily by about {slope:.1f} MB per hour over the last {window} hours ({start:.0f} MB to {end:.0f} MB).{eta} Caches and warm-up can look like this too, so check whether it keeps growing.",
        )
    return firing
