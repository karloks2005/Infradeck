from __future__ import annotations

from typing import Annotated

import redis
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from .auth import authenticate_user, ensure_default_user, get_recent_logs, get_user_from_session, logout_user, record_audit_log, register_user
from .db import SessionLocal, check_database, get_db
from .models import AuditLog, Base, User

from .db import engine
from .config import (
    LOGIN_RATE_LIMIT,
    LOGIN_RATE_WINDOW_SECONDS,
    REDIS_URL,
    REGISTER_RATE_LIMIT,
    REGISTER_RATE_WINDOW_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_SECONDS,
)
from .rate_limit import enforce_login_limit, enforce_register_limit, reset_limits
from ..agent.service import send_agent_message


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


redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="Homelab Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://localhost:5176"],
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
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS command TEXT"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS command_reason TEXT"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS command_risk VARCHAR(50)"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS approval_status VARCHAR(50)"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS exit_code INTEGER"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS stdout TEXT"))
            connection.execute(text("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS stderr TEXT"))
    db = SessionLocal()
    try:
        ensure_default_user(db)
    finally:
        db.close()
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


@app.post("/auth/register")
def auth_register(payload: RegisterRequest, request: Request, response: Response, db=Depends(get_db)):
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

    return {
        "total_users": total_users,
        "total_logs": total_logs,
        "error_logs": error_logs,
        "uptime": "99.97%",
        "commands_run": 1284,
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
        command=payload.get("command"),
        command_reason=payload.get("command_reason"),
        command_risk=payload.get("command_risk"),
        approval_status=payload.get("approval_status"),
        exit_code=payload.get("exit_code"),
        stdout=payload.get("stdout"),
        stderr=payload.get("stderr"),
    )


@app.post("/agent/chat")
def agent_chat(payload: AgentChatRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required.")

    try:
        response_text = send_agent_message(
            user["id"],
            payload.conversation_id,
            message,
            command_logger=lambda event: record_audit_log(
                db,
                user_id=user["id"],
                source="command",
                task_title=message[:255],
                steps=event.get("steps", []),
                conversation_id=payload.conversation_id,
                event_type=event.get("event_type"),
                message=event.get("message", "Command event"),
                level=event.get("level", "INFO"),
                command=event.get("command"),
                command_reason=event.get("reason"),
                command_risk=event.get("risk"),
                approval_status=event.get("approval_status"),
                exit_code=event.get("exit_code"),
                stdout=event.get("stdout"),
                stderr=event.get("stderr"),
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Agent is unavailable.") from exc

    command_lines = [line.removeprefix("[COMMAND]:").strip() for line in response_text.splitlines() if line.startswith("[COMMAND]:")]
    steps = ["Received the task and evaluated the request.", "Generated a structured response for the requested task."]
    if command_lines:
        steps.append(f"Prepared {len(command_lines)} command(s) for review.")

    record_audit_log(
        db,
        user_id=user["id"],
        level="INFO",
        message=response_text.splitlines()[0][:255] if response_text.splitlines() else "Agent task completed",
        source="homie",
        task_title=message[:255],
        steps=steps,
    )
    return {"message": response_text}


@app.get("/")
def root():
    return {"message": "Homelab Agent API"}
