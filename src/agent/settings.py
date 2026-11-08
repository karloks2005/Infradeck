"""The agent's switches in Admin › Incident agent, stored in app_settings.

- Automatic diagnosis: new and worsening incidents are diagnosed without anyone asking (on by default).
- Show diagnoses to users: when off, only admins see the agent's diagnoses and their summaries in notifications;
  everyone else sees the rule-based diagnosis (on by default).
- Chat access: "admins_only", or "per_user", where each user's own switch in Admin › Users decides. Admins always
  have chat while it is switched on.
- Tool budgets: how many look-ups (tools/) the agent may make for one diagnosis and for one chat message. 0 means it
  gets no tools and answers from what it was given.
"""
from __future__ import annotations

from src.backend.models import AppSetting

AUTO_DIAGNOSE_KEY = "agent_auto_diagnose"
SHOW_TO_USERS_KEY = "agent_show_diagnoses_to_users"
CHAT_ACCESS_KEY = "agent_chat_access"
CHAT_ACCESS = ("admins_only", "per_user")
# kind: (setting, default, highest allowed)
TOOL_BUDGETS = {"diagnosis": ("agent_tool_budget_diagnosis", 8, 20), "chat": ("agent_tool_budget_chat", 4, 10)}


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


def tool_budget(db, kind: str) -> int:
    key, default, highest = TOOL_BUDGETS[kind]
    try:
        return max(0, min(highest, int(_setting(db, key) or default)))
    except ValueError:
        return default


def validate_tool_budget(kind: str, value) -> int:
    highest = TOOL_BUDGETS[kind][2]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= highest:
        raise ValueError(f"The {kind} tool budget must be a whole number between 0 and {highest}.")
    return value
