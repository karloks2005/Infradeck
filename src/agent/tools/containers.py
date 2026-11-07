"""Tools about a machine's Docker containers: every container at one check-in, and one container over time."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from src.backend import history
from src.backend.models import ContainerMetric, ContainerRollup
from src.backend.retention import DETAILED_KEPT

from ..bounds import clip, stamp, utc
from .common import MAX_LIST, Facts, ToolError, find_server, moment, read, span, summary, window

MAX_CONTAINERS = 40
MAX_IDS = 5          # a container recreated more often than this in one window is read from its newest IDs
SERIES = {"cpu": ("CPU", "%"), "memory_mb": ("memory", "MB"), "restarts": ("restart count (running total)", "")}


def list_containers(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"])
    at = moment(arguments["at"], now)
    if at < now - DETAILED_KEPT:
        raise ToolError(f"Snapshots of every container are kept for {DETAILED_KEPT.days} days. For older times use container_history.")
    taken = db.query(func.max(ContainerMetric.time)).filter(ContainerMetric.server_id == server.id, ContainerMetric.time <= at, ContainerMetric.time >= now - DETAILED_KEPT).scalar()
    rows = db.query(ContainerMetric).filter(ContainerMetric.server_id == server.id, ContainerMetric.time == taken).all() if taken else []
    rows.sort(key=lambda row: (row.status == "running", -(row.memory_used_mb or 0)))
    output = []
    for row in rows[:MAX_CONTAINERS]:
        fact_id = facts.add(f"container:{server.hostname}/{row.name}", f"{row.name} on {server.hostname} at {stamp(taken)[5:16].replace('T', ' ')} UTC")
        output.append({"id": fact_id, "name": row.name, "image": clip(row.image, 120), "project": row.project, "status": row.status, "health": row.healthcheck_status,
                       "cpu_pct": row.cpu_pct, "memory_mb": row.memory_used_mb, "memory_limit_mb": row.memory_limit_mb, "memory_pct": row.memory_pct,
                       "restarts": row.restart_count, "exit_code": row.exit_code, "oom_killed": row.oom_killed, "started_at": row.started_at})
    return {"server": server.hostname, "at": stamp(taken), "containers": output, "more_containers": max(0, len(rows) - MAX_CONTAINERS),
            "note": None if rows else "No container data from this server at or before that time."}


def _ids(db, server_id: int, name: str, since: datetime, until: datetime) -> list[str]:
    """The Docker IDs the container had in the window, newest first (it gets a new one each time it is recreated)."""
    raw = db.query(ContainerMetric.container_id, func.max(ContainerMetric.time)).filter(ContainerMetric.server_id == server_id, ContainerMetric.name == name,
                                                                                        ContainerMetric.time >= since, ContainerMetric.time < until).group_by(ContainerMetric.container_id).all()
    rolled = db.query(ContainerRollup.container_id, func.max(ContainerRollup.bucket_start)).filter(ContainerRollup.server_id == server_id, ContainerRollup.name == name,
                                                                                                   ContainerRollup.bucket_start >= since, ContainerRollup.bucket_start < until).group_by(ContainerRollup.container_id).all()
    newest: dict[str, datetime] = {}
    for container_id, last in raw + rolled:
        newest[container_id] = max(newest.get(container_id, utc(last)), utc(last))
    return sorted(newest, key=newest.get, reverse=True)[:MAX_IDS]


def _changes(db, server_id: int, name: str, since: datetime, until: datetime) -> list[dict]:
    """Moments its state changed: status, health, restarts, exit code, OOM kill, image. Only from check-ins."""
    rows = db.query(ContainerMetric).filter(ContainerMetric.server_id == server_id, ContainerMetric.name == name, ContainerMetric.time >= since, ContainerMetric.time < until) \
        .order_by(ContainerMetric.time).limit(50_000).all()
    changes, previous = [], None
    for row in rows:
        if previous is not None:
            what = []
            if row.status != previous.status:
                what.append(f"status {previous.status} → {row.status}")
            if row.healthcheck_status != previous.healthcheck_status:
                what.append(f"health {previous.healthcheck_status or 'none'} → {row.healthcheck_status or 'none'}")
            if row.restart_count > previous.restart_count:
                what.append(f"restarted {row.restart_count - previous.restart_count}×")
            if row.exit_code is not None and row.exit_code != previous.exit_code:
                what.append(f"exit code {row.exit_code}")
            if row.oom_killed and not previous.oom_killed:
                what.append("killed for running out of memory")
            if row.image_id and previous.image_id and row.image_id != previous.image_id:
                what.append("now runs a different image build")
            if what:
                changes.append({"time": stamp(row.time), "change": ", ".join(what)})
        previous = row
    return changes[-MAX_LIST:]


def container_history(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"])
    name = arguments["container"]
    since, until = window(arguments, now)
    ids = _ids(db, server.id, name, since, until)
    if not ids:
        known = [found for (found,) in db.query(ContainerMetric.name).filter(ContainerMetric.server_id == server.id, ContainerMetric.time >= now - DETAILED_KEPT).distinct().order_by(ContainerMetric.name).limit(MAX_LIST)]
        raise ToolError(f"No container {name!r} on {server.hostname} in that window. Containers seen there this week: {', '.join(known) or 'none'}.")
    items, source = [], "check-ins"
    for container_id in ids:
        found, source = read(db, history.CONTAINER, server.id, container_id, list(SERIES), since, until, now)
        items += found
    items.sort(key=lambda item: item[0])
    series = []
    for metric, (label, unit) in SERIES.items():
        fact_id = f"metric:{server.hostname}/{name}.{metric}"
        found = summary(fact_id, f"{name} {label} · {server.hostname} · {span(since, until)}", unit, items, metric, source)
        if found:
            facts.add(fact_id, found["label"])
            series.append(found)
    samples, down = sum(agg.samples for _, agg in items), sum(agg.down for _, agg in items)
    latest = db.query(ContainerMetric).filter(ContainerMetric.server_id == server.id, ContainerMetric.name == name, ContainerMetric.time < until).order_by(ContainerMetric.time.desc()).first()
    raw_from = max(since, now - DETAILED_KEPT)
    return {
        "id": facts.add(f"container:{server.hostname}/{name}", f"{name} on {server.hostname} · {span(since, until)}"),
        "server": server.hostname, "container": name, "from": stamp(since), "to": stamp(until), "recreated": len(ids) > 1,
        "not_running_pct": round(100 * down / samples, 1) if samples else None,
        "memory_limit_mb": latest.memory_limit_mb if latest else None, "image": clip(latest.image, 120) if latest else None,
        "series": series,
        "changes": _changes(db, server.id, name, raw_from, until) if raw_from < until else [],
        "changes_from": stamp(raw_from) if raw_from < until else None,
    }
