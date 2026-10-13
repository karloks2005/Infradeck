"""Fleet-wide statistics for the Statistics page: incidents over time and how reliably servers reported.

Only aggregated numbers leave this module, never raw rows. Incidents are counted by the time they opened, in
buckets that suit the range (hours up to a day, days up to a month, weeks up to a year, months beyond), and the
amount of data read is capped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, case, cast, extract, func, select

from . import history, rollups
from .alerts import CRITICAL, INFO, SEVERITIES, WARNING
from .models import ContainerMetric, ContainerRollup, HostMetric, IncidentAlert, Server

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


# --- containers over a range ------------------------------------------------------------------------------

SPARKLINE_POINTS = 40
MAX_ROLLUP_ROWS = 200_000
# Which summaries answer a range for every container of a server at once. A week is read from the hourly ones
# rather than from check-ins: a week of check-ins for a few dozen containers is over a million rows.
CONTAINER_TIER = {"7d": rollups.HOUR, "30d": rollups.HOUR, "90d": rollups.DAY, "1y": rollups.DAY, "all": rollups.DAY}
BUSIEST_TIER = {"7d": rollups.HOUR, "30d": rollups.HOUR, "90d": rollups.WEEK, "1y": rollups.WEEK, "all": rollups.MONTH}


def _epoch_bucket(column, step: int, origin: int = 0):
    """The whole number of ``step``-second periods a timestamp is past ``origin``, worked out by the database."""
    return (cast(extract("epoch", column), Integer) - origin) // step


class _Usage:
    """What one container did over a range, added up from samples or summaries."""

    __slots__ = ("name", "cpu", "cpu_n", "cpu_max", "memory", "memory_n", "memory_max", "restarts_high", "restarts_low", "samples", "down", "spark", "swap", "swap_n", "swap_max")

    def __init__(self):
        self.name, self.cpu, self.cpu_n, self.cpu_max, self.memory, self.memory_n, self.memory_max = "", 0.0, 0, None, 0.0, 0, None
        self.restarts_high, self.restarts_low, self.samples, self.down, self.spark = None, None, 0, 0, {}
        self.swap, self.swap_n, self.swap_max = 0.0, 0, None

    def add_swap(self, total, count, maximum):
        if total is not None and count:
            self.swap, self.swap_n = self.swap + total, self.swap_n + count
        self.swap_max = maximum if maximum is not None and (self.swap_max is None or maximum > self.swap_max) else self.swap_max

    def add(self, name, cpu_total, cpu_n, cpu_max, memory_total, memory_n, memory_max, restarts_high, restarts_low, samples, down, slot):
        self.name = name or self.name
        if cpu_total is not None and cpu_n:
            self.cpu, self.cpu_n = self.cpu + cpu_total, self.cpu_n + cpu_n
            cell = self.spark.setdefault(slot, [0.0, 0])
            cell[0], cell[1] = cell[0] + cpu_total, cell[1] + cpu_n
        if memory_total is not None and memory_n:
            self.memory, self.memory_n = self.memory + memory_total, self.memory_n + memory_n
        self.cpu_max = cpu_max if cpu_max is not None and (self.cpu_max is None or cpu_max > self.cpu_max) else self.cpu_max
        self.memory_max = memory_max if memory_max is not None and (self.memory_max is None or memory_max > self.memory_max) else self.memory_max
        for value in (restarts_high, restarts_low):
            if value is not None:
                self.restarts_high = value if self.restarts_high is None else max(self.restarts_high, value)
                self.restarts_low = value if self.restarts_low is None else min(self.restarts_low, value)
        self.samples, self.down = self.samples + (samples or 0), self.down + (down or 0)


def _raw_usage(db, usage: dict, server_id: int, since: datetime, until: datetime, origin: int, slot_seconds: int) -> None:
    table = ContainerMetric
    slot = _epoch_bucket(table.time, slot_seconds, origin).label("slot")
    query = select(
        table.container_id, func.max(table.name), slot, func.sum(table.cpu_pct), func.count(table.cpu_pct), func.max(table.cpu_pct),
        func.sum(table.memory_used_mb), func.count(table.memory_used_mb), func.max(table.memory_used_mb), func.max(table.restart_count), func.min(table.restart_count),
        func.count(), func.sum(case((table.status != "running", 1), else_=0)),
        func.sum(table.swap_mb), func.count(table.swap_mb), func.max(table.swap_mb),
    ).where(table.server_id == server_id, table.time >= since, table.time < until).group_by(table.container_id, slot)
    for container_id, name, index, *figures in db.execute(query):
        item = usage.setdefault(container_id, _Usage())
        item.add(name, *figures[:-3], max(0, min(SPARKLINE_POINTS - 1, int(index))))
        item.add_swap(*figures[-3:])


def container_usage(db, server: Server, range_name: str, now: datetime | None = None) -> dict:
    """Every container of a server over a range: average and peak CPU and memory, restarts in the range, the share of
    check-ins it was down, and a small CPU series. The busiest comes first."""
    now = _utc(now or datetime.now(timezone.utc))
    length = history.RANGES[range_name]
    tier = CONTAINER_TIER.get(range_name)
    first = db.execute(select(func.min(ContainerMetric.time)).where(ContainerMetric.server_id == server.id)).scalar()
    oldest = db.execute(select(func.min(ContainerRollup.bucket_start)).where(ContainerRollup.server_id == server.id)).scalar()
    known = [_utc(moment) for moment in (first, oldest) if moment]
    since = min(known) if known else now
    start = max(now - length, since) if length else since
    origin = int(start.timestamp())
    slot_seconds = max(1, -(-int((now - start).total_seconds() + 1) // SPARKLINE_POINTS))
    usage: dict[str, _Usage] = {}
    raw_from = start
    if tier:
        done = rollups._read_watermark(db, rollups.HOUR if tier == rollups.HOUR else tier)
        closed = min(done, now) if done and done > start else start
        table = ContainerRollup
        query = select(
            table.container_id, table.name, table.bucket_start, table.avg_cpu, table.n_cpu, table.max_cpu, table.avg_memory_mb, table.n_memory_mb, table.max_memory_mb,
            table.max_restarts, table.samples, table.down_samples, table.avg_swap_mb, table.n_swap_mb, table.max_swap_mb,
        ).where(table.server_id == server.id, table.tier == tier, table.bucket_start >= rollups.floor(tier, start), table.bucket_start < closed).limit(MAX_ROLLUP_ROWS)
        for container_id, name, bucket, avg_cpu, n_cpu, max_cpu, avg_memory, n_memory, max_memory, restarts, samples, down, avg_swap, n_swap, max_swap in db.execute(query):
            slot = max(0, min(SPARKLINE_POINTS - 1, (int(_utc(bucket).timestamp()) - origin) // slot_seconds))
            usage.setdefault(container_id, _Usage()).add(
                name, avg_cpu * n_cpu if avg_cpu is not None else None, n_cpu, max_cpu, avg_memory * n_memory if avg_memory is not None else None, n_memory, max_memory,
                restarts, restarts, samples, down, slot)
            usage[container_id].add_swap(avg_swap * n_swap if avg_swap is not None and n_swap else None, n_swap or 0, max_swap)
        # What the summaries haven't reached yet comes from the check-ins themselves, when those are still recent.
        raw_from = max(closed, now - timedelta(days=2)) if closed > start else start if now - start <= timedelta(days=2) else closed
    _raw_usage(db, usage, server.id, raw_from, now + timedelta(seconds=1), origin, slot_seconds)
    containers = []
    for container_id, item in usage.items():
        containers.append({
            "container_id": container_id, "name": item.name,
            "avg_cpu": item.cpu / item.cpu_n if item.cpu_n else None, "max_cpu": item.cpu_max,
            "avg_memory_mb": item.memory / item.memory_n if item.memory_n else None, "max_memory_mb": item.memory_max,
            "avg_swap_mb": item.swap / item.swap_n if item.swap_n else None, "max_swap_mb": item.swap_max,
            "restarts": item.restarts_high - item.restarts_low if item.restarts_high is not None else None,
            "samples": item.samples, "down_samples": item.down,
            "sparkline": [round(item.spark[slot][0] / item.spark[slot][1], 2) if slot in item.spark else None for slot in range(SPARKLINE_POINTS)],
        })
    containers.sort(key=lambda item: (-(item["avg_cpu"] if item["avg_cpu"] is not None else -1), item["name"]))
    return {"server_id": server.id, "range": range_name, "start": start.isoformat(), "end": now.isoformat(), "containers": containers}


def busiest_containers(db, server: Server, range_name: str, now: datetime | None = None) -> dict:
    """Per period of the range, the container that used the most CPU and how much, so a spike on the server's
    chart can name what caused it. Periods in which no container reported are left out."""
    now = _utc(now or datetime.now(timezone.utc))
    length = history.RANGES[range_name]
    tier = BUSIEST_TIER.get(range_name)
    best: dict[int, tuple[float, str]] = {}

    def offer(moment: int, name: str, cpu: float | None) -> None:
        if cpu is not None and (moment not in best or cpu > best[moment][0]):
            best[moment] = (cpu, name)

    if tier:
        step = history.NATURAL_SECONDS[tier]
        table = ContainerRollup
        query = select(table.bucket_start, table.name, table.max_cpu).where(table.server_id == server.id, table.tier == tier, table.max_cpu.isnot(None))
        if length:
            query = query.where(table.bucket_start >= rollups.floor(tier, now - length))
        for bucket, name, cpu in db.execute(query.limit(MAX_ROLLUP_ROWS)):
            offer(int(_utc(bucket).timestamp()), name, cpu)
        done = rollups._read_watermark(db, tier)
        raw_from = max(done, now - timedelta(days=2)) if done else now - min(length or timedelta(days=2), timedelta(days=2))
    else:
        step = history.nice_step(max(length.total_seconds() / history.MAX_POINTS, server.check_interval_seconds))
        raw_from = now - length
    # Check-ins the summaries haven't reached (or, for the short ranges, all of them), grouped by the database.
    raw_step = step if not tier else history.NATURAL_SECONDS[rollups.HOUR] if tier == rollups.HOUR else None
    if raw_step:
        table = ContainerMetric
        bucket = _epoch_bucket(table.time, raw_step).label("bucket")
        query = select(bucket, table.container_id, func.max(table.name), func.max(table.cpu_pct)).where(table.server_id == server.id, table.time >= raw_from, table.time <= now).group_by(bucket, table.container_id)
        for index, _container_id, name, cpu in db.execute(query):
            offer(int(index) * raw_step, name, cpu)
    return {"server_id": server.id, "range": range_name, "bucket_seconds": step,
            "points": [{"time": datetime.fromtimestamp(moment, timezone.utc).isoformat(), "name": name, "cpu": round(cpu, 2)} for moment, (cpu, name) in sorted(best.items())]}


SPIKE_PERCENT = 90.0
MAX_SPIKES = 500
MAX_SPIKE_ROWS = 150_000


def cpu_spikes(db, server: Server, range_name: str, now: datetime | None = None) -> dict:
    """Every stretch in which the server's CPU stayed at or above 90%, with how long it lasted, read from the
    check-ins themselves so a spike shorter than a chart point still has a length. Only ranges that still have
    check-ins (up to a week) can be answered; longer ones give no spikes and say so."""
    now = _utc(now or datetime.now(timezone.utc))
    length = history.RANGES[range_name]
    if history.SOURCE[range_name] != history.DETAILED or length is None:
        return {"server_id": server.id, "range": range_name, "available": False, "threshold": SPIKE_PERCENT, "spikes": []}
    rows = db.execute(select(HostMetric.time, HostMetric.cpu_utilization_pct).where(HostMetric.server_id == server.id, HostMetric.time >= now - length, HostMetric.time <= now).order_by(HostMetric.time).limit(MAX_SPIKE_ROWS))
    spikes, current, previous = [], None, None
    # A check-in that is late by more than a few intervals ends a spike: nobody knows what happened in between.
    patience = timedelta(seconds=server.check_interval_seconds * 3)
    for moment, cpu in rows:
        moment = _utc(moment)
        high = cpu is not None and cpu >= SPIKE_PERCENT
        if current and (not high or moment - previous > patience):
            spikes.append(current)
            current = None
        if high:
            if current is None:
                current = {"start": moment, "end": moment, "peak": cpu}
            current["end"], current["peak"] = moment, max(current["peak"], cpu)
        previous = moment
    if current:
        spikes.append(current)
    return {"server_id": server.id, "range": range_name, "available": True, "threshold": SPIKE_PERCENT, "spikes": [
        # One check-in at 90% stands for about one interval, not for no time at all.
        {"time": spike["start"].isoformat(), "seconds": round((spike["end"] - spike["start"]).total_seconds()) + server.check_interval_seconds, "peak": round(spike["peak"], 1)}
        for spike in spikes[-MAX_SPIKES:]]}
