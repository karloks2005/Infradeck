"""Fleet-wide statistics for the Statistics page: incidents over time and how reliably servers reported.

Only aggregated numbers leave this module, never raw rows. Incidents are counted by the time they opened, in
buckets that suit the range (hours up to a day, days up to a month, weeks up to a year, months beyond), and the
amount of data read is capped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from . import history, rollups
from .alerts import CRITICAL, INFO, SEVERITIES, WARNING
from .models import HostMetric, IncidentAlert, Server

MAX_INCIDENTS_READ = 20000
# The bucket an incident count is grouped into, by range.
INCIDENT_BUCKET = {"1h": None, "6h": rollups.HOUR, "24h": rollups.HOUR, "7d": rollups.DAY, "30d": rollups.DAY, "90d": rollups.WEEK, "1y": rollups.WEEK, "all": rollups.MONTH}
KIND_LABELS = {
    "host:disk": "Disk space", "host:inodes": "Inodes", "host:memory": "Memory", "host:swap": "Swap", "host:load": "Load",
    "system:reboot": "Reboots", "system:oom": "Out-of-memory kills", "system:kernel": "Kernel faults", "system:ntp": "Clock not synced",
    "system:docker_unreachable": "Docker unreachable", "system:docker_slow": "Docker slow", "system:collect_slow": "Collector too slow",
    "system:reboot_required": "Reboot needed",
}


def incident_kind(rule_key: str | None) -> str:
    """A readable group for an incident's rule, so the page never has to show raw rule keys."""
    if not rule_key:
        return "Other"
    if rule_key == "server_offline":
        return "Server offline"
    parts = rule_key.split(":")
    if len(parts) >= 2 and f"{parts[0]}:{parts[1]}" in KIND_LABELS:
        return KIND_LABELS[f"{parts[0]}:{parts[1]}"]
    return {"trend": "Possible memory leaks", "network": "Network", "container": "Containers"}.get(parts[0], "Other")


def _utc(moment: datetime) -> datetime:
    return rollups._utc(moment)


def window(db, range_name: str, now: datetime) -> datetime:
    """When the range starts: a fixed distance back, or the first incident for all time."""
    length = history.RANGES[range_name]
    if length is not None:
        return now - length
    first = db.execute(select(func.min(IncidentAlert.created_at))).scalar()
    return _utc(first) if first else now


def incident_stats(db, range_name: str, now: datetime | None = None) -> dict:
    now = _utc(now or datetime.now(timezone.utc))
    start = window(db, range_name, now)
    bucket = INCIDENT_BUCKET[range_name]
    rows = db.execute(
        select(IncidentAlert.created_at, IncidentAlert.resolved_at, IncidentAlert.severity, IncidentAlert.rule_key, IncidentAlert.status)
        .where(IncidentAlert.created_at >= start).order_by(IncidentAlert.created_at).limit(MAX_INCIDENTS_READ + 1)
    ).all()
    truncated = len(rows) > MAX_INCIDENTS_READ
    rows = rows[:MAX_INCIDENTS_READ]
    columns: dict[datetime, dict[str, int]] = {}
    kinds: dict[str, int] = {}
    minutes: list[float] = []
    for created, resolved, severity, rule_key, status in rows:
        created = _utc(created)
        key = rollups.floor(bucket, created) if bucket else rollups.floor(rollups.HOUR, created)
        column = columns.setdefault(key, {level: 0 for level in SEVERITIES})
        column[severity if severity in column else INFO] += 1
        kinds[incident_kind(rule_key)] = kinds.get(incident_kind(rule_key), 0) + 1
        if resolved is not None and status == "resolved":
            minutes.append(max((_utc(resolved) - created).total_seconds() / 60, 0))
    open_now = {level: count for level, count in db.execute(select(IncidentAlert.severity, func.count()).where(IncidentAlert.status == "active").group_by(IncidentAlert.severity))}
    return {
        "range": range_name, "start": start.isoformat(), "end": now.isoformat(), "bucket": bucket or "hour",
        "opened": len(rows), "resolved": len(minutes), "truncated": truncated,
        "mean_minutes_to_resolve": (sum(minutes) / len(minutes)) if minutes else None,
        "open_now": {level.lower(): open_now.get(level, 0) for level in (CRITICAL, WARNING, INFO)},
        "by_bucket": [{"time": moment.isoformat(), **{level.lower(): counts[level] for level in (CRITICAL, WARNING, INFO)}} for moment, counts in sorted(columns.items())],
        "by_kind": [{"kind": kind, "count": count} for kind, count in sorted(kinds.items(), key=lambda item: (-item[1], item[0]))],
    }


def availability(db, range_name: str, now: datetime | None = None) -> list[dict]:
    """Per server: the share of the check-ins it should have made in the range that actually arrived.

    Approximate: it assumes the check interval it has now held for the whole range, and the range starts when the
    server's first data does, so a new server isn't blamed for the time before it existed.
    """
    now = _utc(now or datetime.now(timezone.utc))
    result = []
    for server in db.execute(select(Server).order_by(Server.hostname)).scalars():
        series = history.history(db, history.HOST, server.id, None, range_name, (), server.check_interval_seconds, now=now)
        received = sum(point["samples"] for point in series["points"])
        span = (now - datetime.fromisoformat(series["start"])).total_seconds()
        expected = max(span / server.check_interval_seconds, 1)
        result.append({"server_id": server.id, "hostname": server.hostname, "checkins": received, "expected": round(expected),
                       "percent": round(min(100.0, 100.0 * received / expected), 2) if series["data_since"] else None})
    return result


HEATMAP_DAYS = 30
HEATMAP_METRICS = ("cpu", "ram")
MAX_OFFSET_MINUTES = 14 * 60


def hour_weekday_heatmap(db, server: Server, metric: str, offset_minutes: int = 0, now: datetime | None = None) -> dict:
    """The average of a metric for each hour of the day and weekday over the last 30 days, for a time zone.

    ``offset_minutes`` is the viewer's offset from UTC (a fixed one: daylight saving changes inside the 30 days
    are ignored). Row 0 is Monday. Hours nobody reported stay None.
    """
    now = _utc(now or datetime.now(timezone.utc))
    items, _ = history._tiered_items(db, history.HOST, (metric,), server.id, None, rollups.HOUR, now - timedelta(days=HEATMAP_DAYS), now)
    totals = [[[0.0, 0] for _ in range(24)] for _ in range(7)]
    for start, agg in items:
        local = start + timedelta(minutes=offset_minutes)
        total, count, _maximum = agg.values.get(metric, (0.0, 0, None))
        cell = totals[local.weekday()][local.hour]
        cell[0] += total
        cell[1] += count
    since = history.data_since(db, history.HOST, server.id, None)
    return {
        "server_id": server.id, "metric": metric, "days": HEATMAP_DAYS, "offset_minutes": offset_minutes,
        "data_since": since.isoformat() if since else None,
        "cells": [[(cell[0] / cell[1]) if cell[1] else None for cell in row] for row in totals],
        "samples": [[cell[1] for cell in row] for row in totals],
    }


def fleet_stats(db, range_name: str, now: datetime | None = None) -> dict:
    return {"incidents": incident_stats(db, range_name, now), "availability": availability(db, range_name, now)}
