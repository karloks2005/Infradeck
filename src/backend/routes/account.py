"""Account: profile, display and notification preferences, API tokens and notification channels. Shared helpers
(`_account_session`, `_preferences`, `_role_name`, `_deliver`) stay in main and are reached through it; the
channel/token payload shapers and `_own_channel` are only used here, so they live here."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException

from .. import main, notify, secrets_box
from ..config import SESSION_COOKIE_NAME
from ..db import get_db
from ..models import ApiToken, NotificationChannel, User

router = APIRouter()

MAX_TOKENS_PER_USER = 20


def _channel_payload(channel: NotificationChannel) -> dict:
    return {"id": channel.id, "kind": channel.kind, "display": channel.display, "last_ok": channel.last_ok, "last_status": channel.last_status,
            "last_checked_at": channel.last_checked_at.isoformat() if channel.last_checked_at else None}


def _token_payload(token: ApiToken) -> dict:
    return {"id": token.id, "name": token.name, "scope": token.scope, "hint": token.hint, "created_at": token.created_at.isoformat(),
            "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None, "expires_at": token.expires_at.isoformat() if token.expires_at else None}


@router.get("/account")
def get_account(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    return {
        "id": user.id, "name": user.name, "username": user.username, "email": user.email, "role": user.role, "role_name": main._role_name(db, user), "avatar_color": user.avatar_color,
        "created_at": user.created_at.isoformat(), "password_changed_at": user.password_changed_at.isoformat() if user.password_changed_at else None,
        "two_factor": {"enabled": user.totp_enabled, "available": secrets_box.available(), "required": main.two_factor_required_for(db, user.role), "recovery_codes_left": main.recovery_codes_left(db, user) if user.totp_enabled else 0},
        "preferences": main._preferences(user), "session_days": round(main.auth.REMEMBER_SESSION_TTL_SECONDS / 86400),
        "tokens": [_token_payload(token) for token in db.query(ApiToken).filter(ApiToken.user_id == user.id).order_by(ApiToken.created_at)],
        "channels": [_channel_payload(channel) for channel in db.query(NotificationChannel).filter(NotificationChannel.user_id == user.id).order_by(NotificationChannel.id)],
        "smtp_configured": notify.smtp_configured(), "secrets_configured": secrets_box.available(),
    }


@router.patch("/account/profile")
def update_profile(payload: main.ProfileRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    if payload.name is not None:
        name = main._printable(payload.name, 255)
        if not name:
            raise HTTPException(status_code=400, detail="The display name can't be empty.")
        user.name = name
    if payload.username is not None:
        username = payload.username.strip().lower()
        if username and not main._USERNAME.fullmatch(username):
            raise HTTPException(status_code=400, detail="A username is 2 to 32 lowercase letters, digits, dots, dashes or underscores.")
        if username and db.query(User.id).filter(User.username == username, User.id != user.id).first():
            raise HTTPException(status_code=409, detail="That username is taken.")
        user.username = username or None
    if payload.avatar_color is not None:
        if payload.avatar_color and not main._COLOR.fullmatch(payload.avatar_color):
            raise HTTPException(status_code=400, detail="The color must look like #A1B2C3.")
        user.avatar_color = payload.avatar_color or None
    db.commit()
    return get_account(session_id, db)


@router.put("/account/preferences")
def update_preferences(payload: main.PreferencesRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Display and notification choices. Everything is checked against a fixed list; unknown keys are dropped."""
    session, user = main._account_session(session_id, db)
    current = main._preferences(user)
    if payload.display is not None:
        for key, choices in main.DISPLAY_CHOICES.items():
            value = payload.display.get(key)
            if value in choices and not isinstance(value, bool):
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
        if not (isinstance(start, str) and isinstance(end, str) and main._CLOCK.fullmatch(start) and main._CLOCK.fullmatch(end)):
            raise HTTPException(status_code=400, detail="Quiet hours need a start and an end like 23:00.")
        current["quiet"] = {"enabled": payload.quiet.get("enabled") is True, "start": start, "end": end}
    user.preferences = main.json.dumps(current, separators=(",", ":"))
    db.commit()
    return current


@router.post("/account/tokens")
def create_api_token(payload: main.TokenRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    name = main._printable(payload.name, 64)
    if not name:
        raise HTTPException(status_code=400, detail="Give the token a name.")
    if payload.scope == "write" and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only owners and admins can create a token that acknowledges incidents.")
    if db.query(ApiToken).filter(ApiToken.user_id == user.id).count() >= MAX_TOKENS_PER_USER:
        raise HTTPException(status_code=400, detail="Revoke a token you no longer use first.")
    secret = f"{main.API_TOKEN_PREFIX}{'w' if payload.scope == 'write' else 'r'}_{secrets.token_urlsafe(32)}"
    token = ApiToken(user_id=user.id, name=name, scope=payload.scope, token_hash=main._hash_secret(secret), hint=secret[-4:],
                     expires_at=datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None)
    db.add(token)
    db.commit()
    main.record_audit_log(db, user_id=user.id, level="INFO", message=f"Created API token {name} ({payload.scope})", source="auth")
    return {**_token_payload(token), "token": secret}


@router.delete("/account/tokens/{token_id}")
def revoke_api_token(token_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    token = db.query(ApiToken).filter(ApiToken.id == token_id, ApiToken.user_id == user.id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found.")
    name = token.name
    db.delete(token)
    db.commit()
    main.record_audit_log(db, user_id=user.id, level="INFO", message=f"Revoked API token {name}", source="auth")
    return {"status": "ok"}


@router.post("/account/channels")
def add_channel(payload: main.ChannelRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
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


@router.post("/account/channels/{channel_id}/test")
def test_channel(channel_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    main.enforce_limit(main.redis_client, scope="channel-test", subject=user.id, limit=10, window_seconds=600)
    channel = _own_channel(db, user, channel_id)
    main._deliver(db, channel, user, "Infradeck test", f"This is a test from {main.get_instance_name(db) or 'Infradeck'}. Alerts for {user.name} will arrive here.", "INFO")
    return _channel_payload(channel)


@router.delete("/account/channels/{channel_id}")
def remove_channel(channel_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    db.delete(_own_channel(db, user, channel_id))
    db.commit()
    return {"status": "ok"}
