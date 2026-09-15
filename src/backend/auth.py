import json
import uuid

import bcrypt

from .config import SESSION_TTL_SECONDS
from .models import AuditLog, User

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
    redis_client.set(key, value, ex=SESSION_TTL_SECONDS)


def _read_session(redis_client, session_id: str):
    key = _session_key(session_id)
    return redis_client.get(key)


def _delete_session(redis_client, session_id: str):
    key = _session_key(session_id)
    redis_client.delete(key)


def record_audit_log(
    db,
    *,
    user_id: int | None,
    level: str,
    message: str,
    source: str = "system",
    task_title: str | None = None,
    steps: list[str] | None = None,
    conversation_id: str | None = None,
    event_type: str | None = None,
    command: str | None = None,
    command_reason: str | None = None,
    command_risk: str | None = None,
    approval_status: str | None = None,
    exit_code: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
):
    log_entry = AuditLog(
        user_id=user_id,
        level=level.upper(),
        message=message,
        source=source,
        task_title=task_title,
        steps=json.dumps(steps or []),
        conversation_id=conversation_id,
        event_type=event_type,
        command=command,
        command_reason=command_reason,
        command_risk=command_risk,
        approval_status=approval_status,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
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
        "task_title": log_entry.task_title,
        "steps": steps or [],
        "created_at": log_entry.created_at.isoformat(),
        "conversation_id": log_entry.conversation_id,
        "event_type": log_entry.event_type,
        "command": log_entry.command,
        "command_reason": log_entry.command_reason,
        "command_risk": log_entry.command_risk,
        "approval_status": log_entry.approval_status,
        "exit_code": log_entry.exit_code,
        "stdout": log_entry.stdout,
        "stderr": log_entry.stderr,
    }


def get_recent_logs(
    db,
    *,
    limit: int = 20,
    user_id: int | None = None,
    conversation_id: str | None = None,
):
    query = db.query(AuditLog).filter(AuditLog.source != "auth")
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    if conversation_id:
        query = query.filter(AuditLog.conversation_id == conversation_id)
    logs = (
        query
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for log in logs:
        try:
            steps = json.loads(log.steps or "[]")
        except (TypeError, ValueError):
            steps = []
        result.append(
            {
                "id": log.id,
                "user_id": log.user_id,
                "level": log.level,
                "message": log.message,
                "source": log.source,
                "task_title": log.task_title or log.message,
                "steps": steps if isinstance(steps, list) else [],
                "created_at": log.created_at.isoformat(),
                "conversation_id": log.conversation_id,
                "event_type": log.event_type,
                "command": log.command,
                "command_reason": log.command_reason,
                "command_risk": log.command_risk,
                "approval_status": log.approval_status,
                "exit_code": log.exit_code,
                "stdout": log.stdout,
                "stderr": log.stderr,
            }
        )
    return result


def ensure_default_user(db):
    email = "admin@example.com"
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user

    user = User(
        name="Admin User",
        email=email,
        password_hash=bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8"),
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


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

    if not user or not user.is_active:
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
