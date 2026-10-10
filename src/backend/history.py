"""Range-aware history for charts: the last hour to all time, from the right level of detail.

A request names a range (``1h`` ... ``all``). Short ranges are read from the check-in samples, longer ones from the
hourly, weekly or monthly summaries (rollups.py). Whatever the source, the answer is a series of at most about
``MAX_POINTS`` points, each with the average and maximum of every requested metric, so a year costs about as much
as an hour. The newest period, which the rollup job has not closed yet, is built from the finer data below it, so
the right-hand edge is always current. Periods without data have no point: a gap stays a gap, and the response
says how long one point's period is (``bucket_seconds``) so a chart can tell a gap from a neighbour.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from . import rollups
from .models import ContainerMetric, ContainerRollup, HostMetric, HostRollup, MountMetric, MountRollup

DETAILED = "detailed"
RANGES: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24), "7d": timedelta(days=7),
    "30d": timedelta(days=30), "90d": timedelta(days=90), "1y": timedelta(days=365), "all": None,
}
# Which level of detail answers each range.
SOURCE = {"1h": DETAILED, "6h": DETAILED, "24h": DETAILED, "7d": DETAILED,
          "30d": rollups.HOUR, "90d": rollups.WEEK, "1y": rollups.WEEK, "all": rollups.MONTH}
MAX_POINTS = 500
NATURAL_SECONDS = {rollups.HOUR: 3600, rollups.DAY: 86400, rollups.WEEK: 7 * 86400, rollups.MONTH: 31 * 86400}
# Bucket sizes a chart can be given; the smallest that keeps a range within MAX_POINTS is used.
NICE_STEPS = (15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400, 7 * 86400)
# The tiers a partly finished period of each tier is assembled from, coarsest first.
FINER_CHAIN = {rollups.HOUR: (), rollups.DAY: (rollups.HOUR,), rollups.WEEK: (rollups.DAY, rollups.HOUR), rollups.MONTH: (rollups.DAY, rollups.HOUR)}


@dataclass(frozen=True)
class Metric:
    """How one charted value is read from a raw row and from a rollup row. Either side may be absent."""

    raw: str
    avg: str | None = None   # rollup column holding the average (its count column is n_<name>)
    max: str | None = None


def _m(raw, name, *, avg=True, maximum=True):
    return Metric(raw, f"avg_{name}" if avg else None, f"max_{name}" if maximum else None)


HOST_METRICS = {
    "cpu": _m("cpu_utilization_pct", "cpu"), "load": _m("load_1m", "load"), "ram": _m("ram_pct", "ram"), "swap": _m("swap_pct", "swap"),
    "disk": _m("disk_usage_pct", "disk"), "inodes": _m("disk_inodes_pct", "inodes"),
    "docker_ms": _m("docker_latency_ms", "docker_ms"), "collect_ms": _m("collect_duration_ms", "collect_ms"),
}
MOUNT_METRICS = {
    "used_pct": _m("used_pct", "used_pct"), "used_gb": _m("used_gb", "used_gb", maximum=False), "total_gb": _m("total_gb", "total_gb", avg=False),
    "inodes_pct": _m("inodes_pct", "inodes_pct"),
}
CONTAINER_METRICS = {
    "cpu": _m("cpu_pct", "cpu"), "memory_mb": _m("memory_used_mb", "memory_mb"), "memory_pct": _m("memory_pct", "memory_pct"),
    "memory_limit_mb": _m("memory_limit_mb", "memory_limit_mb", avg=False), "restarts": _m("restart_count", "restarts", avg=False),
}
DEFAULT_HOST_METRICS = ("cpu", "ram", "swap", "load", "disk")


@dataclass(frozen=True)
class Series:
    raw: Any
    rollup: Any
    metrics: dict[str, Metric]
    has_down: bool = False


HOST = Series(HostMetric, HostRollup, HOST_METRICS)
MOUNT = Series(MountMetric, MountRollup, MOUNT_METRICS)
CONTAINER = Series(ContainerMetric, ContainerRollup, CONTAINER_METRICS, has_down=True)


class Agg:
    """Sum, count and maximum of each metric over some check-ins; adding two of them gives the exact combined figures."""

    __slots__ = ("samples", "down", "values")

    def __init__(self, samples: int = 0, down: int = 0):
        self.samples, self.down, self.values = samples, down, {}

    def add_value(self, name: str, total: float | None, count: int, maximum: float | None) -> None:
        current = self.values.setdefault(name, [0.0, 0, None])
        if total is not None and count:
            current[0] += total
            current[1] += count
        if maximum is not None and (current[2] is None or maximum > current[2]):
            current[2] = maximum

    def merge(self, other: "Agg") -> None:
        self.samples += other.samples
        self.down += other.down
        for name, (total, count, maximum) in other.values.items():
            self.add_value(name, total, count, maximum)


def _raw_agg(spec: Series, wanted, row) -> Agg:
    agg = Agg(samples=1)
    for index, name in enumerate(wanted):
        value = row[index + 1]
        if value is not None:
            agg.add_value(name, float(value), 1, float(value))
    if spec.has_down:
        agg.down = 0 if row[-1] == "running" else 1
    return agg


def _rollup_agg(spec: Series, wanted, row) -> Agg:
    agg = Agg(samples=row.samples, down=getattr(row, "down_samples", 0) if spec.has_down else 0)
    for name in wanted:
        metric = spec.metrics[name]
        average = getattr(row, metric.avg) if metric.avg else None
        count = getattr(row, "n_" + metric.avg[len("avg_"):]) if metric.avg else 0
        maximum = getattr(row, metric.max) if metric.max else None
        agg.add_value(name, average * count if average is not None and count else None, count if average is not None else 0, maximum)
    return agg


def _point(spec: Series, wanted, start: datetime, agg: Agg) -> dict:
    point: dict[str, Any] = {"time": start.isoformat(), "samples": agg.samples}
    if spec.has_down:
        point["down"] = agg.down
    for name in wanted:
        total, count, maximum = agg.values.get(name, (0.0, 0, None))
        metric = spec.metrics[name]
        value: dict[str, float] = {}
        if metric.avg and count:
            value["avg"] = total / count
        if metric.max and maximum is not None:
            value["max"] = maximum
        point[name] = value or None
    return point


def nice_step(seconds: float) -> int:
    return next((step for step in NICE_STEPS if step >= seconds), NICE_STEPS[-1])


def _where(spec: Series, kind: str, server_id: int, key: str | None):
    """Filters that pick one server's series (and one mount or container) from a raw or rollup table."""
    table = spec.raw if kind == "raw" else spec.rollup
    conditions = [table.server_id == server_id]
    if spec is MOUNT:
        conditions.append(table.mount == key)
    elif spec is CONTAINER:
        conditions.append(table.container_id == key)
    return conditions


def _raw_items(db, spec: Series, wanted, server_id, key, since: datetime, until: datetime):
    columns = [getattr(spec.raw, spec.metrics[name].raw) for name in wanted]
    if spec.has_down:
        columns.append(spec.raw.status)
    query = select(spec.raw.time, *columns).where(*_where(spec, "raw", server_id, key), spec.raw.time >= since, spec.raw.time < until).order_by(spec.raw.time)
    return [(rollups._utc(row[0]), _raw_agg(spec, wanted, row)) for row in db.execute(query)]


def _rollup_items(db, spec: Series, wanted, server_id, key, tier: str, since: datetime, until: datetime):
    if until <= since:
        return []
    query = select(spec.rollup).where(*_where(spec, "rollup", server_id, key), spec.rollup.tier == tier,
                                      spec.rollup.bucket_start >= since, spec.rollup.bucket_start < until).order_by(spec.rollup.bucket_start)
    return [(rollups._utc(row.bucket_start), _rollup_agg(spec, wanted, row)) for row in db.scalars(query)]


def data_since(db, spec: Series, server_id: int, key: str | None) -> datetime | None:
    """The earliest moment this series has data: the first sample if it is still stored, else the start of the
    oldest summary that reaches further back than anything finer.

    A summary's bucket starts on a boundary (midnight, Monday), which can be long before the first check-in, so a
    coarser tier only counts when its oldest bucket ends before the finer data begins; otherwise the finer data
    already says exactly when the history starts.
    """
    first = db.execute(select(func.min(spec.raw.time)).where(*_where(spec, "raw", server_id, key))).scalar()
    since = rollups._utc(first) if first else None
    for tier in (rollups.HOUR, rollups.DAY, rollups.WEEK, rollups.MONTH):
        oldest = db.execute(select(func.min(spec.rollup.bucket_start)).where(*_where(spec, "rollup", server_id, key), spec.rollup.tier == tier)).scalar()
        if oldest is None:
            continue
        oldest = rollups._utc(oldest)
        if since is None or rollups.following(tier, oldest) <= since:
            since = oldest
    return since


def _group(items, bucket_of):
    grouped: dict[datetime, Agg] = {}
    for start, agg in items:
        grouped.setdefault(bucket_of(start), Agg()).merge(agg)
    return grouped


def _tiered_items(db, spec, wanted, server_id, key, tier: str, window_start: datetime, now: datetime):
    """One Agg per bucket of ``tier``: closed buckets from the rollups, the newest ones from the finer data."""
    first = rollups.floor(tier, window_start)
    done = rollups._read_watermark(db, tier)
    closed_until = min(done, now) if done and done > first else first
    items = _rollup_items(db, spec, wanted, server_id, key, tier, first, closed_until)
    grouped = {start: agg for start, agg in items}
    tail: list = []
    cursor = closed_until
    for finer in FINER_CHAIN[tier]:
        finer_done = rollups._read_watermark(db, finer)
        if finer_done and finer_done > cursor:
            tail += _rollup_items(db, spec, wanted, server_id, key, finer, cursor, min(finer_done, now))
            cursor = min(finer_done, now)
    tail += _raw_items(db, spec, wanted, server_id, key, cursor, now + timedelta(seconds=1))
    for start, agg in _group(tail, lambda moment: rollups.floor(tier, moment)).items():
        grouped.setdefault(start, Agg()).merge(agg)
    return sorted(grouped.items()), NATURAL_SECONDS[tier]


def history(db, spec: Series, server_id: int, key: str | None, range_name: str, wanted, interval_seconds: int, now: datetime | None = None) -> dict:
    """The chart series for one server (and mount or container) over ``range_name``."""
    now = rollups._utc(now or datetime.now(timezone.utc))
    since = data_since(db, spec, server_id, key)
    length = RANGES[range_name]
    window_start = max(now - length, since) if length and since else (since or now - (length or timedelta(0)))
    source = SOURCE[range_name]
    if since is None:
        items, natural = [], NATURAL_SECONDS.get(source, interval_seconds)
    elif source == DETAILED:
        items, natural = _raw_items(db, spec, wanted, server_id, key, window_start, now + timedelta(seconds=1)), interval_seconds
    else:
        items, natural = _tiered_items(db, spec, wanted, server_id, key, source, window_start, now)
    span = max((now - window_start).total_seconds(), 1)
    step = max(nice_step(max(span / MAX_POINTS, interval_seconds if source == DETAILED else 0)), natural if source != DETAILED else 0)
    if source == DETAILED or step > natural:
        epoch = lambda moment: datetime.fromtimestamp(int(moment.timestamp()) // step * step, tz=timezone.utc)  # noqa: E731
        items = sorted(_group(items, epoch).items())
    else:
        step = natural
    return {
        "range": range_name, "source": source, "bucket_seconds": step,
        "start": window_start.isoformat(), "end": now.isoformat(), "data_since": since.isoformat() if since else None,
        "metrics": list(wanted), "points": [_point(spec, wanted, start, agg) for start, agg in items],
    }
