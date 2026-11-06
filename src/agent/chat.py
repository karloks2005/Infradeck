from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable

from .health_checks import HealthCheckRunner
from .prompts import SYSTEM_PROMPT
from .providers import Completion, Provider, get_provider

MAX_CACHED_CHATS = 200
# Only the most recent turns go back to the model, so a long conversation can't grow every request without limit.
MAX_HISTORY_MESSAGES = 40

# Every reply is a message and at most one predefined check; Structured Outputs makes the model keep to it.
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "action": {
            "anyOf": [
                {"type": "object", "properties": {"check": {"type": "string", "enum": sorted(HealthCheckRunner.ALLOWED_CHECKS)}}, "required": ["check"], "additionalProperties": False},
                {"type": "null"},
            ],
        },
    },
    "required": ["message", "action"],
    "additionalProperties": False,
}


class Reply:
    def __init__(self, text: str, completion: Completion | None = None):
        self.text = text
        self.completion = completion


class Chat:
    """One conversation's turns, sent in full (up to MAX_HISTORY_MESSAGES) with every new message."""

    def __init__(self):
        self.history: list[dict] = []

    def send_message(self, provider: Provider, message: str) -> Reply:
        self.history.append({"role": "user", "content": message})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.history[-MAX_HISTORY_MESSAGES:]]
        try:
            completion = provider.complete(messages, REPLY_SCHEMA, "agent_reply", effort="low")
        except Exception:
            self.history.pop()   # a failed turn is not part of the conversation
            raise
        self.history.append({"role": "assistant", "content": completion.text})
        return Reply(completion.text, completion)


class AgentService:
    """LLM interface restricted to a small, fixed health-check capability set."""

    def __init__(self, health_checks: HealthCheckRunner | None = None):
        self.health_checks = health_checks or HealthCheckRunner()
        # Least recently used first (a dict keeps insertion order). A chat only lives in memory so the assistant
        # remembers the conversation; the cache is bounded so endless new conversations can't grow it without limit.
        self.chats = {}
        self.lock = threading.Lock()

    def _new_chat(self) -> Chat:
        return Chat()

    def _get_chat(self, user_id: int, conversation_id: str):
        chat_key = (user_id, conversation_id)
        with self.lock:
            if chat_key not in self.chats:
                self.chats[chat_key] = self._new_chat()
            chat = self.chats.pop(chat_key)
            self.chats[chat_key] = chat   # now the most recently used
            while len(self.chats) > MAX_CACHED_CHATS:
                del self.chats[next(iter(self.chats))]
            return chat

    @staticmethod
    def _ask(chat, provider: Provider, message: str, usage_logger: Callable[[Completion, int], None] | None):
        """One call to the model; ``usage_logger`` gets its tokens and how long it took, in milliseconds."""
        started = time.monotonic()
        reply = chat.send_message(provider, message)
        completion = getattr(reply, "completion", None)
        if usage_logger and completion is not None:
            usage_logger(completion, round((time.monotonic() - started) * 1000))
        return reply

    def send(self, user_id: int, conversation_id: str, message: str, provider: Provider, command_logger: Callable[[dict], None] | None = None,
             usage_logger: Callable[[Completion, int], None] | None = None) -> str:
        chat = self._get_chat(user_id, conversation_id)
        response_text, action = self._parse_agent_response(self._response_text(self._ask(chat, provider, message, usage_logger)))
        if not action:
            return response_text

        check_name = action["check"]
        self._log_event(command_logger, {
            "event_type": "health_check_requested", "message": "Agent requested a predefined health check",
            "check": check_name,
            "steps": ["Validated the requested check against the closed allow-list."],
        })
        result = self.health_checks.run(check_name)
        level = "ERROR" if result["status"] == "critical" else "WARN" if result["status"] in {"warning", "unknown"} else "INFO"
        self._log_event(command_logger, {
            "event_type": "health_check_completed", "message": f"Predefined health check completed: {check_name}",
            "check": check_name, "level": level,
            "steps": [f"Completed predefined check '{check_name}' with status {result['status']}."],
        })
        final_text, final_action = self._parse_agent_response(self._response_text(self._ask(chat, provider, "[HEALTH-CHECK-RESULT]\n" + json.dumps(result), usage_logger)))
        if final_action:
            raise ValueError("Agent must summarize a health-check result without requesting another check.")
        return final_text

    def delete_conversation(self, user_id: int, conversation_id: str) -> None:
        """Discard the in-memory context after its persisted chat is deleted."""
        with self.lock:
            self.chats.pop((user_id, conversation_id), None)

    @staticmethod
    def _response_text(response) -> str:
        return getattr(response, "text", "") or "The agent returned an empty response."

    @staticmethod
    def _parse_agent_response(response_text: str) -> tuple[str, dict | None]:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise ValueError("Agent returned invalid JSON.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), str):
            raise ValueError("Agent response must contain a string message.")
        action = payload.get("action")
        if action is None:
            return payload["message"].strip(), None
        if not isinstance(action, dict) or set(action) != {"check"} or action.get("check") not in HealthCheckRunner.ALLOWED_CHECKS:
            raise ValueError("Agent requested a health check outside the allow-list.")
        return payload["message"].strip(), {"check": action["check"]}

    @staticmethod
    def _log_event(command_logger: Callable[[dict], None] | None, event: dict) -> None:
        if command_logger:
            command_logger(event)


_agent_service = None


def send_agent_message(user_id: int, conversation_id: str, message: str, provider: Provider, command_logger=None, usage_logger=None) -> str:
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service.send(user_id, conversation_id, message, provider, command_logger=command_logger, usage_logger=usage_logger)


def delete_agent_conversation(user_id: int, conversation_id: str) -> None:
    if _agent_service is not None:
        _agent_service.delete_conversation(user_id, conversation_id)


def check_provider_connection(db) -> dict:
    """For Admin's "Test connection": the key works and the configured model exists. Spends no tokens."""
    return get_provider(db).check()
