from __future__ import annotations

from typing import Annotated

import redis
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from .auth import authenticate_user, get_recent_logs, get_user_from_session, logout_user, record_audit_log, register_user
from .db import SessionLocal, check_database, get_db
from .models import AuditLog, Base, User

from .db import engine
from .config import REDIS_URL


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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
def auth_register(payload: RegisterRequest, db=Depends(get_db)):
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

    return result


@app.post("/auth/login")
def auth_login(payload: LoginRequest, db=Depends(get_db)):
    try:
        result = authenticate_user(
            db,
            redis_client,
            email=str(payload.email),
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return result


@app.get("/auth/me")
def auth_me(
    session_id: Annotated[str | None, Header(alias="x-session-id")] = None,
    db=Depends(get_db),
):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    return {"user": user}


@app.post("/auth/logout")
def auth_logout(
    session_id: Annotated[str | None, Header(alias="x-session-id")] = None,
):
    if logout_user(redis_client, session_id):
        return {"status": "ok"}
    raise HTTPException(status_code=400, detail="No session id supplied.")


@app.get("/dashboard/summary")
def dashboard_summary(
    session_id: Annotated[str | None, Header(alias="x-session-id")] = None,
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
    session_id: Annotated[str | None, Header(alias="x-session-id")] = None,
    db=Depends(get_db),
):
    user = get_user_from_session(redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")

    return {"logs": get_recent_logs(db, limit=20)}


@app.post("/logs")
def create_log(
    payload: dict,
    session_id: Annotated[str | None, Header(alias="x-session-id")] = None,
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
    )


@app.get("/")
def root():
    return {"message": "Homelab Agent API"}
