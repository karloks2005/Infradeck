"""Time-based one-time passwords (RFC 6238) and recovery codes, using only the standard library."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

PERIOD_SECONDS = 30
DIGITS = 6
# Codes from the previous and next period are accepted to tolerate a little clock drift.
WINDOW_STEPS = 1
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
RECOVERY_CODE_LENGTH = 16  # 32 symbols ** 16 = 80 bits


def _now() -> float:
    return time.time()


def new_secret() -> str:
    """A random 160-bit secret in the base32 form authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_for_step(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return f"{number:0{DIGITS}d}"


def code_at(secret: str, timestamp: float) -> str:
    return _code_for_step(secret, int(timestamp // PERIOD_SECONDS))


def normalize_code(code) -> str | None:
    """The six digits of a code typed with optional spaces or a hyphen, or None if it isn't one."""
    if not isinstance(code, str):
        return None
    cleaned = code.replace(" ", "").replace("-", "")
    return cleaned if len(cleaned) == DIGITS and cleaned.isascii() and cleaned.isdigit() else None


def verify(secret: str, code, last_step: int | None = None, now: float | None = None) -> int | None:
    """The time step the code belongs to if it is valid and newer than ``last_step``, else None.

    Requiring a step newer than the last accepted one stops an intercepted code being used twice.
    All steps in the window are always compared, in constant time.
    """
    cleaned = normalize_code(code)
    if cleaned is None:
        return None
    current = int((_now() if now is None else now) // PERIOD_SECONDS)
    matched: int | None = None
    for step in range(current - WINDOW_STEPS, current + WINDOW_STEPS + 1):
        if hmac.compare_digest(_code_for_step(secret, step), cleaned):
            matched = step
    if matched is None or (last_step is not None and matched <= last_step):
        return None
    return matched


def otpauth_uri(secret: str, account: str, issuer: str = "Infradeck") -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={PERIOD_SECONDS}"


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Single-use codes formatted XXXX-XXXX-XXXX-XXXX."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
        codes.append("-".join(raw[i:i + 4] for i in range(0, RECOVERY_CODE_LENGTH, 4)))
    return codes


def normalize_recovery_code(code) -> str | None:
    """The 16 symbols of a recovery code typed with any case, spaces or hyphens, or None if it isn't one."""
    if not isinstance(code, str):
        return None
    cleaned = code.replace(" ", "").replace("-", "").upper()
    return cleaned if len(cleaned) == RECOVERY_CODE_LENGTH and all(char in _RECOVERY_ALPHABET for char in cleaned) else None


def hash_recovery_code(code: str) -> str:
    """Recovery codes carry 80 bits of randomness, so a plain SHA-256 is enough to protect them at rest."""
    normalized = normalize_recovery_code(code)
    if normalized is None:
        raise ValueError("Not a recovery code.")
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()
