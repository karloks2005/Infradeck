"""Keeps what the agent is told small: text clipped, series cut to a few dozen points with the peaks kept, numbers
rounded. Shared by the evidence pack (evidence.py) and the read-only tools (tools/), so both read the same way.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

SERIES_POINTS = 40
MAX_TEXT = 300


def utc(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def stamp(moment: datetime | None) -> str | None:
    """A time as the agent sees it everywhere: ISO 8601 in UTC, to the minute."""
    return None if moment is None else utc(moment).isoformat(timespec="minutes")


def clip(text: str | None, limit: int = MAX_TEXT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def points(rows: list[tuple[datetime, float | None]], limit: int = SERIES_POINTS) -> list[list]:
    """[[time, value], …] with at most ``limit`` points, keeping each group's highest value (peaks matter)."""
    rows = [(moment, value) for moment, value in rows if value is not None]
    if len(rows) > limit:
        size = math.ceil(len(rows) / limit)
        rows = [(rows[start][0], max(value for _, value in rows[start:start + size])) for start in range(0, len(rows), size)]
    return [[stamp(moment), round(value, 2)] for moment, value in rows]


def stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"min": round(min(values), 2), "max": round(max(values), 2), "avg": round(sum(values) / len(values), 2), "first": round(values[0], 2), "last": round(values[-1], 2)}


def series(fact_id: str, label: str, unit: str, rows: list[tuple[datetime, float | None]], limit: float | None = None) -> dict | None:
    values = [value for _, value in rows if value is not None]
    if not values:
        return None
    return {"id": fact_id, "label": label, "unit": unit, "limit": limit, "stats": stats(values), "points": points(rows)}
