"""Read models for the Overview page: sparklines, the all-clear summary and a server's event timeline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .alerts import INFO, SERVER_OFFLINE_RULE
from .models import ContainerMetric, HostMetric, IncidentAlert, Server, SystemEvent
from .rules import CONTAINER_EVENT_KINDS, NETWORK_EVENT_KINDS

SPARKLINE_POINTS = 40
MAX_EVENTS = 2000


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def downsample(values: list[float | None], limit: int = SPARKLINE_POINTS) -> list[float | None]:
    """At most ``limit`` points, each the average of its share of ``values``; gaps stay gaps."""
    if len(values) <= limit:
        return [None if value is None else round(value, 2) for value in values]
    result = []
    for index in range(limit):
        chunk = [value for value in values[index * len(values) // limit:(index + 1) * len(values) // limit] if value is not None]
        result.append(round(sum(chunk) / len(chunk), 2) if chunk else None)
    return result


def fleet_sparklines(db, minutes: int, now: datetime | None = None) -> dict[int, list[float | None]]:
    """Recent CPU use of every server, oldest first, for the fleet rack."""
    since = (now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)
    series: dict[int, list[float | None]] = {}
    for server_id, cpu in db.query(HostMetric.server_id, HostMetric.cpu_utilization_pct).filter(HostMetric.time >= since).order_by(HostMetric.time):
        series.setdefault(server_id, []).append(cpu)
    return {server_id: downsample(values) for server_id, values in series.items()}


def container_sparklines(db, server_id: int, minutes: int, now: datetime | None = None) -> dict[str, list[float | None]]:
    """Recent CPU use of every container on one server, oldest first, for the container table."""
    since = (now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)
    series: dict[str, list[float | None]] = {}
    rows = db.query(ContainerMetric.container_id, ContainerMetric.cpu_pct).filter(ContainerMetric.server_id == server_id, ContainerMetric.time >= since).order_by(ContainerMetric.time)
    for container_id, cpu in rows:
        series.setdefault(container_id, []).append(cpu)
    return {container_id: downsample(values) for container_id, values in series.items()}


def longest_quiet_seconds(spans: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> float:
    """The longest stretch between ``start`` and ``end`` that none of the (opened, closed) ``spans`` covers."""
    longest, cursor = 0.0, start
    for opened, closed in sorted(spans):
        if opened > cursor:
            longest = max(longest, (min(opened, end) - cursor).total_seconds())
        cursor = max(cursor, closed)
        if cursor >= end:
            break
    return max(longest, (end - cursor).total_seconds(), 0.0)


def quiet_summary(db, now: datetime | None = None) -> dict:
    """What the Overview says when nothing needs attention: the last incident and how long it has been quiet.

    Notices (INFO) don't count: they never asked for attention in the first place.
    """
    now = _utc(now or datetime.now(timezone.utc))
    serious = IncidentAlert.severity != INFO
    last = db.query(IncidentAlert).filter(serious, IncidentAlert.status == "resolved", IncidentAlert.resolved_at.isnot(None)).order_by(IncidentAlert.resolved_at.desc()).first()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_server = db.query(Server.created_at).order_by(Server.created_at).first()
    # A month the install only partly existed in is measured from when it started watching.
    window_start = max(month_start, _utc(first_server[0])) if first_server else now
    spans = [
        (_utc(alert.created_at), _utc(alert.resolved_at) if alert.resolved_at else now)
        for alert in db.query(IncidentAlert).filter(serious, (IncidentAlert.resolved_at.is_(None)) | (IncidentAlert.resolved_at >= window_start))
    ]
    hostname = db.query(Server.hostname).filter(Server.id == last.server_id).scalar() if last else None
    return {
        "last_incident": {"title": last.title, "hostname": hostname, "severity": last.severity, "created_at": _utc(last.created_at).isoformat(), "resolved_at": _utc(last.resolved_at).isoformat()} if last else None,
        "quiet_since": (_utc(last.resolved_at) if last else _utc(first_server[0]) if first_server else now).isoformat(),
        "longest_quiet_seconds_this_month": round(longest_quiet_seconds(spans, window_start, now)),
    }


def _event_source(kind: str) -> str:
    return "container" if kind in CONTAINER_EVENT_KINDS else "network" if kind in NETWORK_EVENT_KINDS else "system"


def server_events(db, server: Server, days: int, now: datetime | None = None) -> list[dict]:
    """Everything that happened on one server, newest first: its recorded events plus alerts opening and clearing.

    A server going offline and coming back is told as the collector's story rather than as an alert.
    """
    now = _utc(now or datetime.now(timezone.utc))
    since = now - timedelta(days=days)
    events: list[dict] = []
    for event in db.query(SystemEvent).filter(SystemEvent.server_id == server.id, SystemEvent.time >= since).order_by(SystemEvent.time.desc()).limit(MAX_EVENTS):
        events.append({"time": _utc(event.time).isoformat(), "source": _event_source(event.kind), "kind": event.kind, "detail": event.detail})
    alerts = db.query(IncidentAlert).filter(IncidentAlert.server_id == server.id, (IncidentAlert.created_at >= since) | (IncidentAlert.resolved_at >= since)).order_by(IncidentAlert.created_at.desc()).limit(MAX_EVENTS)
    for alert in alerts:
        offline = alert.rule_key == SERVER_OFFLINE_RULE
        created, resolved = _utc(alert.created_at), _utc(alert.resolved_at) if alert.resolved_at else None
        common = {"alert_id": alert.id, "rule_key": alert.rule_key, "severity": alert.severity, "title": alert.title}
        if created >= since:
            events.append({**common, "time": created.isoformat(), "source": "collector" if offline else "alert", "kind": "collector_stopped" if offline else "alert_opened",
                           "detail": alert.description, "resolved": resolved is not None})
        if resolved and resolved >= since:
            events.append({**common, "time": resolved.isoformat(), "source": "collector" if offline else "alert", "kind": "collector_resumed" if offline else "alert_resolved",
                           "detail": alert.description, "duration_seconds": round((resolved - created).total_seconds())})
    events.sort(key=lambda event: event["time"], reverse=True)
    return events[:MAX_EVENTS]
