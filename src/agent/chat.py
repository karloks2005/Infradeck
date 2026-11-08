"""Chat with the agent. Each message is one investigation (investigate.py): the model may make up to the chat tool
budget's look-ups with the read-only tools before it replies. Only the questions and the final replies stay in the
conversation's memory; the look-ups of earlier messages are not sent again."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable

from . import diagnosis, tools, usage
from .investigate import Auditor, Recorder, investigate
from .prompts import SYSTEM_PROMPT
from .providers import Completion, Provider, get_provider

MAX_CACHED_CHATS = 200
# Only the most recent turns go back to the model, so a long conversation can't grow every request without limit.
MAX_HISTORY_MESSAGES = 40
# After this long the agent stops looking things up and replies, so the request finishes before nginx gives up
# waiting (proxy_read_timeout 120 s), even if the final call takes as long as the provider allows.
DEADLINE_SECONDS = 45

# Every reply is one message; Structured Outputs makes the model keep to it.
REPLY_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
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

    def send_message(self, provider: Provider, message: str, db=None, budget: int = 0, record: Recorder | None = None, audit: Auditor | None = None) -> Reply:
        self.history.append({"role": "user", "content": message})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.history[-MAX_HISTORY_MESSAGES:]]
        try:
            found = investigate(db, provider, messages, REPLY_SCHEMA, "agent_reply", budget=budget, effort="low", record=record or (lambda *_: None),
                                audit=audit or (lambda _: None), deadline_seconds=DEADLINE_SECONDS)
        except Exception:
            self.history.pop()   # a failed turn is not part of the conversation
            raise
        self.history.append({"role": "assistant", "content": found.completion.text})
        return Reply(found.completion.text, found.completion)


def parse_reply(text: str) -> str:
    try:
        payload = json.loads(text or "")
    except json.JSONDecodeError as exc:
        raise ValueError("Agent returned invalid JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), str) or not payload["message"].strip():
        raise ValueError("Agent response must contain a message.")
    return payload["message"].strip()


class AgentService:
    """Keeps each conversation's turns in memory, so the agent remembers what was said."""

    def __init__(self):
        # Least recently used first (a dict keeps insertion order). The cache is bounded so endless new
        # conversations can't grow it without limit.
        self.chats = {}
        self.lock = threading.Lock()

    def _get_chat(self, user_id: int, conversation_id: str) -> Chat:
        chat_key = (user_id, conversation_id)
        with self.lock:
            if chat_key not in self.chats:
                self.chats[chat_key] = Chat()
            chat = self.chats.pop(chat_key)
            self.chats[chat_key] = chat   # now the most recently used
            while len(self.chats) > MAX_CACHED_CHATS:
                del self.chats[next(iter(self.chats))]
            return chat

    def send(self, user_id: int, conversation_id: str, message: str, provider: Provider, *, db=None, budget: int = 0,
             usage_logger: Callable[[Completion, int], None] | None = None, audit: Auditor | None = None) -> str:
        chat = self._get_chat(user_id, conversation_id)
        return parse_reply(chat.send_message(provider, message, db, budget, usage_logger, audit).text)

    def delete_conversation(self, user_id: int, conversation_id: str) -> None:
        """Discard the in-memory context after its persisted chat is deleted."""
        with self.lock:
            self.chats.pop((user_id, conversation_id), None)


_agent_service = None


def send_agent_message(user_id: int, conversation_id: str, message: str, provider: Provider, *, db=None, budget: int = 0, usage_logger=None, audit=None) -> str:
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service.send(user_id, conversation_id, message, provider, db=db, budget=budget, usage_logger=usage_logger, audit=audit)


def delete_agent_conversation(user_id: int, conversation_id: str) -> None:
    if _agent_service is not None:
        _agent_service.delete_conversation(user_id, conversation_id)


TRIAL_MESSAGES = [{"role": "system", "content": "This is a connection test. Do not call any tool."},
                  {"role": "user", "content": "Answer with the shortest values the schema allows."}]


def check_provider_connection(db, user_id: int | None = None) -> dict:
    """For Admin's "Test connection": the key works, the configured model exists, and the provider accepts a
    request shaped like a diagnosis: every tool and the diagnosis schema, tools not allowed (#196). That one request
    is the only thing here that costs tokens (a few thousand, mostly the tools' definitions), and it is
    counted in the usage like any other (kind ``test``). Raises the provider's error when it is refused."""
    provider = get_provider(db)
    found = provider.check()
    started = time.monotonic()
    try:
        completion = provider.start(TRIAL_MESSAGES, tools.TOOLS, diagnosis.SCHEMA, "incident_diagnosis", effort="low").step(allow_tools=False).completion
    except Exception as exc:
        usage.record(db, kind="test", status="error", model=provider.model, user_id=user_id, duration_ms=round((time.monotonic() - started) * 1000), error=f"{type(exc).__name__}: {exc}")
        raise
    usage.record(db, kind="test", model=completion.model or provider.model, input_tokens=completion.input_tokens, output_tokens=completion.output_tokens,
                 duration_ms=round((time.monotonic() - started) * 1000), user_id=user_id)
    return {**found, "tokens": completion.input_tokens + completion.output_tokens}
