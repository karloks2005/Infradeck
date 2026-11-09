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
from sqlalchemy import case, func

from .alerts import CONTAINER_RULE_PREFIX, CRITICAL, SERVER_OFFLINE_RULE, SEVERITIES, active_rule_keys, raise_alert, resolve_alerts, sync_alerts
import contextvars

from . import auth, availability, dashboard, history as history_service, incidents, notify, retention, rollups, secrets_box, stats, totp
from .auth import (
    STAGE_ENROLLMENT, STAGE_FULL, count_pending_attempt, create_pending_two_factor, create_session, delete_pending_two_factor, delete_session, get_recent_logs,
    get_session, get_user_from_session, hash_password, issue_recovery_codes, list_user_sessions, pending_two_factor_remember, read_pending_two_factor, session_lifetime, recovery_codes_left, record_audit_log,
    register_user, revoke_other_sessions, revoke_user_session, validate_password, verify_credentials, verify_password, verify_second_factor,
)
from .db import SessionLocal, check_database, get_db
from .models import ApiToken, Invite, NotificationChannel, AppSetting, AuditLog, ChatMessage, Check, ContainerMetric, ContainerRollup, Conversation, EnrollmentKey, FirewallState, HostMetric, HostRollup, IncidentAlert, JoinToken, ListenerState, MountMetric, MountRollup, PendingCollector, RecoveryCode, Server, SystemEvent, User

from .db import engine
from .migrate import upgrade_database
from .config import (
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
from ..agent import jobs as agent_jobs, settings as agent_settings, usage as agent_usage, verify as agent_verify
from ..agent.diagnosis import latest as latest_report, report_json
from ..agent.providers import NoApiKey, get_provider, key_configured
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


RESERVED_EVENT_TYPES = {"agent_tool_call", "health_check_completed", "credentials_replaced", "login", "logout"}


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
    history_retention_days: int | None = None
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
    agent_auto_diagnose: bool | None = None
    agent_show_diagnoses_to_users: bool | None = None
    agent_chat_access: Literal["admins_only", "per_user"] | None = None
    # How many look-ups the agent may make per diagnosis and per chat message (0: none).
    agent_tool_budget_diagnosis: int | None = None
    agent_tool_budget_chat: int | None = None
    # Turning the agent on, or letting more people use it, is confirmed with the password and a code (reauth.py).
    reauth_token: str | None = Field(default=None, max_length=100)
    code: str | None = Field(default=None, max_length=64)


def _starts_sending_data(db, payload: AdminSettingsRequest) -> bool:
    """Whether the change turns something on that sends data to the AI provider, or lets more people do so."""
    return ((payload.agent_chat_enabled is True and not is_agent_chat_enabled(db))
            or (payload.agent_auto_diagnose is True and not agent_settings.auto_diagnose(db))
            or (payload.agent_chat_access == "per_user" and agent_settings.chat_access(db) != "per_user")
            # Tools send more of a server's data (ports, firewall, history) than the evidence pack alone.
            or any(getattr(payload, f"agent_tool_budget_{kind}") and not agent_settings.tool_budget(db, kind) for kind in agent_settings.TOOL_BUDGETS))


class MetricsIngestRequest(BaseModel):
    server_id: int = Field(ge=1, le=2**31 - 1)
    server: dict = {}
    host: dict = {}
    containers: list[dict] = []


def agent_error_message(exc: Exception, detail: bool = False) -> str:
    """Turn provider failures into useful, non-sensitive text. Only ``detail=True`` (for admins, such as Test
    connection) adds the provider's own words, redacted; the chat gets the plain sentence and the details go to the
    server log and the agent's usage record (agent_error_detail)."""
    if isinstance(exc, NoApiKey):
        return "The agent has no API key. An admin adds one in Admin › Incident agent."
    raw_message = str(exc).strip()
    normalized = raw_message.lower()
    if any(marker in normalized for marker in ("401", "invalid_api_key", "incorrect api key", "api key not valid", "api_key_invalid", "authentication")):
        prefix = "The AI provider rejected the API key. Check it in Admin › Incident agent."
    elif any(marker in normalized for marker in ("model_not_found", "does not exist", "not_found_error", "404")):
        prefix = "The AI provider doesn't offer the configured model to this key. Check the model in Admin › Incident agent."
    elif any(marker in normalized for marker in ("resourceexhausted", "overloaded", "capacity", " 503", "status 503", " 500", " 502", " 529")):
        prefix = "The AI provider is currently overloaded or unavailable. Please try again shortly."
    elif any(marker in normalized for marker in ("rate limit", "rate_limit", "quota", " 429", "status 429", "insufficient_quota")):
        prefix = "The AI provider's rate limit or quota was reached. Please try again later."
    elif any(marker in normalized for marker in ("timeout", "timed out", "deadline exceeded")):
        prefix = "The AI provider did not respond before the request timed out. Please try again."
    elif any(marker in normalized for marker in (" 400", "status 400", "invalid_request")):
        prefix = "The AI provider rejected the request. An admin finds the reason in the backend's log, or with Test connection in Admin › Incident agent."
    else:
        prefix = "The AI provider could not complete this request."
    return f"{prefix} Provider error: {agent_error_detail(exc)}" if detail and raw_message else prefix


def agent_error_detail(exc: Exception) -> str:
    """The provider's own error, short and with keys redacted: errors can include request headers or diagnostics."""
    text = re.sub(r"(?i)(api[_ -]?key|authorization|token)\s*[:=]\s*\S+", r"\1=[redacted]", str(exc).strip())
    return re.sub(r"(AIza|sk-)[\w-]+", "[redacted]", text)[:300]


redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
# Jobs for the agent worker are published after the commit that opened or escalated an incident (see src/agent/jobs.py).
agent_jobs.redis_getter = lambda: redis_client
# Default-deny baseline (Option B, #154): every route requires a valid session unless its path is exempt — the
# sign-in/registration pages, the token-authenticated collector endpoints, and the API docs. Handlers keep their own
# checks (role, ownership, the user object); this only makes "a new endpoint forgot its auth" fail closed, not open.
SESSION_EXEMPT_PATHS = {
    "/", "/health", "/auth/config", "/auth/invite", "/auth/register", "/auth/login", "/auth/login/2fa", "/auth/logout",
    # These accept the limited enrolment session (two-factor setup, profile) and enforce it themselves with
    # _account_session(allow_limited=True); the baseline, which only knows full sessions, would wrongly block them.
    "/auth/me", "/auth/2fa/status", "/auth/2fa/setup", "/auth/2fa/enable",
    "/monitoring/collectors/register", "/monitoring/collectors/register/poll", "/monitoring/collectors/enroll",
    "/monitoring/metrics/ingest", "/monitoring/metrics/checks",
    "/openapi.json", "/docs", "/docs/oauth2-redirect",
}


def require_session(request: Request, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)) -> None:
    """Runs before every handler. For a path that isn't exempt it demands a valid session (or API token), raising 401
    otherwise; the handler then re-checks and reads the user. _current_user also accepts API tokens, so read tokens
    keep working. Exempt paths authenticate themselves (a sign-in step, a collector token) or are public."""
    if request.url.path not in SESSION_EXEMPT_PATHS:
        _current_user(session_id, db)


# The interactive API documentation is a map of every endpoint, so it is off unless ENABLE_API_DOCS=true.
app = FastAPI(title="Infradeck API", docs_url="/docs" if ENABLE_API_DOCS else None, redoc_url=None, openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
              dependencies=[Depends(require_session)])

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
    upgrade_database(engine)   # src/backend/migrations; a failing revision stops the start here
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


def end_sessions_without_two_factor(db) -> int:
    """After a two-factor rule is switched on: signs out every account it now applies to that hasn't set two-factor
    up, so they sign in again under the rule (and land on the setup). Returns how many sessions ended."""
    ended = 0
    for account in db.query(User).filter(User.totp_enabled.is_(False)):
        if two_factor_required_for(db, account.role):
            ended += revoke_other_sessions(redis_client, account.id, None)
    return ended


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
    return {"instance_name": get_instance_name(db), "history_retention_days": retention.history_days(db), "agent_chat_enabled": is_agent_chat_enabled(db), "alert_thresholds": get_alert_thresholds(db), "mount_thresholds": get_mount_thresholds(db), "require_2fa_for_admins": require_admin_two_factor(db), "secrets_configured": secrets_box.available(),
            "require_2fa_for_everyone": require_two_factor_for_everyone(db), "disabled_rules": sorted(get_disabled_rules(db)),
            "agent_prices": agent_usage.get_prices(db), "agent_monthly_cap_usd": agent_usage.get_cap(db), "agent_daily_chat_limit": agent_usage.get_daily_limit(db), "agent_auto_diagnose": agent_settings.auto_diagnose(db),
            "agent_show_diagnoses_to_users": agent_settings.show_diagnoses_to_users(db), "agent_chat_access": agent_settings.chat_access(db),
            **{f"agent_tool_budget_{kind}": agent_settings.tool_budget(db, kind) for kind in agent_settings.TOOL_BUDGETS}}


@app.get("/admin/settings")
def get_admin_settings(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _require_admin(session_id, db)
    return _admin_settings(db)


@app.put("/admin/settings")
def update_admin_settings(payload: AdminSettingsRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _require_admin(session_id, db)
    if _starts_sending_data(db, payload):
        reauth.require(db, user, payload.reauth_token, payload.code)
    changes = []
    two_factor_tightened = False
    days = payload.history_retention_days
    if days is not None and days != retention.history_days(db):
        if days not in retention.HISTORY_CHOICES:
            raise HTTPException(status_code=400, detail="History is kept for 1 year, 2 years or forever.")
        _set_setting(db, retention.HISTORY_KEY, str(days))
        changes.append(f"Changed history retention to {retention.describe(days)}")
    if payload.require_2fa_for_admins is not None and payload.require_2fa_for_admins != require_admin_two_factor(db):
        _set_setting(db, REQUIRE_ADMIN_2FA_KEY, "true" if payload.require_2fa_for_admins else "false")
        two_factor_tightened |= payload.require_2fa_for_admins
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
        two_factor_tightened |= payload.require_2fa_for_everyone
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
        for kind, (key, _default, _highest) in agent_settings.TOOL_BUDGETS.items():
            wanted = getattr(payload, f"agent_tool_budget_{kind}")
            if wanted is not None and agent_settings.validate_tool_budget(kind, wanted) != agent_settings.tool_budget(db, kind):
                _set_setting(db, key, str(wanted))
                changes.append(f"Set the agent's tool budget to {wanted} look-ups per {'diagnosis' if kind == 'diagnosis' else 'chat message'}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.agent_auto_diagnose is not None and payload.agent_auto_diagnose != agent_settings.auto_diagnose(db):
        _set_setting(db, agent_settings.AUTO_DIAGNOSE_KEY, "true" if payload.agent_auto_diagnose else "false")
        changes.append(f"{'Turned on' if payload.agent_auto_diagnose else 'Turned off'} automatic diagnosis by the agent")
    if payload.agent_show_diagnoses_to_users is not None and payload.agent_show_diagnoses_to_users != agent_settings.show_diagnoses_to_users(db):
        _set_setting(db, agent_settings.SHOW_TO_USERS_KEY, "true" if payload.agent_show_diagnoses_to_users else "false")
        changes.append(f"{'Showed' if payload.agent_show_diagnoses_to_users else 'Hid'} the agent's diagnoses {'to' if payload.agent_show_diagnoses_to_users else 'from'} users who aren't admins")
    if payload.agent_chat_access is not None and payload.agent_chat_access != agent_settings.chat_access(db):
        _set_setting(db, agent_settings.CHAT_ACCESS_KEY, payload.agent_chat_access)
        changes.append("Agent chat is now for admins only" if payload.agent_chat_access == "admins_only" else "Agent chat is now allowed per user")
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
    if two_factor_tightened:
        ended = end_sessions_without_two_factor(db)
        if ended:
            changes.append(f"Signed out {ended} session{'s' if ended != 1 else ''} of accounts that must now set up two-factor")
    for change in changes:
        record_audit_log(db, user_id=user["id"], level="INFO", message=change, source="admin")
    return _admin_settings(db)


@app.get("/agent/config")
def agent_config(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """What the Agent page needs to know about the agent; readable by every signed-in user."""
    user = _current_user(session_id, db)
    spend = agent_usage.budget(db)
    account = db.get(User, user["id"])
    return {"chat_enabled": is_agent_chat_enabled(db), "may_chat": bool(account) and agent_settings.may_chat(db, account), "key_configured": key_configured(db), "budget_paused": spend["state"] == "paused"}


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


SPARKLINE_MAX_MINUTES = 24 * 60
EVENTS_MAX_DAYS = 366


CONTAINER_DETAIL_FIELDS = (
    "status", "healthcheck_status", "restart_count", "cpu_pct", "memory_used_mb", "memory_limit_mb", "memory_pct",
    "net_rx_mb", "net_tx_mb", "block_read_mb", "block_write_mb", "pids",
)


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


PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PENDING_VISIBLE_SECONDS = 120
PENDING_RETENTION = timedelta(days=1)
MAX_PENDING_COLLECTORS = 100
MAX_OPEN_JOIN_TOKENS = 20
JOIN_TOKEN_PREFIX = "hlj_"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _server_for_machine(db, machine_id: str | None) -> Server | None:
    return db.query(Server).filter(Server.machine_id == machine_id).first() if machine_id else None


def _pending_for_user(pending_id: int, db) -> PendingCollector:
    pending = db.query(PendingCollector).filter(PendingCollector.id == pending_id, PendingCollector.status != "approved").first()
    if not pending:
        raise HTTPException(status_code=404, detail="Pending server not found.")
    return pending


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
        availability.roll(db)
    except Exception as exc:
        db.rollback()
        print(f"Rollups failed: {type(exc).__name__}: {exc}", flush=True)
        return False
    if not purge_due:
        return False
    retention.purge(db)
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


class CheckResultsRequest(BaseModel):
    server_id: int = Field(ge=1, le=2**31 - 1)
    results: list = Field(default=[], max_length=availability.MAX_RESULTS_PER_REPORT)
    tests: list = Field(default=[], max_length=availability.MAX_TESTS_QUEUED * 2)


def _enforce_report_spacing(server: Server) -> None:
    """At most one results report every couple of seconds per server (the collector sends one every 5 s at most)."""
    try:
        allowed = redis_client.set(f"infradeck:checks:{server.id}", "1", nx=True, ex=2)
    except Exception:
        return
    if not allowed:
        raise HTTPException(status_code=429, detail="Check results are arriving too often.", headers={"Retry-After": "2"})


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
    agent_lookups = db.query(AuditLog).filter(AuditLog.event_type == "agent_tool_call").count()

    return {
        "total_users": total_users,
        "total_logs": total_logs,
        "error_logs": error_logs,
        "uptime": "99.97%",
        "agent_lookups": agent_lookups,
    }


LOG_WRITE_LIMIT = 120
CHAT_MESSAGE_LIMIT = 30          # messages to the assistant per user per hour (each can cost the admin's OpenAI quota)
CONVERSATION_CREATE_LIMIT = 30   # new conversations per user per hour
MAX_CONVERSATIONS_PER_USER = 100


# ---------------------------------------------------------------------------------------------------------
# Incidents (the Agent page)

def _agent_note(db, alert: IncidentAlert, server: Server | None, from_report: bool = True) -> str | None:
    """The one line the Overview shows next to an incident, from its diagnosis. Remembered for a minute: the
    Overview is polled every few seconds and working it out reads samples."""
    if server is None:
        return None
    key = f"infradeck:incident-note:{alert.id}" + ("" if from_report else ":rules")
    try:
        cached = redis_client.get(key)
        if cached is not None:
            return (cached.decode() if isinstance(cached, bytes) else cached) or None
    except Exception:
        pass
    report = latest_report(db, alert.id) if from_report else None
    if report is not None and report.status == "ok" and report.summary:
        # The agent's own diagnosis, first sentence; the rule-based note when there is none.
        note = re.split(r"(?<=[.!?])\s", report.summary.strip(), maxsplit=1)[0][:160]
    else:
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


# ---------------------------------------------------------------------------------------------------------
# The account page: profile, preferences, API tokens, notification channels

_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,31}")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_CLOCK = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
# live_refresh: how often the Overview asks for new data, in seconds (null: the default built into the frontend).
DISPLAY_CHOICES = {"time_format": ("24h", "12h"), "sizes": ("GB", "GiB"), "theme": ("dark", "light", "system"), "density": ("comfortable", "compact"), "open_on": ("overview", "statistics", "agent"),
                   "live_refresh": (2, 3, 5, 10, 30, 60)}
DISPLAY_DEFAULTS = {"time_format": "24h", "sizes": "GB", "theme": "dark", "density": "comfortable", "open_on": "overview", "timezone": "", "live_refresh": None}


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


def _deliver(db, channel: NotificationChannel, owner: User, title: str, body: str, severity: str) -> bool:
    try:
        ok, status_text = notify.send(channel.kind, secrets_box.decrypt(channel.target_enc), title, body, severity, allow_private=owner.role == "admin")
    except secrets_box.SecretsUnavailable:
        ok, status_text = False, "SECRETS_KEY problem"
    channel.last_ok, channel.last_status, channel.last_checked_at = ok, status_text[:64], datetime.now(timezone.utc)
    db.commit()
    return ok


NOTIFY_WINDOW = timedelta(hours=24)
NOTIFY_MARK_SECONDS = 7 * 24 * 3600
NOTIFY_SCAN_LIMIT = 2000   # incidents looked at per round; only a flood of incidents reaches it


# A new warning or critical incident waits this long for the agent's diagnosis, so the notification can carry it.
DIAGNOSIS_WAIT = timedelta(minutes=3)


def _diagnosis_coming(db) -> bool:
    """Whether a diagnosis can be expected for a new incident: there is a key, automatic diagnosis is on, the budget
    isn't used up and a worker is running. When not, notifications don't wait for one."""
    if not key_configured(db) or not agent_settings.auto_diagnose(db) or agent_usage.budget(db)["state"] == "paused":
        return False
    try:
        return bool(redis_client.get(agent_jobs.HEARTBEAT))
    except Exception:
        return False


def _agent_summary(report) -> str | None:
    """The first two sentences of a report, for a notification."""
    if report is None or report.status != "ok" or not report.summary:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", report.summary.strip())
    text = " ".join(sentences[:2])
    return text if len(text) <= 320 else text[:319] + "…"


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
    # Critical first, then the latest change first, so a new incident is always looked at at once (#212). An
    # incident everyone was already told about costs one Redis look-up per user, so the cap only bounds a flood.
    recent = db.query(IncidentAlert).filter((IncidentAlert.created_at >= now - NOTIFY_WINDOW) | (IncidentAlert.resolved_at >= now - NOTIFY_WINDOW), IncidentAlert.severity != "INFO") \
        .order_by(case((IncidentAlert.severity == "CRITICAL", 0), else_=1), func.coalesce(IncidentAlert.resolved_at, IncidentAlert.created_at).desc()).limit(NOTIFY_SCAN_LIMIT).all()
    hostnames = dict(db.query(Server.id, Server.hostname).all())
    coming = None
    sent = 0
    for alert in recent:
        if incidents.is_muted(alert, now):
            continue
        event = "resolved" if alert.status == "resolved" else alert.severity.lower()
        if event not in notify.EVENTS:
            continue
        try:
            waiting = [user_id for user_id in by_user if not redis_client.exists(f"infradeck:notified:{alert.id}:{user_id}:{event}")]
        except Exception:   # without the markers everyone would be told again every few seconds
            return sent
        if not waiting:
            continue
        report = latest_report(db, alert.id)
        if event != "resolved" and report is None:
            coming = _diagnosis_coming(db) if coming is None else coming
            created = alert.created_at if alert.created_at.tzinfo else alert.created_at.replace(tzinfo=timezone.utc)
            if coming and now - created < DIAGNOSIS_WAIT:
                continue   # checked again in 15 s; after DIAGNOSIS_WAIT it goes out without the diagnosis
        summary = _agent_summary(report) if event != "resolved" else None
        for user_id in waiting:
            channels = by_user[user_id]
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
            if summary and agent_settings.sees_diagnoses(db, owners[user_id].role):
                body += f"\nAgent: {summary}"
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
    # Whether they may use the agent chat when chat access is "per user".
    agent_chat_enabled: bool | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    role: Literal["admin", "user"] = "user"


class ServerUpdateRequest(BaseModel):
    hostname: str | None = Field(default=None, max_length=255)
    check_interval_seconds: int | None = None


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
    {"kind": "check:down", "when": "An availability check fails its retries in a row", "window": None, "severity": "CRITICAL", "edit": []},
]
NOISY_FIRED = 20


@app.get("/")
def root():
    return {"message": "Infradeck API"}


# Routes split out of this file; imported last so they can reference this module's shared helpers (see routes/).
from . import reauth  # noqa: E402  (uses main, so it is imported once main is defined)
from .routes import account as account_routes, admin as admin_routes, auth as auth_routes, availability as availability_routes, chat as chat_routes, incidents as incidents_routes, monitoring as monitoring_routes, statistics as statistics_routes  # noqa: E402
app.include_router(availability_routes.router)
app.include_router(incidents_routes.router)
app.include_router(account_routes.router)
app.include_router(statistics_routes.router)
app.include_router(chat_routes.router)
app.include_router(admin_routes.router)
app.include_router(auth_routes.router)
app.include_router(monitoring_routes.router)
