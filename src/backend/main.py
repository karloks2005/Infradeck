from __future__ import annotations

import json
import math
import re
import hashlib
import secrets
import hmac
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import urlsplit

import redis
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import case, func, inspect, text

from .alerts import CONTAINER_RULE_PREFIX, CRITICAL, SERVER_OFFLINE_RULE, SEVERITIES, active_rule_keys, raise_alert, resolve_alerts, sync_alerts
from .auth import authenticate_user, get_recent_logs, get_user_from_session, logout_user, record_audit_log, register_user
from .db import SessionLocal, check_database, get_db
from .models import AppSetting, AuditLog, Base, ChatMessage, ContainerMetric, Conversation, EnrollmentKey, FirewallState, HostMetric, IncidentAlert, JoinToken, ListenerState, MountMetric, PendingCollector, Server, SystemEvent, User

from .db import engine
from .config import (
    LOGIN_RATE_LIMIT,
    LOGIN_RATE_WINDOW_SECONDS,
    REDIS_URL,
    REGISTER_RATE_LIMIT,
    REGISTER_RATE_WINDOW_SECONDS,
    ADMIN_BOOTSTRAP_TOKEN,
    ALLOW_REGISTRATION,
    COLLECTOR_ACCESS,
    COLLECTOR_IMAGE,
    COLLECTOR_PUBLIC_URL,
    DOCKER_PROXY_IMAGE,
    JOIN_TOKEN_TTL_SECONDS,
    CORS_ORIGINS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_SECONDS,
)
from .rules import DEFAULT_THRESHOLDS, HOST_RULE_PREFIX, container_rules, host_rules, validate_thresholds, detect_system_events, system_rules, SYSTEM_RULE_PREFIX, validate_mount_thresholds, validate_helper_events, REBOOT_DETAILS, REBOOT_KEY_PREFIX, detect_container_events, memory_trend_rules, CONTAINER_EVENT_KINDS, MAX_CONTAINER_EVENTS_PER_CHECKIN, MAX_CONTAINER_EVENTS_PER_HOUR, TREND_RULE_PREFIX, NETWORK_EVENT_KINDS, NETWORK_RULE_PREFIX, MAX_NETWORK_EVENTS_PER_CHECKIN, MAX_NETWORK_EVENTS_PER_HOUR, INTERNET_STATUSES, LISTENER_SCOPES, FIREWALL_LABELS, detect_listener_changes, detect_firewall_changes, network_rules, validate_listeners, validate_firewall
from .rate_limit import enforce_ip_limit, enforce_login_limit, enforce_register_limit, reset_limits
from ..agent.service import delete_agent_conversation, send_agent_message


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    # Only needed for the very first account; see ensure_bootstrap_token.
    bootstrap_token: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AgentChatRequest(BaseModel):
    conversation_id: str
    message: str


class ConversationRequest(BaseModel):
    conversation_id: str
    title: str = "New chat"


class ServerRequest(BaseModel):
    hostname: str
    ip_address: str | None = None
    check_interval_seconds: int = 60


class EnrollRequest(BaseModel):
    enroll_key: str
    hostname: str
    machine_id: str | None = None


class CollectorRegisterRequest(BaseModel):
    hostname: str
    machine_id: str | None = None
    os_info: str | None = None
    join_token: str | None = None


class ApproveRequest(BaseModel):
    # Required to approve a request whose machine matches a server that already exists.
    replace: bool = False


class AdminSettingsRequest(BaseModel):
    # Both optional so the admin page can save one setting at a time.
    metrics_retention_days: int | None = None
    agent_chat_enabled: bool | None = None
    alert_thresholds: dict | None = None
    # Replaces all per-mount disk overrides: {mount: {disk_warning_pct, disk_critical_pct}}.
    mount_thresholds: dict | None = None


class MetricsIngestRequest(BaseModel):
    server_id: int
    server: dict = {}
    host: dict = {}
    containers: list[dict] = []


def agent_error_message(exc: Exception) -> str:
    """Turn provider failures into useful, non-sensitive chat text."""
    raw_message = str(exc).strip()
    normalized = raw_message.lower()
    if any(marker in normalized for marker in ("resourceexhausted", "overloaded", "capacity", " 503", "status 503")):
        prefix = "Gemini is currently overloaded or unavailable. Please try again shortly."
    elif any(marker in normalized for marker in ("rate limit", "quota", " 429", "status 429")):
        prefix = "Gemini rate limit or quota was reached. Please try again later."
    elif any(marker in normalized for marker in ("timeout", "timed out", "deadline exceeded")):
        prefix = "Gemini did not respond before the request timed out. Please try again."
    else:
        prefix = "Gemini could not complete this request."

    if not raw_message:
        return prefix
    # Errors can include request headers or provider diagnostics: keep a short,
    # redacted detail for the administrator without leaking credentials.
    detail = re.sub(r"(?i)(api[_ -]?key|authorization|token)\s*[:=]\s*\S+", r"\1=[redacted]", raw_message)
    detail = re.sub(r"AIza[\w-]+", "[redacted]", detail)[:300]
    return f"{prefix} Provider error: {detail}"


redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="Homelab Agent API")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def cross_site_request(method: str, headers, allowed_origins: list[str]) -> bool:
    """True for a browser request that came from another site and must be refused.

    Cookies are SameSite=Lax, which still lets a page on a sibling subdomain (other apps behind the same
    reverse proxy count as same-site) send state-changing requests with the session cookie. Browsers
    always say where such a request came from, so refuse it unless that is this dashboard. Tools that
    aren't browsers (collectors, curl) send neither header and are unaffected; they authenticate with
    tokens, not cookies.
    """
    if method not in UNSAFE_METHODS:
        return False
    origin = headers.get("origin")
    if origin is not None:
        if origin in allowed_origins:
            return False
        parsed = urlsplit(origin)
        # Same host and port as the dashboard was reached on; the scheme can't be compared behind a TLS-terminating proxy.
        return not (parsed.scheme in ("http", "https") and parsed.netloc and parsed.netloc == headers.get("host"))
    return headers.get("sec-fetch-site") not in (None, "same-origin", "none")


@app.middleware("http")
async def refuse_cross_site_requests(request: Request, call_next):
    if cross_site_request(request.method, request.headers, CORS_ORIGINS):
        return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    # Only needed when the dashboard is served from a different origin than /api.
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS task_title VARCHAR(255)"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS steps TEXT"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS conversation_id VARCHAR(255)"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS event_type VARCHAR(50)"))
            for column, column_type in (
                ("memory_limit_mb", "DOUBLE PRECISION"), ("net_rx_mb", "DOUBLE PRECISION"), ("net_tx_mb", "DOUBLE PRECISION"),
                ("block_read_mb", "DOUBLE PRECISION"), ("block_write_mb", "DOUBLE PRECISION"), ("pids", "INTEGER"),
                ("started_at", "VARCHAR(64)"), ("ports", "VARCHAR(512)"),
            ):
                connection.execute(text(f"ALTER TABLE container_metrics ADD COLUMN IF NOT EXISTS {column} {column_type}"))
            for column, column_type in (
                ("boot_id", "VARCHAR(64)"), ("oom_kill_total", "INTEGER"), ("kernel_tainted", "INTEGER"),
                ("ntp_synced", "BOOLEAN"), ("reboot_required", "BOOLEAN"), ("last_shutdown", "VARCHAR(16)"),
                ("docker_status", "VARCHAR(16)"), ("docker_reason", "VARCHAR(16)"), ("docker_latency_ms", "INTEGER"),
                ("internet_status", "VARCHAR(16)"), ("internet_latency_ms", "INTEGER"),
            ):
                connection.execute(text(f"ALTER TABLE host_metrics ADD COLUMN IF NOT EXISTS {column} {column_type}"))
            for column, column_type in (("exit_code", "INTEGER"), ("oom_killed", "BOOLEAN"), ("finished_at", "TIMESTAMP WITH TIME ZONE"), ("image_id", "VARCHAR(80)")):
                connection.execute(text(f"ALTER TABLE container_metrics ADD COLUMN IF NOT EXISTS {column} {column_type}"))
            connection.execute(text("ALTER TABLE system_events ADD COLUMN IF NOT EXISTS event_key VARCHAR(128)"))
            connection.execute(text("ALTER TABLE pending_collectors ADD COLUMN IF NOT EXISTS join_token_id INTEGER REFERENCES join_tokens(id) ON DELETE SET NULL"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_system_events_event_key ON system_events (event_key)"))
            connection.execute(text("ALTER TABLE incident_alerts ADD COLUMN IF NOT EXISTS rule_key VARCHAR(255)"))
            connection.execute(text("ALTER TABLE incident_alerts ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_incident_alerts_rule_key ON incident_alerts (rule_key)"))
            # Alerts from before the alert engine: offline alerts keep going under their rule, the rest were
            # duplicated on every check-in with no way to resolve, so close them; the engine re-raises live ones.
            connection.execute(text("UPDATE incident_alerts SET rule_key = :rule WHERE rule_key IS NULL AND description = :description"), {"rule": SERVER_OFFLINE_RULE, "description": OFFLINE_ALERT_DESCRIPTION})
            connection.execute(text("UPDATE incident_alerts SET status = 'resolved', resolved_at = NOW() WHERE rule_key IS NULL AND status = 'active'"))
            # Disk and inode alerts became per mount; the old keys were for the root filesystem.
            connection.execute(text("UPDATE incident_alerts SET rule_key = rule_key || ':/' WHERE rule_key IN ('host:disk', 'host:inodes')"))
    if "servers" in inspect(engine).get_table_names():
        columns = {column["name"] for column in inspect(engine).get_columns("servers")}
        if "collector_token_hash" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE servers ADD COLUMN collector_token_hash VARCHAR(64)"))
        if "machine_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE servers ADD COLUMN machine_id VARCHAR(64)"))
        if "is_dashboard_host" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE servers ADD COLUMN is_dashboard_host BOOLEAN NOT NULL DEFAULT FALSE"))
    db = SessionLocal()
    try:
        ensure_admin_exists(db)
        setup_token = ensure_bootstrap_token(db)
        if setup_token:
            print(f"First-run setup token: {setup_token}  (needed to create the first account; shown again at every start until it exists)", flush=True)
    finally:
        db.close()
    threading.Thread(target=_offline_watch_loop, name="offline-watch", daemon=True).start()
    try:
        redis_client.ping()
    except Exception:
        pass


@app.get("/health")
def health():
    db_ok = check_database()
    try:
        redis_ok = bool(redis_client.ping())
    except Exception:
        redis_ok = False

    return {
        "status": "ok" if db_ok and redis_ok else "degraded",
        "database": "ok" if db_ok else "down",
        "redis": "ok" if redis_ok else "down",
    }


BOOTSTRAP_TOKEN_KEY = "bootstrap_token_hash"


def registration_open(db) -> bool:
    return ALLOW_REGISTRATION or db.query(User).count() == 0


def ensure_bootstrap_token(db) -> str | None:
    """Make sure a fresh install can only be claimed by someone who can read its configuration or logs.

    While there are no accounts, the first one needs a setup token: ADMIN_BOOTSTRAP_TOKEN if set,
    otherwise a random one generated here and returned so the caller can print it. Only its hash is
    stored. Once an account exists the token is deleted and nothing is required any more.
    """
    setting = db.get(AppSetting, BOOTSTRAP_TOKEN_KEY)
    if db.query(User).count() > 0:
        if setting:
            db.delete(setting)
            db.commit()
        return None
    token = ADMIN_BOOTSTRAP_TOKEN or f"hlb_{secrets.token_urlsafe(24)}"
    _set_setting(db, BOOTSTRAP_TOKEN_KEY, _hash_secret(token))
    db.commit()
    return None if ADMIN_BOOTSTRAP_TOKEN else token


def _bootstrap_token_valid(db, token: str | None) -> bool:
    setting = db.get(AppSetting, BOOTSTRAP_TOKEN_KEY)
    # No stored token (the backend hasn't started up properly) means refuse, never allow.
    return bool(setting and token and hmac.compare_digest(setting.value, _hash_secret(token.strip())))


@app.get("/auth/config")
def auth_config(db=Depends(get_db)):
    return {"registration_enabled": registration_open(db), "bootstrap_required": db.query(User).count() == 0}


@app.post("/auth/register")
def auth_register(payload: RegisterRequest, request: Request, response: Response, db=Depends(get_db)):
    if not registration_open(db):
        raise HTTPException(status_code=403, detail="Registration is disabled on this server.")
    enforce_register_limit(
        redis_client,
        ip_address=request.client.host if request.client else "unknown",
        limit=REGISTER_RATE_LIMIT,
        window_seconds=REGISTER_RATE_WINDOW_SECONDS,
    )
    first_account = db.query(User).count() == 0
    if first_account and not _bootstrap_token_valid(db, payload.bootstrap_token):
        raise HTTPException(status_code=403, detail="This install needs its setup token to create the first account. Find it in the backend log: docker compose logs backend | grep \"setup token\"")
    try:
        result = register_user(
            db,
            redis_client,
            name=payload.name,
            email=str(payload.email),
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if first_account:
        ensure_bootstrap_token(db)  # an account exists now: this removes the token
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=result["session_id"],
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    result.pop("session_id", None)
    return result


@app.post("/auth/login")
def auth_login(payload: LoginRequest, request: Request, response: Response, db=Depends(get_db)):
    limit_keys = enforce_login_limit(
        redis_client,
        ip_address=request.client.host if request.client else "unknown",
        email=str(payload.email),
        limit=LOGIN_RATE_LIMIT,
        window_seconds=LOGIN_RATE_WINDOW_SECONDS,
    )
    try:
        result = authenticate_user(
            db,
            redis_client,
            email=str(payload.email),
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    reset_limits(redis_client, *limit_keys)

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=result["session_id"],
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    result.pop("session_id", None)
    return result


@app.get("/auth/me")
def auth_me(
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    db=Depends(get_db),
):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    # Read role and status from the database so admin changes apply without a new login.
    account = db.query(User).filter(User.id == user["id"]).first()
    if not account or not account.is_active:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    return {"user": {**user, "name": account.name, "email": account.email, "role": account.role}}


@app.post("/auth/logout")
def auth_logout(
    response: Response,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
):
    if logout_user(redis_client, session_id):
        response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
        return {"status": "ok"}
    raise HTTPException(status_code=400, detail="No session id supplied.")



def _current_user(session_id, db):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    return user


def _is_admin(db, user_id: int) -> bool:
    account = db.query(User).filter(User.id == user_id).first()
    return bool(account and account.is_active and account.role == "admin")


def _require_admin(session_id, db):
    """Like _current_user, but checks the role in the database rather than the session."""
    user = _current_user(session_id, db)
    if not _is_admin(db, user["id"]):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def ensure_admin_exists(db) -> None:
    """Installs from before roles were enforced have no admin: promote the oldest account."""
    if db.query(User).filter(User.role == "admin").first():
        return
    oldest = db.query(User).order_by(User.id).first()
    if oldest:
        oldest.role = "admin"
        db.commit()


METRICS_RETENTION_KEY = "metrics_retention_days"
DEFAULT_METRICS_RETENTION_DAYS = 30
MIN_METRICS_RETENTION_DAYS = 1
MAX_METRICS_RETENTION_DAYS = 3650


AGENT_CHAT_ENABLED_KEY = "agent_chat_enabled"
ALERT_THRESHOLDS_KEY = "alert_thresholds"
MOUNT_THRESHOLDS_KEY = "mount_thresholds"


def _set_setting(db, key: str, value: str) -> None:
    setting = db.get(AppSetting, key)
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def get_metrics_retention_days(db) -> int:
    setting = db.get(AppSetting, METRICS_RETENTION_KEY)
    try:
        return int(setting.value) if setting else DEFAULT_METRICS_RETENTION_DAYS
    except ValueError:
        return DEFAULT_METRICS_RETENTION_DAYS


def is_agent_chat_enabled(db) -> bool:
    setting = db.get(AppSetting, AGENT_CHAT_ENABLED_KEY)
    return setting is None or setting.value == "true"


def get_alert_thresholds(db) -> dict:
    """Saved thresholds over the defaults, so thresholds added later get their default value."""
    setting = db.get(AppSetting, ALERT_THRESHOLDS_KEY)
    try:
        saved = json.loads(setting.value) if setting else {}
        return validate_thresholds({k: v for k, v in saved.items() if k in DEFAULT_THRESHOLDS}, DEFAULT_THRESHOLDS)
    except (ValueError, AttributeError):
        return dict(DEFAULT_THRESHOLDS)


def get_mount_thresholds(db) -> dict:
    """Per-mount disk threshold overrides; anything unreadable is ignored in favor of the global thresholds."""
    setting = db.get(AppSetting, MOUNT_THRESHOLDS_KEY)
    try:
        return validate_mount_thresholds(json.loads(setting.value)) if setting else {}
    except ValueError:
        return {}


def _admin_settings(db) -> dict:
    return {"metrics_retention_days": get_metrics_retention_days(db), "agent_chat_enabled": is_agent_chat_enabled(db), "alert_thresholds": get_alert_thresholds(db), "mount_thresholds": get_mount_thresholds(db)}


def purge_old_metrics(db, now: datetime | None = None) -> int:
    """Delete host and container samples older than the retention period."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=get_metrics_retention_days(db))
    deleted = db.query(HostMetric).filter(HostMetric.time < cutoff).delete(synchronize_session=False)
    deleted += db.query(ContainerMetric).filter(ContainerMetric.time < cutoff).delete(synchronize_session=False)
    deleted += db.query(MountMetric).filter(MountMetric.time < cutoff).delete(synchronize_session=False)
    deleted += db.query(SystemEvent).filter(SystemEvent.time < cutoff).delete(synchronize_session=False)
    db.commit()
    return deleted


@app.get("/admin/settings")
def get_admin_settings(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    return _admin_settings(db)


@app.put("/admin/settings")
def update_admin_settings(payload: AdminSettingsRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _require_admin(session_id, db)
    changes = []
    days = payload.metrics_retention_days
    if days is not None:
        if not MIN_METRICS_RETENTION_DAYS <= days <= MAX_METRICS_RETENTION_DAYS:
            raise HTTPException(status_code=400, detail=f"Retention must be between {MIN_METRICS_RETENTION_DAYS} and {MAX_METRICS_RETENTION_DAYS} days.")
        _set_setting(db, METRICS_RETENTION_KEY, str(days))
        changes.append(f"Changed metrics retention to {days} days")
    if payload.agent_chat_enabled is not None:
        _set_setting(db, AGENT_CHAT_ENABLED_KEY, "true" if payload.agent_chat_enabled else "false")
        changes.append(f"{'Enabled' if payload.agent_chat_enabled else 'Disabled'} chat with the agent")
    if payload.alert_thresholds is not None:
        current = get_alert_thresholds(db)
        try:
            thresholds = validate_thresholds(payload.alert_thresholds, current)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        changed = {key: value for key, value in thresholds.items() if current[key] != value}
        _set_setting(db, ALERT_THRESHOLDS_KEY, json.dumps(thresholds))
        if changed:
            changes.append("Changed alert thresholds: " + ", ".join(f"{key}={value}" for key, value in changed.items()))
    if payload.mount_thresholds is not None:
        try:
            mount_thresholds = validate_mount_thresholds(payload.mount_thresholds)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if mount_thresholds != get_mount_thresholds(db):
            _set_setting(db, MOUNT_THRESHOLDS_KEY, json.dumps(mount_thresholds))
            changes.append("Changed per-mount disk thresholds: " + (", ".join(f"{mount}={limits['disk_warning_pct']:g}/{limits['disk_critical_pct']:g}" for mount, limits in mount_thresholds.items()) or "none"))
    db.commit()
    for change in changes:
        record_audit_log(db, user_id=user["id"], level="INFO", message=change, source="admin")
    return _admin_settings(db)


@app.get("/agent/config")
def agent_config(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """What the Agent page needs to know about the agent; readable by every signed-in user."""
    _current_user(session_id, db)
    return {"chat_enabled": is_agent_chat_enabled(db)}


OVERVIEW_ALERT_LIMIT = 50


SYSTEM_EVENTS_SHOWN = 10


def recent_system_events(db, since: datetime) -> dict[int, list[dict]]:
    """Each server's recent events, newest first, for the dashboard's System row.

    Kernel errors repeat on every boot, so identical messages are collapsed into one row with a count
    (and the first and last time seen) and capped separately; otherwise they would push reboots and
    OOM kills out of the list. Every other kind stays one row per occurrence.
    """
    result: dict[int, list[dict]] = {}
    for event in db.query(SystemEvent).filter(SystemEvent.time >= since, SystemEvent.kind != "kernel_error", SystemEvent.kind.notin_(CONTAINER_EVENT_KINDS + NETWORK_EVENT_KINDS)).order_by(SystemEvent.time.desc()):
        rows = result.setdefault(event.server_id, [])
        if len(rows) < SYSTEM_EVENTS_SHOWN:
            rows.append({"time": event.time.isoformat(), "first_time": event.time.isoformat(), "kind": event.kind, "detail": event.detail, "count": 1})
    shown: dict[int, int] = {}
    grouped = (
        db.query(SystemEvent.server_id, SystemEvent.detail, func.count(), func.min(SystemEvent.time), func.max(SystemEvent.time))
        .filter(SystemEvent.time >= since, SystemEvent.kind == "kernel_error")
        .group_by(SystemEvent.server_id, SystemEvent.detail)
        .order_by(func.max(SystemEvent.time).desc())
    )
    for server_id, detail, count, first, last in grouped:
        if shown.get(server_id, 0) < SYSTEM_EVENTS_SHOWN:
            shown[server_id] = shown.get(server_id, 0) + 1
            result.setdefault(server_id, []).append({"time": last.isoformat(), "first_time": first.isoformat(), "kind": "kernel_error", "detail": detail, "count": count})
    for rows in result.values():
        rows.sort(key=lambda row: row["time"], reverse=True)
    return result


CONTAINER_EVENTS_SHOWN = 15


def recent_container_events(db, since: datetime) -> dict[int, list[dict]]:
    """Each server's recent container changes (appeared, removed, image changed), newest first.

    Kept apart from the System row so a burst of container churn can't push reboots or OOM kills out of it.
    """
    result: dict[int, list[dict]] = {}
    for event in db.query(SystemEvent).filter(SystemEvent.time >= since, SystemEvent.kind.in_(CONTAINER_EVENT_KINDS)).order_by(SystemEvent.time.desc()):
        rows = result.setdefault(event.server_id, [])
        if len(rows) < CONTAINER_EVENTS_SHOWN:
            rows.append({"time": event.time.isoformat(), "kind": event.kind, "detail": event.detail})
    return result


NETWORK_EVENTS_SHOWN = 15
LISTENERS_SHOWN = 200


def recent_network_events(db, since: datetime) -> dict[int, list[dict]]:
    """Each server's recent network changes (ports opened or closed, firewall changes), newest first."""
    result: dict[int, list[dict]] = {}
    for event in db.query(SystemEvent).filter(SystemEvent.time >= since, SystemEvent.kind.in_(NETWORK_EVENT_KINDS)).order_by(SystemEvent.time.desc()):
        rows = result.setdefault(event.server_id, [])
        if len(rows) < NETWORK_EVENTS_SHOWN:
            rows.append({"time": event.time.isoformat(), "kind": event.kind, "detail": event.detail})
    return result


@app.get("/monitoring/overview")
def monitoring_overview(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    # Servers belong to the install: everyone signed in can view them, only admins manage them.
    user = _current_user(session_id, db)
    # The dashboard's own machine first, then the newest servers.
    servers = db.query(Server).order_by(Server.is_dashboard_host.desc(), Server.created_at.desc()).all()
    alert_counts = dict(db.query(IncidentAlert.server_id, func.count()).filter(IncidentAlert.status == "active").group_by(IncidentAlert.server_id).all())
    since = datetime.now(timezone.utc) - timedelta(days=7)
    events = recent_system_events(db, since)
    container_events = recent_container_events(db, since)
    network_events = recent_network_events(db, since)
    listeners: dict[int, list[dict]] = {}
    for row in db.query(ListenerState).order_by(ListenerState.port, ListenerState.proto):
        if len(listeners.setdefault(row.server_id, [])) < LISTENERS_SHOWN:
            listeners[row.server_id].append({"proto": row.proto, "port": row.port, "scope": row.scope, "first_seen": row.first_seen.isoformat()})
    firewalls: dict[int, list[dict]] = {}
    for row in db.query(FirewallState).order_by(FirewallState.name):
        firewalls.setdefault(row.server_id, []).append({"name": row.name, "label": FIREWALL_LABELS.get(row.name, row.name), "rules": row.rules, "enabled": row.enabled, "updated_at": row.updated_at.isoformat()})
    result = []
    for server in servers:
        metric = db.query(HostMetric).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc()).first()
        latest = {item.container_id: item for item in latest_containers(db, server.id, metric.time if metric else None)}
        mounts = db.query(MountMetric).filter(MountMetric.server_id == server.id, MountMetric.time == metric.time).order_by(MountMetric.mount).all() if metric else []
        result.append({
            "id": server.id, "hostname": server.hostname, "ip_address": server.ip_address, "os_info": server.os_info,
            "cpu_cores": server.cpu_cores, "total_ram_gb": server.total_ram_gb,
            "check_interval_seconds": server.check_interval_seconds, "is_dashboard_host": server.is_dashboard_host,
            "active_alert_count": alert_counts.get(server.id, 0),
            "last_seen_at": server.last_seen_at.isoformat() if server.last_seen_at else None,
            "host": ({key: getattr(metric, key) for key in ("time", "cpu_utilization_pct", "load_1m", "load_5m", "load_15m", "ram_used_gb", "ram_available_gb", "ram_pct", "swap_pct", "disk_usage_pct", "disk_inodes_pct", "net_bytes_sent_mb", "net_bytes_recv_mb", "system_uptime_seconds", "oom_kill_total", "kernel_tainted", "ntp_synced", "reboot_required", "last_shutdown", "docker_status", "docker_reason", "docker_latency_ms", "internet_status", "internet_latency_ms")} if metric else None),
            "network": {"listeners": listeners.get(server.id, []), "firewall": firewalls.get(server.id, []), "events": network_events.get(server.id, [])},
            "container_events": container_events.get(server.id, []),
            "system_events": events.get(server.id, []),
            "mounts": [{key: getattr(m, key) for key in ("mount", "fs_type", "total_gb", "used_gb", "used_pct", "inodes_pct")} for m in mounts],
            "containers": [{"container_id": c.container_id, "name": c.name, "image": c.image, "status": c.status, "restart_count": c.restart_count, "cpu_pct": c.cpu_pct, "memory_used_mb": c.memory_used_mb, "memory_pct": c.memory_pct, "healthcheck_status": c.healthcheck_status, "exit_code": c.exit_code, "oom_killed": c.oom_killed, "finished_at": c.finished_at.isoformat() if c.finished_at else None, "time": c.time.isoformat()} for c in latest.values()],
        })
    active_alerts = db.query(IncidentAlert).filter(IncidentAlert.status == "active")
    # Most severe first, then newest.
    severity_rank = case({severity: rank for rank, severity in enumerate(reversed(SEVERITIES))}, value=IncidentAlert.severity, else_=len(SEVERITIES))
    alerts = active_alerts.order_by(severity_rank, IncidentAlert.created_at.desc()).limit(OVERVIEW_ALERT_LIMIT).all()
    hostnames = {server.id: server.hostname for server in servers}
    thresholds = get_alert_thresholds(db)
    pending = visible_pending_collectors(db) if _is_admin(db, user["id"]) else []
    return {
        "servers": result, "pending_collectors": pending, "alert_count": active_alerts.count(),
        # Lets the dashboard color disk bars the same way the alert rules judge them.
        "mount_thresholds": get_mount_thresholds(db),
        "disk_thresholds": {key: thresholds[key] for key in ("disk_warning_pct", "disk_critical_pct", "inodes_warning_pct", "inodes_critical_pct")},
        "alerts": [{"id": a.id, "server_id": a.server_id, "hostname": hostnames.get(a.server_id), "severity": a.severity, "title": a.title, "description": a.description, "created_at": a.created_at.isoformat()} for a in alerts],
    }


def latest_containers(db, server_id: int, host_time: datetime | None) -> list[ContainerMetric]:
    """The containers reported in the server's latest check-in, not every container seen within retention.

    A check-in saves its host and container rows with one shared timestamp.
    """
    if host_time is None:
        return []
    return db.query(ContainerMetric).filter(ContainerMetric.server_id == server_id, ContainerMetric.time == host_time).all()


HISTORY_MAX_MINUTES = 24 * 60


@app.get("/monitoring/servers/{server_id}/history")
def server_history(server_id: int, minutes: int = 30, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """CPU, memory and root disk usage of one server over the last ``minutes``, oldest first."""
    _current_user(session_id, db)
    if not db.query(Server).filter(Server.id == server_id).first():
        raise HTTPException(status_code=404, detail="Server not found.")
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, min(minutes, HISTORY_MAX_MINUTES)))
    rows = db.query(HostMetric.time, HostMetric.cpu_utilization_pct, HostMetric.ram_pct, HostMetric.disk_usage_pct).filter(HostMetric.server_id == server_id, HostMetric.time >= since).order_by(HostMetric.time).all()
    return {"server_id": server_id, "samples": [{"time": row.time.isoformat(), "cpu": row.cpu_utilization_pct, "ram": row.ram_pct, "disk": row.disk_usage_pct} for row in rows]}


CONTAINER_DETAIL_FIELDS = (
    "status", "healthcheck_status", "restart_count", "cpu_pct", "memory_used_mb", "memory_limit_mb", "memory_pct",
    "net_rx_mb", "net_tx_mb", "block_read_mb", "block_write_mb", "pids",
)


@app.get("/monitoring/servers/{server_id}/containers/{container_id}")
def container_detail(server_id: int, container_id: str, limit: int = 60, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    samples = (
        db.query(ContainerMetric)
        .filter(ContainerMetric.server_id == server.id, ContainerMetric.container_id == container_id)
        .order_by(ContainerMetric.time.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    if not samples:
        raise HTTPException(status_code=404, detail="Container not found.")
    latest = samples[0]
    return {
        "server_id": server.id,
        "hostname": server.hostname,
        "container_id": latest.container_id,
        "name": latest.name,
        "image": latest.image,
        "started_at": latest.started_at,
        "ports": latest.ports,
        "latest": {"time": latest.time.isoformat(), **{field: getattr(latest, field) for field in CONTAINER_DETAIL_FIELDS}},
        "history": [{"time": s.time.isoformat(), **{field: getattr(s, field) for field in CONTAINER_DETAIL_FIELDS}} for s in reversed(samples)],
    }


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _collector_via(request) -> str:
    """Which listener the request came in on: "vpn", "internal", or "" (public or direct).

    nginx sets the header on its collector listeners and clears it on the public one, so a client
    can't claim a listener it didn't use. In strict mode only collector listeners are answered.
    """
    via = getattr(request, "headers", {}).get("x-collector-via", "")
    if COLLECTOR_ACCESS == "strict" and via not in ("vpn", "internal"):
        raise HTTPException(status_code=404, detail="Not Found")
    return via


def require_collector_listener(request: Request) -> None:
    _collector_via(request)


@app.get("/monitoring/enrollment-key")
def get_enrollment_key(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _require_admin(session_id, db)
    key = db.query(EnrollmentKey).filter(EnrollmentKey.owner_id == user["id"]).first()
    return {"exists": key is not None, "created_at": key.created_at.isoformat() if key else None}


@app.post("/monitoring/enrollment-key")
def rotate_enrollment_key(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Create or replace the account's enrollment key. The plaintext is returned only once."""
    user = _require_admin(session_id, db)
    plaintext = f"hlk_{secrets.token_urlsafe(32)}"
    key = db.query(EnrollmentKey).filter(EnrollmentKey.owner_id == user["id"]).first()
    if key:
        key.key_hash = _hash_secret(plaintext)
        key.created_at = datetime.now(timezone.utc)
    else:
        key = EnrollmentKey(owner_id=user["id"], key_hash=_hash_secret(plaintext))
        db.add(key)
    db.commit()
    return {"enroll_key": plaintext, "created_at": key.created_at.isoformat()}


@app.delete("/monitoring/enrollment-key")
def revoke_enrollment_key(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _require_admin(session_id, db)
    db.query(EnrollmentKey).filter(EnrollmentKey.owner_id == user["id"]).delete()
    db.commit()
    return {"status": "revoked"}


@app.post("/monitoring/collectors/enroll", dependencies=[Depends(require_collector_listener)])
def enroll_collector(payload: EnrollRequest, db=Depends(get_db)):
    """Register (or re-register) a collector using the owner's enrollment key.

    Servers are matched on machine_id, so a collector that lost its saved
    credentials gets its existing server back with a fresh token.
    """
    key = db.query(EnrollmentKey).filter(EnrollmentKey.key_hash == _hash_secret(payload.enroll_key.strip())).first()
    if not key:
        raise HTTPException(status_code=401, detail="Enrollment key is invalid or has been rotated.")
    if not _is_admin(db, key.owner_id):
        raise HTTPException(status_code=403, detail="Enrollment key belongs to an account that can no longer add servers.")
    hostname = payload.hostname.strip()[:255]
    machine_id = (payload.machine_id or "").strip()[:64] or None
    if not hostname:
        raise HTTPException(status_code=400, detail="Hostname is required.")

    server = None
    if machine_id:
        server = db.query(Server).filter(Server.machine_id == machine_id).first()
    if not server:
        server = Server(owner_id=key.owner_id, hostname=hostname, machine_id=machine_id)
        db.add(server)
    token = secrets.token_urlsafe(32)
    server.collector_token_hash = _hash_secret(token)
    db.commit()
    db.refresh(server)
    return {"server_id": server.id, "collector_token": token, "check_interval_seconds": server.check_interval_seconds}


PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PENDING_VISIBLE_SECONDS = 120
PENDING_RETENTION = timedelta(days=1)
MAX_PENDING_COLLECTORS = 100
MAX_OPEN_JOIN_TOKENS = 20
JOIN_TOKEN_PREFIX = "hlj_"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.post("/monitoring/join-tokens")
def create_join_token(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Mint the single-use token the Connect a server command carries. The plaintext is returned only once."""
    user = _require_admin(session_id, db)
    now = datetime.now(timezone.utc)
    if db.query(JoinToken).filter(JoinToken.used_at.is_(None), JoinToken.expires_at > now).count() >= MAX_OPEN_JOIN_TOKENS:
        raise HTTPException(status_code=429, detail="Too many unused join tokens. Wait for them to expire or add the servers already waiting.")
    plaintext = f"{JOIN_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    db.add(JoinToken(token_hash=_hash_secret(plaintext), created_by=user["id"], expires_at=now + timedelta(seconds=JOIN_TOKEN_TTL_SECONDS)))
    db.commit()
    return {"join_token": plaintext, "expires_in_seconds": JOIN_TOKEN_TTL_SECONDS}


@app.get("/monitoring/install-info")
def install_info(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """What the Connect a server panel needs to build its command."""
    _require_admin(session_id, db)
    return {"strict": COLLECTOR_ACCESS == "strict", "collector_url": COLLECTOR_PUBLIC_URL or None, "collector_image": COLLECTOR_IMAGE or None, "proxy_image": DOCKER_PROXY_IMAGE or None}


@app.post("/monitoring/collectors/register", dependencies=[Depends(require_collector_listener)])
def register_collector(payload: CollectorRegisterRequest, request: Request, db=Depends(get_db)):
    """A new collector asks to join and waits until a user clicks Add.

    Over the VPN listener a join token from the dashboard's Connect a server command is required.
    """
    via = _collector_via(request)
    ip_address = _client_ip(request)
    enforce_ip_limit(redis_client, scope="collector-register", ip_address=ip_address, limit=30, window_seconds=3600)
    hostname = payload.hostname.strip()[:255]
    if not hostname:
        raise HTTPException(status_code=400, detail="Hostname is required.")
    machine_id = (payload.machine_id or "").strip()[:64] or None
    join_token = None
    if payload.join_token:
        join_token = db.query(JoinToken).filter(JoinToken.token_hash == _hash_secret(payload.join_token.strip()), JoinToken.used_at.is_(None), JoinToken.expires_at > datetime.now(timezone.utc)).first()
        if not join_token:
            raise HTTPException(status_code=403, detail="The join token is invalid, expired or already used. Copy a fresh command from the dashboard.")
    elif COLLECTOR_ACCESS == "strict" and via == "vpn":
        raise HTTPException(status_code=403, detail="A join token is required. Copy the command from the dashboard's Connect a server button.")
    if join_token:
        # The same host retrying with its token replaces its earlier request until someone approves it.
        db.query(PendingCollector).filter(PendingCollector.join_token_id == join_token.id, PendingCollector.status != "approved").delete(synchronize_session=False)
    if machine_id:
        # A restarted collector replaces its own earlier request instead of piling up duplicates.
        db.query(PendingCollector).filter(PendingCollector.machine_id == machine_id, PendingCollector.ip_address == ip_address, PendingCollector.status != "approved").delete(synchronize_session=False)
    if db.query(PendingCollector).count() >= MAX_PENDING_COLLECTORS:
        raise HTTPException(status_code=503, detail="Too many servers are waiting for approval.")
    secret = secrets.token_urlsafe(32)
    code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(6))
    pending = PendingCollector(
        poll_secret_hash=_hash_secret(secret), pairing_code=f"{code[:3]}-{code[3:]}", hostname=hostname,
        machine_id=machine_id, ip_address=ip_address[:64], os_info=(payload.os_info or "")[:255] or None,
        join_token_id=join_token.id if join_token else None,
    )
    db.add(pending)
    db.commit()
    return {"request_secret": secret, "pairing_code": pending.pairing_code, "poll_interval_seconds": 5}


@app.post("/monitoring/collectors/register/poll", dependencies=[Depends(require_collector_listener)])
def poll_collector_registration(authorization: Annotated[str | None, Header()] = None, db=Depends(get_db)):
    secret = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else ""
    pending = db.query(PendingCollector).filter(PendingCollector.poll_secret_hash == _hash_secret(secret)).first() if secret else None
    if not pending:
        raise HTTPException(status_code=404, detail="Registration request not found or expired.")
    pending.last_poll_at = datetime.now(timezone.utc)
    if pending.status != "approved":
        db.commit()
        return {"status": pending.status}
    server = db.query(Server).filter(Server.id == pending.approved_server_id).first()
    db.delete(pending)
    if not server:
        db.commit()
        raise HTTPException(status_code=404, detail="The approved server was deleted.")
    # The token is minted only now, so it never sits in the database in readable form.
    token = secrets.token_urlsafe(32)
    server.collector_token_hash = _hash_secret(token)
    db.commit()
    return {"status": "approved", "server_id": server.id, "collector_token": token, "check_interval_seconds": server.check_interval_seconds}


def _server_for_machine(db, machine_id: str | None) -> Server | None:
    return db.query(Server).filter(Server.machine_id == machine_id).first() if machine_id else None


def _pending_for_user(pending_id: int, db) -> PendingCollector:
    pending = db.query(PendingCollector).filter(PendingCollector.id == pending_id, PendingCollector.status != "approved").first()
    if not pending:
        raise HTTPException(status_code=404, detail="Pending server not found.")
    return pending


@app.post("/monitoring/pending-collectors/{pending_id}/approve")
def approve_pending_collector(pending_id: int, payload: ApproveRequest | None = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _require_admin(session_id, db)
    pending = _pending_for_user(pending_id, db)
    server = _server_for_machine(db, pending.machine_id)
    if server and not (payload and payload.replace):
        # Approving mints new credentials for that server and locks out its current collector, so never do it silently.
        raise HTTPException(status_code=409, detail=f"This machine matches the existing server {server.hostname}. Approving replaces that server's credentials and signs its current collector out. Confirm to continue.")
    if not server:
        server = Server(owner_id=user["id"], hostname=pending.hostname, machine_id=pending.machine_id, ip_address=pending.ip_address, os_info=pending.os_info)
        db.add(server)
        db.flush()
    pending.status = "approved"
    pending.approved_server_id = server.id
    if pending.join_token_id:
        db.query(JoinToken).filter(JoinToken.id == pending.join_token_id).update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    return {"status": "approved", "server_id": server.id}


@app.post("/monitoring/pending-collectors/{pending_id}/dismiss")
def dismiss_pending_collector(pending_id: int, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    pending = _pending_for_user(pending_id, db)
    pending.status = "dismissed"
    db.commit()
    return {"status": "dismissed"}


def visible_pending_collectors(db, now: datetime | None = None) -> list[dict]:
    """Pending and dismissed requests from collectors that are still polling."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=PENDING_VISIBLE_SECONDS)
    rows = db.query(PendingCollector).filter(PendingCollector.status.in_(("pending", "dismissed"))).order_by(PendingCollector.created_at.desc()).all()
    visible = []
    for row in rows:
        last_poll = row.last_poll_at if row.last_poll_at.tzinfo else row.last_poll_at.replace(tzinfo=timezone.utc)
        if last_poll >= cutoff:
            existing = _server_for_machine(db, row.machine_id)
            visible.append({
                "id": row.id, "hostname": row.hostname, "pairing_code": row.pairing_code, "ip_address": row.ip_address, "os_info": row.os_info, "status": row.status, "created_at": row.created_at.isoformat(),
                "verified": row.join_token_id is not None,
                "replaces": {"id": existing.id, "hostname": existing.hostname} if existing else None,
            })
    return visible


def purge_stale_pending(db, now: datetime | None = None) -> None:
    cutoff = (now or datetime.now(timezone.utc)) - PENDING_RETENTION
    db.query(PendingCollector).filter(PendingCollector.last_poll_at < cutoff).delete(synchronize_session=False)
    db.query(JoinToken).filter(JoinToken.expires_at < (now or datetime.now(timezone.utc)) - timedelta(days=1)).delete(synchronize_session=False)
    db.commit()


OFFLINE_AFTER_MISSED_CHECKS = 3
OFFLINE_ALERT_DESCRIPTION = "The collector has stopped reporting. Check that the server is up and can reach the backend."


def _offline_title(server: Server) -> str:
    return f"Server {server.hostname} is offline"


def mark_offline_servers(db, now: datetime | None = None) -> int:
    """Raise one active alert per server that has missed several check-ins."""
    now = now or datetime.now(timezone.utc)
    raised = 0
    for server in db.query(Server).filter(Server.last_seen_at.isnot(None)).all():
        last_seen = server.last_seen_at if server.last_seen_at.tzinfo else server.last_seen_at.replace(tzinfo=timezone.utc)
        if now - last_seen < timedelta(seconds=server.check_interval_seconds * OFFLINE_AFTER_MISSED_CHECKS):
            continue
        if raise_alert(db, server.id, SERVER_OFFLINE_RULE, CRITICAL, _offline_title(server), OFFLINE_ALERT_DESCRIPTION):
            raised += 1
    db.commit()
    return raised


METRICS_PURGE_INTERVAL_SECONDS = 3600


def analyze_memory_trends(db, now: datetime | None = None) -> None:
    """Hourly: flag containers whose memory climbs steadily, and clear flags that no longer hold."""
    now = now or datetime.now(timezone.utc)
    thresholds = get_alert_thresholds(db)
    for server in db.query(Server).all():
        sync_alerts(db, server.id, TREND_RULE_PREFIX, memory_trend_rules(db, server.id, thresholds, now), now)
    db.commit()


def _offline_watch_loop() -> None:
    last_metrics_purge: float | None = None
    while True:
        time.sleep(60)
        db = SessionLocal()
        try:
            mark_offline_servers(db)
            purge_stale_pending(db)
            if last_metrics_purge is None or time.monotonic() - last_metrics_purge >= METRICS_PURGE_INTERVAL_SECONDS:
                purge_old_metrics(db)
                analyze_memory_trends(db)
                last_metrics_purge = time.monotonic()
        except Exception as exc:  # keep the watcher alive through transient DB errors
            print(f"Offline watch failed: {type(exc).__name__}: {exc}", flush=True)
        finally:
            db.close()


@app.post("/monitoring/servers")
def add_monitored_server(payload: ServerRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _require_admin(session_id, db)
    if not payload.hostname.strip() or not 15 <= payload.check_interval_seconds <= 86400:
        raise HTTPException(status_code=400, detail="Hostname is required and interval must be between 15 and 86400 seconds.")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    server = Server(owner_id=user["id"], hostname=payload.hostname.strip()[:255], ip_address=payload.ip_address, check_interval_seconds=payload.check_interval_seconds, collector_token_hash=token_hash)
    db.add(server); db.commit(); db.refresh(server)
    return {"id": server.id, "hostname": server.hostname, "check_interval_seconds": server.check_interval_seconds, "collector_token": token}


@app.delete("/monitoring/servers/{server_id}")
def delete_monitored_server(server_id: int, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")

    db.query(HostMetric).filter(HostMetric.server_id == server.id).delete(synchronize_session=False)
    db.query(ContainerMetric).filter(ContainerMetric.server_id == server.id).delete(synchronize_session=False)
    db.query(MountMetric).filter(MountMetric.server_id == server.id).delete(synchronize_session=False)
    db.query(SystemEvent).filter(SystemEvent.server_id == server.id).delete(synchronize_session=False)
    db.query(ListenerState).filter(ListenerState.server_id == server.id).delete(synchronize_session=False)
    db.query(FirewallState).filter(FirewallState.server_id == server.id).delete(synchronize_session=False)
    db.query(IncidentAlert).filter(IncidentAlert.server_id == server.id).delete(synchronize_session=False)
    db.query(PendingCollector).filter(PendingCollector.approved_server_id == server.id).delete(synchronize_session=False)
    db.delete(server)
    db.commit()
    return {"status": "deleted", "server_id": server_id}


@app.patch("/monitoring/servers/{server_id}/interval")
def update_check_interval(server_id: int, payload: dict, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    interval = payload.get("check_interval_seconds")
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    if not isinstance(interval, int) or not 15 <= interval <= 86400:
        raise HTTPException(status_code=400, detail="Interval must be between 15 and 86400 seconds.")
    server.check_interval_seconds = interval; db.commit()
    return {"id": server.id, "check_interval_seconds": server.check_interval_seconds}


def record_helper_events(db, server_id: int, raw, now: datetime) -> None:
    """Store the helper's kernel-log events once each; the helper republishes the whole ring buffer."""
    events = validate_helper_events(raw, now)
    if not events:
        return
    known = {key for (key,) in db.query(SystemEvent.event_key).filter(SystemEvent.server_id == server_id, SystemEvent.event_key.in_([event[3] for event in events]))}
    for kind, time, detail, key in events:
        if key not in known:
            known.add(key)
            db.add(SystemEvent(server_id=server_id, time=time, kind=kind, detail=detail, event_key=key))


DOCKER_STATUSES = {"ok", "unreachable", "not_configured"}
DOCKER_REASON_CODES = {"timeout", "refused", "missing", "denied", "error"}
MAX_CONTAINERS_PER_CHECKIN = 500
# Names end up inside incident keys (255 characters, with a prefix and suffix), so they are kept well under that.
MAX_CONTAINER_NAME = 128
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_CONTAINER_FLOATS = ("cpu_pct", "memory_used_mb", "memory_pct", "memory_limit_mb", "net_rx_mb", "net_tx_mb", "block_read_mb", "block_write_mb")
_CONTAINER_TEXT = {"image": 255, "started_at": 64, "ports": 512, "healthcheck_status": 64}


def _printable(text: str, limit: int) -> str:
    """Trim surrounding whitespace, replace control characters, and cap the length."""
    return "".join(char if char.isprintable() else "?" for char in text.strip())[:limit].strip()


def _whole_number(value, low: int, high: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def _clean_container(raw, now: datetime) -> dict | None:
    """One container record reduced to known fields of the right type and size, or None if unusable.

    Everything here comes from a collector, so nothing is trusted: text is made printable and bounded,
    numbers are range-checked, and anything unexpected becomes None (unknown, which never alerts).
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("container_id"), str) or not isinstance(raw.get("status"), str):
        return None
    container_id, status = _printable(raw["container_id"], 128), _printable(raw["status"], 64).lower()
    if not container_id or not status:
        return None
    name = raw.get("name")
    clean: dict = {"container_id": container_id, "status": status, "name": (_printable(name, MAX_CONTAINER_NAME) if isinstance(name, str) else "") or container_id[:12]}
    for key, limit in _CONTAINER_TEXT.items():
        clean[key] = (_printable(raw[key], limit) or None) if isinstance(raw.get(key), str) else None
    clean["restart_count"] = _whole_number(raw.get("restart_count"), 0, 10**9) or 0
    clean["pids"] = _whole_number(raw.get("pids"), 0, 10**9)
    for key in _CONTAINER_FLOATS:
        value = raw.get(key)
        clean[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1e12 else None
    clean["exit_code"] = _whole_number(raw.get("exit_code"), -1, 65535)
    clean["oom_killed"] = raw["oom_killed"] if isinstance(raw.get("oom_killed"), bool) else None
    clean["image_id"] = raw["image_id"] if isinstance(raw.get("image_id"), str) and _IMAGE_ID.fullmatch(raw["image_id"]) else None
    clean["finished_at"] = None
    if isinstance(raw.get("finished_at"), str):
        try:
            finished = datetime.strptime(raw["finished_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            clean["finished_at"] = finished if datetime(2000, 1, 1, tzinfo=timezone.utc) <= finished <= now + timedelta(minutes=5) else None
        except ValueError:
            pass
    return clean


def record_capped_events(db, server_id: int, events: list[tuple[str, str, str]], kinds: tuple[str, ...], notice_kind: str, subject: str, now: datetime,
                         per_check_in: int, per_hour: int) -> None:
    """Store (kind, detail, key) events with hard caps, so churn can't flood the table.

    At most ``per_check_in`` are stored per call and ``per_hour`` per server and category; anything beyond
    that is summarised by a single notice per hour.
    """
    if not events:
        return
    since = now - timedelta(hours=1)
    recent = db.query(func.count(SystemEvent.id)).filter(SystemEvent.server_id == server_id, SystemEvent.kind.in_(kinds), SystemEvent.time >= since).scalar() or 0
    allowed = events[:max(0, min(per_check_in, per_hour - recent))]
    for kind, detail, key in allowed:
        db.add(SystemEvent(server_id=server_id, time=now, kind=kind, detail=detail[:512], event_key=key[:128]))
    dropped = len(events) - len(allowed)
    if dropped and not db.query(SystemEvent.id).filter(SystemEvent.server_id == server_id, SystemEvent.kind == notice_kind, SystemEvent.time >= since).first():
        db.add(SystemEvent(server_id=server_id, time=now, kind=notice_kind, detail=f"{dropped} more {subject} were not recorded (too many at once)"))


def record_container_events(db, server_id: int, events: list[tuple[str, str, str]], now: datetime) -> None:
    record_capped_events(db, server_id, events, CONTAINER_EVENT_KINDS, "container_notice", "container changes", now, MAX_CONTAINER_EVENTS_PER_CHECKIN, MAX_CONTAINER_EVENTS_PER_HOUR)


def sync_listeners(db, server_id: int, listeners: list[tuple[str, int, str]] | None, now: datetime) -> list[tuple[str, str, str]]:
    """Update the known listeners from a validated report and return what changed.

    The first report is a silent baseline. An empty report is treated as unknown rather than as "everything
    closed", since a host always listens on something and an empty list means the table couldn't be read.
    """
    if not listeners:
        return []
    current = set(listeners)
    known = {(row.proto, row.port, row.scope): row for row in db.query(ListenerState).filter(ListenerState.server_id == server_id)}
    events = detect_listener_changes(known, current)
    for key in sorted(current - known.keys()):
        db.add(ListenerState(server_id=server_id, proto=key[0], port=key[1], scope=key[2], first_seen=now, last_seen=now))
    for key in known.keys() - current:
        db.delete(known[key])
    for key in current & known.keys():
        known[key].last_seen = now
    return events


def sync_firewall(db, server_id: int, firewall: dict, now: datetime) -> list[tuple[str, str, str]]:
    """Update the stored firewall fingerprints from a validated report and return what changed (see detect_firewall_changes)."""
    known = {row.name: row for row in db.query(FirewallState).filter(FirewallState.server_id == server_id)}
    events = detect_firewall_changes(known, firewall)
    for name, (sha, count) in firewall["files"].items():
        enabled = firewall["ufw_enabled"] if name == "ufw-conf" else None
        if name in known:
            row = known[name]
            if (row.sha, row.rules, row.enabled) != (sha, count, enabled):
                row.sha, row.rules, row.enabled, row.updated_at = sha, count, enabled, now
        else:
            db.add(FirewallState(server_id=server_id, name=name, sha=sha, rules=count, enabled=enabled, updated_at=now))
    for name in known.keys() - firewall["files"].keys():
        db.delete(known[name])
    return events


def _clean_system_fields(fields: dict) -> None:
    """Drop system-state values of the wrong type; a collector reports None when it can't tell."""
    for key, kind in (("oom_kill_total", int), ("kernel_tainted", int), ("ntp_synced", bool), ("reboot_required", bool)):
        if key in fields and not (isinstance(fields[key], kind) and (kind is bool or not isinstance(fields[key], bool))):
            fields[key] = None
    if "last_shutdown" in fields and fields["last_shutdown"] not in {"clean", "unclean", "unknown"}:
        fields["last_shutdown"] = None
    if "docker_status" in fields and fields["docker_status"] not in DOCKER_STATUSES:
        fields["docker_status"] = None
    if "docker_reason" in fields and fields["docker_reason"] not in DOCKER_REASON_CODES:
        fields["docker_reason"] = None
    if "docker_latency_ms" in fields:
        fields["docker_latency_ms"] = _whole_number(fields["docker_latency_ms"], 0, 600_000)
    if "internet_status" in fields and fields["internet_status"] not in INTERNET_STATUSES:
        fields["internet_status"] = None
    if "internet_latency_ms" in fields:
        fields["internet_latency_ms"] = _whole_number(fields["internet_latency_ms"], 0, 600_000)
    if "boot_id" in fields:
        fields["boot_id"] = fields["boot_id"][:64] if isinstance(fields["boot_id"], str) and fields["boot_id"] else None


@app.post("/monitoring/metrics/ingest", dependencies=[Depends(require_collector_listener)])
def ingest_metrics(payload: MetricsIngestRequest, authorization: Annotated[str | None, Header()] = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    server = db.query(Server).filter(Server.id == payload.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else None
    if server.collector_token_hash and token_hash and hmac.compare_digest(server.collector_token_hash, token_hash):
        pass
    else:
        _require_admin(session_id, db)
    for field in ("hostname", "ip_address", "os_info", "cpu_cores", "total_ram_gb"):
        if field in payload.server and payload.server[field] is not None:
            setattr(server, field, payload.server[field])
    # Only one server can be the dashboard's own machine; the latest collector to claim it wins.
    server.is_dashboard_host = payload.server.get("dashboard_host") is True
    if server.is_dashboard_host:
        db.query(Server).filter(Server.id != server.id, Server.is_dashboard_host.is_(True)).update({"is_dashboard_host": False}, synchronize_session=False)
    now = datetime.now(timezone.utc)
    host_fields = {k: v for k, v in payload.host.items() if k in HostMetric.__table__.columns.keys() and k not in {"id", "server_id", "time"}}
    _clean_system_fields(host_fields)
    previous = db.query(HostMetric).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc(), HostMetric.id.desc()).first()
    # Only a collector that found the system helper sends its events; then they replace the OOM counter.
    helper_active = isinstance(payload.host.get("helper_events"), list)
    for kind, detail, key in detect_system_events(previous, host_fields, helper_active):
        db.add(SystemEvent(server_id=server.id, time=now, kind=kind, detail=detail, event_key=key))
    record_helper_events(db, server.id, payload.host.get("helper_events"), now)
    # The helper can finish after a reboot was first noticed; fill in how the last shutdown went.
    if host_fields.get("boot_id") and host_fields.get("last_shutdown") in REBOOT_DETAILS:
        db.query(SystemEvent).filter(SystemEvent.server_id == server.id, SystemEvent.event_key == f"{REBOOT_KEY_PREFIX}{host_fields['boot_id']}").update({"detail": REBOOT_DETAILS[host_fields["last_shutdown"]]}, synchronize_session=False)
    db.add(HostMetric(server_id=server.id, time=now, **host_fields))
    mounts = payload.host.get("mounts")
    for mount in mounts if isinstance(mounts, list) else []:
        if isinstance(mount, dict) and isinstance(mount.get("mount"), str) and mount["mount"]:
            allowed = {k: v for k, v in mount.items() if k in MountMetric.__table__.columns.keys() and k not in {"id", "server_id", "time"}}
            db.add(MountMetric(server_id=server.id, time=now, **allowed))
    containers = [clean for clean in (_clean_container(raw, now) for raw in payload.containers[:MAX_CONTAINERS_PER_CHECKIN]) if clean]
    # Compare with the previous check-in before this one's rows exist.
    previous_containers = latest_containers(db, server.id, previous.time) if previous else []
    record_container_events(db, server.id, detect_container_events(previous_containers, containers), now)
    for container in containers:
        db.add(ContainerMetric(server_id=server.id, time=now, **container))
    # Listening ports and firewall files: compare with what was seen last time (collectors that don't send them are skipped).
    network_events: list[tuple[str, str, str]] = []
    if "listeners" in payload.host:
        network_events += sync_listeners(db, server.id, validate_listeners(payload.host["listeners"]), now)
    if "firewall" in payload.host and (firewall := validate_firewall(payload.host["firewall"])) is not None:
        network_events += sync_firewall(db, server.id, firewall, now)
    record_capped_events(db, server.id, network_events, NETWORK_EVENT_KINDS, "network_notice", "network changes", now, MAX_NETWORK_EVENTS_PER_CHECKIN, MAX_NETWORK_EVENTS_PER_HOUR)
    db.flush()  # the rules read this check-in back from the history tables
    thresholds = get_alert_thresholds(db)
    sync_alerts(db, server.id, HOST_RULE_PREFIX, host_rules(db, server, thresholds, active_rule_keys(db, server.id, HOST_RULE_PREFIX), get_mount_thresholds(db)))
    sync_alerts(db, server.id, SYSTEM_RULE_PREFIX, system_rules(db, server, thresholds, now, active_rule_keys(db, server.id, SYSTEM_RULE_PREFIX)))
    sync_alerts(db, server.id, NETWORK_RULE_PREFIX, network_rules(db, server, thresholds, now))
    # The collector sends no containers when Docker is unreachable; keep container alerts as they are rather than resolving them all.
    if containers:
        sync_alerts(db, server.id, CONTAINER_RULE_PREFIX, container_rules(db, server.id, containers, thresholds, now))
    server.last_seen_at = now
    resolve_alerts(db, server.id, [SERVER_OFFLINE_RULE])
    db.commit()
    return {"status": "accepted", "interval_seconds": server.check_interval_seconds}

@app.get("/dashboard/summary")
def dashboard_summary(
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    db=Depends(get_db),
):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")

    total_users = db.query(User).count()
    total_logs = db.query(AuditLog).count()
    error_logs = db.query(AuditLog).filter(AuditLog.level.in_(["ERROR", "WARN"])).count()
    health_checks = db.query(AuditLog).filter(AuditLog.event_type == "health_check_completed").count()

    return {
        "total_users": total_users,
        "total_logs": total_logs,
        "error_logs": error_logs,
        "uptime": "99.97%",
        "health_checks": health_checks,
    }


@app.get("/logs")
def list_logs(
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    conversation_id: str | None = Query(default=None),
    db=Depends(get_db),
):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")

    return {"logs": get_recent_logs(db, limit=100, user_id=user["id"], conversation_id=conversation_id)}


def _conversation_payload(conversation: Conversation, messages: list[ChatMessage]) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "messages": [{"id": item.id, "role": item.role, "text": item.content, "created_at": item.created_at.isoformat()} for item in messages],
    }


@app.get("/conversations")
def list_conversations(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    conversations = db.query(Conversation).filter(Conversation.user_id == user["id"]).order_by(Conversation.updated_at.desc()).all()
    return {"conversations": [_conversation_payload(item, db.query(ChatMessage).filter(ChatMessage.conversation_id == item.id).order_by(ChatMessage.created_at).all()) for item in conversations]}


@app.post("/conversations")
def create_conversation(payload: ConversationRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    if db.query(Conversation).filter(Conversation.id == payload.conversation_id).first():
        raise HTTPException(status_code=409, detail="Conversation already exists.")
    conversation = Conversation(id=payload.conversation_id, user_id=user["id"], title=payload.title.strip()[:255] or "New chat")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_payload(conversation, [])


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.user_id == user["id"]).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.user_id == user["id"], AuditLog.conversation_id == conversation_id).delete(synchronize_session=False)
    db.delete(conversation)
    db.commit()
    delete_agent_conversation(user["id"], conversation_id)
    return {"status": "deleted"}


@app.post("/logs")
def create_log(
    payload: dict,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    db=Depends(get_db),
):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")

    return record_audit_log(
        db,
        user_id=user["id"],
        level=str(payload.get("level", "INFO")).upper(),
        message=str(payload.get("message", "New event")),
        source=str(payload.get("source", "dashboard")),
        task_title=str(payload.get("task_title") or payload.get("message", "New task")),
        steps=[str(step) for step in payload.get("steps", []) if str(step).strip()],
        conversation_id=payload.get("conversation_id"),
        event_type=payload.get("event_type"),
    )


@app.post("/agent/chat")
def agent_chat(payload: AgentChatRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    if not is_agent_chat_enabled(db):
        raise HTTPException(status_code=403, detail="Chat with the agent has been turned off by an admin.")

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required.")
    conversation = db.query(Conversation).filter(Conversation.id == payload.conversation_id, Conversation.user_id == user["id"]).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content=message))
    if conversation.title == "New chat":
        conversation.title = message[:255]
    db.commit()

    try:
        response_text = send_agent_message(
            user["id"],
            payload.conversation_id,
            message,
            command_logger=lambda event: record_audit_log(
                db,
                user_id=user["id"],
                source="health_monitor",
                task_title=message[:255],
                steps=event.get("steps", []),
                conversation_id=payload.conversation_id,
                event_type=event.get("event_type"),
                message=event.get("message", "Command event"),
                level=event.get("level", "INFO"),
            ),
        )
    except Exception as exc:
        user_message = agent_error_message(exc)
        record_audit_log(
            db,
            user_id=user["id"],
            level="ERROR",
            message=user_message[:255],
            source="health_monitor",
            task_title=message[:255],
            steps=["The AI provider request failed before a health check could run."],
            conversation_id=payload.conversation_id,
            event_type="agent_error",
        )
        db.add(ChatMessage(conversation_id=conversation.id, role="agent", content=f"Error: {user_message}"))
        db.commit()
        raise HTTPException(status_code=503, detail={"message": user_message}) from exc

    steps = ["Received the task and evaluated the request.", "Generated a structured response for the requested task."]

    record_audit_log(
        db,
        user_id=user["id"],
        level="INFO",
        message=response_text.splitlines()[0][:255] if response_text.splitlines() else "Agent task completed",
        source="homie",
        task_title=message[:255],
        steps=steps,
        conversation_id=conversation.id,
    )
    db.add(ChatMessage(conversation_id=conversation.id, role="agent", content=response_text))
    db.commit()
    return {"message": response_text}


@app.get("/")
def root():
    return {"message": "Homelab Agent API"}
