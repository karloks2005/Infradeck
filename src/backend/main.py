from __future__ import annotations

import ipaddress
import os
import json
import math
import unicodedata
import re
import hashlib
import secrets
import hmac
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

import redis
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import case, func, inspect, text

from .alerts import CONTAINER_RULE_PREFIX, CRITICAL, SERVER_OFFLINE_RULE, SEVERITIES, active_rule_keys, raise_alert, resolve_alerts, sync_alerts
import contextvars

from . import auth, dashboard, history as history_service, incidents, notify, rollups, secrets_box, stats, totp
from .auth import (
    STAGE_ENROLLMENT, STAGE_FULL, count_pending_attempt, create_pending_two_factor, create_session, delete_pending_two_factor, delete_session, get_recent_logs,
    get_session, get_user_from_session, hash_password, issue_recovery_codes, list_user_sessions, pending_two_factor_remember, read_pending_two_factor, session_lifetime, recovery_codes_left, record_audit_log,
    register_user, revoke_other_sessions, revoke_user_session, validate_password, verify_credentials, verify_password, verify_second_factor,
)
from .db import SessionLocal, check_database, get_db
from .models import ApiToken, Invite, NotificationChannel, AppSetting, AuditLog, Base, ChatMessage, ContainerMetric, ContainerRollup, Conversation, EnrollmentKey, FirewallState, HostMetric, HostRollup, IncidentAlert, JoinToken, ListenerState, MountMetric, MountRollup, PendingCollector, RecoveryCode, Server, SystemEvent, User

from .db import engine
from .config import (
    AGENT_MODEL,
    APP_VERSION,
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
    ENABLE_API_DOCS,
    ENROLL_RATE_LIMIT,
    INGEST_MIN_SPACING_RATIO,
    INGEST_MIN_SPACING_SECONDS,
    MAX_SERVERS,
    CORS_ORIGINS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_SECONDS,
)
from .rules import HELPER_EVENT_KINDS, DEFAULT_THRESHOLDS, HOST_RULE_PREFIX, container_rules, host_rules, validate_thresholds, detect_system_events, system_rules, SYSTEM_RULE_PREFIX, validate_mount_thresholds, validate_helper_events, REBOOT_DETAILS, REBOOT_KEY_PREFIX, detect_container_events, memory_trend_rules, CONTAINER_EVENT_KINDS, MAX_CONTAINER_EVENTS_PER_CHECKIN, MAX_CONTAINER_EVENTS_PER_HOUR, TREND_RULE_PREFIX, NETWORK_EVENT_KINDS, NETWORK_RULE_PREFIX, MAX_NETWORK_EVENTS_PER_CHECKIN, MAX_NETWORK_EVENTS_PER_HOUR, INTERNET_STATUSES, LISTENER_SCOPES, LISTENER_PROTOCOLS, MAX_LISTENERS, FIREWALL_LABELS, detect_listener_changes, detect_firewall_changes, network_rules, validate_listeners, validate_firewall
from .rate_limit import enforce_ip_limit, enforce_limit, enforce_login_limit, enforce_register_limit, refund_attempt, reset_limits
from ..agent import jobs as agent_jobs, usage as agent_usage
from ..agent.providers import configured_key
from ..agent.chat import check_provider_connection, delete_agent_conversation, send_agent_message


class RegisterRequest(BaseModel):
    name: str = Field(max_length=255)
    email: EmailStr
    password: str = Field(max_length=1024)
    # Only needed for the very first account; see ensure_bootstrap_token.
    bootstrap_token: str | None = Field(default=None, max_length=200)
    # From an admin's invitation link; lets one person register while registration is closed.
    invite_token: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1024)
    # "Keep me signed in for 30 days"; without it the session lasts SESSION_TTL_SECONDS.
    remember: bool = False


class TwoFactorLoginRequest(BaseModel):
    pending_token: str = Field(max_length=100)
    code: str = Field(max_length=64)


class CodeRequest(BaseModel):
    code: str = Field(max_length=64)


class SetupRequest(BaseModel):
    password: str | None = Field(default=None, max_length=1024)


class ReauthRequest(BaseModel):
    password: str = Field(max_length=1024)
    code: str = Field(max_length=64)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


class AgentChatRequest(BaseModel):
    conversation_id: str = Field(max_length=64)
    message: str = Field(max_length=8000)
    # The incident the question is about; the backend adds what it knows about it for the assistant.
    incident_id: int | None = Field(default=None, ge=1, le=2**31 - 1)


class ConversationRequest(BaseModel):
    conversation_id: str = Field(max_length=64)
    title: str = Field(default="New chat", max_length=255)


RESERVED_EVENT_TYPES = {"health_check_completed", "credentials_replaced", "login", "logout"}


class LogCreateRequest(BaseModel):
    """An entry the dashboard adds to the user's own audit log. The source is always the dashboard, so nobody can
    write entries that look like they came from sign-in or administration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    message: str = Field(default="New event", max_length=2000)
    task_title: str | None = Field(default=None, max_length=255)
    steps: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=50)
    conversation_id: str | None = Field(default=None, max_length=255)
    event_type: str | None = Field(default=None, max_length=50, pattern=r"^[a-z0-9_\-]*$")

    @field_validator("event_type")
    @classmethod
    def _not_a_system_event(cls, value):
        # These are counted on the dashboard (or mean something to the system), so a user can't write them.
        if value in RESERVED_EVENT_TYPES:
            raise ValueError("That event type is reserved.")
        return value

    @field_validator("level", mode="before")
    @classmethod
    def _upper_case_level(cls, value):
        return value.upper() if isinstance(value, str) else value


class ServerRequest(BaseModel):
    hostname: str = Field(max_length=255)
    ip_address: str | None = Field(default=None, max_length=64)
    check_interval_seconds: int = 60


class EnrollRequest(BaseModel):
    enroll_key: str = Field(max_length=200)
    hostname: str = Field(max_length=255)
    machine_id: str | None = Field(default=None, max_length=64)


class CollectorRegisterRequest(BaseModel):
    hostname: str = Field(max_length=255)
    machine_id: str | None = Field(default=None, max_length=64)
    os_info: str | None = Field(default=None, max_length=255)
    details: dict | None = None
    join_token: str | None = Field(default=None, max_length=200)


class ApproveRequest(BaseModel):
    # Required to approve a request whose machine matches a server that already exists.
    replace: bool = False
    # A name chosen on the dashboard; it then stays, whatever hostname the collector reports.
    hostname: str | None = Field(default=None, max_length=255)


class AdminSettingsRequest(BaseModel):
    # Both optional so the admin page can save one setting at a time.
    metrics_retention_days: int | None = None
    agent_chat_enabled: bool | None = None
    require_2fa_for_admins: bool | None = None
    alert_thresholds: dict | None = None
    # Replaces all per-mount disk overrides: {mount: {disk_warning_pct, disk_critical_pct}}.
    mount_thresholds: dict | None = None
    require_2fa_for_everyone: bool | None = None
    # Kinds of rule that should not open incidents (see incidents.rule_kind), replacing the whole list.
    disabled_rules: list[str] | None = Field(default=None, max_length=100)
    # What this install calls itself on the sign-in page and in the sidebar; an empty string goes back to the default.
    instance_name: str | None = Field(default=None, max_length=200)
    # The agent's spending: USD per million tokens per model (replacing the whole table), a monthly budget in USD
    # (0 removes it) and how many chat messages one person may send in 24 hours.
    agent_prices: dict | None = None
    agent_monthly_cap_usd: float | None = None
    agent_daily_chat_limit: int | None = None


class MetricsIngestRequest(BaseModel):
    server_id: int = Field(ge=1, le=2**31 - 1)
    server: dict = {}
    host: dict = {}
    containers: list[dict] = []


def agent_error_message(exc: Exception) -> str:
    """Turn provider failures into useful, non-sensitive chat text."""
    raw_message = str(exc).strip()
    normalized = raw_message.lower()
    if "openai_api_key is not configured" in normalized:
        return "The agent has no API key. An admin sets OPENAI_API_KEY in .env and restarts the backend."
    if any(marker in normalized for marker in ("401", "invalid_api_key", "incorrect api key", "authentication")):
        prefix = "OpenAI rejected the API key. Check OPENAI_API_KEY in .env."
    elif any(marker in normalized for marker in ("model_not_found", "does not exist", "404")):
        prefix = "OpenAI doesn't offer the configured model to this key. Check AGENT_MODEL in .env."
    elif any(marker in normalized for marker in ("resourceexhausted", "overloaded", "capacity", " 503", "status 503", " 500", " 502")):
        prefix = "OpenAI is currently overloaded or unavailable. Please try again shortly."
    elif any(marker in normalized for marker in ("rate limit", "quota", " 429", "status 429", "insufficient_quota")):
        prefix = "OpenAI rate limit or quota was reached. Please try again later."
    elif any(marker in normalized for marker in ("timeout", "timed out", "deadline exceeded")):
        prefix = "OpenAI did not respond before the request timed out. Please try again."
    else:
        prefix = "OpenAI could not complete this request."

    if not raw_message:
        return prefix
    # Errors can include request headers or provider diagnostics: keep a short,
    # redacted detail for the administrator without leaking credentials.
    detail = re.sub(r"(?i)(api[_ -]?key|authorization|token)\s*[:=]\s*\S+", r"\1=[redacted]", raw_message)
    detail = re.sub(r"(AIza|sk-)[\w-]+", "[redacted]", detail)[:300]
    return f"{prefix} Provider error: {detail}"


redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
# Jobs for the agent worker are published after the commit that opened or escalated an incident (see src/agent/jobs.py).
agent_jobs.redis_getter = lambda: redis_client
# The interactive API documentation is a map of every endpoint, so it is off unless ENABLE_API_DOCS=true.
app = FastAPI(title="Infradeck API", docs_url="/docs" if ENABLE_API_DOCS else None, redoc_url=None, openapi_url="/openapi.json" if ENABLE_API_DOCS else None)

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


API_TOKEN_PREFIX = "idk_"
# The API token a request carried, if any, with its method and path. Checked in _current_user.
_request_token: contextvars.ContextVar[tuple[str, str, str] | None] = contextvars.ContextVar("request_token", default=None)


@app.middleware("http")
async def note_api_token(request: Request, call_next):
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else ""
    marker = _request_token.set((token, request.method, request.url.path) if token.startswith(API_TOKEN_PREFIX) and len(token) <= 100 else None)
    try:
        return await call_next(request)
    finally:
        _request_token.reset(marker)


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


def registration_warning(db) -> str | None:
    """A warning for the log when anyone who can reach the dashboard may create an account."""
    if ALLOW_REGISTRATION and db.query(User).count() > 0:
        return ("SECURITY WARNING: ALLOW_REGISTRATION is true, so anyone who can reach the dashboard can create an account and see "
                "every server, container and incident. Set ALLOW_REGISTRATION=false unless that is what you want.")
    return None


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
                ("internet_status", "VARCHAR(16)"), ("internet_latency_ms", "INTEGER"), ("collect_duration_ms", "INTEGER"),
            ):
                connection.execute(text(f"ALTER TABLE host_metrics ADD COLUMN IF NOT EXISTS {column} {column_type}"))
            for column, column_type in (("exit_code", "INTEGER"), ("oom_killed", "BOOLEAN"), ("finished_at", "TIMESTAMP WITH TIME ZONE"), ("image_id", "VARCHAR(80)"), ("project", "VARCHAR(128)"), ("volumes", "VARCHAR(512)"), ("swap_mb", "DOUBLE PRECISION")):
                connection.execute(text(f"ALTER TABLE container_metrics ADD COLUMN IF NOT EXISTS {column} {column_type}"))
            connection.execute(text("ALTER TABLE system_events ADD COLUMN IF NOT EXISTS event_key VARCHAR(128)"))
            connection.execute(text("ALTER TABLE pending_collectors ADD COLUMN IF NOT EXISTS join_token_id INTEGER REFERENCES join_tokens(id) ON DELETE SET NULL"))
            connection.execute(text("ALTER TABLE pending_collectors ADD COLUMN IF NOT EXISTS details TEXT"))
            for column, column_type in (("avg_swap_mb", "DOUBLE PRECISION"), ("n_swap_mb", "INTEGER NOT NULL DEFAULT 0"), ("max_swap_mb", "DOUBLE PRECISION")):
                connection.execute(text(f"ALTER TABLE container_rollups ADD COLUMN IF NOT EXISTS {column} {column_type}"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_system_events_event_key ON system_events (event_key)"))
            # Composite indexes that match how the metrics tables are read, then the single-column ones they make
            # redundant (and the pointless indexes on each table's own primary key). Safe to repeat. Creating an index
            # briefly blocks writes to that table; on a very large table create them first by hand with CONCURRENTLY.
            for table, name, columns in (
                ("host_metrics", "ix_host_metrics_server_time", "server_id, time"),
                ("mount_metrics", "ix_mount_metrics_server_time", "server_id, time"),
                ("system_events", "ix_system_events_server_time", "server_id, time"),
                ("container_metrics", "ix_container_metrics_server_container_time", "server_id, container_id, time"),
            ):
                connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
            for redundant in (
                "ix_host_metrics_id", "ix_host_metrics_server_id", "ix_mount_metrics_id", "ix_mount_metrics_server_id",
                "ix_system_events_id", "ix_system_events_server_id",
                "ix_container_metrics_id", "ix_container_metrics_server_id", "ix_container_metrics_container_id",
            ):
                connection.execute(text(f"DROP INDEX IF EXISTS {redundant}"))
            for column, column_type in (("totp_secret_enc", "TEXT"), ("totp_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"), ("totp_last_step", "INTEGER"), ("password_changed_at", "TIMESTAMP WITH TIME ZONE")):
                connection.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {column} {column_type}"))
            connection.execute(text("ALTER TABLE incident_alerts ADD COLUMN IF NOT EXISTS rule_key VARCHAR(255)"))
            connection.execute(text("ALTER TABLE incident_alerts ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE"))
            for column, column_type in (("resolution", "VARCHAR(255)"), ("acknowledged_at", "TIMESTAMP WITH TIME ZONE"), ("acknowledged_by", "INTEGER"), ("muted_until", "TIMESTAMP WITH TIME ZONE"),
                                        ("notified_at", "TIMESTAMP WITH TIME ZONE"), ("resolve_notified_at", "TIMESTAMP WITH TIME ZONE")):
                connection.execute(text(f"ALTER TABLE incident_alerts ADD COLUMN IF NOT EXISTS {column} {column_type}"))
            for column, column_type in (("username", "VARCHAR(64)"), ("avatar_color", "VARCHAR(16)"), ("preferences", "TEXT"), ("last_seen_at", "TIMESTAMP WITH TIME ZONE")):
                connection.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {column} {column_type}"))
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
        if "system_info" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE servers ADD COLUMN system_info TEXT"))
        if "name_locked" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE servers ADD COLUMN name_locked BOOLEAN NOT NULL DEFAULT FALSE"))
    db = SessionLocal()
    try:
        ensure_admin_exists(db)
        if require_admin_two_factor(db) and not secrets_box.available():
            print("SECURITY WARNING: two-factor is required for administrators, but SECRETS_KEY is missing or invalid, so it is NOT being enforced. "
                  "Generate a key with `python -m src.backend.secrets_box` and set SECRETS_KEY in .env.", flush=True)
        if moved := auth.migrate_key_prefix(redis_client):
            print(f"Moved {moved} Redis keys from the old homelab: prefix to infradeck:.", flush=True)
        if removed := auth.purge_legacy_session_keys(redis_client):
            print(f"Removed {removed} session records written in the old format (sign-ins before this version ended).", flush=True)
        if warning := registration_warning(db):
            print(warning, flush=True)
        setup_token = ensure_bootstrap_token(db)
        if setup_token:
            print(f"First-run setup token: {setup_token}  (needed to create the first account; shown again at every start until it exists)", flush=True)
    finally:
        db.close()
    threading.Thread(target=_offline_watch_loop, name="offline-watch", daemon=True).start()
    threading.Thread(target=_notify_loop, name="notify", daemon=True).start()
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


INSTANCE_NAME_KEY = "instance_name"
MAX_INSTANCE_NAME = 60


def get_instance_name(db) -> str | None:
    """What this install calls itself: the name set in the admin settings, else "<first admin>'s homelab".
    None on a new install, which has nobody to name it after."""
    setting = db.get(AppSetting, INSTANCE_NAME_KEY)
    if setting and setting.value.strip():
        return setting.value.strip()
    owner = db.query(User).filter(User.role == "admin").order_by(User.id).first()
    first = _printable(owner.name, 40).split()[0].lower() if owner and _printable(owner.name, 40) else ""
    return f"{first}'s homelab" if first else None


@app.get("/auth/config")
def auth_config(db=Depends(get_db)):
    """What the sign-in pages show before anyone has signed in. Everything here is public by design: keep it to
    what a visitor may know (see SECURITY.md)."""
    return {
        "registration_enabled": registration_open(db), "bootstrap_required": db.query(User).count() == 0,
        "instance_name": get_instance_name(db), "server_count": db.query(Server).count(), "version": APP_VERSION,
    }


def _valid_invite(db, token: str | None) -> Invite | None:
    if not token or not 20 <= len(token) <= 200:
        return None
    return db.query(Invite).filter(Invite.token_hash == _hash_secret(token.strip()), Invite.used_at.is_(None), Invite.expires_at > datetime.now(timezone.utc)).first()


@app.get("/auth/invite")
def auth_invite(token: Annotated[str, Query(min_length=20, max_length=200)], request: Request, db=Depends(get_db)):
    """Whether an invitation link still works and who it is for, so the register page can say so."""
    enforce_ip_limit(redis_client, scope="invite-check", ip_address=_client_ip(request), limit=30, window_seconds=3600)
    invite = _valid_invite(db, token)
    if not invite:
        raise HTTPException(status_code=404, detail="This invitation is no longer valid.")
    return {"email": invite.email, "role": invite.role}


@app.post("/auth/register")
def auth_register(payload: RegisterRequest, request: Request, response: Response, db=Depends(get_db)):
    invite = _valid_invite(db, payload.invite_token)
    if payload.invite_token and not invite:
        raise HTTPException(status_code=403, detail="This invitation is no longer valid. Ask for a new one.")
    if invite and invite.email != str(payload.email).strip().lower():
        raise HTTPException(status_code=403, detail="This invitation was made for a different email address.")
    if not invite and not registration_open(db):
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
            admin_requires_two_factor=two_factor_enforced(db),
            everyone_requires_two_factor=two_factor_required_for(db, "user"),
            role=invite.role if invite and not first_account else None,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if invite:
        invite.used_at = datetime.now(timezone.utc)
        db.commit()
    if first_account:
        ensure_bootstrap_token(db)  # an account exists now: this removes the token
    _set_session_cookie(response, result.pop("session_id"))
    return result


def _set_session_cookie(response: Response, session_id: str, lifetime: int = SESSION_TTL_SECONDS) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=lifetime,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _user_payload(user: User, **extra) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "is_active": user.is_active, **extra}


def _complete_login(db, request: Request, response: Response, user: User, how: str = "", remember: bool = False) -> dict:
    """Open the session after every required step has passed.

    An administrator without two-factor, while it is required, gets an enrollment session: it can reach
    the two-factor setup and nothing else until setup is done.
    """
    stage = STAGE_ENROLLMENT if not user.totp_enabled and two_factor_required_for(db, user.role) else STAGE_FULL
    user.last_seen_at = datetime.now(timezone.utc)
    session_id, _public_id = create_session(redis_client, user, stage=stage, ip=_client_ip(request), user_agent=request.headers.get("user-agent"), remember=remember)
    # The cookie lasts exactly as long as the session behind it.
    _set_session_cookie(response, session_id, session_lifetime(stage, remember))
    record_audit_log(db, user_id=user.id, level="INFO", message=f"User signed in{how}{' (staying signed in for 30 days)' if remember and stage == STAGE_FULL else ''}", source="auth")
    return {"user": _user_payload(user), "setup_required": stage == STAGE_ENROLLMENT}


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
        user = verify_credentials(db, email=str(payload.email), password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # This email's counter is cleared. The address's counter only gets this one successful attempt back: clearing it
    # would let anyone with an account of their own reset the limit between guesses at someone else's password.
    reset_limits(redis_client, limit_keys[1])
    refund_attempt(redis_client, limit_keys[0])
    if user.totp_enabled:
        # No session exists until the second step passes; this token only carries the login between the steps.
        return {"status": "two_factor_required", "pending_token": create_pending_two_factor(redis_client, user.id, payload.remember)}
    return _complete_login(db, request, response, user, remember=payload.remember)


def _second_factor_or_error(db, user: User, code: str) -> str:
    """Verify a code for the user, with the shared brute-force limit. Raises 401 (or 429/503); returns "totp" or "recovery"."""
    limit_key = enforce_limit(redis_client, scope="2fa-user", subject=user.id, limit=TWO_FACTOR_ATTEMPTS, window_seconds=TWO_FACTOR_WINDOW_SECONDS)
    try:
        method = verify_second_factor(db, user, code)
    except secrets_box.SecretsUnavailable as exc:
        raise HTTPException(status_code=503, detail="Two-factor codes can't be checked on this server right now (a SECRETS_KEY problem). A recovery code still works.") from exc
    if method is None:
        record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong two-factor code was entered", source="auth")
        raise HTTPException(status_code=401, detail="Invalid code.")
    reset_limits(redis_client, limit_key)
    return method


@app.post("/auth/login/2fa")
def auth_login_two_factor(payload: TwoFactorLoginRequest, request: Request, response: Response, db=Depends(get_db)):
    enforce_ip_limit(redis_client, scope="2fa-ip", ip_address=_client_ip(request), limit=20, window_seconds=TWO_FACTOR_WINDOW_SECONDS)
    expired = HTTPException(status_code=401, detail="Sign-in expired. Please sign in again.")
    user_id = read_pending_two_factor(redis_client, payload.pending_token)
    if user_id is None:
        raise expired
    if not count_pending_attempt(redis_client, payload.pending_token):
        raise HTTPException(status_code=401, detail="Too many wrong codes. Please sign in again.")
    user = db.get(User, user_id)
    if not user or not user.is_active or not user.totp_enabled:
        raise expired
    method = _second_factor_or_error(db, user, payload.code)
    db.commit()  # the code that was just accepted is now spent
    remember = pending_two_factor_remember(redis_client, payload.pending_token)
    delete_pending_two_factor(redis_client, payload.pending_token)
    if method == "recovery":
        record_audit_log(db, user_id=user.id, level="WARNING", message="A two-factor recovery code was used to sign in", source="auth")
    result = _complete_login(db, request, response, user, " with two-factor", remember=remember)
    result["recovery_codes_left"] = recovery_codes_left(db, user)
    return result


def _account_session(session_id, db, *, allow_limited: bool = False) -> tuple[dict, User]:
    """The caller's session and account for the account endpoints. Enrollment sessions only where allowed."""
    session = get_session(redis_client, session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    if session["stage"] != STAGE_FULL and not allow_limited:
        raise HTTPException(status_code=403, detail="Finish setting up two-factor first.")
    user = db.get(User, session["id"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    return session, user


@app.get("/auth/me")
def auth_me(
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    db=Depends(get_db),
):
    session, account = _account_session(session_id, db, allow_limited=True)
    # Role and status come from the database so admin changes apply without a new login.
    return {"user": _user_payload(account, setup_required=session["stage"] != STAGE_FULL, two_factor_enabled=account.totp_enabled)}


@app.post("/auth/logout")
def auth_logout(
    response: Response,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
):
    if not session_id:
        raise HTTPException(status_code=400, detail="No session id supplied.")
    delete_session(redis_client, session_id)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


@app.get("/auth/2fa/status")
def two_factor_status(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db, allow_limited=True)
    return {
        "enabled": user.totp_enabled, "available": secrets_box.available(),
        "required": two_factor_required_for(db, user.role),
        "recovery_codes_left": recovery_codes_left(db, user) if user.totp_enabled else 0,
        "setup_required": session["stage"] != STAGE_FULL,
    }


@app.post("/auth/2fa/setup")
def two_factor_setup(payload: SetupRequest | None = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db, allow_limited=True)
    if not secrets_box.available():
        raise HTTPException(status_code=503, detail="Two-factor needs SECRETS_KEY to be configured on the server. See docs/accounts.md (Two-factor sign-in).")
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="Two-factor is already turned on. Turn it off first to set it up again.")
    if session.get("stage") != STAGE_ENROLLMENT:
        # Turning two-factor on from a normal session needs the password again. Otherwise a stolen session could
        # enrol its own authenticator, which signs out the real owner and locks them out.
        _confirm_password(db, user, payload.password if payload else None)
    enforce_limit(redis_client, scope="2fa-setup", subject=user.id, limit=10, window_seconds=3600)
    secret = totp.new_secret()
    # Stored encrypted and inactive until a code from the app has been confirmed.
    user.totp_secret_enc, user.totp_last_step = secrets_box.encrypt(secret), None
    db.commit()
    return {"secret": secret, "otpauth_uri": totp.otpauth_uri(secret, user.email)}


@app.post("/auth/2fa/enable")
def two_factor_enable(payload: CodeRequest, request: Request, response: Response, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db, allow_limited=True)
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="Two-factor is already turned on.")
    if not user.totp_secret_enc:
        raise HTTPException(status_code=400, detail="Start the setup first.")
    limit_key = enforce_limit(redis_client, scope="2fa-user", subject=user.id, limit=TWO_FACTOR_ATTEMPTS, window_seconds=TWO_FACTOR_WINDOW_SECONDS)
    try:
        step = totp.verify(secrets_box.decrypt(user.totp_secret_enc), payload.code)
    except secrets_box.SecretsUnavailable as exc:
        raise HTTPException(status_code=503, detail="Two-factor can't be set up on this server right now (a SECRETS_KEY problem).") from exc
    if step is None:
        raise HTTPException(status_code=400, detail="That code is not valid. Check the clock on your phone and try again.")
    reset_limits(redis_client, limit_key)
    user.totp_enabled, user.totp_last_step = True, step
    codes = issue_recovery_codes(db, user)
    db.commit()
    record_audit_log(db, user_id=user.id, level="INFO", message="Two-factor was turned on", source="auth")
    # A fresh session ID: this is the moment an enrollment session becomes a full one, and other sessions end.
    new_session_id, new_public_id = create_session(redis_client, user, stage=STAGE_FULL, ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    delete_session(redis_client, session_id)
    revoke_other_sessions(redis_client, user.id, new_public_id)
    _set_session_cookie(response, new_session_id)
    return {"recovery_codes": codes, "setup_required": False}


def _confirm_password(db, user: User, password: str | None) -> None:
    """Ask for the current password again before a sensitive change. Every try counts toward the two-factor lockout,
    the right password included, so a locked-out session can't keep guessing until it is right."""
    limit_key = enforce_limit(redis_client, scope="2fa-user", subject=user.id, limit=TWO_FACTOR_ATTEMPTS, window_seconds=TWO_FACTOR_WINDOW_SECONDS)
    if not password or not verify_password(user, password):
        record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong or missing password was given while setting up two-factor", source="auth")
        raise HTTPException(status_code=401, detail="Enter your current password to set up two-factor.")
    reset_limits(redis_client, limit_key)


def _reauthenticate(db, user: User, password: str, code: str) -> None:
    """Password plus a second-factor code, for changes to the two-factor setup. One generic error for either being wrong."""
    wrong = HTTPException(status_code=401, detail="The password or the code is not correct.")
    if not verify_password(user, password):
        enforce_limit(redis_client, scope="2fa-user", subject=user.id, limit=TWO_FACTOR_ATTEMPTS, window_seconds=TWO_FACTOR_WINDOW_SECONDS)
        record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong password was entered while changing two-factor", source="auth")
        raise wrong
    try:
        _second_factor_or_error(db, user, code)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise wrong from exc
        raise


@app.post("/auth/2fa/disable")
def two_factor_disable(payload: ReauthRequest, request: Request, response: Response, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="Two-factor is not turned on.")
    if two_factor_required_for(db, user.role):
        raise HTTPException(status_code=403, detail="Two-factor is required for administrators on this server.")
    _reauthenticate(db, user, payload.password, payload.code)
    user.totp_enabled, user.totp_secret_enc, user.totp_last_step = False, None, None
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    # A fresh session ID for this one too, and every other session ends.
    new_session_id, new_public_id = create_session(redis_client, user, stage=STAGE_FULL, ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    delete_session(redis_client, session_id)
    revoked = revoke_other_sessions(redis_client, user.id, new_public_id)
    _set_session_cookie(response, new_session_id)
    record_audit_log(db, user_id=user.id, level="WARNING", message=f"Two-factor was turned off ({revoked} other sessions signed out)", source="auth")
    return {"status": "ok"}


@app.post("/auth/2fa/recovery-codes")
def two_factor_new_recovery_codes(payload: ReauthRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _session, user = _account_session(session_id, db)
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="Two-factor is not turned on.")
    _reauthenticate(db, user, payload.password, payload.code)
    codes = issue_recovery_codes(db, user)
    db.commit()
    record_audit_log(db, user_id=user.id, level="INFO", message="Two-factor recovery codes were regenerated", source="auth")
    return {"recovery_codes": codes}


@app.post("/auth/password")
def change_password(payload: PasswordChangeRequest, request: Request, response: Response, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    enforce_limit(redis_client, scope="pw-change", subject=user.id, limit=5, window_seconds=600)
    if not verify_password(user, payload.current_password):
        record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong current password was entered while changing the password", source="auth")
        raise HTTPException(status_code=400, detail="The current password is not correct.")
    try:
        validate_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="The new password must be different from the current one.")
    user.password_hash, user.password_changed_at = hash_password(payload.new_password), datetime.now(timezone.utc)
    db.commit()
    # A fresh session ID for this browser, and every other session ends: whoever had the old password is out.
    new_session_id, new_public_id = create_session(redis_client, user, stage=STAGE_FULL, ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    delete_session(redis_client, session_id)
    revoked = revoke_other_sessions(redis_client, user.id, new_public_id)
    _set_session_cookie(response, new_session_id)
    record_audit_log(db, user_id=user.id, level="INFO", message=f"Password was changed ({revoked} other sessions signed out)", source="auth")
    return {"status": "ok", "other_sessions_signed_out": revoked}


@app.get("/auth/sessions")
def auth_sessions(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    return {"sessions": list_user_sessions(redis_client, user.id, session["public_id"])}


@app.post("/auth/sessions/revoke-others")
def auth_revoke_other_sessions(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    revoked = revoke_other_sessions(redis_client, user.id, session["public_id"])
    record_audit_log(db, user_id=user.id, level="INFO", message=f"Signed out {revoked} other sessions", source="auth")
    return {"signed_out": revoked}


@app.delete("/auth/sessions/{public_id}")
def auth_revoke_session(public_id: str, response: Response, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    if not revoke_user_session(redis_client, user.id, public_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    if public_id == session["public_id"]:
        response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


_TOKEN_WRITE_PATH = re.compile(r"/incidents/\d{1,10}/(acknowledge|resolve|mute|unmute)")
_TOKEN_FORBIDDEN = ("/admin", "/auth", "/account", "/agent", "/conversations", "/logs")


def _token_user(db) -> dict | None:
    """The account behind the API token this request carried, if the token is valid for this method and path.

    A read token may only GET; a write token may also acknowledge, resolve and mute incidents. No token reaches
    the account, admin or chat endpoints, whatever its scope: a leaked token must not be able to mint another
    one, change a password or spend the provider's quota."""
    noted = _request_token.get()
    if not noted:
        return None
    token, method, path = noted
    if path.startswith("/api"):
        path = path[4:]
    row = db.query(ApiToken).filter(ApiToken.token_hash == _hash_secret(token)).first()
    now = datetime.now(timezone.utc)
    if not row or (row.expires_at and (row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)) <= now):
        raise HTTPException(status_code=401, detail="The API token is invalid or has expired.")
    owner = db.get(User, row.user_id)
    if not owner or not owner.is_active:
        raise HTTPException(status_code=401, detail="The API token is invalid or has expired.")
    allowed = method == "GET" or (row.scope == "write" and method == "POST" and _TOKEN_WRITE_PATH.fullmatch(path))
    if not allowed or path.startswith(_TOKEN_FORBIDDEN):
        raise HTTPException(status_code=403, detail="This API token is not allowed to do that.")
    # Noting each use would be a write on every poll; once a minute is enough to show that it is alive.
    last = row.last_used_at if row.last_used_at is None or row.last_used_at.tzinfo else row.last_used_at.replace(tzinfo=timezone.utc)
    if last is None or now - last > timedelta(seconds=60):
        row.last_used_at = now
        db.commit()
    return {"id": owner.id, "name": owner.name, "email": owner.email, "role": owner.role, "stage": STAGE_FULL, "token": row.scope}


def _current_user(session_id, db):
    session = get_session(redis_client, session_id)
    if session and session["stage"] != STAGE_FULL:
        raise HTTPException(status_code=403, detail="Finish setting up two-factor first.")
    user = get_user_from_session(redis_client, session_id)
    if not user:
        user = _token_user(db)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    return user


def _is_admin(db, user_id: int) -> bool:
    account = db.query(User).filter(User.id == user_id).first()
    return bool(account and account.is_active and account.role == "admin")


def _require_admin(session_id, db):
    """Like _current_user, but checks the role in the database rather than the session."""
    user = _current_user(session_id, db)
    if user.get("token") or not _is_admin(db, user["id"]):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def _require_operator(session_id, db):
    """Who may acknowledge, resolve and mute incidents: an admin in the browser, or an admin's write token."""
    user = _current_user(session_id, db)
    if not _is_admin(db, user["id"]):
        raise HTTPException(status_code=403, detail="Only owners and admins can change an incident.")
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
REQUIRE_ADMIN_2FA_KEY = "require_admin_2fa"
TWO_FACTOR_ATTEMPTS = 5
TWO_FACTOR_WINDOW_SECONDS = 300
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


def require_admin_two_factor(db) -> bool:
    """The admin setting: on unless an admin has turned it off."""
    setting = db.get(AppSetting, REQUIRE_ADMIN_2FA_KEY)
    return setting is None or setting.value == "true"


def two_factor_enforced(db) -> bool:
    """Whether admins are actually required to use two-factor: the setting is on and enrolment is possible (a key is configured)."""
    return require_admin_two_factor(db) and secrets_box.available()


REQUIRE_2FA_EVERYONE_KEY = "require_2fa_for_everyone"


def require_two_factor_for_everyone(db) -> bool:
    setting = db.get(AppSetting, REQUIRE_2FA_EVERYONE_KEY)
    return setting is not None and setting.value == "true"


def two_factor_required_for(db, role: str) -> bool:
    """Whether an account with this role must have two-factor: admins by default, everyone when the admin says so.
    Never when enrolment is impossible (no SECRETS_KEY), or nobody could sign in at all."""
    if not secrets_box.available():
        return False
    return require_two_factor_for_everyone(db) or (role == "admin" and require_admin_two_factor(db))


DISABLED_RULES_KEY = "disabled_rules"


def get_disabled_rules(db) -> set[str]:
    setting = db.get(AppSetting, DISABLED_RULES_KEY)
    try:
        return {kind for kind in json.loads(setting.value) if isinstance(kind, str)} if setting else set()
    except ValueError:
        return set()


def _enabled_rules(db, firing: dict) -> dict:
    """``firing`` without the rules an admin switched off; what is left out resolves like a rule that cleared."""
    disabled = get_disabled_rules(db)
    return {key: value for key, value in firing.items() if incidents.rule_kind(key) not in disabled} if disabled else firing


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
    return {"instance_name": get_instance_name(db), "metrics_retention_days": get_metrics_retention_days(db), "agent_chat_enabled": is_agent_chat_enabled(db), "alert_thresholds": get_alert_thresholds(db), "mount_thresholds": get_mount_thresholds(db), "require_2fa_for_admins": require_admin_two_factor(db), "secrets_configured": secrets_box.available(),
            "require_2fa_for_everyone": require_two_factor_for_everyone(db), "disabled_rules": sorted(get_disabled_rules(db)),
            "agent_prices": agent_usage.get_prices(db), "agent_monthly_cap_usd": agent_usage.get_cap(db), "agent_daily_chat_limit": agent_usage.get_daily_limit(db)}


def purge_old_metrics(db, now: datetime | None = None) -> int:
    """Delete host and container samples older than the retention period."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=get_metrics_retention_days(db))
    # Check-in samples that haven't been summarised yet are never deleted, whatever the retention says.
    summarised_until = rollups.hour_watermark(db)
    if summarised_until is not None:
        cutoff = min(cutoff, summarised_until)
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
    if payload.require_2fa_for_admins is not None and payload.require_2fa_for_admins != require_admin_two_factor(db):
        _set_setting(db, REQUIRE_ADMIN_2FA_KEY, "true" if payload.require_2fa_for_admins else "false")
        changes.append(f"{'Required' if payload.require_2fa_for_admins else 'Stopped requiring'} two-factor for administrators")
    if payload.instance_name is not None:
        name = _printable(payload.instance_name, MAX_INSTANCE_NAME)
        if payload.instance_name.strip() and (not name or len(payload.instance_name.strip()) > MAX_INSTANCE_NAME):
            raise HTTPException(status_code=400, detail=f"The name must be printable text of at most {MAX_INSTANCE_NAME} characters.")
        if name != (get_instance_name(db) or ""):
            _set_setting(db, INSTANCE_NAME_KEY, name)
            changes.append(f"Renamed this install to {name}" if name else "Reset this install's name to the default")
    if payload.require_2fa_for_everyone is not None and payload.require_2fa_for_everyone != require_two_factor_for_everyone(db):
        _set_setting(db, REQUIRE_2FA_EVERYONE_KEY, "true" if payload.require_2fa_for_everyone else "false")
        changes.append(f"{'Required' if payload.require_2fa_for_everyone else 'Stopped requiring'} two-factor for everyone")
    if payload.disabled_rules is not None:
        known = {row["kind"] for row in RULE_ROWS}
        wanted = sorted({kind for kind in payload.disabled_rules if kind in known})
        if set(wanted) != get_disabled_rules(db):
            _set_setting(db, DISABLED_RULES_KEY, json.dumps(wanted))
            changes.append("Switched off alert rules: " + (", ".join(wanted) or "none"))
    try:
        if payload.agent_prices is not None:
            prices = agent_usage.validate_prices(payload.agent_prices)
            if prices != agent_usage.get_prices(db):
                _set_setting(db, agent_usage.PRICES_KEY, json.dumps(prices))
                changes.append("Changed the agent's model prices: " + (", ".join(f"{model} {price['input']:g}/{price['output']:g}" for model, price in prices.items()) or "none"))
        if payload.agent_monthly_cap_usd is not None:
            cap = agent_usage.validate_cap(payload.agent_monthly_cap_usd)
            if cap != agent_usage.get_cap(db):
                _set_setting(db, agent_usage.CAP_KEY, str(cap or 0))
                changes.append(f"Set the agent's monthly budget to ${cap:g}" if cap else "Removed the agent's monthly budget")
        if payload.agent_daily_chat_limit is not None:
            limit = agent_usage.validate_daily_limit(payload.agent_daily_chat_limit)
            if limit != agent_usage.get_daily_limit(db):
                _set_setting(db, agent_usage.DAILY_LIMIT_KEY, str(limit))
                changes.append(f"Set the agent chat limit to {limit} messages per person per day")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    spend = agent_usage.budget(db)
    return {"chat_enabled": is_agent_chat_enabled(db), "key_configured": bool(configured_key()), "budget_paused": spend["state"] == "paused"}


OVERVIEW_ALERT_LIMIT = 50
OVERVIEW_ALERTS_PER_SERVER = 10
OVERVIEW_ALERT_SCAN = 1000
CREDENTIALS_ALERT_KEY = "system:credentials"


def _spread_alerts(alerts: list) -> list:
    """The most severe alerts first, but at most a few per server, so one server with a flood of alerts can't push
    everybody else's off the list. The alert count shown elsewhere is still the true total.

    The warning that a server's credentials were replaced is always listed, on top of that allowance: whoever
    holds the new credentials decides what the server reports, so it must not be crowdable by what it reports."""
    pinned = [alert for alert in alerts if alert.rule_key == CREDENTIALS_ALERT_KEY][:OVERVIEW_ALERT_LIMIT]
    per_server: dict[int, int] = {}
    chosen = []
    for alert in alerts:
        if alert.rule_key == CREDENTIALS_ALERT_KEY:
            continue
        per_server[alert.server_id] = per_server.get(alert.server_id, 0) + 1
        if per_server[alert.server_id] <= OVERVIEW_ALERTS_PER_SERVER:
            chosen.append(alert)
        if len(chosen) >= OVERVIEW_ALERT_LIMIT - len(pinned):
            break
    return pinned + chosen


SYSTEM_EVENTS_SHOWN = 10
# The note that a server's credentials were replaced is shown whatever else that server reports, so it can't be pushed out.
CREDENTIALS_EVENTS_SHOWN = 3


def recent_system_events(db, since: datetime) -> dict[int, list[dict]]:
    """Each server's recent events, newest first, for the dashboard's System row.

    Kernel errors repeat on every boot, so identical messages are collapsed into one row with a count
    (and the first and last time seen) and capped separately; otherwise they would push reboots and
    OOM kills out of the list. Every other kind stays one row per occurrence.
    """
    result: dict[int, list[dict]] = {}
    ordinary = (SystemEvent.time >= since, SystemEvent.kind != "kernel_error", SystemEvent.kind != CREDENTIALS_REPLACED_KIND, SystemEvent.kind.notin_(CONTAINER_EVENT_KINDS + NETWORK_EVENT_KINDS))
    # The database picks the newest few per server, so a server with thousands of events costs no more to show.
    for events, limit in (((ordinary), SYSTEM_EVENTS_SHOWN), ((SystemEvent.time >= since, SystemEvent.kind == CREDENTIALS_REPLACED_KIND), CREDENTIALS_EVENTS_SHOWN)):
        ranked = db.query(SystemEvent.id.label("event_id"), func.row_number().over(partition_by=SystemEvent.server_id, order_by=SystemEvent.time.desc()).label("rank")).filter(*events).subquery()
        for event in db.query(SystemEvent).join(ranked, ranked.c.event_id == SystemEvent.id).filter(ranked.c.rank <= limit):
            result.setdefault(event.server_id, []).append({"time": event.time.isoformat(), "first_time": event.time.isoformat(), "kind": event.kind, "detail": event.detail, "count": 1})
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
    unmuted = (IncidentAlert.status == "active", (IncidentAlert.muted_until.is_(None)) | (IncidentAlert.muted_until <= datetime.now(timezone.utc)))
    alert_counts = dict(db.query(IncidentAlert.server_id, func.count()).filter(*unmuted).group_by(IncidentAlert.server_id).all())
    severity_counts: dict[int, dict[str, int]] = {}
    for server_id, severity, count in db.query(IncidentAlert.server_id, IncidentAlert.severity, func.count()).filter(*unmuted).group_by(IncidentAlert.server_id, IncidentAlert.severity):
        severity_counts.setdefault(server_id, {})[severity] = count
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
            "active_alert_count": alert_counts.get(server.id, 0), "alerts_by_severity": severity_counts.get(server.id, {}),
            "system": _load_json(server.system_info), "created_at": server.created_at.isoformat(),
            "last_seen_at": server.last_seen_at.isoformat() if server.last_seen_at else None,
            "host": ({key: getattr(metric, key) for key in ("time", "cpu_utilization_pct", "load_1m", "load_5m", "load_15m", "ram_used_gb", "ram_available_gb", "ram_pct", "swap_pct", "disk_usage_pct", "disk_inodes_pct", "net_bytes_sent_mb", "net_bytes_recv_mb", "system_uptime_seconds", "oom_kill_total", "kernel_tainted", "ntp_synced", "reboot_required", "last_shutdown", "docker_status", "docker_reason", "docker_latency_ms", "internet_status", "internet_latency_ms")} if metric else None),
            "network": {"listeners": listeners.get(server.id, []), "firewall": firewalls.get(server.id, []), "events": network_events.get(server.id, [])},
            "container_events": container_events.get(server.id, []),
            "system_events": events.get(server.id, []),
            "mounts": [{key: getattr(m, key) for key in ("mount", "fs_type", "total_gb", "used_gb", "used_pct", "inodes_pct")} for m in mounts],
            "containers": [{"container_id": c.container_id, "name": c.name, "image": c.image, "status": c.status, "restart_count": c.restart_count, "cpu_pct": c.cpu_pct, "memory_used_mb": c.memory_used_mb, "memory_pct": c.memory_pct, "memory_limit_mb": c.memory_limit_mb, "swap_mb": c.swap_mb, "ports": c.ports, "project": c.project, "started_at": c.started_at, "healthcheck_status": c.healthcheck_status, "exit_code": c.exit_code, "oom_killed": c.oom_killed, "finished_at": c.finished_at.isoformat() if c.finished_at else None, "time": c.time.isoformat()} for c in latest.values()],
        })
    active_alerts = db.query(IncidentAlert).filter(*unmuted)
    # Most severe first, then newest.
    severity_rank = case({severity: rank for rank, severity in enumerate(reversed(SEVERITIES))}, value=IncidentAlert.severity, else_=len(SEVERITIES))
    alerts = _spread_alerts(active_alerts.order_by(severity_rank, IncidentAlert.created_at.desc()).limit(OVERVIEW_ALERT_SCAN).all())
    hostnames = {server.id: server.hostname for server in servers}
    servers_by_id = {server.id: server for server in servers}
    thresholds = get_alert_thresholds(db)
    pending = visible_pending_collectors(db) if _is_admin(db, user["id"]) and not user.get("token") else []
    totals: dict[str, int] = {}
    for counts in severity_counts.values():
        for severity, count in counts.items():
            totals[severity] = totals.get(severity, 0) + count
    return {
        "servers": result, "pending_collectors": pending, "alert_count": active_alerts.count(), "alerts_by_severity": totals,
        "version": APP_VERSION, "instance_name": get_instance_name(db), "server_time": datetime.now(timezone.utc).isoformat(),
        # Only worked out when nothing needs attention, which is the only time the page shows it.
        "quiet": dashboard.quiet_summary(db) if not (totals.get(CRITICAL) or totals.get("WARNING")) else None,
        # Lets the dashboard color disk bars the same way the alert rules judge them.
        "mount_thresholds": get_mount_thresholds(db),
        # The same limits the alert rules use, so the page's amber and orange mean what an alert would.
        "alert_thresholds": {key: thresholds[key] for key in ("memory_warning_pct", "memory_critical_pct", "swap_warning_pct")},
        "disk_thresholds": {key: thresholds[key] for key in ("disk_warning_pct", "disk_critical_pct", "inodes_warning_pct", "inodes_critical_pct")},
        "alerts": [{"id": a.id, "server_id": a.server_id, "hostname": hostnames.get(a.server_id), "severity": a.severity, "title": a.title, "description": a.description, "rule_key": a.rule_key, "created_at": a.created_at.isoformat(), "reference": incidents.reference(a.id), "acknowledged": a.acknowledged_at is not None,
                    "agent_note": _agent_note(db, a, servers_by_id.get(a.server_id))} for a in alerts],
    }


def latest_containers(db, server_id: int, host_time: datetime | None) -> list[ContainerMetric]:
    """The containers reported in the server's latest check-in, not every container seen within retention.

    A check-in saves its host and container rows with one shared timestamp.
    """
    if host_time is None:
        return []
    return db.query(ContainerMetric).filter(ContainerMetric.server_id == server_id, ContainerMetric.time == host_time).all()


HISTORY_MAX_MINUTES = 24 * 60


# An ID in the address must fit the database's INTEGER column, or the query itself fails with a server error.
PathId = Annotated[int, PathParam(ge=1, le=2**31 - 1)]
RangeName = Literal["1h", "6h", "24h", "7d", "30d", "90d", "1y", "all"]


def _wanted_metrics(raw: str | None, allowed: dict, default: tuple[str, ...]) -> tuple[str, ...]:
    """The metrics a history request asked for, from a comma-separated list; only known names are accepted."""
    if raw is None:
        return default
    names = tuple(dict.fromkeys(name.strip() for name in raw.split(",")))
    if not names or len(raw) > 200 or any(name not in allowed for name in names):
        raise HTTPException(status_code=422, detail=f"metrics must be a comma-separated list of: {', '.join(allowed)}.")
    return names


def _history_response(db, spec, server: Server, key: str | None, range_name: str, wanted: tuple[str, ...], previous: bool = False) -> dict:
    """``previous`` answers for the period just before the range (the 7 days before the last 7), for comparing."""
    end = history_service.previous_end(range_name) if previous else None
    if previous and end is None:
        raise HTTPException(status_code=422, detail="There is nothing before \"all\" to compare with.")
    result = history_service.history(db, spec, server.id, key, range_name, wanted, server.check_interval_seconds, now=end)
    return {"server_id": server.id, "previous": previous, **result}


@app.get("/monitoring/servers/{server_id}/history")
def server_history(
    server_id: PathId, minutes: int = 30, range_name: Annotated[RangeName | None, Query(alias="range")] = None, metrics: str | None = None, previous: bool = False,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db),
):
    """A server's metrics over time.

    With ``range`` (1h to all): at most about 500 points from the right level of detail, each with the average and
    maximum of the requested ``metrics``. Without it: the original answer for the last ``minutes`` (CPU, memory and
    root disk, oldest first), which the dashboard's live chart uses.
    """
    _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    if range_name is not None:
        return _history_response(db, history_service.HOST, server, None, range_name, _wanted_metrics(metrics, history_service.HOST_METRICS, history_service.DEFAULT_HOST_METRICS), previous)
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, min(minutes, HISTORY_MAX_MINUTES)))
    rows = db.query(HostMetric.time, HostMetric.cpu_utilization_pct, HostMetric.ram_pct, HostMetric.disk_usage_pct).filter(HostMetric.server_id == server_id, HostMetric.time >= since).order_by(HostMetric.time).all()
    return {"server_id": server_id, "samples": [{"time": row.time.isoformat(), "cpu": row.cpu_utilization_pct, "ram": row.ram_pct, "disk": row.disk_usage_pct} for row in rows]}


@app.get("/monitoring/servers/{server_id}/live")
def server_live(server_id: PathId, minutes: int = 30, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Every check-in of the last ``minutes`` for the server panel's live charts, oldest first. Network figures are
    running totals; the page turns them into a rate."""
    _current_user(session_id, db)
    if not db.query(Server.id).filter(Server.id == server_id).first():
        raise HTTPException(status_code=404, detail="Server not found.")
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, min(minutes, HISTORY_MAX_MINUTES)))
    rows = db.query(HostMetric.time, HostMetric.cpu_utilization_pct, HostMetric.ram_pct, HostMetric.load_1m, HostMetric.swap_pct, HostMetric.net_bytes_sent_mb, HostMetric.net_bytes_recv_mb).filter(HostMetric.server_id == server_id, HostMetric.time >= since).order_by(HostMetric.time).all()
    return {"server_id": server_id, "samples": [{"time": row.time.isoformat(), "cpu": row.cpu_utilization_pct, "ram": row.ram_pct, "load": row.load_1m, "swap": row.swap_pct, "net_sent_mb": row.net_bytes_sent_mb, "net_recv_mb": row.net_bytes_recv_mb} for row in rows]}


SPARKLINE_MAX_MINUTES = 24 * 60
EVENTS_MAX_DAYS = 366


@app.get("/monitoring/sparklines")
def monitoring_sparklines(minutes: int = 30, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Recent CPU use of every server, for the small charts in the fleet rack."""
    _current_user(session_id, db)
    return {"minutes": minutes, "servers": dashboard.fleet_sparklines(db, max(1, min(minutes, SPARKLINE_MAX_MINUTES)))}


@app.get("/monitoring/servers/{server_id}/containers/sparklines")
def monitoring_container_sparklines(server_id: PathId, minutes: int = 15, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Recent CPU use of every container on one server, for the small charts in the container table."""
    _current_user(session_id, db)
    if not db.query(Server.id).filter(Server.id == server_id).first():
        raise HTTPException(status_code=404, detail="Server not found.")
    return {"minutes": minutes, "containers": dashboard.container_sparklines(db, server_id, max(1, min(minutes, SPARKLINE_MAX_MINUTES)))}


@app.get("/monitoring/servers/{server_id}/events")
def monitoring_server_events(server_id: PathId, days: int = 7, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """One server's timeline: recorded events and alerts opening and clearing, newest first."""
    _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    days = max(1, min(days, EVENTS_MAX_DAYS))
    return {"server_id": server.id, "days": days, "retention_days": get_metrics_retention_days(db), "events": dashboard.server_events(db, server, days)}


STATS_CACHE_SECONDS = 60


@app.get("/stats/fleet")
def fleet_statistics(range_name: Annotated[RangeName, Query(alias="range")] = "24h", session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Incidents over time and per-server availability for the Statistics page's all-servers view."""
    _current_user(session_id, db)
    # Keyed only by the validated range, so a request can't create an unbounded number of cache entries.
    cache_key = f"infradeck:stats:fleet:{range_name}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:  # a cache problem must never break the page
        cached = None
    result = stats.fleet_stats(db, range_name)
    try:
        redis_client.set(cache_key, json.dumps(result), ex=STATS_CACHE_SECONDS)
    except Exception:
        pass
    return result


def _cached_server_stats(db, server_id: int, name: str, range_name: str, build) -> dict:
    """A per-server statistic, remembered for a minute. Only existing servers and known ranges get this far, so the
    cache can't grow without limit; a cache problem never breaks the page."""
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    cache_key = f"infradeck:stats:{name}:{server.id}:{range_name}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    result = build(db, server, range_name)
    try:
        redis_client.set(cache_key, json.dumps(result), ex=STATS_CACHE_SECONDS)
    except Exception:
        pass
    return result


@app.get("/stats/servers/{server_id}/containers")
def server_container_usage(server_id: PathId, range_name: Annotated[RangeName, Query(alias="range")] = "24h", session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Every container of a server over a range, busiest first, for the Statistics page's ranked table."""
    _current_user(session_id, db)
    return _cached_server_stats(db, server_id, "containers", range_name, stats.container_usage)


@app.get("/stats/servers/{server_id}/busiest")
def server_busiest_containers(server_id: PathId, range_name: Annotated[RangeName, Query(alias="range")] = "24h", session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """The container using the most CPU in each period of a range, for the timeline's tooltip."""
    _current_user(session_id, db)
    return _cached_server_stats(db, server_id, "busiest", range_name, stats.busiest_containers)


@app.get("/stats/servers/{server_id}/spikes")
def server_cpu_spikes(server_id: PathId, range_name: Annotated[RangeName, Query(alias="range")] = "24h", session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """When the server's CPU was at or above 90% and for how long, for the Statistics page's findings."""
    _current_user(session_id, db)
    return _cached_server_stats(db, server_id, "spikes", range_name, stats.cpu_spikes)


@app.get("/stats/servers/{server_id}/heatmap")
def server_heatmap(
    server_id: PathId, metric: Literal["cpu", "ram"] = "cpu", offset: Annotated[int, Query(ge=-stats.MAX_OFFSET_MINUTES, le=stats.MAX_OFFSET_MINUTES)] = 0,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db),
):
    """When a server is busy: its average per hour of the day and weekday over the last 30 days."""
    _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    # Only existing servers, known metrics and a bounded offset get this far, so the cache can't grow without limit.
    cache_key = f"infradeck:stats:heatmap:{server.id}:{metric}:{offset}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    result = stats.hour_weekday_heatmap(db, server, metric, offset)
    try:
        redis_client.set(cache_key, json.dumps(result), ex=STATS_CACHE_SECONDS)
    except Exception:
        pass
    return result


@app.get("/monitoring/servers/{server_id}/mounts/history")
def mount_history(
    server_id: PathId, mount: Annotated[str, Query(min_length=1, max_length=255)], range_name: Annotated[RangeName, Query(alias="range")] = "24h", metrics: str | None = None,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db),
):
    """One filesystem's usage over time (``mount`` is its path, for example ``/data``)."""
    _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server or not mount.startswith("/"):
        raise HTTPException(status_code=404, detail="Server or mount not found.")
    wanted = _wanted_metrics(metrics, history_service.MOUNT_METRICS, ("used_pct", "used_gb", "total_gb"))
    result = _history_response(db, history_service.MOUNT, server, mount, range_name, wanted)
    if result["data_since"] is None:
        raise HTTPException(status_code=404, detail="Server or mount not found.")
    return {**result, "mount": mount}


@app.get("/monitoring/servers/{server_id}/containers/{container_id}/history")
def container_history(
    server_id: PathId, container_id: Annotated[str, PathParam(min_length=1, max_length=128)], range_name: Annotated[RangeName, Query(alias="range")] = "24h", metrics: str | None = None, previous: bool = False,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db),
):
    """One container's CPU and memory over time."""
    _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server or container not found.")
    wanted = _wanted_metrics(metrics, history_service.CONTAINER_METRICS, ("cpu", "memory_mb", "memory_pct", "memory_limit_mb"))
    result = _history_response(db, history_service.CONTAINER, server, container_id, range_name, wanted, previous)
    if result["data_since"] is None:
        raise HTTPException(status_code=404, detail="Server or container not found.")
    name = db.query(ContainerMetric.name).filter(ContainerMetric.server_id == server.id, ContainerMetric.container_id == container_id).order_by(ContainerMetric.time.desc()).limit(1).scalar()
    if name is None:
        name = db.query(ContainerRollup.name).filter(ContainerRollup.server_id == server.id, ContainerRollup.container_id == container_id).order_by(ContainerRollup.bucket_start.desc()).limit(1).scalar()
    return {**result, "container_id": container_id, "name": name}


CONTAINER_DETAIL_FIELDS = (
    "status", "healthcheck_status", "restart_count", "cpu_pct", "memory_used_mb", "memory_limit_mb", "memory_pct",
    "net_rx_mb", "net_tx_mb", "block_read_mb", "block_write_mb", "pids",
)


@app.get("/monitoring/servers/{server_id}/containers/{container_id}")
def container_detail(server_id: PathId, container_id: str, limit: int = 60, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
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
        "volumes": latest.volumes,
        "project": latest.project,
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
def enroll_collector(payload: EnrollRequest, db=Depends(get_db), request: Request = None):
    """Register (or re-register) a collector using the owner's enrollment key.

    Servers are matched on machine_id, so a collector that lost its saved
    credentials gets its existing server back with a fresh token. That makes the key powerful, so every use is
    rate limited, the number of servers is capped, and replacing an existing server's credentials is recorded
    as an event (it opens a warning), so it can't happen unnoticed.
    """
    client_ip = _client_ip(request) if request else "unknown"
    _soft_limit(scope="enroll-ip", subject=client_ip, limit=ENROLL_RATE_LIMIT, window_seconds=ENROLL_RATE_WINDOW_SECONDS)
    key = db.query(EnrollmentKey).filter(EnrollmentKey.key_hash == _hash_secret(payload.enroll_key.strip())).first()
    if not key:
        raise HTTPException(status_code=401, detail="Enrollment key is invalid or has been rotated.")
    if not _is_admin(db, key.owner_id):
        raise HTTPException(status_code=403, detail="Enrollment key belongs to an account that can no longer add servers.")
    _soft_limit(scope="enroll-key", subject=key.id, limit=ENROLL_RATE_LIMIT * 2, window_seconds=ENROLL_RATE_WINDOW_SECONDS)
    hostname = _hostname_or_error(payload.hostname)
    machine_id = (payload.machine_id or "").strip()[:64] or None
    if not hostname:
        raise HTTPException(status_code=400, detail="Hostname is required.")

    server = None
    if machine_id:
        server = db.query(Server).filter(Server.machine_id == machine_id).first()
    replaced = server is not None
    if not server:
        _ensure_server_capacity(db)
        server = Server(owner_id=key.owner_id, hostname=_unique_hostname(db, hostname), machine_id=machine_id)
        db.add(server)
        db.flush()
    token = secrets.token_urlsafe(32)
    server.collector_token_hash = _hash_secret(token)
    if replaced:
        now = datetime.now(timezone.utc)
        detail = f"This server's credentials were replaced through the enrollment key (request from {_printable(client_ip, 64)}). Its previous collector can no longer report."
        db.add(SystemEvent(server_id=server.id, time=now, kind=CREDENTIALS_REPLACED_KIND, detail=detail[:512], event_key=f"credentials:{now.isoformat()}"))
        record_audit_log(db, user_id=key.owner_id, level="warning", message=f"Enrollment key replaced the credentials of server {server.id} ({server.hostname}) from {_printable(client_ip, 64)}", source="enrollment", event_type="credentials_replaced")
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
    hostname = _hostname_or_error(payload.hostname)
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
    details = _clean_registration_details(payload.details)
    # Only the stack's own collector, coming in through the internal listener, can be "this machine" (see ingest_metrics).
    details["dashboard_host"] = details.get("dashboard_host") is True and via in ("internal", "")
    pending = PendingCollector(
        poll_secret_hash=_hash_secret(secret), pairing_code=f"{code[:3]}-{code[3:]}", hostname=hostname,
        machine_id=machine_id, ip_address=ip_address[:64], os_info=(payload.os_info or "")[:255] or None,
        join_token_id=join_token.id if join_token else None, details=json.dumps(details),
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
def approve_pending_collector(pending_id: PathId, payload: ApproveRequest | None = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _require_admin(session_id, db)
    pending = _pending_for_user(pending_id, db)
    server = _server_for_machine(db, pending.machine_id)
    if server and not (payload and payload.replace):
        # Approving mints new credentials for that server and locks out its current collector, so never do it silently.
        raise HTTPException(status_code=409, detail=f"This machine matches the existing server {server.hostname}. Approving replaces that server's credentials and signs its current collector out. Confirm to continue.")
    chosen = _hostname_or_error(payload.hostname) if payload and payload.hostname and payload.hostname.strip() else None
    if chosen and _hostname_in_use(db, chosen, excluding=server.id if server else None):
        raise HTTPException(status_code=409, detail=f"Another server is already called {chosen}. Pick a different name.")
    if not server:
        _ensure_server_capacity(db)
        server = Server(owner_id=user["id"], hostname=chosen or _unique_hostname(db, _clean_hostname(pending.hostname) or "unnamed server"), machine_id=pending.machine_id, ip_address=pending.ip_address, os_info=pending.os_info)
        db.add(server)
        db.flush()
    # A name that differs from what the machine calls itself was chosen on purpose, so it is kept.
    if chosen and _name_key(chosen) != _name_key(_clean_hostname(pending.hostname) or ""):
        server.hostname, server.name_locked = chosen, True
    pending.status = "approved"
    pending.approved_server_id = server.id
    if pending.join_token_id:
        db.query(JoinToken).filter(JoinToken.id == pending.join_token_id).update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    return {"status": "approved", "server_id": server.id}


@app.post("/monitoring/pending-collectors/{pending_id}/dismiss")
def dismiss_pending_collector(pending_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
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
                "verified": row.join_token_id is not None, "details": _load_json(row.details),
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
    if SERVER_OFFLINE_RULE in get_disabled_rules(db):
        return 0
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


def metrics_maintenance(db, purge_due: bool) -> bool:
    """Summarise newly completed periods, then (when due) purge and analyse. True when the purge ran.

    Summaries come first and the purge is skipped if they fail, so history is never deleted before it was kept.
    """
    try:
        rollups.run_rollups(db)
    except Exception as exc:
        db.rollback()
        print(f"Rollups failed: {type(exc).__name__}: {exc}", flush=True)
        return False
    if not purge_due:
        return False
    purge_old_metrics(db)
    analyze_memory_trends(db)
    return True


def _offline_watch_loop() -> None:
    last_metrics_purge: float | None = None
    while True:
        time.sleep(60)
        db = SessionLocal()
        try:
            mark_offline_servers(db)
            purge_stale_pending(db)
            purge_due = last_metrics_purge is None or time.monotonic() - last_metrics_purge >= METRICS_PURGE_INTERVAL_SECONDS
            if metrics_maintenance(db, purge_due):
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
    _ensure_server_capacity(db)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    server = Server(owner_id=user["id"], hostname=_unique_hostname(db, _hostname_or_error(payload.hostname)), ip_address=payload.ip_address, check_interval_seconds=payload.check_interval_seconds, collector_token_hash=token_hash)
    db.add(server); db.commit(); db.refresh(server)
    return {"id": server.id, "hostname": server.hostname, "check_interval_seconds": server.check_interval_seconds, "collector_token": token}


@app.delete("/monitoring/servers/{server_id}")
def delete_monitored_server(server_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")

    for rollup in (HostRollup, MountRollup, ContainerRollup):
        db.query(rollup).filter(rollup.server_id == server.id).delete(synchronize_session=False)
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
def update_check_interval(server_id: PathId, payload: dict, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    interval = payload.get("check_interval_seconds")
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    if not isinstance(interval, int) or not 15 <= interval <= 86400:
        raise HTTPException(status_code=400, detail="Interval must be between 15 and 86400 seconds.")
    server.check_interval_seconds = interval; db.commit()
    return {"id": server.id, "check_interval_seconds": server.check_interval_seconds}


MAX_HELPER_EVENTS_PER_CHECKIN = 20
MAX_HELPER_EVENTS_PER_WEEK = 500
HELPER_NOTICE_KIND = "helper_notice"


def record_helper_events(db, server_id: int, raw, now: datetime) -> None:
    """Store the helper's kernel-log events once each; the helper republishes the whole ring buffer.

    A collector chooses the keys that make events "new", so without a cap it could add rows without end. At most
    MAX_HELPER_EVENTS_PER_CHECKIN are stored per check-in (the rest are offered again by the next one, because the
    helper republishes its buffer) and MAX_HELPER_EVENTS_PER_WEEK per server; past that there is one notice.
    """
    events = validate_helper_events(raw, now)
    if not events:
        return
    known = {key for (key,) in db.query(SystemEvent.event_key).filter(SystemEvent.server_id == server_id, SystemEvent.event_key.in_([event[3] for event in events]))}
    fresh, seen = [], set()
    for event in events:
        if event[3] not in known and event[3] not in seen:
            seen.add(event[3])
            fresh.append(event)
    if not fresh:
        return
    week = now - timedelta(days=7)
    stored = db.query(func.count(SystemEvent.id)).filter(SystemEvent.server_id == server_id, SystemEvent.kind.in_(HELPER_EVENT_KINDS), SystemEvent.event_key.isnot(None), SystemEvent.time >= week).scalar() or 0
    room = max(0, MAX_HELPER_EVENTS_PER_WEEK - stored)
    for kind, time, detail, key in fresh[:min(MAX_HELPER_EVENTS_PER_CHECKIN, room)]:
        db.add(SystemEvent(server_id=server_id, time=time, kind=kind, detail=detail, event_key=key))
    if room <= 0 and not db.query(SystemEvent.id).filter(SystemEvent.server_id == server_id, SystemEvent.kind == HELPER_NOTICE_KIND, SystemEvent.time >= now - timedelta(hours=1)).first():
        db.add(SystemEvent(server_id=server_id, time=now, kind=HELPER_NOTICE_KIND, detail=f"This week's limit of {MAX_HELPER_EVENTS_PER_WEEK} kernel-log events was reached; newer ones are not recorded until older ones age out"))


DOCKER_STATUSES = {"ok", "unreachable", "not_configured"}
DOCKER_REASON_CODES = {"timeout", "refused", "missing", "denied", "error"}
MAX_CONTAINERS_PER_CHECKIN = 500
# Names end up inside incident keys (255 characters, with a prefix and suffix), so they are kept well under that.
MAX_CONTAINER_NAME = 128
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_CONTAINER_FLOATS = ("cpu_pct", "memory_used_mb", "memory_pct", "memory_limit_mb", "net_rx_mb", "net_tx_mb", "block_read_mb", "block_write_mb", "swap_mb")
_CONTAINER_TEXT = {"image": 255, "started_at": 64, "ports": 512, "healthcheck_status": 64, "project": 128, "volumes": 512}


def _printable(text: str, limit: int) -> str:
    """Trim surrounding whitespace, replace control characters, and cap the length."""
    return "".join(char if char.isprintable() else "?" for char in text.strip())[:limit].strip()


def _whole_number(value, low: int, high: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def _one_of(value, allowed) -> bool:
    """Whether a reported value is one of the allowed strings. A list or object is simply "no", not an error."""
    return isinstance(value, str) and value in allowed


def _finite_number(value, low: float, high: float) -> float | None:
    """A real number inside [low, high], or None for anything else: text, booleans, NaN, infinity, out of range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:  # an integer too large for a float
        return None
    return number if math.isfinite(number) and low <= number <= high else None


# What a collector may report for the host, as (lowest, highest). Anything outside becomes unknown (None).
HOST_NUMBER_LIMITS = {
    "cpu_utilization_pct": (0, 100), "ram_pct": (0, 100), "swap_pct": (0, 100), "disk_usage_pct": (0, 100), "disk_inodes_pct": (0, 100),
    "load_1m": (0, 1e6), "load_5m": (0, 1e6), "load_15m": (0, 1e6),
    "ram_used_gb": (0, 1e9), "ram_available_gb": (0, 1e9),
    "net_bytes_sent_mb": (0, 1e12), "net_bytes_recv_mb": (0, 1e12), "system_uptime_seconds": (0, 1e10),
}
MAX_MOUNTS_PER_CHECKIN = 64
# A mount name ends up inside incident keys (255 characters with a prefix), so it is kept well under that.
MAX_MOUNT_NAME = 200
INT_MAX = 2**31 - 1  # the database's INTEGER columns


def _clean_host_numbers(fields: dict) -> None:
    """Range-check every number a collector reported about the host. A single NaN or Infinity stored here would
    make every page that reads the latest sample fail for every user, so nothing unchecked is stored."""
    for key, (low, high) in HOST_NUMBER_LIMITS.items():
        if key in fields:
            fields[key] = _finite_number(fields[key], low, high)


def _clean_mount(raw) -> dict | None:
    """One filesystem record reduced to known fields of the right type and size, or None if unusable."""
    if not isinstance(raw, dict) or not isinstance(raw.get("mount"), str):
        return None
    mount = _printable(raw["mount"], MAX_MOUNT_NAME)
    if not mount.startswith("/"):
        return None
    fs_type = raw.get("fs_type")
    return {
        "mount": mount, "fs_type": (_printable(fs_type, 64) or None) if isinstance(fs_type, str) else None,
        "total_gb": _finite_number(raw.get("total_gb"), 0, 1e9), "used_gb": _finite_number(raw.get("used_gb"), 0, 1e9),
        "used_pct": _finite_number(raw.get("used_pct"), 0, 100), "inodes_pct": _finite_number(raw.get("inodes_pct"), 0, 100),
    }


def _clean_mounts(raw) -> list[dict]:
    """At most MAX_MOUNTS_PER_CHECKIN valid, distinct filesystems."""
    seen: set[str] = set()
    cleaned = []
    for item in raw if isinstance(raw, list) else []:
        mount = _clean_mount(item)
        if mount and mount["mount"] not in seen:
            seen.add(mount["mount"])
            cleaned.append(mount)
            if len(cleaned) >= MAX_MOUNTS_PER_CHECKIN:
                break
    return cleaned


# Letters from these alphabets look like Latin ones, which is how a name poses as another (srv with a Cyrillic "a").
_LOOKALIKE_SCRIPTS = {"CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE", "COPTIC"}


def _clean_hostname(text: str) -> str | None:
    """A hostname made printable and bounded, in its compatibility-normal form (so full-width letters become plain
    ones), or None if it mixes Latin letters with a lookalike alphabet. A name written entirely in one alphabet is
    fine; the mix is what is used to impersonate another server."""
    name = _printable(unicodedata.normalize("NFKC", text), 255)
    scripts = {unicodedata.name(char, "").split(" ")[0] for char in name if char.isalpha()}
    if not name or ("LATIN" in scripts and scripts & _LOOKALIKE_SCRIPTS):
        return None
    return name


def _name_key(name: str) -> str:
    """What two hostnames are compared by: case, accents and width are ignored."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _hostname_in_use(db, name: str, *, excluding: int | None = None) -> bool:
    key = _name_key(name)
    return any(_name_key(existing) == key for (existing,) in db.query(Server.hostname).filter(Server.id != excluding if excluding else True))


def _unique_hostname(db, name: str) -> str:
    """The name for a new server: as given, or numbered ("pi (2)") if another server already has it, so two machines
    can be told apart (default names such as "raspberrypi" are common) and nothing can sit behind a twin."""
    if not _hostname_in_use(db, name):
        return name
    for number in range(2, 1000):
        candidate = f"{name} ({number})"[:255]
        if not _hostname_in_use(db, candidate):
            return candidate
    return name


def _hostname_or_error(text: str) -> str:
    name = _clean_hostname(text)
    if not name:
        raise HTTPException(status_code=400, detail="The hostname is empty, or mixes alphabets (for example Latin and Cyrillic letters), which is refused because it can be used to impersonate another server.")
    return name


def _apply_server_fields(db, server: Server, reported: dict) -> None:
    """Copy the descriptive fields a collector reports onto its server, each checked, never trusted.

    A hostname that another server already uses is ignored, so one collector can't pose as another machine in
    alert titles.
    """
    hostname = reported.get("hostname")
    if not server.name_locked and isinstance(hostname, str) and (name := _clean_hostname(hostname)):
        if not _hostname_in_use(db, name, excluding=server.id):
            server.hostname = name
    address = reported.get("ip_address")
    if isinstance(address, str):
        try:
            server.ip_address = str(ipaddress.ip_address(address.strip()))
        except ValueError:
            pass
    os_info = reported.get("os_info")
    if isinstance(os_info, str) and (text := _printable(os_info, 255)):
        server.os_info = text
    if (cores := _whole_number(reported.get("cpu_cores"), 1, 4096)) is not None:
        server.cpu_cores = cores
    if (ram := _finite_number(reported.get("total_ram_gb"), 0, 1e6)) is not None:
        server.total_ram_gb = ram
    if isinstance(reported.get("system"), dict):
        server.system_info = json.dumps(_clean_system_info(reported["system"]), separators=(",", ":"))


_SYSTEM_TEXT = {"kernel": 64, "arch": 16, "cpu_model": 128, "timezone": 64, "time_service": 32, "collector_version": 16, "docker_version": 32, "reboot_kernel": 64, "updates_manager": 16}
_SYSTEM_NUMBERS = {"swap_total_gb": (0, 1e6), "clock_offset_ms": (-1e9, 1e9), "updates_checked_at": (0, 1e11), "updates_applied_at": (0, 1e11)}
_SYSTEM_COUNTS = {"updates_pending": 100_000, "updates_security": 100_000, "disk_io_errors": INT_MAX}
_DOCKER_DISK_SIZES = ("images_gb", "images_unused_gb", "build_cache_gb", "build_cache_unused_gb")
_SYSTEM_FLAGS = ("auto_updates", "helper_installed")
MAX_INTERFACES = 16
MAX_TEMPERATURES = 8


def _clean_system_info(raw: dict) -> dict:
    """The machine facts a collector reported, reduced to known keys of the right type and size.

    Like everything else from a collector this is untrusted: it is stored as JSON and sent to every browser, so
    unknown keys are dropped, text is made printable and bounded, and numbers are range-checked.
    """
    clean: dict = {}
    for key, limit in _SYSTEM_TEXT.items():
        if isinstance(raw.get(key), str) and (value := _printable(raw[key], limit)):
            clean[key] = value
    for key, (low, high) in _SYSTEM_NUMBERS.items():
        if (number := _finite_number(raw.get(key), low, high)) is not None:
            clean[key] = number
    for key, high in _SYSTEM_COUNTS.items():
        if (count := _whole_number(raw.get(key), 0, high)) is not None:
            clean[key] = count
    for key in _SYSTEM_FLAGS:
        if isinstance(raw.get(key), bool):
            clean[key] = raw[key]
    temperatures = []
    for item in raw["temperatures"][:MAX_TEMPERATURES] if isinstance(raw.get("temperatures"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("label"), str) and (label := _printable(item["label"], 32)) and (celsius := _finite_number(item.get("celsius"), -50, 200)) is not None:
            temperatures.append({"label": label, "celsius": celsius})
    clean["temperatures"] = temperatures
    interfaces = []
    for item in raw["interfaces"][:MAX_INTERFACES] if isinstance(raw.get("interfaces"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not (name := _printable(item["name"], 16)):
            continue
        entry: dict = {"name": name}
        if isinstance(item.get("network"), str):
            try:
                entry["network"] = str(ipaddress.ip_network(item["network"].strip(), strict=False))
            except ValueError:
                pass
        for key in ("rx_mbps", "tx_mbps"):
            if (rate := _finite_number(item.get(key), 0, 1e6)) is not None:
                entry[key] = rate
        if (speed := _whole_number(item.get("speed_mbit"), 1, 10_000_000)) is not None:
            entry["speed_mbit"] = speed
        interfaces.append(entry)
    clean["interfaces"] = interfaces
    devices = {}
    for mount, device in list(raw["mount_devices"].items())[:MAX_MOUNTS_PER_CHECKIN] if isinstance(raw.get("mount_devices"), dict) else []:
        if isinstance(mount, str) and isinstance(device, str) and (mount_name := _printable(mount, MAX_MOUNT_NAME)) and (device_name := _printable(device, 64)):
            devices[mount_name] = device_name
    clean["mount_devices"] = devices
    allowed = []
    for item in raw["firewall_allowed"][:MAX_LISTENERS] if isinstance(raw.get("firewall_allowed"), list) else []:
        if isinstance(item, dict) and item.get("proto") in LISTENER_PROTOCOLS and item.get("file") in FIREWALL_LABELS and (port := _whole_number(item.get("port"), 1, 65535)) is not None:
            allowed.append({"proto": item["proto"], "port": port, "file": item["file"]})
    clean["firewall_allowed"] = allowed
    if isinstance(raw.get("docker_disk"), dict):
        disk = {key: size for key in _DOCKER_DISK_SIZES if (size := _finite_number(raw["docker_disk"].get(key), 0, 1e9)) is not None}
        for key in ("images", "images_unused"):
            if (count := _whole_number(raw["docker_disk"].get(key), 0, 10_000_000)) is not None:
                disk[key] = count
        if disk:
            clean["docker_disk"] = disk
    # None means the port-owners helper isn't running there; a list (even an empty one) means it is.
    if isinstance(raw.get("listener_owners"), list):
        owners = []
        for item in raw["listener_owners"][:MAX_LISTENERS]:
            if isinstance(item, dict) and item.get("proto") in LISTENER_PROTOCOLS and (port := _whole_number(item.get("port"), 1, 65535)) is not None and isinstance(item.get("process"), str) and (process := _printable(item["process"], 64)):
                owners.append({"proto": item["proto"], "port": port, "process": process})
        clean["listener_owners"] = owners
    return clean


def _clean_registration_details(raw: dict | None) -> dict:
    """What a collector asking to join says it found on its machine; shown to an admin, so checked like the rest."""
    if not isinstance(raw, dict):
        return {}
    clean: dict = {}
    for key, high in (("cpu_cores", 4096), ("containers", 100_000), ("mounts", 1000)):
        if (count := _whole_number(raw.get(key), 0, high)) is not None:
            clean[key] = count
    for key in ("helper_installed", "dashboard_host"):
        if isinstance(raw.get(key), bool):
            clean[key] = raw[key]
    return clean


PACKAGES_UPDATED_KIND = "packages_updated"


def packages_updated_detail(before: dict, after: dict) -> str | None:
    """A sentence for the event log when a machine's package manager has installed updates since the last check-in
    (its log file is newer), or None. The count is how far the pending number fell, when both are known."""
    was, now = before.get("updates_applied_at"), after.get("updates_applied_at")
    if not isinstance(was, (int, float)) or not isinstance(now, (int, float)) or now <= was + 1:
        return None
    manager = after.get("updates_manager") or "the package manager"
    fell = (before.get("updates_pending") or 0) - (after.get("updates_pending") or 0) if isinstance(before.get("updates_pending"), int) and isinstance(after.get("updates_pending"), int) else 0
    what = f"{fell} {'package' if fell == 1 else 'packages'} updated by {manager}" if fell > 0 else f"Packages installed or updated by {manager}"
    return what + (f", kernel {after['reboot_kernel']} waiting for reboot" if after.get("reboot_kernel") and after.get("reboot_kernel") != before.get("reboot_kernel") else "")


def _load_json(text_value: str | None) -> dict:
    try:
        loaded = json.loads(text_value) if text_value else {}
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


ENROLL_RATE_WINDOW_SECONDS = 3600
CREDENTIALS_REPLACED_KIND = "credentials_replaced"


def _soft_limit(*, scope: str, subject, limit: int, window_seconds: int) -> None:
    """Count an attempt and refuse with 429 past the limit. Doesn't get in the way if Redis itself is down."""
    try:
        enforce_limit(redis_client, scope=scope, subject=subject, limit=limit, window_seconds=window_seconds)
    except HTTPException as exc:
        if exc.status_code == 429:
            raise
        # 503 means the counter itself is unavailable (Redis down); that must not stop servers from enrolling
    except Exception:
        return


def _ensure_server_capacity(db) -> None:
    """Refuse to add yet another server once the install holds MAX_SERVERS, however it is being added."""
    if (db.query(func.count(Server.id)).scalar() or 0) >= MAX_SERVERS:
        raise HTTPException(status_code=409, detail=f"This install already has the most servers it allows ({MAX_SERVERS}). Remove one first, or raise MAX_SERVERS.")


def _enforce_ingest_spacing(server: Server) -> None:
    """Refuse a check-in that follows the previous one too closely. Runs after authentication, so only the
    server's own collector can use up its slot."""
    if INGEST_MIN_SPACING_RATIO <= 0:
        return
    spacing = max(INGEST_MIN_SPACING_SECONDS, int(server.check_interval_seconds * INGEST_MIN_SPACING_RATIO))
    try:
        allowed = redis_client.set(f"infradeck:ingest:{server.id}", "1", nx=True, ex=spacing)
    except Exception:  # losing the limit during a Redis outage is better than losing the monitoring
        return
    if not allowed:
        raise HTTPException(status_code=429, detail="Check-ins are arriving faster than this server's interval allows.", headers={"Retry-After": str(spacing)})


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
    for key in ("oom_kill_total", "kernel_tainted"):
        if key in fields:
            fields[key] = _whole_number(fields[key], 0, INT_MAX)
    if "last_shutdown" in fields and not _one_of(fields["last_shutdown"], {"clean", "unclean", "unknown"}):
        fields["last_shutdown"] = None
    if "docker_status" in fields and not _one_of(fields["docker_status"], DOCKER_STATUSES):
        fields["docker_status"] = None
    if "docker_reason" in fields and not _one_of(fields["docker_reason"], DOCKER_REASON_CODES):
        fields["docker_reason"] = None
    if "docker_latency_ms" in fields:
        fields["docker_latency_ms"] = _whole_number(fields["docker_latency_ms"], 0, 600_000)
    if "collect_duration_ms" in fields:
        fields["collect_duration_ms"] = _whole_number(fields["collect_duration_ms"], 0, 3_600_000)
    if "internet_status" in fields and not _one_of(fields["internet_status"], INTERNET_STATUSES):
        fields["internet_status"] = None
    if "internet_latency_ms" in fields:
        fields["internet_latency_ms"] = _whole_number(fields["internet_latency_ms"], 0, 600_000)
    if "boot_id" in fields:
        fields["boot_id"] = (_printable(fields["boot_id"], 64) or None) if isinstance(fields["boot_id"], str) else None


@app.post("/monitoring/metrics/ingest", dependencies=[Depends(require_collector_listener)])
def ingest_metrics(payload: MetricsIngestRequest, authorization: Annotated[str | None, Header()] = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db), request: Request = None):
    server = db.query(Server).filter(Server.id == payload.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else None
    if server.collector_token_hash and token_hash and hmac.compare_digest(server.collector_token_hash, token_hash):
        pass
    else:
        _require_admin(session_id, db)
    _enforce_ingest_spacing(server)
    updates_before = _load_json(server.system_info)
    _apply_server_fields(db, server, payload.server)
    if detail := packages_updated_detail(updates_before, _load_json(server.system_info)):
        db.add(SystemEvent(server_id=server.id, time=datetime.now(timezone.utc), kind=PACKAGES_UPDATED_KIND, detail=detail))
    # Only the stack's own collector, which reaches the backend through the gate's internal listener, may claim to
    # be the dashboard's machine; a collector coming in over the VPN can't take that place. (With a single
    # listener, in the development stack, there is no way to tell, so the claim is accepted.)
    server.is_dashboard_host = payload.server.get("dashboard_host") is True and _collector_via(request) in ("internal", "")
    if server.is_dashboard_host:
        db.query(Server).filter(Server.id != server.id, Server.is_dashboard_host.is_(True)).update({"is_dashboard_host": False}, synchronize_session=False)
    now = datetime.now(timezone.utc)
    host_fields = {k: v for k, v in payload.host.items() if k in HostMetric.__table__.columns.keys() and k not in {"id", "server_id", "time"}}
    _clean_system_fields(host_fields)
    _clean_host_numbers(host_fields)
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
    for mount in _clean_mounts(payload.host.get("mounts")):
        db.add(MountMetric(server_id=server.id, time=now, **mount))
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
    sync_alerts(db, server.id, HOST_RULE_PREFIX, _enabled_rules(db, host_rules(db, server, thresholds, active_rule_keys(db, server.id, HOST_RULE_PREFIX), get_mount_thresholds(db))))
    sync_alerts(db, server.id, SYSTEM_RULE_PREFIX, _enabled_rules(db, system_rules(db, server, thresholds, now, active_rule_keys(db, server.id, SYSTEM_RULE_PREFIX))))
    sync_alerts(db, server.id, NETWORK_RULE_PREFIX, _enabled_rules(db, network_rules(db, server, thresholds, now)))
    # The collector sends no containers when Docker is unreachable; keep container alerts as they are rather than resolving them all.
    if containers:
        sync_alerts(db, server.id, CONTAINER_RULE_PREFIX, _enabled_rules(db, container_rules(db, server.id, containers, thresholds, now)))
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
    enforce_limit(redis_client, scope="conversation-user", subject=user["id"], limit=CONVERSATION_CREATE_LIMIT, window_seconds=3600)
    if db.query(Conversation).filter(Conversation.id == payload.conversation_id).first():
        raise HTTPException(status_code=409, detail="Conversation already exists.")
    if db.query(func.count(Conversation.id)).filter(Conversation.user_id == user["id"]).scalar() >= MAX_CONVERSATIONS_PER_USER:
        raise HTTPException(status_code=409, detail=f"You already have {MAX_CONVERSATIONS_PER_USER} conversations. Delete some before starting a new one.")
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


LOG_WRITE_LIMIT = 120
CHAT_MESSAGE_LIMIT = 30          # messages to the assistant per user per hour (each can cost the admin's OpenAI quota)
CONVERSATION_CREATE_LIMIT = 30   # new conversations per user per hour
MAX_CONVERSATIONS_PER_USER = 100


@app.post("/logs")
def create_log(
    payload: LogCreateRequest,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    db=Depends(get_db),
):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    enforce_limit(redis_client, scope="logs-user", subject=user["id"], limit=LOG_WRITE_LIMIT, window_seconds=3600)

    return record_audit_log(
        db,
        user_id=user["id"],
        level=payload.level,
        message=payload.message,
        source="dashboard",
        task_title=payload.task_title or payload.message[:255],
        steps=[step for step in payload.steps if step.strip()],
        conversation_id=payload.conversation_id,
        event_type=payload.event_type or None,
    )


@app.post("/agent/chat")
def agent_chat(payload: AgentChatRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    if not is_agent_chat_enabled(db):
        raise HTTPException(status_code=403, detail="Chat with the agent has been turned off by an admin.")
    enforce_limit(redis_client, scope="chat-user", subject=user["id"], limit=CHAT_MESSAGE_LIMIT, window_seconds=3600)
    spend = agent_usage.budget(db)
    if spend["state"] == "paused":
        raise HTTPException(status_code=429, detail=f"The agent's budget for this month is used up (${spend['cost']:.2f} of ${spend['cap']:.2f}). An admin can raise it in Admin › Incident agent.")
    enforce_limit(redis_client, scope="chat-user-day", subject=user["id"], limit=agent_usage.get_daily_limit(db), window_seconds=86400)

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

    def log_usage(completion, duration_ms: int) -> None:
        agent_usage.record(db, kind="chat", model=completion.model, input_tokens=completion.input_tokens, output_tokens=completion.output_tokens, duration_ms=duration_ms,
                           user_id=user["id"], conversation_id=payload.conversation_id, incident_id=payload.incident_id)

    try:
        response_text = send_agent_message(
            user["id"],
            payload.conversation_id,
            _incident_context(db, payload.incident_id) + message,
            usage_logger=log_usage,
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
        agent_usage.record(db, kind="chat", status="error", model=AGENT_MODEL, user_id=user["id"], conversation_id=payload.conversation_id, incident_id=payload.incident_id, error=user_message)
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
    _warn_about_budget(db, spend)

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


# ---------------------------------------------------------------------------------------------------------
# Incidents (the Agent page)

def _agent_note(db, alert: IncidentAlert, server: Server | None) -> str | None:
    """The one line the Overview shows next to an incident, from its diagnosis. Remembered for a minute: the
    Overview is polled every few seconds and working it out reads samples."""
    if server is None:
        return None
    key = f"infradeck:incident-note:{alert.id}"
    try:
        cached = redis_client.get(key)
        if cached is not None:
            return (cached.decode() if isinstance(cached, bytes) else cached) or None
    except Exception:
        pass
    note = incidents.diagnose(db, alert, server).get("note")
    try:
        redis_client.set(key, note or "", ex=60)
    except Exception:
        pass
    return note


def _incident_context(db, incident_id: int | None) -> str:
    """What Infradeck knows about an incident, put in front of a chat question about it. Built here from stored
    data, never from what the browser sent."""
    alert = db.get(IncidentAlert, incident_id) if incident_id else None
    server = db.get(Server, alert.server_id) if alert else None
    if not alert or not server:
        return ""
    found = incidents.diagnose(db, alert, server)
    facts = [f"- {signal['text']}" for signal in found["signals"]] + [f"- {line['text']}" for line in found["lines"][-6:]]
    return (f"[INCIDENT {incidents.reference(alert.id)} on {server.hostname}, {alert.severity.lower()}, {alert.status}]\n{alert.title}\n{alert.description}\n"
            + ("\n".join(facts) + "\n" if facts else "") + "[QUESTION]\n")


class MuteRequest(BaseModel):
    minutes: int = Field(default=60, ge=5, le=7 * 24 * 60)


def _incident_or_404(db, incident_id: int) -> tuple[IncidentAlert, Server]:
    alert = db.get(IncidentAlert, incident_id)
    server = db.get(Server, alert.server_id) if alert else None
    if not alert or not server:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return alert, server


@app.get("/incidents")
def list_incidents(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _current_user(session_id, db)
    return {**incidents.inbox(db), "can_act": _is_admin(db, user["id"])}


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _current_user(session_id, db)
    alert, server = _incident_or_404(db, incident_id)
    users = dict(db.query(User.id, User.name).all())
    return {**incidents.summary(alert, server.hostname, datetime.now(timezone.utc), users), "diagnosis": incidents.diagnose(db, alert, server), "cpu_cores": server.cpu_cores}


def _incident_action(db, incident_id: int, session_id, change, message: str) -> dict:
    user = _require_operator(session_id, db)
    alert, server = _incident_or_404(db, incident_id)
    change(alert, user)
    db.commit()
    record_audit_log(db, user_id=user["id"], level="INFO", message=f"{message} {incidents.reference(alert.id)} ({alert.title})"[:255], source="incidents")
    return incidents.summary(alert, server.hostname, datetime.now(timezone.utc), dict(db.query(User.id, User.name).all()))


@app.post("/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    def change(alert, user):
        alert.acknowledged_at, alert.acknowledged_by = datetime.now(timezone.utc), user["id"]
    return _incident_action(db, incident_id, session_id, change, "Acknowledged")


@app.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Close an incident by hand. If the rule behind it is still firing at the next check-in, a new one opens."""
    def change(alert, user):
        if alert.status != "active":
            raise HTTPException(status_code=409, detail="This incident is already resolved.")
        alert.status, alert.resolved_at, alert.resolution = "resolved", datetime.now(timezone.utc), f"Marked resolved by {user['name']}"[:255]
    return _incident_action(db, incident_id, session_id, change, "Marked resolved")


@app.post("/incidents/{incident_id}/mute")
def mute_incident(incident_id: PathId, payload: MuteRequest | None = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    minutes = (payload or MuteRequest()).minutes

    def change(alert, user):
        alert.muted_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return _incident_action(db, incident_id, session_id, change, f"Muted for {minutes} min")


@app.post("/incidents/{incident_id}/unmute")
def unmute_incident(incident_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    def change(alert, user):
        alert.muted_until = None
    return _incident_action(db, incident_id, session_id, change, "Unmuted")


# ---------------------------------------------------------------------------------------------------------
# The account page: profile, preferences, API tokens, notification channels

_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,31}")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_CLOCK = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
DISPLAY_CHOICES = {"time_format": ("24h", "12h"), "sizes": ("GB", "GiB"), "theme": ("dark", "light", "system"), "density": ("comfortable", "compact"), "open_on": ("overview", "statistics", "agent")}
DISPLAY_DEFAULTS = {"time_format": "24h", "sizes": "GB", "theme": "dark", "density": "comfortable", "open_on": "overview", "timezone": ""}


class ProfileRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=64)
    avatar_color: str | None = Field(default=None, max_length=16)


class PreferencesRequest(BaseModel):
    display: dict | None = None
    routing: dict | None = None
    quiet: dict | None = None


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scope: Literal["read", "write"] = "read"
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ChannelRequest(BaseModel):
    kind: Literal["ntfy", "discord", "webhook", "email"]
    target: str | None = Field(default=None, max_length=500)


def _preferences(user: User) -> dict:
    stored = _load_json(user.preferences)
    display = {**DISPLAY_DEFAULTS, **{key: value for key, value in (stored.get("display") or {}).items() if key in DISPLAY_DEFAULTS}}
    quiet = {"enabled": False, "start": "23:00", "end": "07:00", **(stored.get("quiet") if isinstance(stored.get("quiet"), dict) else {})}
    return {"display": display, "routing": stored.get("routing") if isinstance(stored.get("routing"), dict) else {}, "quiet": quiet}


def _owner_id(db) -> int | None:
    return db.query(func.min(User.id)).filter(User.role == "admin").scalar()


def _role_name(db, user: User) -> str:
    return "Owner" if user.id == _owner_id(db) else "Admin" if user.role == "admin" else "Viewer"


def _channel_payload(channel: NotificationChannel) -> dict:
    return {"id": channel.id, "kind": channel.kind, "display": channel.display, "last_ok": channel.last_ok, "last_status": channel.last_status,
            "last_checked_at": channel.last_checked_at.isoformat() if channel.last_checked_at else None}


def _token_payload(token: ApiToken) -> dict:
    return {"id": token.id, "name": token.name, "scope": token.scope, "hint": token.hint, "created_at": token.created_at.isoformat(),
            "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None, "expires_at": token.expires_at.isoformat() if token.expires_at else None}


@app.get("/account")
def get_account(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    return {
        "id": user.id, "name": user.name, "username": user.username, "email": user.email, "role": user.role, "role_name": _role_name(db, user), "avatar_color": user.avatar_color,
        "created_at": user.created_at.isoformat(), "password_changed_at": user.password_changed_at.isoformat() if user.password_changed_at else None,
        "two_factor": {"enabled": user.totp_enabled, "available": secrets_box.available(), "required": two_factor_required_for(db, user.role), "recovery_codes_left": recovery_codes_left(db, user) if user.totp_enabled else 0},
        "preferences": _preferences(user), "session_days": round(auth.REMEMBER_SESSION_TTL_SECONDS / 86400),
        "tokens": [_token_payload(token) for token in db.query(ApiToken).filter(ApiToken.user_id == user.id).order_by(ApiToken.created_at)],
        "channels": [_channel_payload(channel) for channel in db.query(NotificationChannel).filter(NotificationChannel.user_id == user.id).order_by(NotificationChannel.id)],
        "smtp_configured": notify.smtp_configured(), "secrets_configured": secrets_box.available(),
    }


@app.patch("/account/profile")
def update_profile(payload: ProfileRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    if payload.name is not None:
        name = _printable(payload.name, 255)
        if not name:
            raise HTTPException(status_code=400, detail="The display name can't be empty.")
        user.name = name
    if payload.username is not None:
        username = payload.username.strip().lower()
        if username and not _USERNAME.fullmatch(username):
            raise HTTPException(status_code=400, detail="A username is 2 to 32 lowercase letters, digits, dots, dashes or underscores.")
        if username and db.query(User.id).filter(User.username == username, User.id != user.id).first():
            raise HTTPException(status_code=409, detail="That username is taken.")
        user.username = username or None
    if payload.avatar_color is not None:
        if payload.avatar_color and not _COLOR.fullmatch(payload.avatar_color):
            raise HTTPException(status_code=400, detail="The color must look like #A1B2C3.")
        user.avatar_color = payload.avatar_color or None
    db.commit()
    return get_account(session_id, db)


@app.put("/account/preferences")
def update_preferences(payload: PreferencesRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Display and notification choices. Everything is checked against a fixed list; unknown keys are dropped."""
    session, user = _account_session(session_id, db)
    current = _preferences(user)
    if payload.display is not None:
        for key, choices in DISPLAY_CHOICES.items():
            if payload.display.get(key) in choices:
                current["display"][key] = payload.display[key]
        zone = payload.display.get("timezone")
        if isinstance(zone, str) and len(zone) <= 64:
            try:
                notify.ZoneInfo(zone) if zone else None
                current["display"]["timezone"] = zone
            except (notify.ZoneInfoNotFoundError, ValueError):
                raise HTTPException(status_code=400, detail="That time zone is not known.")
    if payload.routing is not None:
        own = {str(channel_id) for (channel_id,) in db.query(NotificationChannel.id).filter(NotificationChannel.user_id == user.id)}
        current["routing"] = {event: {channel: bool(on) for channel, on in (payload.routing.get(event) or {}).items() if channel in own} for event in notify.EVENTS if isinstance(payload.routing.get(event), dict)}
    if payload.quiet is not None:
        start, end = payload.quiet.get("start"), payload.quiet.get("end")
        if not (isinstance(start, str) and isinstance(end, str) and _CLOCK.fullmatch(start) and _CLOCK.fullmatch(end)):
            raise HTTPException(status_code=400, detail="Quiet hours need a start and an end like 23:00.")
        current["quiet"] = {"enabled": payload.quiet.get("enabled") is True, "start": start, "end": end}
    user.preferences = json.dumps(current, separators=(",", ":"))
    db.commit()
    return current


MAX_TOKENS_PER_USER = 20


@app.post("/account/tokens")
def create_api_token(payload: TokenRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    name = _printable(payload.name, 64)
    if not name:
        raise HTTPException(status_code=400, detail="Give the token a name.")
    if payload.scope == "write" and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only owners and admins can create a token that acknowledges incidents.")
    if db.query(ApiToken).filter(ApiToken.user_id == user.id).count() >= MAX_TOKENS_PER_USER:
        raise HTTPException(status_code=400, detail="Revoke a token you no longer use first.")
    secret = f"{API_TOKEN_PREFIX}{'w' if payload.scope == 'write' else 'r'}_{secrets.token_urlsafe(32)}"
    token = ApiToken(user_id=user.id, name=name, scope=payload.scope, token_hash=_hash_secret(secret), hint=secret[-4:],
                     expires_at=datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None)
    db.add(token)
    db.commit()
    record_audit_log(db, user_id=user.id, level="INFO", message=f"Created API token {name} ({payload.scope})", source="auth")
    return {**_token_payload(token), "token": secret}


@app.delete("/account/tokens/{token_id}")
def revoke_api_token(token_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    token = db.query(ApiToken).filter(ApiToken.id == token_id, ApiToken.user_id == user.id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found.")
    name = token.name
    db.delete(token)
    db.commit()
    record_audit_log(db, user_id=user.id, level="INFO", message=f"Revoked API token {name}", source="auth")
    return {"status": "ok"}


@app.post("/account/channels")
def add_channel(payload: ChannelRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    if not secrets_box.available():
        raise HTTPException(status_code=503, detail="Channels can't be saved on this server yet: it has no SECRETS_KEY to encrypt their addresses with.")
    if db.query(NotificationChannel).filter(NotificationChannel.user_id == user.id).count() >= notify.MAX_CHANNELS_PER_USER:
        raise HTTPException(status_code=400, detail="Remove a channel you no longer use first.")
    if payload.kind == "email":
        if not notify.smtp_configured():
            raise HTTPException(status_code=400, detail="Email isn't set up on this server. An admin needs to set SMTP_HOST and SMTP_FROM in .env.")
        if db.query(NotificationChannel).filter(NotificationChannel.user_id == user.id, NotificationChannel.kind == "email").first():
            raise HTTPException(status_code=409, detail="Email is already one of your channels.")
        target, display = user.email, user.email   # only ever the account's own address
    else:
        try:
            target, display = notify.check_url(payload.target or "", allow_private=user.role == "admin")
        except notify.ChannelError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    channel = NotificationChannel(user_id=user.id, kind=payload.kind, target_enc=secrets_box.encrypt(target), display=display)
    db.add(channel)
    db.commit()
    return _channel_payload(channel)


def _own_channel(db, user: User, channel_id: int) -> NotificationChannel:
    channel = db.query(NotificationChannel).filter(NotificationChannel.id == channel_id, NotificationChannel.user_id == user.id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found.")
    return channel


def _deliver(db, channel: NotificationChannel, owner: User, title: str, body: str, severity: str) -> bool:
    try:
        ok, status_text = notify.send(channel.kind, secrets_box.decrypt(channel.target_enc), title, body, severity, allow_private=owner.role == "admin")
    except secrets_box.SecretsUnavailable:
        ok, status_text = False, "SECRETS_KEY problem"
    channel.last_ok, channel.last_status, channel.last_checked_at = ok, status_text[:64], datetime.now(timezone.utc)
    db.commit()
    return ok


@app.post("/account/channels/{channel_id}/test")
def test_channel(channel_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    enforce_limit(redis_client, scope="channel-test", subject=user.id, limit=10, window_seconds=600)
    channel = _own_channel(db, user, channel_id)
    _deliver(db, channel, user, "Infradeck test", f"This is a test from {get_instance_name(db) or 'Infradeck'}. Alerts for {user.name} will arrive here.", "INFO")
    return _channel_payload(channel)


@app.delete("/account/channels/{channel_id}")
def remove_channel(channel_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = _account_session(session_id, db)
    db.delete(_own_channel(db, user, channel_id))
    db.commit()
    return {"status": "ok"}


NOTIFY_WINDOW = timedelta(hours=24)
NOTIFY_MARK_SECONDS = 7 * 24 * 3600


def dispatch_notifications(db, now: datetime | None = None) -> int:
    """Send what hasn't been sent: incidents that opened or resolved in the last day, to every user with channels,
    following each user's routing and quiet hours. Returns how many messages went out.

    A warning that opens during someone's quiet hours waits and is sent when they end, if it is still open.
    Critical incidents are never held back. Muted incidents notify nobody."""
    now = now or datetime.now(timezone.utc)
    owners = {user.id: user for user in db.query(User).filter(User.is_active.is_(True))}
    by_user: dict[int, list[NotificationChannel]] = {}
    for channel in db.query(NotificationChannel).order_by(NotificationChannel.id):
        if channel.user_id in owners:
            by_user.setdefault(channel.user_id, []).append(channel)
    if not by_user:
        return 0
    recent = db.query(IncidentAlert).filter((IncidentAlert.created_at >= now - NOTIFY_WINDOW) | (IncidentAlert.resolved_at >= now - NOTIFY_WINDOW), IncidentAlert.severity != "INFO").order_by(IncidentAlert.created_at).limit(200).all()
    hostnames = dict(db.query(Server.id, Server.hostname).all())
    sent = 0
    for alert in recent:
        if incidents.is_muted(alert, now):
            continue
        event = "resolved" if alert.status == "resolved" else alert.severity.lower()
        if event not in notify.EVENTS:
            continue
        for user_id, channels in by_user.items():
            preferences = _preferences(owners[user_id])
            routing = preferences["routing"].get(event, {})
            wanted = [channel for channel in channels if routing.get(str(channel.id), notify.DEFAULT_ROUTING[event])]
            if not wanted or (event != "critical" and notify.in_quiet_hours(preferences["quiet"], preferences["display"]["timezone"], now)):
                continue
            marker = f"infradeck:notified:{alert.id}:{user_id}:{event}"
            try:
                if not redis_client.set(marker, "1", nx=True, ex=NOTIFY_MARK_SECONDS):
                    continue
            except Exception:   # without the marker everyone would be told again every few seconds
                return sent
            title = f"{'Resolved: ' if event == 'resolved' else ''}{alert.title}"
            body = f"{hostnames.get(alert.server_id, 'server')} · {alert.severity.lower()} · {incidents.reference(alert.id)}\n{alert.resolution or 'The condition cleared.' if event == 'resolved' else alert.description}"
            for channel in wanted:
                sent += 1 if _deliver(db, channel, owners[user_id], title, body, "INFO" if event == "resolved" else alert.severity) else 0
            if event == "resolved":
                alert.resolve_notified_at = alert.resolve_notified_at or now
            else:
                alert.notified_at = alert.notified_at or now
    db.commit()
    return sent


def _warn_about_budget(db, before: dict) -> None:
    """Tell the admins once a month when the agent passes 80 % of its budget, and again when it pauses at 100 %."""
    after = agent_usage.budget(db)
    rank = {"ok": 0, "warn": 1, "paused": 2}
    if rank[after["state"]] <= rank[before["state"]] or not after["cap"]:
        return
    try:
        if not redis_client.set(f"infradeck:agent-budget:{datetime.now(timezone.utc):%Y-%m}:{after['state']}", "1", nx=True, ex=40 * 86400):
            return
    except Exception:
        return
    paused = after["state"] == "paused"
    title = "The incident agent paused: its monthly budget is used up" if paused else f"The incident agent has used {round(after['share'] * 100)}% of its monthly budget"
    body = f"${after['cost']:.2f} of ${after['cap']:.2f} this month. {'Chat stays off until next month or until an admin raises the budget' if paused else 'At 100% the agent pauses until next month'}. Incidents and notifications keep working. Admin › Incident agent."
    record_audit_log(db, user_id=None, level="WARN", message=title[:255], source="agent", event_type="agent_budget")
    admins = {user.id: user for user in db.query(User).filter(User.role == "admin", User.is_active.is_(True))}
    for channel in db.query(NotificationChannel).filter(NotificationChannel.user_id.in_(list(admins))).order_by(NotificationChannel.id):
        _deliver(db, channel, admins[channel.user_id], title, body, "WARNING")


def _notify_loop() -> None:
    while True:
        time.sleep(15)
        db = SessionLocal()
        try:
            dispatch_notifications(db)
        except Exception as exc:  # a broken channel or a database hiccup must not end the loop
            print(f"Notifications failed: {type(exc).__name__}: {exc}", flush=True)
        finally:
            db.close()


# ---------------------------------------------------------------------------------------------------------
# Admin: users, invitations, alert rules, servers, the install itself

INVITE_TTL = timedelta(days=7)


class UserUpdateRequest(BaseModel):
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    role: Literal["admin", "user"] = "user"


class ServerUpdateRequest(BaseModel):
    hostname: str | None = Field(default=None, max_length=255)
    check_interval_seconds: int | None = None


def _admin_users(db) -> dict:
    owner = _owner_id(db)
    now = datetime.now(timezone.utc)
    users = []
    for account in db.query(User).order_by(User.id):
        users.append({"id": account.id, "name": account.name, "email": account.email, "role": account.role, "role_name": "Owner" if account.id == owner else "Admin" if account.role == "admin" else "Viewer",
                      "is_active": account.is_active, "two_factor": account.totp_enabled, "avatar_color": account.avatar_color, "sessions": len(list_user_sessions(redis_client, account.id, None)),
                      "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else None, "created_at": account.created_at.isoformat()})
    invites = [{"id": invite.id, "email": invite.email, "role": invite.role, "created_at": invite.created_at.isoformat(), "expires_at": invite.expires_at.isoformat()}
               for invite in db.query(Invite).filter(Invite.used_at.is_(None), Invite.expires_at > now).order_by(Invite.created_at)]
    return {"users": users, "invites": invites, "require_2fa_for_everyone": require_two_factor_for_everyone(db), "require_2fa_for_admins": require_admin_two_factor(db), "secrets_configured": secrets_box.available()}


@app.get("/admin/users")
def admin_users(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    return _admin_users(db)


@app.patch("/admin/users/{user_id}")
def admin_update_user(user_id: PathId, payload: UserUpdateRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Change someone's role or switch their account off. The owner can't be changed, and nobody changes themselves
    here, so an install can't be left without an admin."""
    admin = _require_admin(session_id, db)
    account = db.get(User, user_id)
    if not account:
        raise HTTPException(status_code=404, detail="User not found.")
    if account.id == _owner_id(db):
        raise HTTPException(status_code=403, detail="The owner's account can't be changed here.")
    if account.id == admin["id"]:
        raise HTTPException(status_code=403, detail="You can't change your own role or switch yourself off.")
    changes = []
    if payload.role is not None and payload.role != account.role:
        account.role = payload.role
        changes.append(f"role to {payload.role}")
    if payload.is_active is not None and payload.is_active != account.is_active:
        account.is_active = payload.is_active
        changes.append("enabled" if payload.is_active else "disabled")
    db.commit()
    if changes:
        # A role in a live session would outlast the change, and a disabled account must be out at once.
        revoke_other_sessions(redis_client, account.id, None)
        record_audit_log(db, user_id=admin["id"], level="WARNING", message=f"Changed {account.email}: {', '.join(changes)}", source="admin")
    return _admin_users(db)


@app.post("/admin/users/{user_id}/sign-out")
def admin_sign_out_user(user_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    admin = _require_admin(session_id, db)
    account = db.get(User, user_id)
    if not account:
        raise HTTPException(status_code=404, detail="User not found.")
    session = get_session(redis_client, session_id)
    signed_out = revoke_other_sessions(redis_client, account.id, session["public_id"] if account.id == admin["id"] and session else None)
    record_audit_log(db, user_id=admin["id"], level="WARNING", message=f"Signed {account.email} out everywhere", source="admin")
    return {"signed_out": signed_out}


@app.post("/admin/invites")
def admin_create_invite(payload: InviteRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """An invitation link for one email address. Infradeck sends no email, so the admin passes the link on; it is
    shown once, works once and expires in a week."""
    admin = _require_admin(session_id, db)
    email = str(payload.email).strip().lower()
    if db.query(User.id).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    db.query(Invite).filter(Invite.email == email, Invite.used_at.is_(None)).delete(synchronize_session=False)
    if db.query(Invite).filter(Invite.used_at.is_(None)).count() >= 50:
        raise HTTPException(status_code=400, detail="Too many invitations are waiting. Cancel some first.")
    token = secrets.token_urlsafe(32)
    invite = Invite(email=email, role=payload.role, token_hash=_hash_secret(token), created_by=admin["id"], expires_at=datetime.now(timezone.utc) + INVITE_TTL)
    db.add(invite)
    db.commit()
    record_audit_log(db, user_id=admin["id"], level="INFO", message=f"Invited {email} as {payload.role}", source="admin")
    return {"id": invite.id, "email": email, "role": payload.role, "token": token, "expires_at": invite.expires_at.isoformat()}


@app.delete("/admin/invites/{invite_id}")
def admin_cancel_invite(invite_id: PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    db.query(Invite).filter(Invite.id == invite_id).delete(synchronize_session=False)
    db.commit()
    return _admin_users(db)


# The rules that open incidents, as the Admin page lists them. ``when`` is a sentence with threshold names in braces;
# ``edit`` names the thresholds an admin can change on that row.
RULE_ROWS = [
    {"kind": "container:restart_loop", "when": "Container restarts ≥ {restart_loop_count}", "window": "{restart_loop_window_minutes} min", "severity": "CRITICAL", "edit": ["restart_loop_count", "restart_loop_window_minutes"]},
    {"kind": SERVER_OFFLINE_RULE, "when": f"Server misses {OFFLINE_AFTER_MISSED_CHECKS} check-ins in a row", "window": None, "severity": "CRITICAL", "edit": []},
    {"kind": "host:disk", "when": "Disk used > {disk_critical_pct}% (warning from {disk_warning_pct}%)", "window": "{sustained_checks} check-ins", "severity": "CRITICAL", "edit": ["disk_warning_pct", "disk_critical_pct", "sustained_checks"]},
    {"kind": "host:inodes", "when": "Inodes used > {inodes_critical_pct}% (warning from {inodes_warning_pct}%)", "window": "{sustained_checks} check-ins", "severity": "CRITICAL", "edit": ["inodes_warning_pct", "inodes_critical_pct", "sustained_checks"]},
    {"kind": "host:memory", "when": "Memory used > {memory_warning_pct}% (critical from {memory_critical_pct}%)", "window": "{sustained_checks} check-ins", "severity": "WARNING", "edit": ["memory_warning_pct", "memory_critical_pct", "sustained_checks"]},
    {"kind": "host:swap", "when": "Swap used > {swap_warning_pct}%", "window": "{sustained_checks} check-ins", "severity": "WARNING", "edit": ["swap_warning_pct", "sustained_checks"]},
    {"kind": "host:load", "when": "Load > {load_warning_per_core}× the cores (critical from {load_critical_per_core}×)", "window": "{sustained_checks} check-ins", "severity": "WARNING", "edit": ["load_warning_per_core", "load_critical_per_core", "sustained_checks"]},
    {"kind": "trend:memory", "when": "A container's memory climbs ≥ {leak_min_mb_per_hour} MB/h and {leak_min_growth_pct}% steadily", "window": "{leak_window_hours} h", "severity": "WARNING", "edit": ["leak_min_mb_per_hour", "leak_min_growth_pct", "leak_window_hours"]},
    {"kind": "container:unhealthy", "when": "A container's healthcheck fails", "window": None, "severity": "WARNING", "edit": []},
    {"kind": "container:exit", "when": "A container exits with an error or is killed for memory", "window": "{event_window_minutes} min", "severity": "WARNING", "edit": ["event_window_minutes"]},
    {"kind": "container:image", "when": "A container starts running a different image", "window": "{event_window_minutes} min", "severity": "INFO", "edit": ["event_window_minutes"]},
    {"kind": "container:removed", "when": "A container disappears from its server", "window": "{event_window_minutes} min", "severity": "INFO", "edit": ["event_window_minutes"]},
    {"kind": "network:port", "when": "New port reachable from other machines", "window": None, "severity": "WARNING", "edit": []},
    {"kind": "network:firewall", "when": "The firewall configuration is weakened", "window": None, "severity": "WARNING", "edit": []},
    {"kind": "system:oom", "when": "The kernel kills a process to free memory", "window": "{event_window_minutes} min", "severity": "WARNING", "edit": []},
    {"kind": "system:docker_unreachable", "when": "Docker stops answering", "window": None, "severity": "CRITICAL", "edit": []},
    {"kind": "system:docker_slow", "when": "Docker takes > {docker_slow_ms} ms to list containers", "window": "{sustained_checks} check-ins", "severity": "WARNING", "edit": ["docker_slow_ms", "sustained_checks"]},
    {"kind": "system:collect_slow", "when": "Collecting data takes > {collect_slow_pct}% of the check interval", "window": "{sustained_checks} check-ins", "severity": "WARNING", "edit": ["collect_slow_pct", "sustained_checks"]},
    {"kind": "system:ntp", "when": "The clock is not synchronized", "window": None, "severity": "WARNING", "edit": []},
    {"kind": "system:reboot_required", "when": "Reboot required for updates", "window": None, "severity": "INFO", "edit": []},
]
NOISY_FIRED = 20


@app.get("/admin/rules")
def admin_rules(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Every rule as a sentence, whether it is on, and how often it opened an incident in the last 30 days."""
    _require_admin(session_id, db)
    thresholds = get_alert_thresholds(db)
    disabled = get_disabled_rules(db)
    fired: dict[str, int] = {}
    for (rule_key,) in db.query(IncidentAlert.rule_key).filter(IncidentAlert.created_at >= datetime.now(timezone.utc) - timedelta(days=30)).limit(50_000):
        kind = incidents.rule_kind(rule_key)
        fired[kind] = fired.get(kind, 0) + 1
    fill = lambda text_value: text_value.format(**{key: f"{value:g}" for key, value in thresholds.items()}) if text_value else None  # noqa: E731
    return {"rules": [{"kind": row["kind"], "when": fill(row["when"]), "window": fill(row["window"]), "severity": row["severity"], "edit": row["edit"], "enabled": row["kind"] not in disabled,
                       "fired": fired.get(row["kind"], 0), "noisy": fired.get(row["kind"], 0) >= NOISY_FIRED} for row in RULE_ROWS],
            "thresholds": thresholds}


@app.patch("/monitoring/servers/{server_id}")
def update_server(server_id: PathId, payload: ServerUpdateRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Rename a server or change how often it checks in. A name given here stays, whatever the machine calls itself."""
    admin = _require_admin(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    if payload.hostname is not None and payload.hostname.strip() and _name_key(payload.hostname) != _name_key(server.hostname):
        name = _hostname_or_error(payload.hostname)
        if _hostname_in_use(db, name, excluding=server.id):
            raise HTTPException(status_code=409, detail=f"Another server is already called {name}.")
        record_audit_log(db, user_id=admin["id"], level="INFO", message=f"Renamed server {server.hostname} to {name}", source="admin")
        server.hostname, server.name_locked = name, True
    if payload.check_interval_seconds is not None:
        if payload.check_interval_seconds not in (15, 30, 60, 300, 900):
            raise HTTPException(status_code=400, detail="The check interval must be 15, 30, 60, 300 or 900 seconds.")
        server.check_interval_seconds = payload.check_interval_seconds
    db.commit()
    return {"id": server.id, "hostname": server.hostname, "check_interval_seconds": server.check_interval_seconds}


@app.get("/admin/system")
def admin_system(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Read-only facts about this install for the Admin page: version, database size, what the agent was used for."""
    _require_admin(session_id, db)
    now = datetime.now(timezone.utc)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    size = None
    if engine.dialect.name == "postgresql":
        size = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
    week = now - timedelta(days=7)
    return {
        "version": APP_VERSION, "database": {"engine": engine.dialect.name, "bytes": size,
                                             "host_rows": db.query(func.count(HostMetric.id)).scalar(), "host_rows_week": db.query(func.count(HostMetric.id)).filter(HostMetric.time >= week).scalar()},
        "retention_days": get_metrics_retention_days(db),
        "agent": {"provider": "OpenAI", "model": AGENT_MODEL, "key_configured": bool(configured_key()), "budget": agent_usage.budget(db), "chat_enabled": is_agent_chat_enabled(db),
                  "incidents_this_month": db.query(IncidentAlert).filter(IncidentAlert.created_at >= month).count(),
                  "chat_messages_this_month": db.query(ChatMessage).filter(ChatMessage.role == "user", ChatMessage.created_at >= month).count()},
        "smtp_configured": notify.smtp_configured(), "secrets_configured": secrets_box.available(),
    }


@app.get("/admin/agent/usage")
def admin_agent_usage(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """This month's model calls, tokens and estimated cost against the budget, per model and per day."""
    _require_admin(session_id, db)
    return {**agent_usage.month_summary(db), "model": AGENT_MODEL}


@app.get("/admin/agent/worker")
def admin_agent_worker(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Whether the agent worker is running, how many jobs wait, and the latest results."""
    _require_admin(session_id, db)
    try:
        return agent_jobs.status(redis_client)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Redis is not answering, so the agent's queue can't be read.") from exc


@app.post("/admin/agent/test")
def admin_agent_test(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Admin's "Test connection": the API key works and the configured model exists. Spends no tokens."""
    admin = _require_admin(session_id, db)
    enforce_limit(redis_client, scope="agent-test", subject=admin["id"], limit=10, window_seconds=600)
    try:
        result = check_provider_connection()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=agent_error_message(exc)) from exc
    return {"ok": True, **result}


@app.get("/")
def root():
    return {"message": "Infradeck API"}
