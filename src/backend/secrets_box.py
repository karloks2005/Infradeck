"""Encryption at rest for secrets the app must be able to read back (TOTP secrets, later notification tokens).

The key comes from the SECRETS_KEY environment variable, never from the database, so a stolen database
or backup does not reveal the secrets. Several comma-separated keys are accepted for rotation: the first
one encrypts, all of them decrypt. If no valid key is configured nothing is ever stored in plain text:
callers get SecretsUnavailable and must refuse the feature.

Generate a key with:  python -m src.backend.secrets_box
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretsUnavailable(RuntimeError):
    """No usable key is configured, or a stored value can't be decrypted with the configured keys."""


def _fernet() -> MultiFernet:
    keys = [key.strip() for key in os.getenv("SECRETS_KEY", "").split(",") if key.strip()]
    if not keys:
        raise SecretsUnavailable("SECRETS_KEY is not configured.")
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except (ValueError, TypeError) as exc:
        raise SecretsUnavailable("SECRETS_KEY is not a valid key.") from exc


def available() -> bool:
    """True if a valid key is configured."""
    try:
        _fernet()
    except SecretsUnavailable:
        return False
    return True


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        # A wrong key and a tampered value look the same, and neither reveals anything.
        raise SecretsUnavailable("The stored secret can't be decrypted with the configured SECRETS_KEY.") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


if __name__ == "__main__":
    print(generate_key())
