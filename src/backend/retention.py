"""How long history is kept, and deleting what is older.

- Every check-in (host, container and filesystem samples) and every availability result: 7 days. Charts for
  longer ranges read the summaries (rollups.py), so nothing past a week needs the per-minute rows.
- Hourly summaries, the hourly availability summaries and events (reboots, OOM kills, kernel messages, container
  and network changes): the admin's setting, one year by default, two years or forever. Never less than a year.
- Daily, weekly and monthly summaries: forever. They are a few kilobytes per server a year.

Nothing is deleted before the coarser level that replaces it exists: check-ins only once their hour is summarised,
hourly summaries only once their day is. Deletes go in chunks, so the first purge on a big table doesn't lock it
for long.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import availability, rollups
from .models import AppSetting, CheckRollup, ContainerMetric, ContainerRollup, HostMetric, HostRollup, MountMetric, MountRollup, SystemEvent

DETAILED_KEPT = timedelta(days=7)
HISTORY_KEY = "history_retention_days"
FOREVER = 0
HISTORY_CHOICES = (365, 730, FOREVER)
DEFAULT_HISTORY_DAYS = 365
CHUNK = 10_000


def history_days(db) -> int:
    """How many days hourly summaries and events are kept; FOREVER (0) keeps them."""
    setting = db.get(AppSetting, HISTORY_KEY)
    try:
        days = int(setting.value) if setting else DEFAULT_HISTORY_DAYS
    except ValueError:
        return DEFAULT_HISTORY_DAYS
    return days if days in HISTORY_CHOICES else DEFAULT_HISTORY_DAYS


def describe(days: int) -> str:
    return "forever" if days == FOREVER else "1 year" if days == 365 else f"{days // 365} years"


def _delete_in_chunks(db, model, *conditions) -> int:
    total = 0
    while True:
        ids = [row_id for (row_id,) in db.query(model.id).filter(*conditions).limit(CHUNK)]
        if not ids:
            return total
        db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        total += len(ids)


def purge(db, now: datetime | None = None) -> int:
    """Delete everything past its retention. Returns how many rows went."""
    now = now or datetime.now(timezone.utc)
    deleted = 0
    summarised_until = rollups.hour_watermark(db)
    if summarised_until is not None:
        cutoff = min(now - DETAILED_KEPT, summarised_until)
        for model in (HostMetric, ContainerMetric, MountMetric):
            deleted += _delete_in_chunks(db, model, model.time < cutoff)
    deleted += availability.purge(db, now)
    days = history_days(db)
    if days == FOREVER:
        return deleted
    cutoff = now - timedelta(days=days)
    deleted += _delete_in_chunks(db, SystemEvent, SystemEvent.time < cutoff)
    deleted += _delete_in_chunks(db, CheckRollup, CheckRollup.hour < cutoff)
    days_until = rollups._read_watermark(db, rollups.DAY)
    if days_until is not None:
        cutoff = min(cutoff, days_until)
        for model in (HostRollup, MountRollup, ContainerRollup):
            deleted += _delete_in_chunks(db, model, model.tier == rollups.HOUR, model.bucket_start < cutoff)
    return deleted
