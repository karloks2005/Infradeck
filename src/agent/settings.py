"""The agent's switches in Admin › Incident agent, stored in app_settings.

- Automatic diagnosis: new and worsening incidents are diagnosed without anyone asking (on by default).
- Show diagnoses to users: when off, only admins see the agent's diagnoses and their summaries in notifications;
  everyone else sees the rule-based diagnosis (on by default).
- Chat access: "admins_only", or "per_user", where each user's own switch in Admin › Users decides. Admins always
  have chat while it is switched on.
"""
from __future__ import annotations

from src.backend.models import AppSetting

AUTO_DIAGNOSE_KEY = "agent_auto_diagnose"
SHOW_TO_USERS_KEY = "agent_show_diagnoses_to_users"
CHAT_ACCESS_KEY = "agent_chat_access"
CHAT_ACCESS = ("admins_only", "per_user")


def _setting(db, key: str) -> str | None:
    setting = db.get(AppSetting, key)
    return setting.value if setting else None


def auto_diagnose(db) -> bool:
    return _setting(db, AUTO_DIAGNOSE_KEY) != "false"


def show_diagnoses_to_users(db) -> bool:
    return _setting(db, SHOW_TO_USERS_KEY) != "false"


def sees_diagnoses(db, role: str | None) -> bool:
    """Whether someone with ``role`` sees the agent's diagnoses."""
    return role == "admin" or show_diagnoses_to_users(db)


def chat_access(db) -> str:
    value = _setting(db, CHAT_ACCESS_KEY)
    return value if value in CHAT_ACCESS else "admins_only"


def may_chat(db, user) -> bool:
    """Whether ``user`` (a User row) may use the chat, leaving aside the global Chat switch."""
    return user.role == "admin" or (chat_access(db) == "per_user" and bool(user.agent_chat_enabled))
