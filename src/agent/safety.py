"""Flags fix commands that could destroy data or open the machine up, before the page offers them to copy.

The agent never runs anything; people copy its commands into a shell. A suggestion such as ``docker system prune -a
--volumes`` is sometimes right, but it must never look like the obvious next step. A flagged step is shown with a
warning and is never marked recommended. The list is about catching the dangerous cases, not about being complete,
so any command that removes something (``removes_data``) is never marked recommended either, only shown with a note.
"""
from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, str]] = [
    # Any recursive rm, however its flags are written (-rf, -r -f, -R --force, --recursive).
    (re.compile(r"(?<![\w-])rm\b[^\n;|&]*\s(-[a-zA-Z]*[rR]|--recursive\b)"), "deletes files recursively"),
    (re.compile(r"\bfind\b[^\n;|&]*\s(-delete\b|-exec\s+rm\b)"), "deletes every file it finds"),
    (re.compile(r"\bdocker\s+(system|volume)\s+prune\b[^\n]*(--volumes|\bvolume\s+prune)|\bdocker\s+volume\s+(prune|rm)\b"
                r"|\bdocker\s+(container\s+)?rm\b[^\n;|&]*\s(-[a-zA-Z]*v|--volumes\b)"), "deletes Docker volumes, which hold container data"),
    (re.compile(r"\bdocker\s+system\s+prune\b[^\n]*(-a\b|--all\b)"), "removes every image no container uses right now"),
    (re.compile(r"\bdocker\s+(compose\s+down|-compose\s+down)\b[^\n]*(-v\b|--volumes\b)"), "removes the stack's volumes, which hold its data"),
    (re.compile(r"(?i)\b(drop\s+(database|table|schema)|truncate\s+table)\b"), "deletes database data"),
    (re.compile(r"(?i)\bdelete\s+from\s+\w+\s*(;|$)"), "deletes every row of a table"),
    (re.compile(r"\bmkfs(\.\w+)?\b|\bwipefs\b|\bdd\b[^\n]*\bof=/dev/"), "overwrites a disk or a partition"),
    (re.compile(r"\bchmod\s+(-R\s+)?(0?777|a\+rwx)\b"), "makes files writable by everyone"),
    (re.compile(r"\bchown\s+-R\b[^\n]*\s/(\s|$)"), "changes the owner of the whole filesystem"),
    (re.compile(r"(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(ba|z)?sh\b"), "runs a script straight from the internet"),
    (re.compile(r"\bufw\s+disable\b|\biptables\s+-F\b|\bnft\s+flush\s+ruleset\b"), "turns the firewall off"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "a fork bomb"),
    (re.compile(r"\b(shutdown|poweroff|halt)\b|\bsystemctl\s+(poweroff|halt)\b"), "shuts the machine down"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f)"), "throws away local changes"),
    (re.compile(r">\s*/dev/sd[a-z]|>\s*/etc/(passwd|shadow|sudoers)\b"), "overwrites a disk or a system file"),
]


# A line break runs the first line as soon as it is pasted into a shell, and control, direction (bidi) and invisible
# characters can make a command read differently from what is copied (#208).
_HIDDEN = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")
HIDDEN_REASON = "contains a line break or hidden characters, so what you copy isn't what you see"

# Commands that remove something. Often the right fix (a full disk), but never the obvious next step to copy: they
# are not marked recommended even when no rule above calls them dangerous.
_REMOVES = re.compile(r"(?i)(?<![\w-])(rm|rmdir|unlink|shred|prune|drop|truncate)\b|\s-delete\b")   # not the --rm of docker run


def check(command: str | None) -> list[str]:
    """Why ``command`` is dangerous, or an empty list."""
    if not command:
        return []
    return [reason for pattern, reason in _RULES if pattern.search(command)] + ([HIDDEN_REASON] if _HIDDEN.search(command) else [])


def removes_data(command: str | None) -> bool:
    """Whether ``command`` deletes something, dangerous or not."""
    return bool(command) and bool(_REMOVES.search(command))
