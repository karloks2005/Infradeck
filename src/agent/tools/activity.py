"""Tools about what happened: events on a machine, earlier incidents, availability check results, and the state of
the whole fleet at one moment."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import or_

from src.backend import availability, incidents
from src.backend.models import Check, CheckResult, CheckRollup, HostMetric, IncidentAlert, Server, SystemEvent

from ..bounds import clip, series as bounded_series, stamp, utc
from .common import MAX_LIST, OFFLINE_AFTER_MISSED_CHECKS, Facts, ToolError, find_server, is_online, moment, span, window

EVENT_KINDS = ("reboot", "oom_kill", "kernel_fault", "kernel_error", "image_changed", "container_added", "container_removed", "container_notice",
               "port_opened", "port_closed", "firewall_changed", "firewall_allowed", "firewall_weakened", "packages_updated", "helper_notice")
INCIDENT_WINDOW = timedelta(days=30)
MAX_CHECKS = 10
MAX_SERVERS = 50
MAX_ERRORS = 5


def _event_label(event: SystemEvent, hostname: str) -> str:
    return f"{stamp(event.time)[5:16].replace('T', ' ')} {event.kind.replace('_', ' ')} · {hostname}"


def events(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"])
    since, until = window(arguments, now)
    query = db.query(SystemEvent).filter(SystemEvent.server_id == server.id, SystemEvent.time >= since, SystemEvent.time < until)
    if arguments["kinds"]:
        query = query.filter(SystemEvent.kind.in_(arguments["kinds"]))
    total = query.count()
    newest = query.order_by(SystemEvent.time.desc()).limit(MAX_LIST).all()
    return {"server": server.hostname, "from": stamp(since), "to": stamp(until), "total": total, "shown": len(newest),
            "events": [{"id": facts.add(f"event:{event.id}", _event_label(event, server.hostname)), "time": stamp(event.time), "kind": event.kind, "text": clip(event.detail)}
                       for event in reversed(newest)]}


def find_incidents(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    server = find_server(db, arguments["server"]) if arguments["server"] is not None else None
    since, until = window(arguments, now, INCIDENT_WINDOW)
    query = db.query(IncidentAlert).filter(IncidentAlert.created_at >= since, IncidentAlert.created_at < until)
    if server:
        query = query.filter(IncidentAlert.server_id == server.id)
    found = [item for item in query.order_by(IncidentAlert.created_at.desc()).limit(2000) if arguments["kind"] is None or incidents.rule_kind(item.rule_key) == arguments["kind"]]
    names = dict(db.query(Server.id, Server.hostname).all())
    listed = []
    for item in found[:MAX_LIST]:
        reference = incidents.reference(item.id)
        opened, resolved = utc(item.created_at), utc(item.resolved_at)
        listed.append({"id": facts.add(f"incident:{item.id}", f"{reference} · {clip(item.title, 120)}"), "reference": reference, "server": names.get(item.server_id),
                       "kind": incidents.rule_kind(item.rule_key), "severity": item.severity, "title": clip(item.title), "state": incidents.state_of(item),
                       "opened_at": stamp(opened), "resolved_at": stamp(resolved), "lasted_minutes": round(((resolved or now) - opened).total_seconds() / 60),
                       "resolution": clip(item.resolution) if item.resolution else None})
    return {"server": server.hostname if server else "all", "from": stamp(since), "to": stamp(until), "total": len(found), "incidents": listed}


def _pick_checks(db, arguments: dict) -> list[Check]:
    if (arguments["check"] is None) == (arguments["server"] is None):
        raise ToolError("Give either check (its name) or server, not both.")
    if arguments["check"] is not None:
        name = arguments["check"].strip().lower()
        checks = [check for check in db.query(Check).order_by(Check.name) if check.name.lower() == name]
        if not checks:
            known = [check_name for (check_name,) in db.query(Check.name).order_by(Check.name).limit(MAX_LIST)]
            raise ToolError(f"No availability check is named {arguments['check']!r}. Checks: {', '.join(known) or 'none'}.")
        return checks[:1]
    server = find_server(db, arguments["server"])
    return [check for check in db.query(Check).order_by(Check.name) if server.id in availability.servers_of(check)][:MAX_CHECKS]


def _check_report(db, check: Check, since: datetime, until: datetime, now: datetime, names: dict[int, str], facts: Facts) -> dict:
    """Up and down over the window: completed hours from the hourly summaries once raw results are gone, then every
    result (which also tells when it was down and why)."""
    total = down = 0
    response: list[tuple[datetime, float | None]] = []
    rolled = utc(check.rolled_until)
    raw_from = since
    if since < now - availability.RAW_KEPT and rolled:
        raw_from = min(max(since, rolled), until)
        for hour, slots_count, down_count, max_ms in db.query(CheckRollup.hour, CheckRollup.slots, CheckRollup.down_slots, CheckRollup.max_ms) \
                .filter(CheckRollup.check_id == check.id, CheckRollup.hour >= since, CheckRollup.hour < raw_from).order_by(CheckRollup.hour):
            total, down = total + slots_count, down + down_count
            response.append((utc(hour), max_ms))
    results = availability._results(db, check, raw_from, until) if raw_from < until else []
    periods: list[list[datetime]] = []   # [start, end] of each run of down slots
    step = timedelta(seconds=check.interval_seconds)
    for slot in availability.slots(check, results):
        start = datetime.fromisoformat(slot["start"])
        total += 1
        response.append((start, slot["response_ms"]))
        if slot["status"] == "down":
            down += 1
            if periods and periods[-1][1] == start:
                periods[-1][1] = start + step
            else:
                periods.append([start, start + step])
    errors = Counter(clip(result.detail, 160) for result in results if not result.ok and result.detail)
    fact_id = facts.add(f"uptime:{check.id}", f"check {clip(check.name, 60)} · {span(since, until)}")
    return {
        "id": fact_id, "name": check.name, "type": check.type, "target": availability.redacted_target(check.target), "state": check.state, "paused": check.paused,
        "runs_from": [names.get(server, f"server {server}") for server in availability.servers_of(check)],
        "uptime_pct": round(100 * (total - down) / total, 2) if total else None, "checks_in_window": total, "down": down,
        "down_periods": [{"from": stamp(start), "to": stamp(end)} for start, end in periods[-MAX_LIST:]], "errors": [{"detail": detail, "count": count} for detail, count in errors.most_common(MAX_ERRORS)],
        "response_ms": bounded_series(fact_id, "response time (each point the slowest of its stretch)", "ms", response),
    }


def check_results(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    checks = _pick_checks(db, arguments)
    since, until = window(arguments, now)
    names = dict(db.query(Server.id, Server.hostname).all())
    return {"from": stamp(since), "to": stamp(until), "checks": [_check_report(db, check, since, until, now, names, facts) for check in checks],
            "note": None if checks else "No availability checks run from this server."}


def _failing(db, at: datetime) -> dict[int, list[str]]:
    """Per server, the checks whose last result there before ``at`` failed."""
    failing: dict[int, list[str]] = {}
    for check in db.query(Check).filter(Check.paused.is_(False)):
        recent = max(timedelta(minutes=10), timedelta(seconds=check.interval_seconds * (check.retries + 2)))
        last: dict[int, bool] = {}
        for result in db.query(CheckResult).filter(CheckResult.check_id == check.id, CheckResult.time > at - recent, CheckResult.time <= at).order_by(CheckResult.time):
            last[result.server_id] = result.ok
        for server_id, ok in last.items():
            if not ok:
                failing.setdefault(server_id, []).append(clip(check.name, 80))
    return failing


def fleet_snapshot(db, arguments: dict, now: datetime, facts: Facts) -> dict:
    at = moment(arguments["at"], now)
    servers = db.query(Server).order_by(Server.hostname).limit(MAX_SERVERS + 1).all()
    open_then = db.query(IncidentAlert).filter(IncidentAlert.created_at <= at, or_(IncidentAlert.resolved_at.is_(None), IncidentAlert.resolved_at > at)) \
        .order_by(IncidentAlert.created_at.desc()).limit(500).all()
    checks_known = at >= now - availability.RAW_KEPT
    failing = _failing(db, at) if checks_known else {}
    output = []
    for server in servers[:MAX_SERVERS]:
        if at >= now - timedelta(minutes=1):
            online = is_online(server, now)
        else:
            gap = timedelta(seconds=server.check_interval_seconds * OFFLINE_AFTER_MISSED_CHECKS)
            online = db.query(HostMetric.id).filter(HostMetric.server_id == server.id, HostMetric.time > at - gap, HostMetric.time <= at).first() is not None
        mine = [item for item in open_then if item.server_id == server.id]
        for item in mine[:3]:
            facts.add(f"incident:{item.id}", f"{incidents.reference(item.id)} · {clip(item.title, 120)}")
        output.append({
            "id": facts.add(f"fleet:{server.hostname}", f"{server.hostname} at {stamp(at)[5:16].replace('T', ' ')} UTC"),
            "server": server.hostname, "online": online, "open_incidents": len(mine),
            "worst": "CRITICAL" if any(item.severity == "CRITICAL" for item in mine) else "WARNING" if mine else None,
            "newest_incidents": [{"id": f"incident:{item.id}", "title": clip(item.title, 120), "severity": item.severity} for item in mine[:3]],
            "failing_checks": failing.get(server.id, []) if checks_known else None,
        })
    return {"at": stamp(at), "servers": output, "more_servers": max(0, len(servers) - MAX_SERVERS),
            "note": None if checks_known else f"Check results older than {availability.RAW_KEPT.days} days are kept only as hourly totals, so failing checks are not known for that time."}
