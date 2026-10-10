import json
import re
import secrets
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import or_

from . import secrets_box, totp
from .config import SESSION_TTL_SECONDS
from .models import AuditLog, RecoveryCode, User

# A "full" session can use everything. An "enrollment" session is what an admin gets when two-factor is
# required but not yet set up: it can only reach the two-factor setup and is refused everywhere else.
STAGE_FULL = "full"
STAGE_ENROLLMENT = "enrollment"
PENDING_TWO_FACTOR_TTL_SECONDS = 300
MAX_PENDING_ATTEMPTS = 5
_PUBLIC_ID = re.compile(r"[0-9a-f]{16}")
# Checked against when the account doesn't exist, so that unknown emails cost the same time as wrong passwords.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())

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


def _user_sessions_key(user_id: int) -> str:
    return f"homelab:user-sessions:{user_id}"


def _pending_key(token: str) -> str:
    return f"homelab:2fa-pending:{token}"


def _printable(text: str | None, limit: int) -> str | None:
    cleaned = "".join(char if char.isprintable() else "?" for char in (text or "").strip())[:limit].strip()
    return cleaned or None


def create_session(redis_client, user: User, *, stage: str = STAGE_FULL, ip: str | None = None, user_agent: str | None = None) -> tuple[str, str]:
    """Open a session and index it under its user. Returns (session_id, public_id).

    The session ID is the secret in the cookie and is never shown to a client. The public ID is a separate
    random value used only to list and revoke a user's own sessions.
    """
    session_id, public_id = secrets.token_urlsafe(32), secrets.token_hex(8)
    _store_session(redis_client, session_id, {
        "user_id": user.id, "email": user.email, "name": user.name, "role": user.role, "stage": stage, "public_id": public_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "ip": _printable(ip, 64), "user_agent": _printable(user_agent, 120),
    })
    index = _user_sessions_key(user.id)
    redis_client.hset(index, public_id, session_id)
    redis_client.expire(index, SESSION_TTL_SECONDS)
    return session_id, public_id


def get_session(redis_client, session_id: str | None) -> dict | None:
    """The session behind a cookie, whatever its stage, or None. Sessions from before stages existed count as full."""
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
        "id": data.get("user_id"), "name": data.get("name"), "email": data.get("email"), "role": data.get("role"),
        "stage": data.get("stage", STAGE_FULL), "public_id": data.get("public_id"),
        "created_at": data.get("created_at"), "ip": data.get("ip"), "user_agent": data.get("user_agent"),
    }


def get_user_from_session(redis_client, session_id: str | None, *, allow_limited: bool = False):
    """The signed-in user, or None. Limited (enrollment) sessions are refused unless the caller opts in."""
    session = get_session(redis_client, session_id)
    if not session or (session["stage"] != STAGE_FULL and not allow_limited):
        return None
    return session


def delete_session(redis_client, session_id: str | None) -> bool:
    """End a session and remove it from its user's index."""
    session = get_session(redis_client, session_id)
    if not session:
        return False
    if session["public_id"] and session["id"] is not None:
        redis_client.hdel(_user_sessions_key(session["id"]), session["public_id"])
    _delete_session(redis_client, session_id)
    return True


def list_user_sessions(redis_client, user_id: int, current_public_id: str | None) -> list[dict]:
    """A user's live sessions, newest first, by public ID only. Entries whose session has expired are dropped."""
    index, sessions = _user_sessions_key(user_id), []
    for public_id, session_id in redis_client.hgetall(index).items():
        session = get_session(redis_client, session_id)
        if not session or session["id"] != user_id or session["public_id"] != public_id:
            redis_client.hdel(index, public_id)
            continue
        sessions.append({"id": public_id, "created_at": session["created_at"], "ip": session["ip"], "user_agent": session["user_agent"],
                         "current": public_id == current_public_id, "limited": session["stage"] != STAGE_FULL})
    return sorted(sessions, key=lambda item: item["created_at"] or "", reverse=True)


def revoke_user_session(redis_client, user_id: int, public_id: str) -> bool:
    """Sign out one of the user's own sessions by public ID. Anything that isn't theirs is simply not found."""
    if not isinstance(public_id, str) or not _PUBLIC_ID.fullmatch(public_id):
        return False
    session_id = redis_client.hget(_user_sessions_key(user_id), public_id)
    session = get_session(redis_client, session_id)
    if not session or session["id"] != user_id:
        redis_client.hdel(_user_sessions_key(user_id), public_id)
        return False
    return delete_session(redis_client, session_id)


def revoke_other_sessions(redis_client, user_id: int, keep_public_id: str | None) -> int:
    """Sign out every session of the user except the one with ``keep_public_id`` (all of them if None)."""
    revoked = 0
    for public_id in list(redis_client.hgetall(_user_sessions_key(user_id))):
        if public_id != keep_public_id and revoke_user_session(redis_client, user_id, public_id):
            revoked += 1
    return revoked


def create_pending_two_factor(redis_client, user_id: int) -> str:
    """The short-lived token that carries a login between the password step and the code step."""
    token = secrets.token_urlsafe(32)
    redis_client.set(_pending_key(token), json.dumps({"user_id": user_id}), ex=PENDING_TWO_FACTOR_TTL_SECONDS)
    return token


def read_pending_two_factor(redis_client, token) -> int | None:
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return None
    raw = redis_client.get(_pending_key(token))
    try:
        return int(json.loads(raw)["user_id"]) if raw else None
    except (TypeError, ValueError, KeyError):
        return None


def count_pending_attempt(redis_client, token: str) -> bool:
    """Count a try against this pending login; after MAX_PENDING_ATTEMPTS the token is destroyed. False means no more tries."""
    key = f"homelab:2fa-attempts:{token}"
    attempts = redis_client.incr(key)
    if attempts == 1:
        redis_client.expire(key, PENDING_TWO_FACTOR_TTL_SECONDS)
    if attempts > MAX_PENDING_ATTEMPTS:
        delete_pending_two_factor(redis_client, token)
        return False
    return True


def delete_pending_two_factor(redis_client, token: str) -> None:
    redis_client.delete(_pending_key(token), f"homelab:2fa-attempts:{token}")


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
            }
        )
    return result


PASSWORD_MIN_LENGTH = 16
# bcrypt only uses the first 72 bytes (and refuses longer input), so longer passwords are rejected
# up front instead of being silently truncated or crashing.
PASSWORD_MAX_BYTES = 72


def validate_password(password: str) -> None:
    """New passwords must be long enough to resist guessing; existing accounts keep working as they are."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long. A few random words make a good passphrase.")
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_BYTES} bytes long.")


def register_user(db, redis_client, *, name: str, email: str, password: str, admin_requires_two_factor: bool = False,
                  ip: str | None = None, user_agent: str | None = None):
    normalized_name = name.strip()
    normalized_email = email.strip().lower()

    if not normalized_name:
        raise ValueError("Name is required.")
    if not normalized_email:
        raise ValueError("Email is required.")
    validate_password(password)

    existing = db.query(User).filter(User.email == normalized_email).first()
    if existing:
        raise ValueError("An account with that email already exists.")

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    # The first account sets up the install, so it becomes the admin.
    is_first_user = db.query(User).count() == 0
    user = User(
        name=normalized_name,
        email=normalized_email,
        password_hash=password_hash,
        role="admin" if is_first_user else "user",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    record_audit_log(db, user_id=user.id, level="INFO", message="New account registered", source="auth")

    stage = STAGE_ENROLLMENT if user.role == "admin" and admin_requires_two_factor else STAGE_FULL
    session_id, _public_id = create_session(redis_client, user, stage=stage, ip=ip, user_agent=user_agent)

    return {"session_id": session_id, "user": _serialize_user(user), "setup_required": stage == STAGE_ENROLLMENT}


def verify_password(user: User, password: str) -> bool:
    # Anything over bcrypt's limit can't be a valid password here; bcrypt would raise with a different message.
    if not isinstance(password, str) or len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8"))


def verify_credentials(db, *, email: str, password: str) -> User:
    """The active account for a correct email and password. Raises one generic error for every kind of failure."""
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not user.is_active:
        # Do the same work as for a wrong password so the response time doesn't reveal which emails exist.
        bcrypt.checkpw(b"not-a-real-password", _DUMMY_PASSWORD_HASH)
        raise ValueError("Invalid email or password.")
    if not verify_password(user, password):
        raise ValueError("Invalid email or password.")
    return user


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_second_factor(db, user: User, code) -> str | None:
    """"totp" or "recovery" if the code is valid for this user, else None. A valid code is consumed.

    TOTP codes must belong to a time step newer than the last accepted one (no replay) and the update is done
    atomically, so two simultaneous attempts with the same code can't both succeed. Recovery codes work once.
    Raises secrets_box.SecretsUnavailable if the stored secret can't be decrypted.
    """
    if totp.normalize_code(code) is not None:
        if not user.totp_enabled or not user.totp_secret_enc:
            return None
        step = totp.verify(secrets_box.decrypt(user.totp_secret_enc), code, user.totp_last_step)
        if step is None:
            return None
        claimed = db.query(User).filter(User.id == user.id, or_(User.totp_last_step.is_(None), User.totp_last_step < step)).update({"totp_last_step": step}, synchronize_session=False)
        db.flush()
        if claimed != 1:
            return None
        user.totp_last_step = step
        return "totp"
    normalized = totp.normalize_recovery_code(code)
    if normalized is None:
        return None
    used = db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id, RecoveryCode.code_hash == totp.hash_recovery_code(normalized), RecoveryCode.used_at.is_(None)).update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)
    db.flush()
    return "recovery" if used == 1 else None


def recovery_codes_left(db, user: User) -> int:
    return db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)).count()


def issue_recovery_codes(db, user: User) -> list[str]:
    """Replace the user's recovery codes with a fresh set and return the plain codes, which are never stored."""
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete(synchronize_session=False)
    codes = totp.generate_recovery_codes()
    db.add_all([RecoveryCode(user_id=user.id, code_hash=totp.hash_recovery_code(code)) for code in codes])
    return codes


def reset_two_factor(db, redis_client, user: User) -> None:
    """Turn two-factor off for an account and sign it out everywhere (the command-line recovery path)."""
    user.totp_enabled, user.totp_secret_enc, user.totp_last_step = False, None, None
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    revoke_other_sessions(redis_client, user.id, None)
    record_audit_log(db, user_id=user.id, level="WARNING", message="Two-factor was reset from the command line", source="auth")


def logout_user(redis_client, session_id: str | None):
    return delete_session(redis_client, session_id)
