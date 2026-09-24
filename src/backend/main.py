from __future__ import annotations

import re
import hashlib
import secrets
import hmac
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Annotated

import redis
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import inspect, text

from .auth import authenticate_user, get_recent_logs, get_user_from_session, logout_user, record_audit_log, register_user
from .db import SessionLocal, check_database, get_db
from .models import AuditLog, Base, ChatMessage, ContainerMetric, Conversation, EnrollmentKey, HostMetric, IncidentAlert, PendingCollector, Server, User

from .db import engine
from .config import (
    LOGIN_RATE_LIMIT,
    LOGIN_RATE_WINDOW_SECONDS,
    REDIS_URL,
    REGISTER_RATE_LIMIT,
    REGISTER_RATE_WINDOW_SECONDS,
    ALLOW_REGISTRATION,
    CORS_ORIGINS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_SECONDS,
)
from .rate_limit import enforce_ip_limit, enforce_login_limit, enforce_register_limit, reset_limits
from ..agent.service import delete_agent_conversation, send_agent_message


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


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
    if "servers" in inspect(engine).get_table_names():
        columns = {column["name"] for column in inspect(engine).get_columns("servers")}
        if "collector_token_hash" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE servers ADD COLUMN collector_token_hash VARCHAR(64)"))
        if "machine_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE servers ADD COLUMN machine_id VARCHAR(64)"))
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


def registration_open(db) -> bool:
    return ALLOW_REGISTRATION or db.query(User).count() == 0


@app.get("/auth/config")
def auth_config(db=Depends(get_db)):
    return {"registration_enabled": registration_open(db)}


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
    return {"user": user}


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


@app.get("/monitoring/overview")
def monitoring_overview(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _current_user(session_id, db)
    servers = db.query(Server).filter(Server.owner_id == user["id"]).order_by(Server.created_at.desc()).all()
    result = []
    for server in servers:
        metric = db.query(HostMetric).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc()).first()
        containers = db.query(ContainerMetric).filter(ContainerMetric.server_id == server.id).order_by(ContainerMetric.time.desc()).all()
        latest = {}
        for item in containers:
            latest.setdefault(item.container_id, item)
        result.append({
            "id": server.id, "hostname": server.hostname, "ip_address": server.ip_address, "os_info": server.os_info,
            "cpu_cores": server.cpu_cores, "total_ram_gb": server.total_ram_gb,
            "check_interval_seconds": server.check_interval_seconds,
            "last_seen_at": server.last_seen_at.isoformat() if server.last_seen_at else None,
            "host": ({key: getattr(metric, key) for key in ("time", "cpu_utilization_pct", "load_1m", "load_5m", "load_15m", "ram_used_gb", "ram_available_gb", "ram_pct", "swap_pct", "disk_usage_pct", "disk_inodes_pct", "net_bytes_sent_mb", "net_bytes_recv_mb", "system_uptime_seconds")} if metric else None),
            "containers": [{"container_id": c.container_id, "name": c.name, "image": c.image, "status": c.status, "restart_count": c.restart_count, "cpu_pct": c.cpu_pct, "memory_used_mb": c.memory_used_mb, "memory_pct": c.memory_pct, "healthcheck_status": c.healthcheck_status, "time": c.time.isoformat()} for c in latest.values()],
        })
    alerts = db.query(IncidentAlert).join(Server).filter(Server.owner_id == user["id"], IncidentAlert.status == "active").order_by(IncidentAlert.created_at.desc()).limit(20).all()
    return {"servers": result, "pending_collectors": visible_pending_collectors(db), "alerts": [{"id": a.id, "server_id": a.server_id, "severity": a.severity, "title": a.title, "description": a.description, "created_at": a.created_at.isoformat()} for a in alerts]}


CONTAINER_DETAIL_FIELDS = (
    "status", "healthcheck_status", "restart_count", "cpu_pct", "memory_used_mb", "memory_limit_mb", "memory_pct",
    "net_rx_mb", "net_tx_mb", "block_read_mb", "block_write_mb", "pids",
)


@app.get("/monitoring/servers/{server_id}/containers/{container_id}")
def container_detail(server_id: int, container_id: str, limit: int = 60, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id, Server.owner_id == user["id"]).first()
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


@app.get("/monitoring/enrollment-key")
def get_enrollment_key(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _current_user(session_id, db)
    key = db.query(EnrollmentKey).filter(EnrollmentKey.owner_id == user["id"]).first()
    return {"exists": key is not None, "created_at": key.created_at.isoformat() if key else None}


@app.post("/monitoring/enrollment-key")
def rotate_enrollment_key(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Create or replace the account's enrollment key. The plaintext is returned only once."""
    user = _current_user(session_id, db)
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
    user = _current_user(session_id, db)
    db.query(EnrollmentKey).filter(EnrollmentKey.owner_id == user["id"]).delete()
    db.commit()
    return {"status": "revoked"}


@app.post("/monitoring/collectors/enroll")
def enroll_collector(payload: EnrollRequest, db=Depends(get_db)):
    """Register (or re-register) a collector using the owner's enrollment key.

    Servers are matched on machine_id, so a collector that lost its saved
    credentials gets its existing server back with a fresh token.
    """
    key = db.query(EnrollmentKey).filter(EnrollmentKey.key_hash == _hash_secret(payload.enroll_key.strip())).first()
    if not key:
        raise HTTPException(status_code=401, detail="Enrollment key is invalid or has been rotated.")
    hostname = payload.hostname.strip()[:255]
    machine_id = (payload.machine_id or "").strip()[:64] or None
    if not hostname:
        raise HTTPException(status_code=400, detail="Hostname is required.")

    server = None
    if machine_id:
        server = db.query(Server).filter(Server.owner_id == key.owner_id, Server.machine_id == machine_id).first()
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
COLLECTOR_SOURCE = Path(__file__).resolve().parents[1] / "agent" / "collector.py"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.get("/collector.py", response_class=PlainTextResponse)
def collector_source():
    """Serve the collector so new machines can run it from a stock Python image."""
    return PlainTextResponse(COLLECTOR_SOURCE.read_text(), media_type="text/x-python")


@app.post("/monitoring/collectors/register")
def register_collector(payload: CollectorRegisterRequest, request: Request, db=Depends(get_db)):
    """Unauthenticated: a new collector asks to join and waits until a user clicks Add."""
    ip_address = _client_ip(request)
    enforce_ip_limit(redis_client, scope="collector-register", ip_address=ip_address, limit=30, window_seconds=3600)
    hostname = payload.hostname.strip()[:255]
    if not hostname:
        raise HTTPException(status_code=400, detail="Hostname is required.")
    machine_id = (payload.machine_id or "").strip()[:64] or None
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
    )
    db.add(pending)
    db.commit()
    return {"request_secret": secret, "pairing_code": pending.pairing_code, "poll_interval_seconds": 5}


@app.post("/monitoring/collectors/register/poll")
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


def _pending_for_user(pending_id: int, db) -> PendingCollector:
    pending = db.query(PendingCollector).filter(PendingCollector.id == pending_id, PendingCollector.status != "approved").first()
    if not pending:
        raise HTTPException(status_code=404, detail="Pending server not found.")
    return pending


@app.post("/monitoring/pending-collectors/{pending_id}/approve")
def approve_pending_collector(pending_id: int, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _current_user(session_id, db)
    pending = _pending_for_user(pending_id, db)
    server = None
    if pending.machine_id:
        server = db.query(Server).filter(Server.owner_id == user["id"], Server.machine_id == pending.machine_id).first()
    if not server:
        server = Server(owner_id=user["id"], hostname=pending.hostname, machine_id=pending.machine_id, ip_address=pending.ip_address, os_info=pending.os_info)
        db.add(server)
        db.flush()
    pending.status = "approved"
    pending.approved_server_id = server.id
    db.commit()
    return {"status": "approved", "server_id": server.id}


@app.post("/monitoring/pending-collectors/{pending_id}/dismiss")
def dismiss_pending_collector(pending_id: int, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _current_user(session_id, db)
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
            visible.append({"id": row.id, "hostname": row.hostname, "pairing_code": row.pairing_code, "ip_address": row.ip_address, "os_info": row.os_info, "status": row.status, "created_at": row.created_at.isoformat()})
    return visible


def purge_stale_pending(db, now: datetime | None = None) -> None:
    cutoff = (now or datetime.now(timezone.utc)) - PENDING_RETENTION
    db.query(PendingCollector).filter(PendingCollector.last_poll_at < cutoff).delete(synchronize_session=False)
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
        already_open = db.query(IncidentAlert).filter(IncidentAlert.server_id == server.id, IncidentAlert.status == "active", IncidentAlert.description == OFFLINE_ALERT_DESCRIPTION).first()
        if not already_open:
            db.add(IncidentAlert(server_id=server.id, severity="CRITICAL", title=_offline_title(server), description=OFFLINE_ALERT_DESCRIPTION))
            raised += 1
    db.commit()
    return raised


def _offline_watch_loop() -> None:
    while True:
        time.sleep(60)
        db = SessionLocal()
        try:
            mark_offline_servers(db)
            purge_stale_pending(db)
        except Exception as exc:  # keep the watcher alive through transient DB errors
            print(f"Offline watch failed: {type(exc).__name__}: {exc}", flush=True)
        finally:
            db.close()


@app.post("/monitoring/servers")
def add_monitored_server(payload: ServerRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _current_user(session_id, db)
    if not payload.hostname.strip() or not 15 <= payload.check_interval_seconds <= 86400:
        raise HTTPException(status_code=400, detail="Hostname is required and interval must be between 15 and 86400 seconds.")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    server = Server(owner_id=user["id"], hostname=payload.hostname.strip()[:255], ip_address=payload.ip_address, check_interval_seconds=payload.check_interval_seconds, collector_token_hash=token_hash)
    db.add(server); db.commit(); db.refresh(server)
    return {"id": server.id, "hostname": server.hostname, "check_interval_seconds": server.check_interval_seconds, "collector_token": token}


@app.delete("/monitoring/servers/{server_id}")
def delete_monitored_server(server_id: int, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id, Server.owner_id == user["id"]).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")

    db.query(HostMetric).filter(HostMetric.server_id == server.id).delete(synchronize_session=False)
    db.query(ContainerMetric).filter(ContainerMetric.server_id == server.id).delete(synchronize_session=False)
    db.query(IncidentAlert).filter(IncidentAlert.server_id == server.id).delete(synchronize_session=False)
    db.query(PendingCollector).filter(PendingCollector.approved_server_id == server.id).delete(synchronize_session=False)
    db.delete(server)
    db.commit()
    return {"status": "deleted", "server_id": server_id}


@app.patch("/monitoring/servers/{server_id}/interval")
def update_check_interval(server_id: int, payload: dict, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = _current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id, Server.owner_id == user["id"]).first()
    interval = payload.get("check_interval_seconds")
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    if not isinstance(interval, int) or not 15 <= interval <= 86400:
        raise HTTPException(status_code=400, detail="Interval must be between 15 and 86400 seconds.")
    server.check_interval_seconds = interval; db.commit()
    return {"id": server.id, "check_interval_seconds": server.check_interval_seconds}


@app.post("/monitoring/metrics/ingest")
def ingest_metrics(payload: MetricsIngestRequest, authorization: Annotated[str | None, Header()] = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    server = db.query(Server).filter(Server.id == payload.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else None
    if server.collector_token_hash and token_hash and hmac.compare_digest(server.collector_token_hash, token_hash):
        pass
    else:
        user = _current_user(session_id, db)
        if server.owner_id != user["id"]:
            raise HTTPException(status_code=404, detail="Server not found.")
    for field in ("hostname", "ip_address", "os_info", "cpu_cores", "total_ram_gb"):
        if field in payload.server and payload.server[field] is not None:
            setattr(server, field, payload.server[field])
    db.add(HostMetric(server_id=server.id, **{k: v for k, v in payload.host.items() if k in HostMetric.__table__.columns.keys() and k not in {"id", "server_id", "time"}}))
    for container in payload.containers:
        if container.get("container_id") and container.get("status"):
            allowed = {k: v for k, v in container.items() if k in ContainerMetric.__table__.columns.keys() and k not in {"id", "server_id", "time"}}
            db.add(ContainerMetric(server_id=server.id, **allowed))
            if str(container.get("status", "")).lower() in {"restarting", "unhealthy"}:
                db.add(IncidentAlert(server_id=server.id, severity="CRITICAL", title=f"Container {container.get('name', container['container_id'])} is {container['status']}", description="Automatski detektirano pri zaprimanju metrika."))
    server.last_seen_at = datetime.now(timezone.utc)
    db.query(IncidentAlert).filter(IncidentAlert.server_id == server.id, IncidentAlert.status == "active", IncidentAlert.description == OFFLINE_ALERT_DESCRIPTION).update({"status": "resolved"}, synchronize_session=False)
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
