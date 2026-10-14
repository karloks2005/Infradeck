"""Masks secrets in anything the agent is about to send to the model provider.

Event text and names come from the monitored machines: a container's command line, a kernel message, a webhook URL
in an error. Before any of it leaves this machine, tokens, passwords, keys and credentials in URLs are replaced with
``[masked]``. The patterns err on the side of masking; a masked word the model needed is a smaller loss than a leaked
secret.
"""
from __future__ import annotations

import re

MASK = "[masked]"

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Private keys, whole blocks.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), MASK),
    # JSON Web Tokens: three base64url parts, the first two starting with {" encoded.
    (re.compile(r"\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}"), MASK),
    # Provider keys and tokens with a recognisable prefix.
    (re.compile(r"\b(sk|pk|rk)-(proj-|live_|test_)?[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"), MASK),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), MASK),
    (re.compile(r"\bidk_[rw]_[A-Za-z0-9_-]{8,}"), MASK),
    # Authorization headers and bearer tokens.
    (re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*(bearer|basic|token)?\s*[^\s,;]+"), r"\1: " + MASK),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + MASK),
    # user:password@ in URLs.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@"), r"\1:" + MASK + "@"),
    # key=value and key: value where the key says it is secret.
    (re.compile(r"(?i)\b([\w.-]*(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|auth)[\w.-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"), r"\1\3" + MASK),
]


def mask_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def mask(value):
    """``value`` with every string in it masked: a str, or lists and dicts of them, any depth."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, list):
        return [mask(item) for item in value]
    if isinstance(value, dict):
        return {key: mask(item) for key, item in value.items()}
    return value
