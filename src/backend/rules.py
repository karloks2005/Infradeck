"""Alert rules evaluated on every collector check-in, and the thresholds they use.

Rules only decide *what is firing*; ``alerts.sync_alerts`` turns that into
opened, refreshed and resolved alerts.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from .alerts import CONTAINER_RULE_PREFIX, CRITICAL, INFO, WARNING
from .models import ContainerMetric, HostMetric, MountMetric, SystemEvent

HOST_RULE_PREFIX = "host:"
SYSTEM_RULE_PREFIX = "system:"

DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "disk_warning_pct": 85, "disk_critical_pct": 95,
    "inodes_warning_pct": 85, "inodes_critical_pct": 95,
    "memory_warning_pct": 90, "memory_critical_pct": 95,
    "swap_warning_pct": 50,
    "load_warning_per_core": 1.5, "load_critical_per_core": 2.0,
    # How many check-ins in a row a host metric must breach before an alert opens.
    "sustained_checks": 3,
    "restart_loop_count": 3, "restart_loop_window_minutes": 30,
    # How long a reboot or OOM kill keeps its alert open after it happened.
    "event_window_minutes": 60,
}

_LIMITS: dict[str, tuple[float, float]] = {
    **{key: (1, 100) for key in DEFAULT_THRESHOLDS if key.endswith("_pct")},
    "load_warning_per_core": (0.1, 50), "load_critical_per_core": (0.1, 50),
    "sustained_checks": (1, 20),
    "restart_loop_count": (1, 100), "restart_loop_window_minutes": (5, 1440),
    "event_window_minutes": (5, 1440),
}
_INTEGER_KEYS = {"sustained_checks", "restart_loop_count", "restart_loop_window_minutes", "event_window_minutes"}


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


def system_rules(db, server, thresholds: dict, now: datetime) -> dict[str, tuple[str, str, str]]:
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
    if faults := taint_faults(latest.kernel_tainted):
        # The taint flags stay set until the next boot, so this alert clears on reboot.
        firing[f"{SYSTEM_RULE_PREFIX}kernel"] = (WARNING, f"Kernel on {host} reported faults", f"The kernel has flagged: {', '.join(faults)}. Check dmesg on the host for details; the flag clears after a reboot.")
    if len(samples) >= sustained and all(sample.ntp_synced is False for sample in samples):
        firing[f"{SYSTEM_RULE_PREFIX}ntp"] = (WARNING, f"Clock on {host} is not synchronized", "The system clock is not synced to a time source. Drifting clocks break TLS, logs and scheduled jobs. Check that chrony, systemd-timesyncd or ntpd is running.")
    if latest.reboot_required:
        firing[f"{SYSTEM_RULE_PREFIX}reboot_required"] = (INFO, f"{host} needs a reboot", "Installed updates (often a new kernel) only take effect after a restart.")
    return firing


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
    return firing
