"""Sign-in, registration, logout, two-factor setup/verification, password change and session management. The
session/token/TOTP logic lives in auth.py, sessions.py and totp.py; the bootstrap-token, instance-name and
`_account_session` helpers stay in main (used across the app). This module reaches those through `main.`."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response

from pydantic import BaseModel, Field

from .. import main, reauth, secrets_box, totp
from ..config import SESSION_COOKIE_NAME
from ..db import get_db
from ..models import Invite, RecoveryCode, Server, User

router = APIRouter()


@router.get("/auth/config")
def auth_config(db=Depends(get_db)):
    """What the sign-in pages show before anyone has signed in. Everything here is public by design: keep it to
    what a visitor may know (see SECURITY.md)."""
    return {
        "registration_enabled": main.registration_open(db), "bootstrap_required": db.query(User).count() == 0,
        "instance_name": main.get_instance_name(db), "server_count": db.query(Server).count(), "version": main.APP_VERSION,
    }

def _valid_invite(db, token: str | None) -> Invite | None:
    if not token or not 20 <= len(token) <= 200:
        return None
    return db.query(Invite).filter(Invite.token_hash == main._hash_secret(token.strip()), Invite.used_at.is_(None), Invite.expires_at > datetime.now(timezone.utc)).first()

@router.get("/auth/invite")
def auth_invite(token: Annotated[str, Query(min_length=20, max_length=200)], request: Request, db=Depends(get_db)):
    """Whether an invitation link still works and who it is for, so the register page can say so."""
    main.enforce_ip_limit(main.redis_client, scope="invite-check", ip_address=main._client_ip(request), limit=30, window_seconds=3600)
    invite = _valid_invite(db, token)
    if not invite:
        raise HTTPException(status_code=404, detail="This invitation is no longer valid.")
    return {"email": invite.email, "role": invite.role}

@router.post("/auth/register")
def auth_register(payload: main.RegisterRequest, request: Request, response: Response, db=Depends(get_db)):
    invite = _valid_invite(db, payload.invite_token)
    if payload.invite_token and not invite:
        raise HTTPException(status_code=403, detail="This invitation is no longer valid. Ask for a new one.")
    if invite and invite.email != str(payload.email).strip().lower():
        raise HTTPException(status_code=403, detail="This invitation was made for a different email address.")
    if not invite and not main.registration_open(db):
        raise HTTPException(status_code=403, detail="Registration is disabled on this server.")
    main.enforce_register_limit(
        main.redis_client,
        ip_address=request.client.host if request.client else "unknown",
        limit=main.REGISTER_RATE_LIMIT,
        window_seconds=main.REGISTER_RATE_WINDOW_SECONDS,
    )
    first_account = db.query(User).count() == 0
    if first_account and not main._bootstrap_token_valid(db, payload.bootstrap_token):
        raise HTTPException(status_code=403, detail="This install needs its setup token to create the first account. Find it in the backend log: docker compose logs backend | grep \"setup token\"")
    try:
        result = main.register_user(
            db,
            main.redis_client,
            name=payload.name,
            email=str(payload.email),
            password=payload.password,
            admin_requires_two_factor=main.two_factor_enforced(db),
            everyone_requires_two_factor=main.two_factor_required_for(db, "user"),
            role=invite.role if invite and not first_account else None,
            ip=main._client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if invite:
        invite.used_at = datetime.now(timezone.utc)
        db.commit()
    if first_account:
        main.ensure_bootstrap_token(db)  # an account exists now: this removes the token
    _set_session_cookie(response, result.pop("session_id"))
    return result

def _set_session_cookie(response: Response, session_id: str, lifetime: int = main.SESSION_TTL_SECONDS) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=lifetime,
        httponly=True,
        secure=main.SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )

def _user_payload(user: User, **extra) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "is_active": user.is_active, **extra}

def _complete_login(db, request: Request, response: Response, user: User, how: str = "", remember: bool = False) -> dict:
    """Open the session after every required step has passed.

    An administrator without two-factor, while it is required, gets an enrollment session: it can reach
    the two-factor setup and nothing else until setup is done.
    """
    stage = main.STAGE_ENROLLMENT if not user.totp_enabled and main.two_factor_required_for(db, user.role) else main.STAGE_FULL
    user.last_seen_at = datetime.now(timezone.utc)
    session_id, _public_id = main.create_session(main.redis_client, user, stage=stage, ip=main._client_ip(request), user_agent=request.headers.get("user-agent"), remember=remember)
    # The cookie lasts exactly as long as the session behind it.
    _set_session_cookie(response, session_id, main.session_lifetime(stage, remember))
    main.record_audit_log(db, user_id=user.id, level="INFO", message=f"User signed in{how}{' (staying signed in for 30 days)' if remember and stage == main.STAGE_FULL else ''}", source="auth")
    return {"user": _user_payload(user), "setup_required": stage == main.STAGE_ENROLLMENT}

@router.post("/auth/login")
def auth_login(payload: main.LoginRequest, request: Request, response: Response, db=Depends(get_db)):
    limit_keys = main.enforce_login_limit(
        main.redis_client,
        ip_address=request.client.host if request.client else "unknown",
        email=str(payload.email),
        limit=main.LOGIN_RATE_LIMIT,
        window_seconds=main.LOGIN_RATE_WINDOW_SECONDS,
    )
    try:
        user = main.verify_credentials(db, email=str(payload.email), password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # This email's counter is cleared. The address's counter only gets this one successful attempt back: clearing it
    # would let anyone with an account of their own reset the limit between guesses at someone else's password.
    main.reset_limits(main.redis_client, limit_keys[1])
    main.refund_attempt(main.redis_client, limit_keys[0])
    if user.totp_enabled:
        # No session exists until the second step passes; this token only carries the login between the steps.
        return {"status": "two_factor_required", "pending_token": main.create_pending_two_factor(main.redis_client, user.id, payload.remember)}
    return _complete_login(db, request, response, user, remember=payload.remember)

@router.post("/auth/login/2fa")
def auth_login_two_factor(payload: main.TwoFactorLoginRequest, request: Request, response: Response, db=Depends(get_db)):
    main.enforce_ip_limit(main.redis_client, scope="2fa-ip", ip_address=main._client_ip(request), limit=20, window_seconds=main.TWO_FACTOR_WINDOW_SECONDS)
    expired = HTTPException(status_code=401, detail="Sign-in expired. Please sign in again.")
    user_id = main.read_pending_two_factor(main.redis_client, payload.pending_token)
    if user_id is None:
        raise expired
    if not main.count_pending_attempt(main.redis_client, payload.pending_token):
        raise HTTPException(status_code=401, detail="Too many wrong codes. Please sign in again.")
    user = db.get(User, user_id)
    if not user or not user.is_active or not user.totp_enabled:
        raise expired
    method = reauth.check_second_factor(db, user, payload.code)
    db.commit()  # the code that was just accepted is now spent
    remember = main.pending_two_factor_remember(main.redis_client, payload.pending_token)
    main.delete_pending_two_factor(main.redis_client, payload.pending_token)
    if method == "recovery":
        main.record_audit_log(db, user_id=user.id, level="WARNING", message="A two-factor recovery code was used to sign in", source="auth")
    result = _complete_login(db, request, response, user, " with two-factor", remember=remember)
    result["recovery_codes_left"] = main.recovery_codes_left(db, user)
    return result

@router.get("/auth/me")
def auth_me(
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    db=Depends(get_db),
):
    session, account = main._account_session(session_id, db, allow_limited=True)
    # Role and status come from the database so admin changes apply without a new login.
    return {"user": _user_payload(account, setup_required=session["stage"] != main.STAGE_FULL, two_factor_enabled=account.totp_enabled)}

@router.post("/auth/logout")
def auth_logout(
    response: Response,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
):
    if not session_id:
        raise HTTPException(status_code=400, detail="No session id supplied.")
    main.delete_session(main.redis_client, session_id)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}

@router.get("/auth/2fa/status")
def two_factor_status(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db, allow_limited=True)
    return {
        "enabled": user.totp_enabled, "available": secrets_box.available(),
        "required": main.two_factor_required_for(db, user.role),
        "recovery_codes_left": main.recovery_codes_left(db, user) if user.totp_enabled else 0,
        "setup_required": session["stage"] != main.STAGE_FULL,
    }

@router.post("/auth/2fa/setup")
def two_factor_setup(payload: main.SetupRequest | None = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db, allow_limited=True)
    if not secrets_box.available():
        raise HTTPException(status_code=503, detail="Two-factor needs SECRETS_KEY to be configured on the server. See docs/accounts.md (Two-factor sign-in).")
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="Two-factor is already turned on. Turn it off first to set it up again.")
    if session.get("stage") != main.STAGE_ENROLLMENT:
        # Turning two-factor on from a normal session needs the password again. Otherwise a stolen session could
        # enrol its own authenticator, which signs out the real owner and locks them out.
        _confirm_password(db, user, payload.password if payload else None)
    main.enforce_limit(main.redis_client, scope="2fa-setup", subject=user.id, limit=10, window_seconds=3600)
    secret = totp.new_secret()
    # Stored encrypted and inactive until a code from the app has been confirmed.
    user.totp_secret_enc, user.totp_last_step = secrets_box.encrypt(secret), None
    db.commit()
    return {"secret": secret, "otpauth_uri": totp.otpauth_uri(secret, user.email)}

@router.post("/auth/2fa/enable")
def two_factor_enable(payload: main.CodeRequest, request: Request, response: Response, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db, allow_limited=True)
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="Two-factor is already turned on.")
    if not user.totp_secret_enc:
        raise HTTPException(status_code=400, detail="Start the setup first.")
    limit_key = main.enforce_limit(main.redis_client, scope="2fa-user", subject=user.id, limit=main.TWO_FACTOR_ATTEMPTS, window_seconds=main.TWO_FACTOR_WINDOW_SECONDS)
    try:
        step = totp.verify(secrets_box.decrypt(user.totp_secret_enc), payload.code)
    except secrets_box.SecretsUnavailable as exc:
        raise HTTPException(status_code=503, detail="Two-factor can't be set up on this server right now (a SECRETS_KEY problem).") from exc
    if step is None:
        raise HTTPException(status_code=400, detail="That code is not valid. Check the clock on your phone and try again.")
    main.reset_limits(main.redis_client, limit_key)
    user.totp_enabled, user.totp_last_step = True, step
    codes = main.issue_recovery_codes(db, user)
    db.commit()
    main.record_audit_log(db, user_id=user.id, level="INFO", message="Two-factor was turned on", source="auth")
    # A fresh session ID: this is the moment an enrollment session becomes a full one, and other sessions end.
    new_session_id, new_public_id = main.create_session(main.redis_client, user, stage=main.STAGE_FULL, ip=main._client_ip(request), user_agent=request.headers.get("user-agent"))
    main.delete_session(main.redis_client, session_id)
    main.revoke_other_sessions(main.redis_client, user.id, new_public_id)
    _set_session_cookie(response, new_session_id)
    return {"recovery_codes": codes, "setup_required": False}

def _confirm_password(db, user: User, password: str | None) -> None:
    """Ask for the current password again before a sensitive change. Every try counts toward the two-factor lockout,
    the right password included, so a locked-out session can't keep guessing until it is right."""
    limit_key = main.enforce_limit(main.redis_client, scope="2fa-user", subject=user.id, limit=main.TWO_FACTOR_ATTEMPTS, window_seconds=main.TWO_FACTOR_WINDOW_SECONDS)
    if not password or not main.verify_password(user, password):
        main.record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong or missing password was given while setting up two-factor", source="auth")
        raise HTTPException(status_code=401, detail="Enter your current password to set up two-factor.")
    main.reset_limits(main.redis_client, limit_key)

def _reauthenticate(db, user: User, password: str, code: str) -> None:
    """Password plus a second-factor code, for changes to the two-factor setup. One generic error for either being wrong."""
    wrong = HTTPException(status_code=401, detail="The password or the code is not correct.")
    if not main.verify_password(user, password):
        main.enforce_limit(main.redis_client, scope="2fa-user", subject=user.id, limit=main.TWO_FACTOR_ATTEMPTS, window_seconds=main.TWO_FACTOR_WINDOW_SECONDS)
        main.record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong password was entered while changing two-factor", source="auth")
        raise wrong
    try:
        reauth.check_second_factor(db, user, code)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise wrong from exc
        raise

@router.post("/auth/2fa/disable")
def two_factor_disable(payload: main.ReauthRequest, request: Request, response: Response, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="Two-factor is not turned on.")
    if main.two_factor_required_for(db, user.role):
        raise HTTPException(status_code=403, detail="Two-factor is required for administrators on this server.")
    _reauthenticate(db, user, payload.password, payload.code)
    user.totp_enabled, user.totp_secret_enc, user.totp_last_step = False, None, None
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    # A fresh session ID for this one too, and every other session ends.
    new_session_id, new_public_id = main.create_session(main.redis_client, user, stage=main.STAGE_FULL, ip=main._client_ip(request), user_agent=request.headers.get("user-agent"))
    main.delete_session(main.redis_client, session_id)
    revoked = main.revoke_other_sessions(main.redis_client, user.id, new_public_id)
    _set_session_cookie(response, new_session_id)
    main.record_audit_log(db, user_id=user.id, level="WARNING", message=f"Two-factor was turned off ({revoked} other sessions signed out)", source="auth")
    return {"status": "ok"}

@router.post("/auth/2fa/recovery-codes")
def two_factor_new_recovery_codes(payload: main.ReauthRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    _session, user = main._account_session(session_id, db)
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="Two-factor is not turned on.")
    _reauthenticate(db, user, payload.password, payload.code)
    codes = main.issue_recovery_codes(db, user)
    db.commit()
    main.record_audit_log(db, user_id=user.id, level="INFO", message="Two-factor recovery codes were regenerated", source="auth")
    return {"recovery_codes": codes}

@router.post("/auth/password")
def change_password(payload: main.PasswordChangeRequest, request: Request, response: Response, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    main.enforce_limit(main.redis_client, scope="pw-change", subject=user.id, limit=5, window_seconds=600)
    if not main.verify_password(user, payload.current_password):
        main.record_audit_log(db, user_id=user.id, level="WARNING", message="A wrong current password was entered while changing the password", source="auth")
        raise HTTPException(status_code=400, detail="The current password is not correct.")
    try:
        main.validate_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="The new password must be different from the current one.")
    user.password_hash, user.password_changed_at = main.hash_password(payload.new_password), datetime.now(timezone.utc)
    db.commit()
    # A fresh session ID for this browser, and every other session ends: whoever had the old password is out.
    new_session_id, new_public_id = main.create_session(main.redis_client, user, stage=main.STAGE_FULL, ip=main._client_ip(request), user_agent=request.headers.get("user-agent"))
    main.delete_session(main.redis_client, session_id)
    revoked = main.revoke_other_sessions(main.redis_client, user.id, new_public_id)
    _set_session_cookie(response, new_session_id)
    main.record_audit_log(db, user_id=user.id, level="INFO", message=f"Password was changed ({revoked} other sessions signed out)", source="auth")
    return {"status": "ok", "other_sessions_signed_out": revoked}

@router.get("/auth/sessions")
def auth_sessions(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    return {"sessions": main.list_user_sessions(main.redis_client, user.id, session["public_id"])}

@router.post("/auth/sessions/revoke-others")
def auth_revoke_other_sessions(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    revoked = main.revoke_other_sessions(main.redis_client, user.id, session["public_id"])
    main.record_audit_log(db, user_id=user.id, level="INFO", message=f"Signed out {revoked} other sessions", source="auth")
    return {"signed_out": revoked}

@router.delete("/auth/sessions/{public_id}")
def auth_revoke_session(public_id: str, response: Response, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    session, user = main._account_session(session_id, db)
    if not main.revoke_user_session(main.redis_client, user.id, public_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    if public_id == session["public_id"]:
        response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


class PasswordConfirmRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)

@router.post("/auth/reauth")
def reauth_password(payload: PasswordConfirmRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Step 1 of confirming a sensitive change: the password. Step 2 sends the token and a code with the change (reauth.py)."""
    session, user = main._account_session(session_id, db)
    return {"reauth_token": reauth.check_password(db, user, session, payload.password)}
