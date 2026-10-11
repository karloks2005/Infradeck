from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable

from dotenv import load_dotenv
from google import genai
from google.genai import types

from .health_checks import HealthCheckRunner
from .prompts import SYSTEM_PROMPT

MAX_CACHED_CHATS = 200

load_dotenv()


class AgentService:
    """LLM interface restricted to a small, fixed health-check capability set."""

    def __init__(self, health_checks: HealthCheckRunner | None = None):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        self.client = genai.Client(api_key=api_key)
        self.health_checks = health_checks or HealthCheckRunner()
        # Least recently used first (a dict keeps insertion order). A chat only lives in memory so the assistant
        # remembers the conversation; the cache is bounded so endless new conversations can't grow it without limit.
        self.chats = {}
        self.lock = threading.Lock()

    def _get_chat(self, user_id: int, conversation_id: str):
        chat_key = (user_id, conversation_id)
        with self.lock:
            if chat_key not in self.chats:
                self.chats[chat_key] = self.client.chats.create(
                    model=os.getenv("AGENT_MODEL", "models/gemini-3.5-flash"),
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.2,
                        response_mime_type="application/json",
                        response_schema={
                            "type": "OBJECT",
                            "properties": {
                                "message": {"type": "STRING"},
                                "action": {
                                    "type": "OBJECT", "nullable": True,
                                    "properties": {"check": {"type": "STRING"}},
                                    "required": ["check"],
                                },
                            },
                            "required": ["message", "action"],
                        },
                    ),
                )
            chat = self.chats.pop(chat_key)
            self.chats[chat_key] = chat   # now the most recently used
            while len(self.chats) > MAX_CACHED_CHATS:
                del self.chats[next(iter(self.chats))]
            return chat

    def send(self, user_id: int, conversation_id: str, message: str, command_logger: Callable[[dict], None] | None = None) -> str:
        chat = self._get_chat(user_id, conversation_id)
        response_text, action = self._parse_agent_response(self._response_text(chat.send_message(message)))
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
        final_text, final_action = self._parse_agent_response(self._response_text(chat.send_message("[HEALTH-CHECK-RESULT]\n" + json.dumps(result))))
        if final_action:
            raise ValueError("Agent must summarize a health-check result without requesting another check.")
        return final_text

    def delete_conversation(self, user_id: int, conversation_id: str) -> None:
        """Discard the provider's in-memory context after its persisted chat is deleted."""
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


def send_agent_message(user_id: int, conversation_id: str, message: str, command_logger=None) -> str:
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service.send(user_id, conversation_id, message, command_logger=command_logger)


def delete_agent_conversation(user_id: int, conversation_id: str) -> None:
    if _agent_service is not None:
        _agent_service.delete_conversation(user_id, conversation_id)
