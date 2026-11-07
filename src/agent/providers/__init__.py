"""The language model behind the agent: which provider and model, chosen in Admin › Incident agent and stored in
app_settings. The API key is stored encrypted with SECRETS_KEY (secrets_box) and never leaves the backend again.

Each provider keeps its own key, so switching between them doesn't lose the others. Provider addresses are fixed
here, not configurable, so the agent's data can only ever go to one of these three APIs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.backend import secrets_box
from src.backend.models import AppSetting

from .base import Completion, Conversation, Provider, Tool, ToolCall, ToolResult, Turn
from .claude import ClaudeProvider
from .openai_compatible import GEMINI_BASE_URL, OpenAIProvider

__all__ = ["Completion", "Conversation", "Provider", "Tool", "ToolCall", "ToolResult", "Turn", "PROVIDERS", "NoApiKey", "current", "get_provider", "key_configured", "save", "remove_key"]

logger = logging.getLogger(__name__)

# id: (name shown in Admin, model used when none is set)
PROVIDERS = {
    "anthropic": ("Anthropic", "claude-haiku-5-5"),
    "openai": ("OpenAI", "gpt-6-luna"),
    "gemini": ("Google Gemini", "gemini-3.1-flash-lite"),
}
PROVIDER_KEY = "agent_provider"
MODEL_KEY = "agent_model"
API_KEY_PREFIX = "agent_api_key_"


class NoApiKey(RuntimeError):
    """No provider is chosen, or it has no usable key."""


@dataclass
class Choice:
    provider: str | None   # None until an admin picks one
    model: str
    key: str


def _get(db, key: str) -> str | None:
    setting = db.get(AppSetting, key)
    return setting.value if setting else None


def _set(db, key: str, value: str | None) -> None:
    setting = db.get(AppSetting, key)
    if value is None:
        if setting is not None:
            db.delete(setting)
    elif setting is None:
        db.add(AppSetting(key=key, value=value))
    else:
        setting.value = value


def current(db) -> Choice:
    provider = _get(db, PROVIDER_KEY)
    if provider not in PROVIDERS:
        return Choice(None, "", "")
    key = ""
    stored = _get(db, API_KEY_PREFIX + provider)
    if stored:
        try:
            key = secrets_box.decrypt(stored)
        except secrets_box.SecretsUnavailable:
            # SECRETS_KEY was changed or removed: the key is unreadable, so the agent behaves as if there were none.
            logger.warning("The stored API key for %s can't be decrypted with the configured SECRETS_KEY.", provider)
    return Choice(provider, _get(db, MODEL_KEY) or PROVIDERS[provider][1], key)


def key_configured(db) -> bool:
    return bool(current(db).key)


def get_provider(db) -> Provider:
    choice = current(db)
    if not choice.key:
        raise NoApiKey("The agent has no API key.")
    if choice.provider == "anthropic":
        return ClaudeProvider(choice.key, choice.model)
    if choice.provider == "gemini":
        return OpenAIProvider(choice.key, choice.model, name="Google Gemini", base_url=GEMINI_BASE_URL)
    return OpenAIProvider(choice.key, choice.model)


def save(db, provider: str, model: str | None, api_key: str | None) -> None:
    """Choose ``provider`` and ``model`` (None: the provider's default). A new ``api_key`` replaces the stored one;
    None keeps it. Raises SecretsUnavailable when a key is given but SECRETS_KEY isn't set. The caller commits."""
    if api_key:
        _set(db, API_KEY_PREFIX + provider, secrets_box.encrypt(api_key))
    _set(db, PROVIDER_KEY, provider)
    _set(db, MODEL_KEY, model or None)


def remove_key(db, provider: str) -> None:
    _set(db, API_KEY_PREFIX + provider, None)
