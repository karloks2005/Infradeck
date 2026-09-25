"""Alert rules evaluated on every collector check-in, and the thresholds they use.

Rules only decide *what is firing*; ``alerts.sync_alerts`` turns that into
opened, refreshed and resolved alerts.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func

from .alerts import CONTAINER_RULE_PREFIX, CRITICAL, WARNING
from .models import ContainerMetric, HostMetric

HOST_RULE_PREFIX = "host:"

DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "disk_warning_pct": 85, "disk_critical_pct": 95,
    "inodes_warning_pct": 85, "inodes_critical_pct": 95,
    "memory_warning_pct": 90, "memory_critical_pct": 95,
    "swap_warning_pct": 50,
    "load_warning_per_core": 1.5, "load_critical_per_core": 2.0,
    # How many check-ins in a row a host metric must breach before an alert opens.
    "sustained_checks": 3,
    "restart_loop_count": 3, "restart_loop_window_minutes": 30,
}

_LIMITS: dict[str, tuple[float, float]] = {
    **{key: (1, 100) for key in DEFAULT_THRESHOLDS if key.endswith("_pct")},
    "load_warning_per_core": (0.1, 50), "load_critical_per_core": (0.1, 50),
    "sustained_checks": (1, 20),
    "restart_loop_count": (1, 100), "restart_loop_window_minutes": (5, 1440),
}
_INTEGER_KEYS = {"sustained_checks", "restart_loop_count", "restart_loop_window_minutes"}


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


def _level(value: float | None, warning: float, critical: float | None) -> int:
    """0 = fine, 1 = warning, 2 = critical."""
    if value is None:
        return 0
    if critical is not None and value >= critical:
        return 2
    return 1 if value >= warning else 0


def host_rules(db, server, thresholds: dict, active_keys: set[str]) -> dict[str, tuple[str, str, str]]:
    """Evaluate the host rules against the server's most recent check-ins (newest first)."""
    sustained = thresholds["sustained_checks"]
    samples = db.query(HostMetric).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc(), HostMetric.id.desc()).limit(sustained).all()
    if not samples:
        return {}
    cores = server.cpu_cores or None

    def load_per_core(sample):
        return sample.load_5m / cores if cores and sample.load_5m is not None else None

    checks = (
        # (rule, value getter, warning, critical, title, description)
        ("disk", lambda s: s.disk_usage_pct, thresholds["disk_warning_pct"], thresholds["disk_critical_pct"],
         "Disk usage on {host} is {value:.0f}%", "The root filesystem is {value:.1f}% full. Free up space before it fills completely."),
        ("inodes", lambda s: s.disk_inodes_pct, thresholds["inodes_warning_pct"], thresholds["inodes_critical_pct"],
         "Inode usage on {host} is {value:.0f}%", "{value:.1f}% of inodes on the root filesystem are used. Many small files (caches, logs, mail queues) are the usual cause."),
        ("memory", lambda s: s.ram_pct, thresholds["memory_warning_pct"], thresholds["memory_critical_pct"],
         "Memory usage on {host} is {value:.0f}%", "{value:.1f}% of RAM is in use. Check the containers and processes using the most memory."),
        ("swap", lambda s: s.swap_pct, thresholds["swap_warning_pct"], None,
         "Swap usage on {host} is {value:.0f}%", "{value:.1f}% of swap has been in use for several check-ins, an early sign of memory pressure."),
        ("load", load_per_core, thresholds["load_warning_per_core"], thresholds["load_critical_per_core"],
         "Load on {host} is {value:.1f}× its cores", "The 5-minute load average is {value:.2f} per CPU core across " + f"{cores or '?'} cores. Work is queuing for the CPU."),
    )
    firing: dict[str, tuple[str, str, str]] = {}
    for rule, value_of, warning, critical, title, description in checks:
        key = f"{HOST_RULE_PREFIX}{rule}"
        latest_value = value_of(samples[0])
        levels = [_level(value_of(sample), warning, critical) for sample in samples]
        # Opening an alert, or escalating it to critical, needs every recent check-in to breach.
        sustained_level = min(levels) if len(samples) >= sustained else 0
        level = max(sustained_level, min(levels[0], 1)) if key in active_keys else sustained_level
        if level:
            firing[key] = (
                CRITICAL if level == 2 else WARNING,
                title.format(host=server.hostname, value=latest_value),
                description.format(value=latest_value),
            )
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
