from __future__ import annotations

import hashlib

from fastapi import HTTPException


def _key(scope: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"homelab:rate-limit:{scope}:{digest}"


def _record_attempt(redis_client, key: str, limit: int, window_seconds: int) -> None:
    try:
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = pipe.execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Rate limiting service unavailable.") from exc

    if count > limit:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(window_seconds)},
        )


def enforce_login_limit(redis_client, *, ip_address: str, email: str, limit: int, window_seconds: int) -> tuple[str, str]:
    keys = (
        _key("login-ip", ip_address),
        _key("login-email", email.lower()),
    )
    for key in keys:
        _record_attempt(redis_client, key, limit, window_seconds)
    return keys


def enforce_register_limit(redis_client, *, ip_address: str, limit: int, window_seconds: int) -> str:
    key = _key("register-ip", ip_address)
    _record_attempt(redis_client, key, limit, window_seconds)
    return key


def enforce_ip_limit(redis_client, *, scope: str, ip_address: str, limit: int, window_seconds: int) -> str:
    key = _key(scope, ip_address)
    _record_attempt(redis_client, key, limit, window_seconds)
    return key


def enforce_limit(redis_client, *, scope: str, subject: str, limit: int, window_seconds: int) -> str:
    """Count an attempt for any subject (a user ID, a token) and refuse with 429 once the limit is passed."""
    key = _key(scope, str(subject))
    _record_attempt(redis_client, key, limit, window_seconds)
    return key


def reset_limits(redis_client, *keys: str) -> None:
    try:
        redis_client.delete(*keys)
    except Exception:
        pass