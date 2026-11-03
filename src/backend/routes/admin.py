"""Admin page endpoints: users and invitations, the alert-rules overview, install facts, and the incident agent's
usage/worker/test. Rule data (RULE_ROWS) and the settings helpers stay in main; this module is the HTTP layer."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException
from sqlalchemy import func, text

from .. import incidents, main, notify, secrets_box
from ...agent import jobs as agent_jobs, settings as agent_settings, usage as agent_usage
from ...agent.providers import configured_key
from ..config import AGENT_MODEL, APP_VERSION, SESSION_COOKIE_NAME
from ..db import engine, get_db
from ..models import ChatMessage, HostMetric, IncidentAlert, Invite, Server, User

router = APIRouter()


def _admin_users(db) -> dict:
    owner = main._owner_id(db)
    now = datetime.now(timezone.utc)
    users = []
    for account in db.query(User).order_by(User.id):
        users.append({"id": account.id, "name": account.name, "email": account.email, "role": account.role, "role_name": "Owner" if account.id == owner else "Admin" if account.role == "admin" else "Viewer",
                      "is_active": account.is_active, "two_factor": account.totp_enabled, "avatar_color": account.avatar_color, "sessions": len(main.list_user_sessions(main.redis_client, account.id, None)),
                      "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else None, "created_at": account.created_at.isoformat(), "agent_chat_enabled": account.agent_chat_enabled})
    invites = [{"id": invite.id, "email": invite.email, "role": invite.role, "created_at": invite.created_at.isoformat(), "expires_at": invite.expires_at.isoformat()}
               for invite in db.query(Invite).filter(Invite.used_at.is_(None), Invite.expires_at > now).order_by(Invite.created_at)]
    return {"users": users, "invites": invites, "require_2fa_for_everyone": main.require_two_factor_for_everyone(db), "require_2fa_for_admins": main.require_admin_two_factor(db), "secrets_configured": secrets_box.available(),
            "agent_chat_access": agent_settings.chat_access(db)}

@router.get("/admin/users")
def admin_users(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    return _admin_users(db)

@router.patch("/admin/users/{user_id}")
def admin_update_user(user_id: main.PathId, payload: main.UserUpdateRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Change someone's role or switch their account off. The owner can't be changed, and nobody changes themselves
    here, so an install can't be left without an admin."""
    admin = main._require_admin(session_id, db)
    account = db.get(User, user_id)
    if not account:
        raise HTTPException(status_code=404, detail="User not found.")
    if account.id == main._owner_id(db):
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
    if payload.agent_chat_enabled is not None and payload.agent_chat_enabled != account.agent_chat_enabled:
        account.agent_chat_enabled = payload.agent_chat_enabled
        main.record_audit_log(db, user_id=admin["id"], level="INFO", message=f"{'Allowed' if payload.agent_chat_enabled else 'Stopped'} agent chat for {account.email}", source="admin")
    db.commit()
    if changes:
        # A role in a live session would outlast the change, and a disabled account must be out at once.
        main.revoke_other_sessions(main.redis_client, account.id, None)
        main.record_audit_log(db, user_id=admin["id"], level="WARNING", message=f"Changed {account.email}: {', '.join(changes)}", source="admin")
    return _admin_users(db)

@router.post("/admin/users/{user_id}/sign-out")
def admin_sign_out_user(user_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    admin = main._require_admin(session_id, db)
    account = db.get(User, user_id)
    if not account:
        raise HTTPException(status_code=404, detail="User not found.")
    session = main.get_session(main.redis_client, session_id)
    signed_out = main.revoke_other_sessions(main.redis_client, account.id, session["public_id"] if account.id == admin["id"] and session else None)
    main.record_audit_log(db, user_id=admin["id"], level="WARNING", message=f"Signed {account.email} out everywhere", source="admin")
    return {"signed_out": signed_out}

@router.post("/admin/invites")
def admin_create_invite(payload: main.InviteRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """An invitation link for one email address. Infradeck sends no email, so the admin passes the link on; it is
    shown once, works once and expires in a week."""
    admin = main._require_admin(session_id, db)
    email = str(payload.email).strip().lower()
    if db.query(User.id).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    db.query(Invite).filter(Invite.email == email, Invite.used_at.is_(None)).delete(synchronize_session=False)
    if db.query(Invite).filter(Invite.used_at.is_(None)).count() >= 50:
        raise HTTPException(status_code=400, detail="Too many invitations are waiting. Cancel some first.")
    token = secrets.token_urlsafe(32)
    invite = Invite(email=email, role=payload.role, token_hash=main._hash_secret(token), created_by=admin["id"], expires_at=datetime.now(timezone.utc) + main.INVITE_TTL)
    db.add(invite)
    db.commit()
    main.record_audit_log(db, user_id=admin["id"], level="INFO", message=f"Invited {email} as {payload.role}", source="admin")
    return {"id": invite.id, "email": email, "role": payload.role, "token": token, "expires_at": invite.expires_at.isoformat()}

@router.delete("/admin/invites/{invite_id}")
def admin_cancel_invite(invite_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    db.query(Invite).filter(Invite.id == invite_id).delete(synchronize_session=False)
    db.commit()
    return _admin_users(db)

@router.get("/admin/rules")
def admin_rules(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Every rule as a sentence, whether it is on, and how often it opened an incident in the last 30 days."""
    main._require_admin(session_id, db)
    thresholds = main.get_alert_thresholds(db)
    disabled = main.get_disabled_rules(db)
    fired: dict[str, int] = {}
    for (rule_key,) in db.query(IncidentAlert.rule_key).filter(IncidentAlert.created_at >= datetime.now(timezone.utc) - timedelta(days=30)).limit(50_000):
        kind = incidents.rule_kind(rule_key)
        fired[kind] = fired.get(kind, 0) + 1
    fill = lambda text_value: text_value.format(**{key: f"{value:g}" for key, value in thresholds.items()}) if text_value else None  # noqa: E731
    return {"rules": [{"kind": row["kind"], "when": fill(row["when"]), "window": fill(row["window"]), "severity": row["severity"], "edit": row["edit"], "enabled": row["kind"] not in disabled,
                       "fired": fired.get(row["kind"], 0), "noisy": fired.get(row["kind"], 0) >= main.NOISY_FIRED} for row in main.RULE_ROWS],
            "thresholds": thresholds}

@router.get("/admin/system")
def admin_system(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Read-only facts about this install for the Admin page: version, database size, what the agent was used for."""
    main._require_admin(session_id, db)
    now = datetime.now(timezone.utc)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    size = None
    if engine.dialect.name == "postgresql":
        size = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
    week = now - timedelta(days=7)
    return {
        "version": APP_VERSION, "database": {"engine": engine.dialect.name, "bytes": size,
                                             "host_rows": db.query(func.count(HostMetric.id)).scalar(), "host_rows_week": db.query(func.count(HostMetric.id)).filter(HostMetric.time >= week).scalar()},
        "history_retention_days": main.retention.history_days(db),
        "agent": {"provider": "OpenAI", "model": AGENT_MODEL, "key_configured": bool(configured_key()), "budget": agent_usage.budget(db), "chat_enabled": main.is_agent_chat_enabled(db),
                  "incidents_this_month": db.query(IncidentAlert).filter(IncidentAlert.created_at >= month).count(),
                  "chat_messages_this_month": db.query(ChatMessage).filter(ChatMessage.role == "user", ChatMessage.created_at >= month).count()},
        "smtp_configured": notify.smtp_configured(), "secrets_configured": secrets_box.available(),
    }

@router.get("/admin/agent/usage")
def admin_agent_usage(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """This month's model calls, tokens and estimated cost against the budget, per model and per day."""
    main._require_admin(session_id, db)
    return {**agent_usage.month_summary(db), "model": AGENT_MODEL}

@router.get("/admin/agent/worker")
def admin_agent_worker(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Whether the agent worker is running, how many jobs wait, and the latest results."""
    main._require_admin(session_id, db)
    try:
        return agent_jobs.status(main.redis_client)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Redis is not answering, so the agent's queue can't be read.") from exc

@router.post("/admin/agent/test")
def admin_agent_test(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Admin's "Test connection": the API key works and the configured model exists. Spends no tokens."""
    admin = main._require_admin(session_id, db)
    main.enforce_limit(main.redis_client, scope="agent-test", subject=admin["id"], limit=10, window_seconds=600)
    try:
        result = main.check_provider_connection()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=main.agent_error_message(exc)) from exc
    return {"ok": True, **result}
