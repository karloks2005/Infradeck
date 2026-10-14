"""The evidence pack: everything the agent is told about one incident, collected from stored data without any model.

Every fact has a stable ID (``metric:main``, ``metric:host.cpu``, ``event:812``, ``check:2``, ``incident:31``), and a
diagnosis may only point at facts by these IDs, so a claim that cites something not in the pack can be found and
dropped. The same incident and data always give the same pack, and its size is bounded: series are cut to a few
dozen points, lists to their newest entries, text to a few hundred characters. All text is masked (masking.py)
before the pack is stored or sent anywhere.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from src.backend import incidents
from src.backend.models import ContainerMetric, HostMetric, IncidentAlert, MountMetric, Server, SystemEvent

from .masking import mask

LEAD = timedelta(minutes=60)
SERIES_POINTS = 40
MAX_EVENTS = 30
MAX_IMAGE_CHANGES = 10
MAX_SIMILAR = 5
MAX_TEXT = 300
SIMILAR_DAYS = 90
IMAGE_HISTORY_DAYS = 30


def _utc(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _clip(text: str | None, limit: int = MAX_TEXT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _points(rows: list[tuple[datetime, float | None]], limit: int = SERIES_POINTS) -> list[list]:
    """[[time, value], …] with at most ``limit`` points, keeping each group's highest value (peaks matter)."""
    rows = [(moment, value) for moment, value in rows if value is not None]
    if len(rows) > limit:
        size = math.ceil(len(rows) / limit)
        rows = [(rows[start][0], max(value for _, value in rows[start:start + size])) for start in range(0, len(rows), size)]
    return [[_utc(moment).isoformat(timespec="minutes"), round(value, 2)] for moment, value in rows]


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"min": round(min(values), 2), "max": round(max(values), 2), "avg": round(sum(values) / len(values), 2), "first": round(values[0], 2), "last": round(values[-1], 2)}


def _series(fact_id: str, label: str, unit: str, rows: list[tuple[datetime, float | None]], limit: float | None = None) -> dict | None:
    values = [value for _, value in rows if value is not None]
    if not values:
        return None
    return {"id": fact_id, "label": label, "unit": unit, "limit": limit, "stats": _stats(values), "points": _points(rows)}


def build(db, incident: IncidentAlert, now: datetime | None = None) -> dict:
    """The pack for ``incident``: what it is, the machine, metrics around it, events, image changes, the rule's own
    checks and similar incidents before it. Masked and bounded."""
    now = _utc(now or datetime.now(timezone.utc))
    server = db.get(Server, incident.server_id)
    created, resolved = _utc(incident.created_at), _utc(incident.resolved_at)
    since, until = created - LEAD, resolved or now
    kind = incidents.rule_kind(incident.rule_key)
    subject = incidents.subject(incident.rule_key)
    rule = incidents.diagnose(db, incident, server, now) if server else {}

    metrics = []
    main = rule.get("evidence")
    if main and main.get("series"):
        rows = [(datetime.fromisoformat(point["time"]), point["value"]) for point in main["series"]]
        metrics.append(_series("metric:main", f"{main['title']} (what the rule watches)", main.get("unit") or "", rows, main.get("limit")))
    host_rows = db.query(HostMetric.time, HostMetric.cpu_utilization_pct, HostMetric.ram_pct, HostMetric.swap_pct, HostMetric.load_1m) \
        .filter(HostMetric.server_id == incident.server_id, HostMetric.time >= since, HostMetric.time <= until).order_by(HostMetric.time).limit(20000).all()
    for index, (fact, label, unit) in enumerate((("host.cpu", "host CPU", "%"), ("host.memory", "host memory", "%"), ("host.swap", "host swap", "%"), ("host.load", "host load (1 min)", "")), start=1):
        metrics.append(_series(f"metric:{fact}", label, unit, [(row[0], row[index]) for row in host_rows], server.cpu_cores if fact == "host.load" and server else None))
    if kind.startswith("container:") and subject:
        container_rows = db.query(ContainerMetric.time, ContainerMetric.cpu_pct, ContainerMetric.memory_used_mb, ContainerMetric.restart_count) \
            .filter(ContainerMetric.server_id == incident.server_id, ContainerMetric.name == subject, ContainerMetric.time >= since, ContainerMetric.time <= until).order_by(ContainerMetric.time).limit(20000).all()
        metrics.append(_series("metric:container.cpu", f"{subject} CPU", "%", [(row[0], row[1]) for row in container_rows]))
        metrics.append(_series("metric:container.restarts", f"{subject} restart count (running total)", "", [(row[0], row[3]) for row in container_rows]))
    if kind in ("host:disk", "host:inodes") and subject:
        mounts = db.query(MountMetric.time, MountMetric.used_gb, MountMetric.total_gb).filter(MountMetric.server_id == incident.server_id, MountMetric.mount == subject, MountMetric.time >= since, MountMetric.time <= until).order_by(MountMetric.time).limit(20000).all()
        metrics.append(_series("metric:mount.used_gb", f"{subject} used (total {mounts[-1][2]:.0f} GB)" if mounts and mounts[-1][2] else f"{subject} used", "GB", [(row[0], row[1]) for row in mounts]))
    metrics = [metric for metric in metrics if metric]

    latest = db.query(HostMetric.time).filter(HostMetric.server_id == incident.server_id).order_by(HostMetric.time.desc()).limit(1).scalar()
    containers = []
    if latest is not None:
        for row in db.query(ContainerMetric).filter(ContainerMetric.server_id == incident.server_id, ContainerMetric.time == latest).order_by(ContainerMetric.memory_used_mb.desc()).limit(8):
            containers.append({"name": row.name, "image": _clip(row.image, 120), "status": row.status, "health": row.healthcheck_status, "cpu_pct": row.cpu_pct, "memory_mb": row.memory_used_mb,
                               "memory_limit_mb": row.memory_limit_mb, "restarts": row.restart_count, "exit_code": row.exit_code, "oom_killed": row.oom_killed})

    events = db.query(SystemEvent).filter(SystemEvent.server_id == incident.server_id, SystemEvent.time >= since - timedelta(hours=1), SystemEvent.time <= until) \
        .order_by(SystemEvent.time.desc()).limit(MAX_EVENTS).all()
    image_changes = []
    if subject and kind.startswith("container:"):
        image_changes = db.query(SystemEvent).filter(SystemEvent.server_id == incident.server_id, SystemEvent.kind == "image_changed", SystemEvent.event_key == subject,
                                                     SystemEvent.time >= created - timedelta(days=IMAGE_HISTORY_DAYS), SystemEvent.time <= until).order_by(SystemEvent.time.desc()).limit(MAX_IMAGE_CHANGES).all()
    seen = {event.id for event in events}

    similar = db.query(IncidentAlert).filter(IncidentAlert.server_id == incident.server_id, IncidentAlert.id != incident.id, IncidentAlert.created_at >= created - timedelta(days=SIMILAR_DAYS),
                                             IncidentAlert.created_at < created).order_by(IncidentAlert.created_at.desc()).limit(200).all()
    similar = [item for item in similar if incidents.rule_kind(item.rule_key) == kind][:MAX_SIMILAR]

    pack = {
        "incident": {"id": f"incident:{incident.id}", "reference": incidents.reference(incident.id), "rule": incident.rule_key, "kind": kind, "subject": subject, "severity": incident.severity,
                     "title": _clip(incident.title), "description": _clip(incident.description, 600), "opened_at": created.isoformat(timespec="minutes"),
                     "resolved_at": resolved.isoformat(timespec="minutes") if resolved else None, "status": incident.status},
        "server": {"hostname": server.hostname if server else None, "os": server.os_info if server else None, "cpu_cores": server.cpu_cores if server else None,
                   "memory_gb": round(server.total_ram_gb, 1) if server and server.total_ram_gb else None},
        "window": {"from": since.isoformat(timespec="minutes"), "to": until.isoformat(timespec="minutes")},
        "metrics": metrics,
        "containers_now": containers,
        "events": [{"id": f"event:{event.id}", "time": _utc(event.time).isoformat(timespec="minutes"), "kind": event.kind, "text": _clip(event.detail)} for event in reversed(events)],
        "image_changes": [{"id": f"event:{event.id}", "time": _utc(event.time).isoformat(timespec="minutes"), "text": _clip(event.detail)} for event in reversed(image_changes) if event.id not in seen]
                         + [{"id": f"event:{event.id}", "time": _utc(event.time).isoformat(timespec="minutes"), "text": _clip(event.detail), "also_in": "events"} for event in reversed(image_changes) if event.id in seen],
        "rule_checks": [{"id": f"check:{index}", "text": _clip(signal["text"]), "holds": signal["ok"], "source": signal.get("source")} for index, signal in enumerate(rule.get("signals") or [], start=1)],
        "similar_incidents": [{"id": f"incident:{item.id}", "reference": incidents.reference(item.id), "title": _clip(item.title), "opened_at": _utc(item.created_at).isoformat(timespec="minutes"),
                               "status": item.status, "resolution": _clip(item.resolution) if item.resolution else None} for item in similar],
    }
    return mask(pack)


def index(pack: dict) -> dict[str, str]:
    """Every fact ID in ``pack`` with a short label, for checking a diagnosis's references and showing them."""
    labels = {pack["incident"]["id"]: f"{pack['incident']['reference']} itself"}
    for metric in pack["metrics"]:
        labels[metric["id"]] = metric["label"]
    for event in pack["events"] + pack["image_changes"]:
        labels[event["id"]] = f"{event['time'][11:16]} {event.get('kind', 'image change').replace('_', ' ')}"
    for check in pack["rule_checks"]:
        labels[check["id"]] = check["text"]
    for item in pack["similar_incidents"]:
        labels[item["id"]] = f"{item['reference']} · {item['title']}"
    return labels
