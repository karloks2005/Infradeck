"""Asking a signed-in user for their password and a two-factor code again before a change that sends data out of
this machine (Admin › Incident agent, #184).

Step 1 (POST /auth/reauth) checks the password and hands out a short-lived token. Step 2 sends that token and a
code together with the change itself, so the password goes over the wire once. Like the sign-in's pending token, it
is kept in Redis only as a digest, lasts five minutes, is bound to the session that asked for it, allows five codes
and is used up by the change it confirms. Every try counts toward the account's two-factor lockout.
"""
from __future__ import annotations

import hashlib
import json
import secrets

from fastapi import HTTPException

from . import main, secrets_box
from .models import User

TTL_SECONDS = 300
MAX_CODE_ATTEMPTS = 5
START_AGAIN = "The confirmation expired. Start again with your password."


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_key(token: str) -> str:
    return f"infradeck:reauth:{_digest(token)}"


def _attempts_key(token: str) -> str:
    return f"infradeck:reauth-attempts:{_digest(token)}"


def check_second_factor(db, user: User, code: str) -> str:
    """Verify a code for the user, with the shared brute-force limit. Raises 401 (or 429/503); returns "totp" or "recovery"."""
    limit_key = main.enforce_limit(main.redis_client, scope="2fa-user", subject=user.id, limit=main.TWO_FACTOR_ATTEMPTS, window_seconds=main.TWO_FACTOR_WINDOW_SECONDS)
    try:
        method = main.verify_second_factor(db, user, code)
    except secrets_box.SecretsUnavailable as exc:
        raise HTTPException(status_code=503, detail="Two-factor codes can't be checked on this server right now (a SECRETS_KEY problem). A recovery code still works.") from exc
    if method is None:
        main.record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong two-factor code was entered", source="auth")
        raise HTTPException(status_code=401, detail="Invalid code.")
    main.reset_limits(main.redis_client, limit_key)
    return method


def check_password(db, user: User, session: dict, password: str) -> str:
    """Step 1: the password. Returns the token for step 2."""
    if not user.totp_enabled:
        raise HTTPException(status_code=403, detail="Set up two-factor in Account › Security first.")
    limit_key = main.enforce_limit(main.redis_client, scope="2fa-user", subject=user.id, limit=main.TWO_FACTOR_ATTEMPTS, window_seconds=main.TWO_FACTOR_WINDOW_SECONDS)
    if not main.verify_password(user, password):
        main.record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong password was entered while confirming a change", source="auth")
        raise HTTPException(status_code=401, detail="The password is not correct.")
    main.reset_limits(main.redis_client, limit_key)
    token = secrets.token_urlsafe(32)
    main.redis_client.set(_token_key(token), json.dumps({"user_id": user.id, "public_id": session.get("public_id")}), ex=TTL_SECONDS)
    return token


def require(db, session: dict, token: str | None, code: str | None) -> None:
    """Step 2, called by the endpoint that makes the change: the token from step 1 and a two-factor code."""
    if not token or not code:
        raise HTTPException(status_code=403, detail="Confirm this change with your password and a two-factor code.")
    user = db.get(User, session["id"])
    raw = main.redis_client.get(_token_key(token)) if 20 <= len(token) <= 100 else None
    try:
        owner = json.loads(raw) if raw else None
    except ValueError:
        owner = None
    if not owner or user is None or owner.get("user_id") != user.id or owner.get("public_id") != session.get("public_id"):
        raise HTTPException(status_code=401, detail=START_AGAIN)
    attempts = main.redis_client.incr(_attempts_key(token))
    if attempts == 1:
        main.redis_client.expire(_attempts_key(token), TTL_SECONDS)
    if attempts > MAX_CODE_ATTEMPTS:
        _forget(token)
        raise HTTPException(status_code=401, detail="Too many wrong codes. Start again with your password.")
    try:
        check_second_factor(db, user, code)
    except HTTPException as exc:
        left = MAX_CODE_ATTEMPTS - attempts
        if exc.status_code == 401 and left > 0:
            raise HTTPException(status_code=401, detail=f"That code is not valid. {left} {'try' if left == 1 else 'tries'} left.") from exc
        if exc.status_code == 401:
            _forget(token)
            raise HTTPException(status_code=401, detail="Too many wrong codes. Start again with your password.") from exc
        raise
    _forget(token)


def _forget(token: str) -> None:
    main.redis_client.delete(_token_key(token))
    main.redis_client.delete(_attempts_key(token))
