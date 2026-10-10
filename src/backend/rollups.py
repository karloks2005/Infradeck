"""Summarised history: hourly, daily, weekly and monthly rollups of the metrics tables.

Charts and statistics over weeks, months and years can't read every per-minute row, and keeping those rows for a
year isn't realistic. A background job therefore turns them into one summary row per bucket (average and maximum
per metric). Each tier is built from the finer one *when its period closes*:

    check-in samples -> hour -> day -> week (Monday to Sunday) -> month

All buckets are aligned to UTC. Rules the job keeps:

* Only complete buckets are written, a few minutes after they end so that check-ins in flight are not missed.
* A bucket is written by deleting whatever was there and inserting it again in one transaction, so running the job
  twice, or again after a failure, never duplicates a row.
* A watermark per tier (``rollup_done_<tier>`` in ``app_settings``) records how far each tier got. A stretch with no
  data advances it too, so a long gap can't make the job stand still. After downtime the job catches up, a limited
  number of buckets per run.
* A period in which a server sent nothing has no row for it. A gap stays a gap; nothing is filled in.
* Averages are exact. Each row stores, per averaged metric, how many check-ins reported it (``n_<metric>``), and a
  coarser tier weights by that, so a day with few check-ins, or a container that was stopped (and so reported no
  CPU) for half of it, counts for what it really measured. The maximum is kept so a short spike stays visible in
  a yearly view. A metric that was never reported stays None.

Deleting old rows is a separate matter (see ``purge_old_metrics`` in main.py); the purge only ever removes
check-in samples that have already been summarised.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, case, cast, delete, func, select

from .models import AppSetting, ContainerMetric, ContainerRollup, HostMetric, HostRollup, MountMetric, MountRollup

HOUR, DAY, WEEK, MONTH = "hour", "day", "week", "month"
TIERS = (HOUR, DAY, WEEK, MONTH)
FINER = {DAY: HOUR, WEEK: DAY, MONTH: DAY}
GRACE = timedelta(minutes=5)
# Per run, so catching up after a long outage (or the first run on a big database) works in chunks.
MAX_BUCKETS_PER_RUN = {HOUR: 400, DAY: 60, WEEK: 30, MONTH: 12}
WATERMARK_PREFIX = "rollup_done_"

# (rollup column, aggregate, source column on the raw table). "avg" is an average of the raw values, or a weighted
# average of the finer tier's averages (weighted by n_<metric>); "max" is a maximum either way.
HOST_COLUMNS = (
    ("avg_cpu", "avg", "cpu_utilization_pct"), ("max_cpu", "max", "cpu_utilization_pct"),
    ("avg_load", "avg", "load_1m"), ("max_load", "max", "load_1m"),
    ("avg_ram", "avg", "ram_pct"), ("max_ram", "max", "ram_pct"),
    ("avg_swap", "avg", "swap_pct"), ("max_swap", "max", "swap_pct"),
    ("avg_disk", "avg", "disk_usage_pct"), ("max_disk", "max", "disk_usage_pct"),
    ("avg_inodes", "avg", "disk_inodes_pct"), ("max_inodes", "max", "disk_inodes_pct"),
    ("avg_docker_ms", "avg", "docker_latency_ms"), ("max_docker_ms", "max", "docker_latency_ms"),
    ("avg_collect_ms", "avg", "collect_duration_ms"), ("max_collect_ms", "max", "collect_duration_ms"),
)
MOUNT_COLUMNS = (
    ("avg_used_pct", "avg", "used_pct"), ("max_used_pct", "max", "used_pct"),
    ("avg_used_gb", "avg", "used_gb"), ("max_total_gb", "max", "total_gb"),
    ("avg_inodes_pct", "avg", "inodes_pct"), ("max_inodes_pct", "max", "inodes_pct"),
)
CONTAINER_COLUMNS = (
    ("avg_cpu", "avg", "cpu_pct"), ("max_cpu", "max", "cpu_pct"),
    ("avg_memory_mb", "avg", "memory_used_mb"), ("max_memory_mb", "max", "memory_used_mb"),
    ("avg_memory_pct", "avg", "memory_pct"), ("max_memory_pct", "max", "memory_pct"),
    ("max_memory_limit_mb", "max", "memory_limit_mb"), ("max_restarts", "max", "restart_count"),
)
# What each kind of rollup groups by and builds from.
KINDS = (
    (HostRollup, HostMetric, ("server_id",), HOST_COLUMNS),
    (MountRollup, MountMetric, ("server_id", "mount"), MOUNT_COLUMNS),
    (ContainerRollup, ContainerMetric, ("server_id", "container_id"), CONTAINER_COLUMNS),
)


def _utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything stored here is UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def floor(tier: str, moment: datetime) -> datetime:
    """The start of the bucket that contains ``moment``."""
    moment = _utc(moment)
    if tier == HOUR:
        return moment.replace(minute=0, second=0, microsecond=0)
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if tier == DAY:
        return day
    if tier == WEEK:
        return day - timedelta(days=day.weekday())  # Monday
    if tier == MONTH:
        return day.replace(day=1)
    raise ValueError(f"Unknown tier: {tier}")


def following(tier: str, start: datetime) -> datetime:
    """The start of the bucket after the one beginning at ``start``."""
    if tier == HOUR:
        return start + timedelta(hours=1)
    if tier == DAY:
        return start + timedelta(days=1)
    if tier == WEEK:
        return start + timedelta(days=7)
    if tier == MONTH:
        return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    raise ValueError(f"Unknown tier: {tier}")


def _read_watermark(db, tier: str) -> datetime | None:
    setting = db.get(AppSetting, WATERMARK_PREFIX + tier)
    try:
        return _utc(datetime.fromisoformat(setting.value)) if setting else None
    except ValueError:
        return None


def _write_watermark(db, tier: str, value: datetime) -> None:
    setting = db.get(AppSetting, WATERMARK_PREFIX + tier)
    if setting:
        setting.value = value.isoformat()
    else:
        db.add(AppSetting(key=WATERMARK_PREFIX + tier, value=value.isoformat()))


def hour_watermark(db) -> datetime | None:
    """Check-in samples before this moment have been summarised and may be purged."""
    return _read_watermark(db, HOUR)


def _aggregates(source, columns, from_raw: bool):
    expressions = []
    for name, kind, raw in columns:
        if kind == "max":
            expressions.append(func.max(getattr(source, raw if from_raw else name)).label(name))
            continue
        count_name = "n_" + name[len("avg_"):]
        if from_raw:
            expressions.append(func.avg(getattr(source, raw)).label(name))
            expressions.append(func.count(getattr(source, raw)).label(count_name))
        else:
            value, count = getattr(source, name), getattr(source, count_name)
            weighted = func.sum(case((value.is_not(None), value * count), else_=0))
            expressions.append((cast(weighted, Float) / func.nullif(func.sum(case((value.is_not(None), count), else_=0)), 0)).label(name))
            expressions.append(func.sum(count).label(count_name))
    return expressions


def _build_bucket(db, tier: str, start: datetime, end: datetime) -> int:
    """Replace every rollup row of ``tier`` that begins at ``start`` and return how many were written."""
    written = 0
    for rollup, raw, keys, columns in KINDS:
        db.execute(delete(rollup).where(rollup.tier == tier, rollup.bucket_start == start))
        from_raw = tier == HOUR
        source = raw if from_raw else rollup
        key_columns = [getattr(source, key) for key in keys]
        extra = []
        if rollup is ContainerRollup:
            extra.append(func.max(source.name).label("name"))
        if from_raw:
            samples = func.count().label("samples")
            if rollup is ContainerRollup:
                extra.append(func.coalesce(func.sum(case((ContainerMetric.status != "running", 1), else_=0)), 0).label("down_samples"))
            window = (source.time >= start, source.time < end)
        else:
            samples = func.sum(source.samples).label("samples")
            if rollup is ContainerRollup:
                extra.append(func.coalesce(func.sum(source.down_samples), 0).label("down_samples"))
            window = (source.tier == FINER[tier], source.bucket_start >= start, source.bucket_start < end)
        query = select(*key_columns, samples, *extra, *_aggregates(source, columns, from_raw)).where(*window).group_by(*key_columns)
        for row in db.execute(query):
            values = row._asdict()
            db.add(rollup(tier=tier, bucket_start=start, **values))
            written += 1
    return written


def _first_source_time(db, tier: str) -> datetime | None:
    """The earliest row the tier can be built from, in any of the tables it summarises."""
    if tier == HOUR:
        found = [db.execute(select(func.min(raw.time))).scalar() for _, raw, _, _ in KINDS]
    else:
        found = [db.execute(select(func.min(rollup.bucket_start)).where(rollup.tier == FINER[tier])).scalar() for rollup, _, _, _ in KINDS]
    found = [_utc(value) for value in found if value]
    return min(found) if found else None


def _run_tier(db, tier: str, now: datetime) -> int:
    cursor = _read_watermark(db, tier)
    if cursor is None:
        first = _first_source_time(db, tier)
        if first is None:
            return 0
        cursor = floor(tier, first)
    # The finer tier must have got past a bucket before that bucket can be built from it.
    ready_until = now - GRACE
    if tier != HOUR:
        finer_done = _read_watermark(db, FINER[tier])
        if finer_done is None:
            return 0
        ready_until = min(ready_until, finer_done)
    built = 0
    while built < MAX_BUCKETS_PER_RUN[tier]:
        end = following(tier, cursor)
        if end > ready_until:
            break
        _build_bucket(db, tier, cursor, end)
        # The rows and the watermark are saved together, so a failure never leaves one without the other.
        _write_watermark(db, tier, end)
        db.commit()
        cursor = end
        built += 1
    return built


def run_rollups(db, now: datetime | None = None) -> dict[str, int]:
    """Build every bucket that has completed since the last run, finest tier first. Returns buckets built per tier."""
    now = _utc(now or datetime.now(timezone.utc))
    return {tier: _run_tier(db, tier, now) for tier in TIERS}
