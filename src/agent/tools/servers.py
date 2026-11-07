"""Tools about one machine: what it is, its metrics over time, its disks, how a value compares with its usual level,
and its open ports and firewall."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.backend import history, rollups
from src.backend.models import FirewallState, HostMetric, HostRollup, ListenerState, MountMetric, SystemEvent

from ..bounds import clip, stamp, utc
from .common import MAX_LIST, Facts, ToolError, find_server, is_online, moment, read, span, summary, window

HOST_METRICS = {   # name: (label, unit)
    "cpu": ("CPU", "%"), "ram": ("memory", "%"), "swap": ("swap", "%"), "load": ("load (1 min)", ""), "disk": ("root disk", "%"),
    "inodes": ("root disk inodes", "%"), "docker_ms": ("Docker API response", "ms"), "collect_ms": ("collector check-in time", "ms"),
}
BASELINE_METRICS = ("cpu", "ram", "swap", "load", "disk", "inodes")
BASELINE_WEEKS = 4
MAX_MOUNTS = 12
NETWORK_EVENTS = ("port_opened", "port_closed", "firewall_changed", "firewall_allowed", "firewall_weakened")
NETWORK_DAYS = 7


def server_info(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"])
    system = json.loads(server.system_info) if server.system_info else {}
    latest = db.query(HostMetric).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc()).first()
    return {
        "id": facts.add(f"server:{server.hostname}", f"{server.hostname}: machine facts"),
        "hostname": server.hostname, "online": is_online(server, now), "last_check_in": stamp(server.last_seen_at), "check_every_seconds": server.check_interval_seconds,
        "runs_the_dashboard": server.is_dashboard_host, "os": server.os_info, "kernel": system.get("kernel"), "arch": system.get("arch"), "cpu_model": system.get("cpu_model"),
        "cpu_cores": server.cpu_cores, "memory_gb": round(server.total_ram_gb, 1) if server.total_ram_gb else None, "swap_gb": system.get("swap_total_gb"),
        "collector_version": system.get("collector_version"), "docker_version": system.get("docker_version"),
        "updates_pending": system.get("updates_pending"), "security_updates_pending": system.get("updates_security"), "automatic_updates": system.get("auto_updates"),
        "uptime_hours": round(latest.system_uptime_seconds / 3600, 1) if latest and latest.system_uptime_seconds else None,
        "reboot_required": latest.reboot_required if latest else None, "clock_synced": latest.ntp_synced if latest else None,
        "docker": latest.docker_status if latest else None, "internet": latest.internet_status if latest else None,
    }


def host_metrics(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"])
    since, until = window(arguments, now)
    names = list(dict.fromkeys(arguments["metrics"])) or list(history.DEFAULT_HOST_METRICS)
    items, source = read(db, history.HOST, server.id, None, names, since, until, now)
    series = []
    for name in names:
        label, unit = HOST_METRICS[name]
        fact_id = f"metric:{server.hostname}.{name}"
        found = summary(fact_id, f"{label} · {server.hostname} · {span(since, until)}", unit, items, name, source)
        if found:
            series.append(found)
            facts.add(fact_id, found["label"])
    return {"server": server.hostname, "from": stamp(since), "to": stamp(until), "series": series,
            "note": None if series else "No check-ins from this server in that window."}


def _growth_per_day(rows: list[tuple[datetime, float]]) -> float | None:
    """Least-squares slope of used space in GB per day; None with too little data to tell."""
    if len(rows) < 3 or (rows[-1][0] - rows[0][0]) < timedelta(hours=6):
        return None
    xs = [(moment - rows[0][0]).total_seconds() / 86400 for moment, _ in rows]
    ys = [value for _, value in rows]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread if spread else None


def disk_usage(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"])
    days = arguments["days"]
    if not 1 <= days <= 90:
        raise ToolError("days must be between 1 and 90.")
    since = now - timedelta(days=days)
    latest = db.query(MountMetric.time).filter(MountMetric.server_id == server.id).order_by(MountMetric.time.desc()).limit(1).scalar()
    mounts = db.query(MountMetric).filter(MountMetric.server_id == server.id, MountMetric.time == latest).order_by(MountMetric.mount).all() if latest else []
    if arguments["mount"] is not None:
        mounts = [mount for mount in mounts if mount.mount == arguments["mount"]]
        if not mounts:
            raise ToolError(f"{server.hostname} reports no mount {arguments['mount']!r}. Leave mount null to see them all.")
    output = []
    for mount in mounts[:MAX_MOUNTS]:
        items, source = read(db, history.MOUNT, server.id, mount.mount, ["used_pct", "used_gb"], since, now, now)
        used = [(start, agg.values["used_gb"][0] / agg.values["used_gb"][1]) for start, agg in items if agg.values.get("used_gb", (0, 0))[1]]
        growth = _growth_per_day(used)
        free = (mount.total_gb or 0) - (mount.used_gb or 0)
        fact_id = f"metric:{server.hostname}.disk:{mount.mount}"
        usage = summary(fact_id, f"{mount.mount} used · {server.hostname} · last {days} d", "%", items, "used_pct", source)
        facts.add(fact_id, usage["label"] if usage else f"{mount.mount} on {server.hostname}")
        output.append({
            "id": fact_id, "mount": mount.mount, "filesystem": mount.fs_type, "used_pct": mount.used_pct, "used_gb": mount.used_gb, "total_gb": mount.total_gb, "inodes_pct": mount.inodes_pct,
            "growth_gb_per_day": round(growth, 2) if growth is not None else None,
            "full_in_days": round(free / growth, 1) if growth and growth > 0.01 and free > 0 else None,
            "usage": usage,
        })
    return {"server": server.hostname, "measured_at": stamp(latest), "mounts": output, "more_mounts": max(0, len(mounts) - MAX_MOUNTS)}


def _hour_average(db, server_id: int, name: str, start: datetime, now: datetime) -> float | None:
    """The metric's average over the hour from ``start`` (so far, for the current hour)."""
    items, _ = read(db, history.HOST, server_id, None, [name], start, min(start + timedelta(hours=1), now + timedelta(seconds=1)), now)
    total = sum(agg.values.get(name, (0.0, 0, None))[0] for _, agg in items)
    count = sum(agg.values.get(name, (0.0, 0, None))[1] for _, agg in items)
    return total / count if count else None


def compare_to_baseline(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"])
    name = arguments["metric"]
    at = moment(arguments["at"], now)
    hour = rollups.floor(rollups.HOUR, at)
    value = _hour_average(db, server.id, name, hour, now)
    yesterday = _hour_average(db, server.id, name, hour - timedelta(days=1), now)
    column = history.HOST_METRICS[name].avg
    rows = db.query(HostRollup.bucket_start, getattr(HostRollup, column)).filter(
        HostRollup.server_id == server.id, HostRollup.tier == rollups.HOUR, HostRollup.bucket_start >= hour - timedelta(weeks=BASELINE_WEEKS), HostRollup.bucket_start < hour).all()
    same_hour = [average for start, average in rows if utc(start).hour == hour.hour and average is not None]
    usual = sum(same_hour) / len(same_hour) if same_hour else None
    label, unit = HOST_METRICS[name]
    fact_id = facts.add(f"baseline:{server.hostname}.{name}", f"{label} at {stamp(hour)[11:16]} UTC vs usual · {server.hostname} · {stamp(hour)[:10]}")
    rounded = lambda number: None if number is None else round(number, 2)  # noqa: E731
    return {
        "id": fact_id, "server": server.hostname, "metric": name, "unit": unit, "hour": stamp(hour),
        "value": rounded(value), "same_hour_yesterday": rounded(yesterday),
        "usual_for_this_hour": rounded(usual), "usual_from_days": len(same_hour),
        "difference": rounded(value - usual) if value is not None and usual is not None else None,
        "difference_pct": round(100 * (value - usual) / usual, 1) if value is not None and usual else None,
    }


def network_state(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"])
    listeners = db.query(ListenerState).filter(ListenerState.server_id == server.id).order_by(ListenerState.port, ListenerState.proto).all()
    firewalls = db.query(FirewallState).filter(FirewallState.server_id == server.id).order_by(FirewallState.name).all()
    changes = db.query(SystemEvent).filter(SystemEvent.server_id == server.id, SystemEvent.kind.in_(NETWORK_EVENTS), SystemEvent.time >= now - timedelta(days=NETWORK_DAYS)) \
        .order_by(SystemEvent.time.desc()).limit(MAX_LIST).all()
    for event in changes:
        facts.add(f"event:{event.id}", f"{stamp(event.time)[5:16].replace('T', ' ')} {event.kind.replace('_', ' ')} · {server.hostname}")
    return {
        "id": facts.add(f"network:{server.hostname}", f"{server.hostname}: open ports and firewall"),
        "server": server.hostname,
        "listening": [{"proto": item.proto, "port": item.port, "reachable_from": {"any": "other machines", "loopback": "this machine only"}.get(item.scope, "one address"),
                       "since": stamp(item.first_seen)} for item in listeners[:MAX_LIST * 2]],
        "more_listening": max(0, len(listeners) - MAX_LIST * 2),
        "firewall": [{"name": item.name, "rules": item.rules, "enabled": item.enabled, "changed_at": stamp(item.updated_at)} for item in firewalls],
        "changes_last_7_days": [{"id": f"event:{event.id}", "time": stamp(event.time), "kind": event.kind, "text": clip(event.detail)} for event in reversed(changes)],
    }
