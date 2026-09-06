import json
import uuid

import bcrypt

from .config import SESSION_TTL_SECONDS
from .models import AuditLog, User

SESSION_FALLBACK = {}


def _serialize_user(user: User):
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
    }


def _session_key(session_id: str) -> str:
    return f"homelab:session:{session_id}"


def _store_session(redis_client, session_id: str, payload: dict):
    key = _session_key(session_id)
    value = json.dumps(payload)
    try:
        redis_client.set(key, value, ex=SESSION_TTL_SECONDS)
    except Exception:
        SESSION_FALLBACK[key] = value


def _read_session(redis_client, session_id: str):
    key = _session_key(session_id)
    try:
        value = redis_client.get(key)
        if value is not None:
            return value
    except Exception:
        pass

    return SESSION_FALLBACK.get(key)


def _delete_session(redis_client, session_id: str):
    key = _session_key(session_id)
    try:
        redis_client.delete(key)
    except Exception:
        pass
    SESSION_FALLBACK.pop(key, None)


def record_audit_log(db, *, user_id: int | None, level: str, message: str, source: str = "system"):
    log_entry = AuditLog(
        user_id=user_id,
        level=level.upper(),
        message=message,
        source=source,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return {
        "id": log_entry.id,
        "user_id": log_entry.user_id,
        "level": log_entry.level,
        "message": log_entry.message,
        "source": log_entry.source,
        "created_at": log_entry.created_at.isoformat(),
    }


def get_recent_logs(db, *, limit: int = 20):
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "level": log.level,
            "message": log.message,
            "source": log.source,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


def register_user(db, redis_client, *, name: str, email: str, password: str):
    normalized_name = name.strip()
    normalized_email = email.strip().lower()

    if not normalized_name:
        raise ValueError("Name is required.")
    if not normalized_email:
        raise ValueError("Email is required.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters long.")

    existing = db.query(User).filter(User.email == normalized_email).first()
    if existing:
        raise ValueError("An account with that email already exists.")

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user = User(
        name=normalized_name,
        email=normalized_email,
        password_hash=password_hash,
        role="user",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    record_audit_log(db, user_id=user.id, level="INFO", message="New account registered", source="auth")

    session_id = str(uuid.uuid4())
    _store_session(
        redis_client,
        session_id,
        {"user_id": user.id, "email": user.email, "name": user.name, "role": user.role},
    )

    return {"session_id": session_id, "user": _serialize_user(user)}


def authenticate_user(db, redis_client, *, email: str, password: str):
    normalized_email = email.strip().lower()
    user = db.query(User).filter(User.email == normalized_email).first()

    if not user:
        raise ValueError("Invalid email or password.")

    if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
        raise ValueError("Invalid email or password.")

    record_audit_log(db, user_id=user.id, level="INFO", message="User signed in", source="auth")

    session_id = str(uuid.uuid4())
    _store_session(
        redis_client,
        session_id,
        {"user_id": user.id, "email": user.email, "name": user.name, "role": user.role},
    )

    return {"session_id": session_id, "user": _serialize_user(user)}


def get_user_from_session(redis_client, session_id: str | None):
    if not session_id:
        return None

    raw = _read_session(redis_client, session_id)
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None

    return {
        "id": data.get("user_id"),
        "name": data.get("name"),
        "email": data.get("email"),
        "role": data.get("role"),
    }


def logout_user(redis_client, session_id: str | None):
    if not session_id:
        return False
    _delete_session(redis_client, session_id)
    return True
