"""Collector lifecycle, servers, metrics ingest and the monitoring reads (overview, history, live, sparklines,
events, container detail). The cleaning/validation/rule helpers and the Pydantic request models stay in main;
this module is the HTTP layer and reaches them through `main.`. All /monitoring routes live here together so their
match order (specific paths before `{param}` paths) is preserved."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request, Response, status
from fastapi import Path as PathParam
from sqlalchemy import case, func, text

from .. import main
from ..config import SESSION_COOKIE_NAME
from ..db import get_db

router = APIRouter()


@router.get("/monitoring/overview")
def monitoring_overview(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    # Servers belong to the install: everyone signed in can view them, only admins manage them.
    user = main._current_user(session_id, db)
    # The dashboard's own machine first, then the newest servers.
    servers = db.query(main.Server).order_by(main.Server.is_dashboard_host.desc(), main.Server.created_at.desc()).all()
    unmuted = (main.IncidentAlert.status == "active", (main.IncidentAlert.muted_until.is_(None)) | (main.IncidentAlert.muted_until <= datetime.now(timezone.utc)))
    alert_counts = dict(db.query(main.IncidentAlert.server_id, func.count()).filter(*unmuted).group_by(main.IncidentAlert.server_id).all())
    severity_counts: dict[int, dict[str, int]] = {}
    for server_id, severity, count in db.query(main.IncidentAlert.server_id, main.IncidentAlert.severity, func.count()).filter(*unmuted).group_by(main.IncidentAlert.server_id, main.IncidentAlert.severity):
        severity_counts.setdefault(server_id, {})[severity] = count
    since = datetime.now(timezone.utc) - timedelta(days=7)
    events = main.recent_system_events(db, since)
    container_events = main.recent_container_events(db, since)
    network_events = main.recent_network_events(db, since)
    listeners: dict[int, list[dict]] = {}
    for row in db.query(main.ListenerState).order_by(main.ListenerState.port, main.ListenerState.proto):
        if len(listeners.setdefault(row.server_id, [])) < main.LISTENERS_SHOWN:
            listeners[row.server_id].append({"proto": row.proto, "port": row.port, "scope": row.scope, "first_seen": row.first_seen.isoformat()})
    firewalls: dict[int, list[dict]] = {}
    for row in db.query(main.FirewallState).order_by(main.FirewallState.name):
        firewalls.setdefault(row.server_id, []).append({"name": row.name, "label": main.FIREWALL_LABELS.get(row.name, row.name), "rules": row.rules, "enabled": row.enabled, "updated_at": row.updated_at.isoformat()})
    result = []
    for server in servers:
        metric = db.query(main.HostMetric).filter(main.HostMetric.server_id == server.id).order_by(main.HostMetric.time.desc()).first()
        latest = {item.container_id: item for item in main.latest_containers(db, server.id, metric.time if metric else None)}
        mounts = db.query(main.MountMetric).filter(main.MountMetric.server_id == server.id, main.MountMetric.time == metric.time).order_by(main.MountMetric.mount).all() if metric else []
        result.append({
            "id": server.id, "hostname": server.hostname, "ip_address": server.ip_address, "os_info": server.os_info,
            "cpu_cores": server.cpu_cores, "total_ram_gb": server.total_ram_gb,
            "check_interval_seconds": server.check_interval_seconds, "is_dashboard_host": server.is_dashboard_host,
            "active_alert_count": alert_counts.get(server.id, 0), "alerts_by_severity": severity_counts.get(server.id, {}),
            "system": main._load_json(server.system_info), "created_at": server.created_at.isoformat(),
            "last_seen_at": server.last_seen_at.isoformat() if server.last_seen_at else None,
            "host": ({key: getattr(metric, key) for key in ("time", "cpu_utilization_pct", "load_1m", "load_5m", "load_15m", "ram_used_gb", "ram_available_gb", "ram_pct", "swap_pct", "disk_usage_pct", "disk_inodes_pct", "net_bytes_sent_mb", "net_bytes_recv_mb", "system_uptime_seconds", "oom_kill_total", "kernel_tainted", "ntp_synced", "reboot_required", "last_shutdown", "docker_status", "docker_reason", "docker_latency_ms", "internet_status", "internet_latency_ms")} if metric else None),
            "network": {"listeners": listeners.get(server.id, []), "firewall": firewalls.get(server.id, []), "events": network_events.get(server.id, [])},
            "container_events": container_events.get(server.id, []),
            "system_events": events.get(server.id, []),
            "mounts": [{key: getattr(m, key) for key in ("mount", "fs_type", "total_gb", "used_gb", "used_pct", "inodes_pct")} for m in mounts],
            "containers": [{"container_id": c.container_id, "name": c.name, "image": c.image, "status": c.status, "restart_count": c.restart_count, "cpu_pct": c.cpu_pct, "memory_used_mb": c.memory_used_mb, "memory_pct": c.memory_pct, "memory_limit_mb": c.memory_limit_mb, "swap_mb": c.swap_mb, "ports": c.ports, "project": c.project, "started_at": c.started_at, "healthcheck_status": c.healthcheck_status, "exit_code": c.exit_code, "oom_killed": c.oom_killed, "finished_at": c.finished_at.isoformat() if c.finished_at else None, "time": c.time.isoformat()} for c in latest.values()],
        })
    active_alerts = db.query(main.IncidentAlert).filter(*unmuted)
    # Most severe first, then newest.
    severity_rank = case({severity: rank for rank, severity in enumerate(reversed(main.SEVERITIES))}, value=main.IncidentAlert.severity, else_=len(main.SEVERITIES))
    alerts = main._spread_alerts(active_alerts.order_by(severity_rank, main.IncidentAlert.created_at.desc()).limit(main.OVERVIEW_ALERT_SCAN).all())
    sees_reports = main.agent_settings.sees_diagnoses(db, "admin" if main._is_admin(db, user["id"]) else "user")
    hostnames = {server.id: server.hostname for server in servers}
    servers_by_id = {server.id: server for server in servers}
    thresholds = main.get_alert_thresholds(db)
    pending = main.visible_pending_collectors(db) if main._is_admin(db, user["id"]) and not user.get("token") else []
    totals: dict[str, int] = {}
    for counts in severity_counts.values():
        for severity, count in counts.items():
            totals[severity] = totals.get(severity, 0) + count
    return {
        "servers": result, "pending_collectors": pending, "alert_count": active_alerts.count(), "alerts_by_severity": totals,
        "checks_down": db.query(main.Check).filter(main.Check.state == "down", main.Check.paused.is_(False)).count(),
        "version": main.APP_VERSION, "instance_name": main.get_instance_name(db), "server_time": datetime.now(timezone.utc).isoformat(),
        # Only worked out when nothing needs attention, which is the only time the page shows it.
        "quiet": main.dashboard.quiet_summary(db) if not (totals.get(main.CRITICAL) or totals.get("WARNING")) else None,
        # Lets the dashboard color disk bars the same way the alert rules judge them.
        "mount_thresholds": main.get_mount_thresholds(db),
        # The same limits the alert rules use, so the page's amber and orange mean what an alert would.
        "alert_thresholds": {key: thresholds[key] for key in ("memory_warning_pct", "memory_critical_pct", "swap_warning_pct")},
        "disk_thresholds": {key: thresholds[key] for key in ("disk_warning_pct", "disk_critical_pct", "inodes_warning_pct", "inodes_critical_pct")},
        "alerts": [{"id": a.id, "server_id": a.server_id, "hostname": hostnames.get(a.server_id), "severity": a.severity, "title": a.title, "description": a.description, "rule_key": a.rule_key, "created_at": a.created_at.isoformat(), "reference": main.incidents.reference(a.id), "acknowledged": a.acknowledged_at is not None,
                    "agent_note": main._agent_note(db, a, servers_by_id.get(a.server_id), sees_reports)} for a in alerts],
    }

@router.get("/monitoring/servers/{server_id}/history")
def server_history(
    server_id: main.PathId, minutes: int = 30, range_name: Annotated[main.RangeName | None, Query(alias="range")] = None, metrics: str | None = None, previous: bool = False,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db),
):
    """A server's metrics over time.

    With ``range`` (1h to all): at most about 500 points from the right level of detail, each with the average and
    maximum of the requested ``metrics``. Without it: the original answer for the last ``minutes`` (CPU, memory and
    root disk, oldest first), which the dashboard's live chart uses.
    """
    main._current_user(session_id, db)
    server = db.query(main.Server).filter(main.Server.id == server_id).first()
    if not server:
        raise main.HTTPException(status_code=404, detail="Server not found.")
    if range_name is not None:
        return main._history_response(db, main.history_service.HOST, server, None, range_name, main._wanted_metrics(metrics, main.history_service.HOST_METRICS, main.history_service.DEFAULT_HOST_METRICS), previous)
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, min(minutes, main.HISTORY_MAX_MINUTES)))
    rows = db.query(main.HostMetric.time, main.HostMetric.cpu_utilization_pct, main.HostMetric.ram_pct, main.HostMetric.disk_usage_pct).filter(main.HostMetric.server_id == server_id, main.HostMetric.time >= since).order_by(main.HostMetric.time).all()
    return {"server_id": server_id, "samples": [{"time": row.time.isoformat(), "cpu": row.cpu_utilization_pct, "ram": row.ram_pct, "disk": row.disk_usage_pct} for row in rows]}

@router.get("/monitoring/servers/{server_id}/live")
def server_live(server_id: main.PathId, minutes: int = 30, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Every check-in of the last ``minutes`` for the server panel's live charts, oldest first. Network figures are
    running totals; the page turns them into a rate."""
    main._current_user(session_id, db)
    if not db.query(main.Server.id).filter(main.Server.id == server_id).first():
        raise main.HTTPException(status_code=404, detail="Server not found.")
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, min(minutes, main.HISTORY_MAX_MINUTES)))
    rows = db.query(main.HostMetric.time, main.HostMetric.cpu_utilization_pct, main.HostMetric.ram_pct, main.HostMetric.load_1m, main.HostMetric.swap_pct, main.HostMetric.net_bytes_sent_mb, main.HostMetric.net_bytes_recv_mb).filter(main.HostMetric.server_id == server_id, main.HostMetric.time >= since).order_by(main.HostMetric.time).all()
    return {"server_id": server_id, "samples": [{"time": row.time.isoformat(), "cpu": row.cpu_utilization_pct, "ram": row.ram_pct, "load": row.load_1m, "swap": row.swap_pct, "net_sent_mb": row.net_bytes_sent_mb, "net_recv_mb": row.net_bytes_recv_mb} for row in rows]}

@router.get("/monitoring/sparklines")
def monitoring_sparklines(minutes: int = 30, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Recent CPU use of every server, for the small charts in the fleet rack."""
    main._current_user(session_id, db)
    return {"minutes": minutes, "servers": main.dashboard.fleet_sparklines(db, max(1, min(minutes, main.SPARKLINE_MAX_MINUTES)))}

@router.get("/monitoring/servers/{server_id}/containers/sparklines")
def monitoring_container_sparklines(server_id: main.PathId, minutes: int = 15, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Recent CPU use of every container on one server, for the small charts in the container table."""
    main._current_user(session_id, db)
    if not db.query(main.Server.id).filter(main.Server.id == server_id).first():
        raise main.HTTPException(status_code=404, detail="Server not found.")
    return {"minutes": minutes, "containers": main.dashboard.container_sparklines(db, server_id, max(1, min(minutes, main.SPARKLINE_MAX_MINUTES)))}

@router.get("/monitoring/servers/{server_id}/events")
def monitoring_server_events(server_id: main.PathId, days: int = 7, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """One server's timeline: recorded events and alerts opening and clearing, newest first."""
    main._current_user(session_id, db)
    server = db.query(main.Server).filter(main.Server.id == server_id).first()
    if not server:
        raise main.HTTPException(status_code=404, detail="Server not found.")
    days = max(1, min(days, main.EVENTS_MAX_DAYS))
    return {"server_id": server.id, "days": days, "retention_days": main.retention.history_days(db), "events": main.dashboard.server_events(db, server, days)}

@router.get("/monitoring/servers/{server_id}/mounts/history")
def mount_history(
    server_id: main.PathId, mount: Annotated[str, Query(min_length=1, max_length=255)], range_name: Annotated[main.RangeName, Query(alias="range")] = "24h", metrics: str | None = None,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db),
):
    """One filesystem's usage over time (``mount`` is its path, for example ``/data``)."""
    main._current_user(session_id, db)
    server = db.query(main.Server).filter(main.Server.id == server_id).first()
    if not server or not mount.startswith("/"):
        raise main.HTTPException(status_code=404, detail="Server or mount not found.")
    wanted = main._wanted_metrics(metrics, main.history_service.MOUNT_METRICS, ("used_pct", "used_gb", "total_gb"))
    result = main._history_response(db, main.history_service.MOUNT, server, mount, range_name, wanted)
    if result["data_since"] is None:
        raise main.HTTPException(status_code=404, detail="Server or mount not found.")
    return {**result, "mount": mount}

@router.get("/monitoring/servers/{server_id}/containers/{container_id}/history")
def container_history(
    server_id: main.PathId, container_id: Annotated[str, PathParam(min_length=1, max_length=128)], range_name: Annotated[main.RangeName, Query(alias="range")] = "24h", metrics: str | None = None, previous: bool = False,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db),
):
    """One container's CPU and memory over time."""
    main._current_user(session_id, db)
    server = db.query(main.Server).filter(main.Server.id == server_id).first()
    if not server:
        raise main.HTTPException(status_code=404, detail="Server or container not found.")
    wanted = main._wanted_metrics(metrics, main.history_service.CONTAINER_METRICS, ("cpu", "memory_mb", "memory_pct", "memory_limit_mb"))
    result = main._history_response(db, main.history_service.CONTAINER, server, container_id, range_name, wanted, previous)
    if result["data_since"] is None:
        raise main.HTTPException(status_code=404, detail="Server or container not found.")
    name = db.query(main.ContainerMetric.name).filter(main.ContainerMetric.server_id == server.id, main.ContainerMetric.container_id == container_id).order_by(main.ContainerMetric.time.desc()).limit(1).scalar()
    if name is None:
        name = db.query(main.ContainerRollup.name).filter(main.ContainerRollup.server_id == server.id, main.ContainerRollup.container_id == container_id).order_by(main.ContainerRollup.bucket_start.desc()).limit(1).scalar()
    return {**result, "container_id": container_id, "name": name}

@router.get("/monitoring/servers/{server_id}/containers/{container_id}")
def container_detail(server_id: main.PathId, container_id: str, limit: int = 60, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._current_user(session_id, db)
    server = db.query(main.Server).filter(main.Server.id == server_id).first()
    if not server:
        raise main.HTTPException(status_code=404, detail="Server not found.")
    samples = (
        db.query(main.ContainerMetric)
        .filter(main.ContainerMetric.server_id == server.id, main.ContainerMetric.container_id == container_id)
        .order_by(main.ContainerMetric.time.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    if not samples:
        raise main.HTTPException(status_code=404, detail="Container not found.")
    latest = samples[0]
    return {
        "server_id": server.id,
        "hostname": server.hostname,
        "container_id": latest.container_id,
        "name": latest.name,
        "image": latest.image,
        "started_at": latest.started_at,
        "ports": latest.ports,
        "volumes": latest.volumes,
        "project": latest.project,
        "latest": {"time": latest.time.isoformat(), **{field: getattr(latest, field) for field in main.CONTAINER_DETAIL_FIELDS}},
        "history": [{"time": s.time.isoformat(), **{field: getattr(s, field) for field in main.CONTAINER_DETAIL_FIELDS}} for s in reversed(samples)],
    }

@router.get("/monitoring/enrollment-key")
def get_enrollment_key(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main._require_admin(session_id, db)
    key = db.query(main.EnrollmentKey).filter(main.EnrollmentKey.owner_id == user["id"]).first()
    return {"exists": key is not None, "created_at": key.created_at.isoformat() if key else None}

@router.post("/monitoring/enrollment-key")
def rotate_enrollment_key(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Create or replace the account's enrollment key. The plaintext is returned only once."""
    user = main._require_admin(session_id, db)
    plaintext = f"hlk_{secrets.token_urlsafe(32)}"
    key = db.query(main.EnrollmentKey).filter(main.EnrollmentKey.owner_id == user["id"]).first()
    if key:
        key.key_hash = main._hash_secret(plaintext)
        key.created_at = datetime.now(timezone.utc)
    else:
        key = main.EnrollmentKey(owner_id=user["id"], key_hash=main._hash_secret(plaintext))
        db.add(key)
    db.commit()
    return {"enroll_key": plaintext, "created_at": key.created_at.isoformat()}

@router.delete("/monitoring/enrollment-key")
def revoke_enrollment_key(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main._require_admin(session_id, db)
    db.query(main.EnrollmentKey).filter(main.EnrollmentKey.owner_id == user["id"]).delete()
    db.commit()
    return {"status": "revoked"}

@router.post("/monitoring/collectors/enroll", dependencies=[Depends(main.require_collector_listener)])
def enroll_collector(payload: main.EnrollRequest, db=Depends(get_db), request: Request = None):
    """Register (or re-register) a collector using the owner's enrollment key.

    Servers are matched on machine_id, so a collector that lost its saved
    credentials gets its existing server back with a fresh token. That makes the key powerful, so every use is
    rate limited, the number of servers is capped, and replacing an existing server's credentials is recorded
    as an event (it opens a warning), so it can't happen unnoticed.
    """
    client_ip = main._client_ip(request) if request else "unknown"
    main._soft_limit(scope="enroll-ip", subject=client_ip, limit=main.ENROLL_RATE_LIMIT, window_seconds=main.ENROLL_RATE_WINDOW_SECONDS)
    key = db.query(main.EnrollmentKey).filter(main.EnrollmentKey.key_hash == main._hash_secret(payload.enroll_key.strip())).first()
    if not key:
        raise main.HTTPException(status_code=401, detail="Enrollment key is invalid or has been rotated.")
    if not main._is_admin(db, key.owner_id):
        raise main.HTTPException(status_code=403, detail="Enrollment key belongs to an account that can no longer add servers.")
    main._soft_limit(scope="enroll-key", subject=key.id, limit=main.ENROLL_RATE_LIMIT * 2, window_seconds=main.ENROLL_RATE_WINDOW_SECONDS)
    hostname = main._hostname_or_error(payload.hostname)
    machine_id = (payload.machine_id or "").strip()[:64] or None
    if not hostname:
        raise main.HTTPException(status_code=400, detail="Hostname is required.")

    server = None
    if machine_id:
        server = db.query(main.Server).filter(main.Server.machine_id == machine_id).first()
    replaced = server is not None
    if not server:
        main._ensure_server_capacity(db)
        server = main.Server(owner_id=key.owner_id, hostname=main._unique_hostname(db, hostname), machine_id=machine_id)
        db.add(server)
        db.flush()
    token = secrets.token_urlsafe(32)
    server.collector_token_hash = main._hash_secret(token)
    if replaced:
        now = datetime.now(timezone.utc)
        detail = f"This server's credentials were replaced through the enrollment key (request from {main._printable(client_ip, 64)}). Its previous collector can no longer report."
        db.add(main.SystemEvent(server_id=server.id, time=now, kind=main.CREDENTIALS_REPLACED_KIND, detail=detail[:512], event_key=f"credentials:{now.isoformat()}"))
        main.record_audit_log(db, user_id=key.owner_id, level="warning", message=f"Enrollment key replaced the credentials of server {server.id} ({server.hostname}) from {main._printable(client_ip, 64)}", source="enrollment", event_type="credentials_replaced")
    db.commit()
    db.refresh(server)
    return {"server_id": server.id, "collector_token": token, "check_interval_seconds": server.check_interval_seconds}

@router.post("/monitoring/join-tokens")
def create_join_token(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Mint the single-use token the Connect a server command carries. The plaintext is returned only once."""
    user = main._require_admin(session_id, db)
    now = datetime.now(timezone.utc)
    if db.query(main.JoinToken).filter(main.JoinToken.used_at.is_(None), main.JoinToken.expires_at > now).count() >= main.MAX_OPEN_JOIN_TOKENS:
        raise main.HTTPException(status_code=429, detail="Too many unused join tokens. Wait for them to expire or add the servers already waiting.")
    plaintext = f"{main.JOIN_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    db.add(main.JoinToken(token_hash=main._hash_secret(plaintext), created_by=user["id"], expires_at=now + timedelta(seconds=main.JOIN_TOKEN_TTL_SECONDS)))
    db.commit()
    return {"join_token": plaintext, "expires_in_seconds": main.JOIN_TOKEN_TTL_SECONDS}

@router.get("/monitoring/install-info")
def install_info(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """What the Connect a server panel needs to build its command."""
    main._require_admin(session_id, db)
    return {"strict": main.COLLECTOR_ACCESS == "strict", "collector_url": main.COLLECTOR_PUBLIC_URL or None, "collector_image": main.COLLECTOR_IMAGE or None, "proxy_image": main.DOCKER_PROXY_IMAGE or None}

@router.post("/monitoring/collectors/register", dependencies=[Depends(main.require_collector_listener)])
def register_collector(payload: main.CollectorRegisterRequest, request: Request, db=Depends(get_db)):
    """A new collector asks to join and waits until a user clicks Add.

    Over the VPN listener a join token from the dashboard's Connect a server command is required.
    """
    via = main._collector_via(request)
    ip_address = main._client_ip(request)
    main.enforce_ip_limit(main.redis_client, scope="collector-register", ip_address=ip_address, limit=30, window_seconds=3600)
    hostname = main._hostname_or_error(payload.hostname)
    machine_id = (payload.machine_id or "").strip()[:64] or None
    join_token = None
    if payload.join_token:
        join_token = db.query(main.JoinToken).filter(main.JoinToken.token_hash == main._hash_secret(payload.join_token.strip()), main.JoinToken.used_at.is_(None), main.JoinToken.expires_at > datetime.now(timezone.utc)).first()
        if not join_token:
            raise main.HTTPException(status_code=403, detail="The join token is invalid, expired or already used. Copy a fresh command from the dashboard.")
    elif main.COLLECTOR_ACCESS == "strict" and via == "vpn":
        raise main.HTTPException(status_code=403, detail="A join token is required. Copy the command from the dashboard's Connect a server button.")
    if join_token:
        # The same host retrying with its token replaces its earlier request until someone approves it.
        db.query(main.PendingCollector).filter(main.PendingCollector.join_token_id == join_token.id, main.PendingCollector.status != "approved").delete(synchronize_session=False)
    if machine_id:
        # A restarted collector replaces its own earlier request instead of piling up duplicates.
        db.query(main.PendingCollector).filter(main.PendingCollector.machine_id == machine_id, main.PendingCollector.ip_address == ip_address, main.PendingCollector.status != "approved").delete(synchronize_session=False)
    if db.query(main.PendingCollector).count() >= main.MAX_PENDING_COLLECTORS:
        raise main.HTTPException(status_code=503, detail="Too many servers are waiting for approval.")
    secret = secrets.token_urlsafe(32)
    code = "".join(secrets.choice(main.PAIRING_ALPHABET) for _ in range(6))
    details = main._clean_registration_details(payload.details)
    # Only the stack's own collector, coming in through the internal listener, can be "this machine" (see ingest_metrics).
    details["dashboard_host"] = details.get("dashboard_host") is True and via in ("internal", "")
    pending = main.PendingCollector(
        poll_secret_hash=main._hash_secret(secret), pairing_code=f"{code[:3]}-{code[3:]}", hostname=hostname,
        machine_id=machine_id, ip_address=ip_address[:64], os_info=(payload.os_info or "")[:255] or None,
        join_token_id=join_token.id if join_token else None, details=json.dumps(details),
    )
    db.add(pending)
    db.commit()
    return {"request_secret": secret, "pairing_code": pending.pairing_code, "poll_interval_seconds": 5}

@router.post("/monitoring/collectors/register/poll", dependencies=[Depends(main.require_collector_listener)])
def poll_collector_registration(authorization: Annotated[str | None, Header()] = None, db=Depends(get_db)):
    secret = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else ""
    pending = db.query(main.PendingCollector).filter(main.PendingCollector.poll_secret_hash == main._hash_secret(secret)).first() if secret else None
    if not pending:
        raise main.HTTPException(status_code=404, detail="Registration request not found or expired.")
    pending.last_poll_at = datetime.now(timezone.utc)
    if pending.status != "approved":
        db.commit()
        return {"status": pending.status}
    server = db.query(main.Server).filter(main.Server.id == pending.approved_server_id).first()
    db.delete(pending)
    if not server:
        db.commit()
        raise main.HTTPException(status_code=404, detail="The approved server was deleted.")
    # The token is minted only now, so it never sits in the database in readable form.
    token = secrets.token_urlsafe(32)
    server.collector_token_hash = main._hash_secret(token)
    db.commit()
    return {"status": "approved", "server_id": server.id, "collector_token": token, "check_interval_seconds": server.check_interval_seconds}

@router.post("/monitoring/pending-collectors/{pending_id}/approve")
def approve_pending_collector(pending_id: main.PathId, payload: main.ApproveRequest | None = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main._require_admin(session_id, db)
    pending = main._pending_for_user(pending_id, db)
    server = main._server_for_machine(db, pending.machine_id)
    if server and not (payload and payload.replace):
        # Approving mints new credentials for that server and locks out its current collector, so never do it silently.
        raise main.HTTPException(status_code=409, detail=f"This machine matches the existing server {server.hostname}. Approving replaces that server's credentials and signs its current collector out. Confirm to continue.")
    chosen = main._hostname_or_error(payload.hostname) if payload and payload.hostname and payload.hostname.strip() else None
    if chosen and main._hostname_in_use(db, chosen, excluding=server.id if server else None):
        raise main.HTTPException(status_code=409, detail=f"Another server is already called {chosen}. Pick a different name.")
    if not server:
        main._ensure_server_capacity(db)
        server = main.Server(owner_id=user["id"], hostname=chosen or main._unique_hostname(db, main._clean_hostname(pending.hostname) or "unnamed server"), machine_id=pending.machine_id, ip_address=pending.ip_address, os_info=pending.os_info)
        db.add(server)
        db.flush()
    # A name that differs from what the machine calls itself was chosen on purpose, so it is kept.
    if chosen and main._name_key(chosen) != main._name_key(main._clean_hostname(pending.hostname) or ""):
        server.hostname, server.name_locked = chosen, True
    pending.status = "approved"
    pending.approved_server_id = server.id
    if pending.join_token_id:
        db.query(main.JoinToken).filter(main.JoinToken.id == pending.join_token_id).update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    return {"status": "approved", "server_id": server.id}

@router.post("/monitoring/pending-collectors/{pending_id}/dismiss")
def dismiss_pending_collector(pending_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    pending = main._pending_for_user(pending_id, db)
    pending.status = "dismissed"
    db.commit()
    return {"status": "dismissed"}

@router.post("/monitoring/servers")
def add_monitored_server(payload: main.ServerRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main._require_admin(session_id, db)
    if not payload.hostname.strip() or not 15 <= payload.check_interval_seconds <= 86400:
        raise main.HTTPException(status_code=400, detail="Hostname is required and interval must be between 15 and 86400 seconds.")
    main._ensure_server_capacity(db)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    server = main.Server(owner_id=user["id"], hostname=main._unique_hostname(db, main._hostname_or_error(payload.hostname)), ip_address=payload.ip_address, check_interval_seconds=payload.check_interval_seconds, collector_token_hash=token_hash)
    db.add(server); db.commit(); db.refresh(server)
    return {"id": server.id, "hostname": server.hostname, "check_interval_seconds": server.check_interval_seconds, "collector_token": token}

@router.delete("/monitoring/servers/{server_id}")
def delete_monitored_server(server_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    server = db.query(main.Server).filter(main.Server.id == server_id).first()
    if not server:
        raise main.HTTPException(status_code=404, detail="Server not found.")

    for rollup in (main.HostRollup, main.MountRollup, main.ContainerRollup):
        db.query(rollup).filter(rollup.server_id == server.id).delete(synchronize_session=False)
    db.query(main.HostMetric).filter(main.HostMetric.server_id == server.id).delete(synchronize_session=False)
    db.query(main.ContainerMetric).filter(main.ContainerMetric.server_id == server.id).delete(synchronize_session=False)
    db.query(main.MountMetric).filter(main.MountMetric.server_id == server.id).delete(synchronize_session=False)
    db.query(main.SystemEvent).filter(main.SystemEvent.server_id == server.id).delete(synchronize_session=False)
    db.query(main.ListenerState).filter(main.ListenerState.server_id == server.id).delete(synchronize_session=False)
    db.query(main.FirewallState).filter(main.FirewallState.server_id == server.id).delete(synchronize_session=False)
    db.query(main.IncidentAlert).filter(main.IncidentAlert.server_id == server.id).delete(synchronize_session=False)
    db.query(main.PendingCollector).filter(main.PendingCollector.approved_server_id == server.id).delete(synchronize_session=False)
    main.availability.forget_server(db, server.id)
    db.delete(server)
    db.commit()
    return {"status": "deleted", "server_id": server_id}

@router.patch("/monitoring/servers/{server_id}/interval")
def update_check_interval(server_id: main.PathId, payload: dict, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    server = db.query(main.Server).filter(main.Server.id == server_id).first()
    interval = payload.get("check_interval_seconds")
    if not server:
        raise main.HTTPException(status_code=404, detail="Server not found.")
    if not isinstance(interval, int) or not 15 <= interval <= 86400:
        raise main.HTTPException(status_code=400, detail="Interval must be between 15 and 86400 seconds.")
    server.check_interval_seconds = interval; db.commit()
    return {"id": server.id, "check_interval_seconds": server.check_interval_seconds}

@router.post("/monitoring/metrics/ingest", dependencies=[Depends(main.require_collector_listener)])
def ingest_metrics(payload: main.MetricsIngestRequest, authorization: Annotated[str | None, Header()] = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db), request: Request = None):
    server = db.query(main.Server).filter(main.Server.id == payload.server_id).first()
    if not server:
        raise main.HTTPException(status_code=404, detail="Server not found.")
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else None
    if server.collector_token_hash and token_hash and hmac.compare_digest(server.collector_token_hash, token_hash):
        pass
    else:
        main._require_admin(session_id, db)
    main._enforce_ingest_spacing(server)
    updates_before = main._load_json(server.system_info)
    main._apply_server_fields(db, server, payload.server)
    if detail := main.packages_updated_detail(updates_before, main._load_json(server.system_info)):
        db.add(main.SystemEvent(server_id=server.id, time=datetime.now(timezone.utc), kind=main.PACKAGES_UPDATED_KIND, detail=detail))
    # Only the stack's own collector, which reaches the backend through the gate's internal listener, may claim to
    # be the dashboard's machine; a collector coming in over the VPN can't take that place. (With a single
    # listener, in the development stack, there is no way to tell, so the claim is accepted.)
    server.is_dashboard_host = payload.server.get("dashboard_host") is True and main._collector_via(request) in ("internal", "")
    if server.is_dashboard_host:
        db.query(main.Server).filter(main.Server.id != server.id, main.Server.is_dashboard_host.is_(True)).update({"is_dashboard_host": False}, synchronize_session=False)
    now = datetime.now(timezone.utc)
    host_fields = {k: v for k, v in payload.host.items() if k in main.HostMetric.__table__.columns.keys() and k not in {"id", "server_id", "time"}}
    main._clean_system_fields(host_fields)
    main._clean_host_numbers(host_fields)
    previous = db.query(main.HostMetric).filter(main.HostMetric.server_id == server.id).order_by(main.HostMetric.time.desc(), main.HostMetric.id.desc()).first()
    # Only a collector that found the system helper sends its events; then they replace the OOM counter.
    helper_active = isinstance(payload.host.get("helper_events"), list)
    for kind, detail, key in main.detect_system_events(previous, host_fields, helper_active):
        db.add(main.SystemEvent(server_id=server.id, time=now, kind=kind, detail=detail, event_key=key))
    main.record_helper_events(db, server.id, payload.host.get("helper_events"), now)
    # The helper can finish after a reboot was first noticed; fill in how the last shutdown went.
    if host_fields.get("boot_id") and host_fields.get("last_shutdown") in main.REBOOT_DETAILS:
        db.query(main.SystemEvent).filter(main.SystemEvent.server_id == server.id, main.SystemEvent.event_key == f"{main.REBOOT_KEY_PREFIX}{host_fields['boot_id']}").update({"detail": main.REBOOT_DETAILS[host_fields["last_shutdown"]]}, synchronize_session=False)
    db.add(main.HostMetric(server_id=server.id, time=now, **host_fields))
    for mount in main._clean_mounts(payload.host.get("mounts")):
        db.add(main.MountMetric(server_id=server.id, time=now, **mount))
    containers = [clean for clean in (main._clean_container(raw, now) for raw in payload.containers[:main.MAX_CONTAINERS_PER_CHECKIN]) if clean]
    # Compare with the previous check-in before this one's rows exist.
    previous_containers = main.latest_containers(db, server.id, previous.time) if previous else []
    main.record_container_events(db, server.id, main.detect_container_events(previous_containers, containers), now)
    for container in containers:
        db.add(main.ContainerMetric(server_id=server.id, time=now, **container))
    # Listening ports and firewall files: compare with what was seen last time (collectors that don't send them are skipped).
    network_events: list[tuple[str, str, str]] = []
    if "listeners" in payload.host:
        network_events += main.sync_listeners(db, server.id, main.validate_listeners(payload.host["listeners"]), now)
    if "firewall" in payload.host and (firewall := main.validate_firewall(payload.host["firewall"])) is not None:
        network_events += main.sync_firewall(db, server.id, firewall, now)
    main.record_capped_events(db, server.id, network_events, main.NETWORK_EVENT_KINDS, "network_notice", "network changes", now, main.MAX_NETWORK_EVENTS_PER_CHECKIN, main.MAX_NETWORK_EVENTS_PER_HOUR)
    db.flush()  # the rules read this check-in back from the history tables
    thresholds = main.get_alert_thresholds(db)
    main.sync_alerts(db, server.id, main.HOST_RULE_PREFIX, main._enabled_rules(db, main.host_rules(db, server, thresholds, main.active_rule_keys(db, server.id, main.HOST_RULE_PREFIX), main.get_mount_thresholds(db))))
    main.sync_alerts(db, server.id, main.SYSTEM_RULE_PREFIX, main._enabled_rules(db, main.system_rules(db, server, thresholds, now, main.active_rule_keys(db, server.id, main.SYSTEM_RULE_PREFIX))))
    main.sync_alerts(db, server.id, main.NETWORK_RULE_PREFIX, main._enabled_rules(db, main.network_rules(db, server, thresholds, now)))
    # The collector sends no containers when Docker is unreachable; keep container alerts as they are rather than resolving them all.
    if containers:
        main.sync_alerts(db, server.id, main.CONTAINER_RULE_PREFIX, main._enabled_rules(db, main.container_rules(db, server.id, containers, thresholds, now)))
    server.last_seen_at = now
    main.resolve_alerts(db, server.id, [main.SERVER_OFFLINE_RULE])
    db.commit()
    return {"status": "accepted", "interval_seconds": server.check_interval_seconds}

@router.post("/monitoring/metrics/checks", dependencies=[Depends(main.require_collector_listener)])
def report_check_results(payload: main.CheckResultsRequest, authorization: Annotated[str | None, Header()] = None, db=Depends(get_db)):
    """A collector reports the runs of its availability checks and gets back the checks it should run.
    Only the server's own collector token is accepted here."""
    server = db.query(main.Server).filter(main.Server.id == payload.server_id).first()
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    if not server or not token or not server.collector_token_hash or not hmac.compare_digest(server.collector_token_hash, hashlib.sha256(token.encode()).hexdigest()):
        raise main.HTTPException(status_code=401, detail="Unknown collector.")
    main._enforce_report_spacing(server)
    now = datetime.now(timezone.utc)
    alerting = "check:down" not in main.get_disabled_rules(db)
    for check_id in sorted(main.availability.store(db, server, payload.results, now)):
        check = db.get(main.Check, check_id)
        check.last_result_at = now
        main.availability.evaluate(db, check, now, alerting)
    db.commit()
    try:   # tests from the form's Test button go through Redis; without it there are none, the checks still work
        main.availability.store_tests(main.redis_client, server.id, payload.tests)
        tests = main.availability.tests_for(main.redis_client, server.id)
    except Exception:
        tests = []
    return {"checks": main.availability.for_collector(db, server), "tests": tests, "report_every": 5, "idle_every": main.availability.IDLE_EVERY}

@router.patch("/monitoring/servers/{server_id}")
def update_server(server_id: main.PathId, payload: main.ServerUpdateRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Rename a server or change how often it checks in. A name given here stays, whatever the machine calls itself."""
    admin = main._require_admin(session_id, db)
    server = db.query(main.Server).filter(main.Server.id == server_id).first()
    if not server:
        raise main.HTTPException(status_code=404, detail="Server not found.")
    if payload.hostname is not None and payload.hostname.strip() and main._name_key(payload.hostname) != main._name_key(server.hostname):
        name = main._hostname_or_error(payload.hostname)
        if main._hostname_in_use(db, name, excluding=server.id):
            raise main.HTTPException(status_code=409, detail=f"Another server is already called {name}.")
        main.record_audit_log(db, user_id=admin["id"], level="INFO", message=f"Renamed server {server.hostname} to {name}", source="admin")
        server.hostname, server.name_locked = name, True
    if payload.check_interval_seconds is not None:
        if payload.check_interval_seconds not in (15, 30, 60, 300, 900):
            raise main.HTTPException(status_code=400, detail="The check interval must be 15, 30, 60, 300 or 900 seconds.")
        server.check_interval_seconds = payload.check_interval_seconds
    db.commit()
    return {"id": server.id, "hostname": server.hostname, "check_interval_seconds": server.check_interval_seconds}
