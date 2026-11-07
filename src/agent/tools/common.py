"""What every read-only tool shares: finding the server, the time window, reading a series at the right level of
detail, and the fact IDs a diagnosis cites.

Fact IDs say what a fact is, not when it was read (``metric:web1.cpu``, ``container:web1/nginx``), so the same
question gives the same ID every time. The window goes into the label instead; when the agent reads the same series
twice, the two labels are joined (``merge_labels``).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func

from src.backend import history, rollups
from src.backend.models import Server
from src.backend.retention import DETAILED_KEPT

from ..bounds import points, stamp, utc

MAX_DAYS = 90                          # how far back any tool reads
DEFAULT_WINDOW = timedelta(hours=24)
MAX_LIST = 30                          # newest entries of any list
MAX_LABEL = 240
OFFLINE_AFTER_MISSED_CHECKS = 3        # as in src/backend/main.py


class ToolError(ValueError):
    """A mistake in the call, told to the model so it can correct it. Never carries internal details."""


class Facts:
    """The fact IDs one call returns, each with a short label for checking citations and showing them."""

    def __init__(self):
        self.labels: dict[str, str] = {}

    def add(self, fact_id: str, label: str) -> str:
        self.labels[fact_id] = label[:MAX_LABEL]
        return fact_id


def merge_labels(known: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """Labels of a whole conversation: a fact read again with another window keeps both windows in its label."""
    merged = dict(known)
    for fact_id, label in new.items():
        old = merged.get(fact_id)
        merged[fact_id] = label if old is None else old if label in old else f"{old}; {label}"[:MAX_LABEL]
    return merged


def parse_time(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        raise ToolError(f"{name} must be an ISO 8601 time such as 2026-11-07T10:00 (UTC), or null.") from None


def oldest(now: datetime) -> datetime:
    return now - timedelta(days=MAX_DAYS)


def window(arguments: dict, now: datetime, default: timedelta = DEFAULT_WINDOW) -> tuple[datetime, datetime]:
    """``from`` and ``to`` from the call: ``to`` defaults to now, ``from`` to ``default`` before ``to``. Both must lie
    within the last MAX_DAYS days, and ``from`` before ``to``."""
    until = min(parse_time(arguments.get("to"), "to") or now, now)
    since = parse_time(arguments.get("from"), "from") or until - default
    if since >= until:
        raise ToolError("from must be earlier than to.")
    if since < oldest(now):
        raise ToolError(f"Tools read at most the last {MAX_DAYS} days: from must be after {stamp(oldest(now))}.")
    return since, until


def moment(value: str | None, now: datetime, name: str = "at") -> datetime:
    """A single moment (``at``): now when null, otherwise within the last MAX_DAYS days."""
    at = min(parse_time(value, name) or now, now)
    if at < oldest(now):
        raise ToolError(f"Tools read at most the last {MAX_DAYS} days: {name} must be after {stamp(oldest(now))}.")
    return at


def span(since: datetime, until: datetime) -> str:
    """A window for a label: ``2026-11-03 10:00–12:00`` or ``2026-11-01 00:00 – 2026-11-07 00:00`` (UTC)."""
    start, end = stamp(since).replace("T", " ")[:16], stamp(until).replace("T", " ")[:16]
    return f"{start}–{end[11:]} UTC" if start[:10] == end[:10] else f"{start} – {end} UTC"


def find_server(db, name: str) -> Server:
    server = db.query(Server).filter(Server.hostname == name).first() or db.query(Server).filter(func.lower(Server.hostname) == name.lower()).first()
    if server is None:
        known = [hostname for (hostname,) in db.query(Server.hostname).order_by(Server.hostname).limit(MAX_LIST)]
        raise ToolError(f"No server is named {name!r}. Servers: {', '.join(known) or 'none'}.")
    return server


def is_online(server: Server, at: datetime) -> bool:
    last = utc(server.last_seen_at)
    return last is not None and at - last < timedelta(seconds=server.check_interval_seconds * OFFLINE_AFTER_MISSED_CHECKS)


def read(db, spec: history.Series, server_id: int, key: str | None, names, since: datetime, until: datetime, now: datetime):
    """[(start, Agg)] for ``names`` between ``since`` and ``until``, and where it came from: every check-in while
    those are kept (DETAILED_KEPT), hourly summaries for anything older."""
    if since >= now - DETAILED_KEPT:
        return history._raw_items(db, spec, list(names), server_id, key, since, until), "check-ins"
    items, _ = history._tiered_items(db, spec, list(names), server_id, key, rollups.HOUR, since, until)
    return [(start, agg) for start, agg in items if since <= start < until], "hourly summaries"


def summary(fact_id: str, label: str, unit: str, items, name: str, source: str) -> dict | None:
    """One metric of ``items`` as the agent sees it: min, max, average, first and last, and up to SERIES_POINTS
    points that each keep the highest value of their stretch, so short peaks are not averaged away."""
    rows = []
    total = count = 0.0
    peak = None
    for start, agg in items:
        value_total, value_count, value_max = agg.values.get(name, (0.0, 0, None))
        average = value_total / value_count if value_count else None
        rows.append((start, value_max if value_max is not None else average, average))
        total, count = total + value_total, count + value_count
        if value_max is not None and (peak is None or value_max > peak):
            peak = value_max
    averages = [average for _, _, average in rows if average is not None]
    if not averages and peak is None:
        return None
    values = averages or [peak]
    return {
        "id": fact_id, "label": label, "unit": unit, "source": source,
        "stats": {"min": round(min(values), 2), "max": round(peak if peak is not None else max(values), 2), "avg": round(total / count if count else sum(values) / len(values), 2),
                  "first": round(values[0], 2), "last": round(values[-1], 2)},
        "points": points([(start, value) for start, value, _ in rows]),
    }
