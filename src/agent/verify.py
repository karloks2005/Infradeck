"""Watching an incident after its diagnosis: the condition the agent gave ("gitlab runs 15 minutes without a restart",
"memory stays under 80 % for 10 minutes") is checked against stored data, without the model, and the incident is
resolved once it holds.

Many rules resolve their incidents themselves as soon as they stop firing. This is for the ones that stay open for
a window after the fact (a container exit, an OOM kill, a restart loop counted over 30 minutes): once the fix
holds, the agent closes the incident and writes down how it was confirmed. So that the rule doesn't open it again
at the next check-in while its window still covers the old events, the same rule on that server stays quiet until
``reopen_after``, unless the condition breaks again first, which lifts the hold at once.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.backend import incidents
from src.backend.models import AgentReport, AppSetting, ContainerMetric, HostMetric, IncidentAlert, MountMetric
from src.backend.rules import DEFAULT_THRESHOLDS

WATCHED = ("container_running_for", "metric_below_for")
STALE_AFTER = timedelta(minutes=5)       # with no sample this recent, there is nothing to judge
MAX_MINUTES = 24 * 60
MAX_WATCHED = 200
HOLD_MARGIN = timedelta(minutes=10)


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def describe(verify: dict | None) -> str | None:
    """The condition in words, or None when nothing is watched."""
    if not verify or verify.get("type") not in WATCHED or not verify.get("minutes"):
        return None
    minutes = int(verify["minutes"])
    if verify["type"] == "container_running_for":
        return f"{verify.get('target') or 'the container'} runs {minutes} min without a restart"
    unit = verify.get("unit") or ""   # from the evidence pack (%, MB, GB); reports from before it was kept have none
    return f"{verify.get('label') or verify.get('target')} stays under {verify.get('threshold'):g}{unit if unit == '%' else f' {unit}' if unit else ''} for {minutes} min"


def _metric_rows(db, incident: IncidentAlert, metric: str, since: datetime) -> list[tuple[datetime, float | None]]:
    """The samples behind a metric ID from the evidence pack, oldest first."""
    kind, subject = incidents.rule_kind(incident.rule_key), incidents.subject(incident.rule_key)
    host = {"metric:host.cpu": HostMetric.cpu_utilization_pct, "metric:host.memory": HostMetric.ram_pct, "metric:host.swap": HostMetric.swap_pct, "metric:host.load": HostMetric.load_1m}
    if metric == "metric:main":
        metric = {"host:memory": "metric:host.memory", "host:swap": "metric:host.swap", "host:load": "metric:host.load"}.get(kind, metric)
    column = mount_column = container_column = None
    if metric in host:
        column = host[metric]
    elif metric == "metric:mount.used_gb" or (metric == "metric:main" and kind in ("host:disk", "host:inodes")):
        mount_column = MountMetric.used_gb if metric == "metric:mount.used_gb" else MountMetric.used_pct if kind == "host:disk" else MountMetric.inodes_pct
    elif metric in ("metric:container.cpu", "metric:container.restarts") or (metric == "metric:main" and kind.startswith(("container:", "trend:"))):
        container_column = {"metric:container.cpu": ContainerMetric.cpu_pct, "metric:container.restarts": ContainerMetric.restart_count}.get(metric, ContainerMetric.memory_used_mb)
    if column is not None:
        return db.query(HostMetric.time, column).filter(HostMetric.server_id == incident.server_id, HostMetric.time >= since).order_by(HostMetric.time).limit(5000).all()
    if mount_column is not None and subject:
        return db.query(MountMetric.time, mount_column).filter(MountMetric.server_id == incident.server_id, MountMetric.mount == subject, MountMetric.time >= since).order_by(MountMetric.time).limit(5000).all()
    if container_column is not None and subject:
        return db.query(ContainerMetric.time, container_column).filter(ContainerMetric.server_id == incident.server_id, ContainerMetric.name == subject, ContainerMetric.time >= since).order_by(ContainerMetric.time).limit(5000).all()
    return []


def _held_for(rows: list, ok) -> float | None:
    """Minutes from the earliest sample of the unbroken run of good samples at the end until the newest one; None
    when the newest sample itself isn't good (the condition is broken right now)."""
    if not rows or not ok(rows[-1]):
        return None
    start = None
    for row in reversed(rows):
        if not ok(row):
            break
        start = row
    return (_utc(rows[-1][0]) - _utc(start[0])).total_seconds() / 60


def evaluate(db, incident: IncidentAlert, verify: dict | None, now: datetime | None = None) -> dict | None:
    """{"text", "minutes", "held_minutes", "holds", "known", "broken"} for a watched condition; None when nothing is
    watched. ``known`` is False when the data is too old to judge (the collector stopped, the container is gone);
    ``broken`` is True when the newest sample fails the condition."""
    text = describe(verify)
    if text is None:
        return None
    now = now or datetime.now(timezone.utc)
    minutes = max(1, min(MAX_MINUTES, int(verify["minutes"])))
    since = now - timedelta(minutes=minutes * 2 + 30)
    if verify["type"] == "container_running_for":
        rows = db.query(ContainerMetric.time, ContainerMetric.status, ContainerMetric.restart_count).filter(
            ContainerMetric.server_id == incident.server_id, ContainerMetric.name == (verify.get("target") or ""), ContainerMetric.time >= since).order_by(ContainerMetric.time).limit(5000).all()
        latest_restarts = rows[-1][2] if rows else None
        held = _held_for(rows, lambda row: row[1] == "running" and row[2] == latest_restarts)
    else:
        threshold = verify.get("threshold")
        rows = _metric_rows(db, incident, verify.get("target") or "", since) if threshold is not None else []
        held = _held_for(rows, lambda row: row[1] is not None and row[1] < threshold)
    known = bool(rows) and now - _utc(rows[-1][0]) <= STALE_AFTER
    return {"text": text, "minutes": minutes, "held_minutes": round(held or 0, 1), "holds": known and held is not None and held >= minutes, "known": known, "broken": known and held is None}


def _hold(db) -> timedelta:
    """How long a rule stays quiet after the agent resolved its incident: its longest event window, and a margin."""
    setting = db.get(AppSetting, "alert_thresholds")
    try:
        thresholds = {**DEFAULT_THRESHOLDS, **(json.loads(setting.value) if setting else {})}
    except ValueError:
        thresholds = dict(DEFAULT_THRESHOLDS)
    return timedelta(minutes=max(thresholds["event_window_minutes"], thresholds["restart_loop_window_minutes"])) + HOLD_MARGIN


def _latest_ok(db, incident_id: int) -> AgentReport | None:
    return db.query(AgentReport).filter(AgentReport.incident_id == incident_id, AgentReport.status == "ok").order_by(AgentReport.version.desc()).first()


def _verify_of(report: AgentReport | None) -> dict | None:
    if report is None or not report.body:
        return None
    verify = json.loads(report.body).get("verify")
    return verify if verify and verify.get("type") in WATCHED else None


def watch(db, now: datetime | None = None) -> dict:
    """One round: resolve the incidents whose condition holds, and lift the hold on the rules whose condition broke
    again. Returns {"resolved": [ids], "released": [ids]}."""
    now = now or datetime.now(timezone.utc)
    resolved, released = [], []
    active = db.query(IncidentAlert).filter(IncidentAlert.status == "active", IncidentAlert.severity.in_(("WARNING", "CRITICAL"))).order_by(IncidentAlert.id.desc()).limit(MAX_WATCHED).all()
    for incident in active:
        verify = _verify_of(_latest_ok(db, incident.id))
        result = evaluate(db, incident, verify, now)
        if result and result["holds"]:
            incident.status, incident.resolved_at = "resolved", now
            incident.resolution = f"Resolved by the agent: {result['text']}"[:255]
            incident.reopen_after = now + _hold(db)
            resolved.append(incident.id)
    held = db.query(IncidentAlert).filter(IncidentAlert.status == "resolved", IncidentAlert.reopen_after.isnot(None), IncidentAlert.reopen_after > now).limit(MAX_WATCHED).all()
    for incident in held:
        result = evaluate(db, incident, _verify_of(_latest_ok(db, incident.id)), now)
        if result and result["broken"]:
            incident.reopen_after = None   # broke again: let the rule open a new incident at once
            released.append(incident.id)
    db.commit()
    return {"resolved": resolved, "released": released}

