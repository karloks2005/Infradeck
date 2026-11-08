"""What every provider returns and offers. A provider never sees the database or the servers; the agent decides
what goes into the messages, and masks them first (masking.py).

A conversation with tools goes: ``provider.start(...)`` once, then ``step()`` until a turn has no tool calls. A turn
with tool calls is answered with ``add_tool_results()`` before the next step. The final answer is JSON matching the
schema, as with ``complete()``. Each provider keeps the history in its own format and only ever appends to it, so
what the model said (including Claude's thinking) goes back exactly as it came.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..masking import mask_text

TIMEOUT_SECONDS = 60
MAX_RETRIES = 2


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Tool:
    name: str
    description: str
    schema: dict   # JSON schema of the arguments: an object with additionalProperties false and every property required


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Turn:
    """One call to the model: tool calls to answer, or (with none) the final answer in ``completion.text``."""
    completion: Completion
    tool_calls: list[ToolCall] = field(default_factory=list)


class Conversation:
    def step(self, allow_tools: bool = True) -> Turn:
        """One call to the model. ``allow_tools=False`` makes it answer now (the tools stay listed, so the cache
        and Claude's earlier thinking stay valid)."""
        raise NotImplementedError

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Answers every tool call of the last turn. Results come from the monitored machines, so they are masked
        here, where no tool can skip it."""
        self._append_results([ToolResult(result.call_id, mask_text(result.content), result.is_error) for result in results])

    def _append_results(self, results: list[ToolResult]) -> None:
        raise NotImplementedError

    def add_user(self, text: str) -> None:
        """A follow-up written by the agent itself, never by a monitored machine: why the last answer couldn't be
        used. Both providers keep plain {"role", "content"} turns in ``self.history``."""
        self.history.append({"role": "user", "content": text})


class Provider(Protocol):
    name: str
    model: str

    def start(self, messages: list[dict], tools: list[Tool], schema: dict, schema_name: str, effort: str = "medium") -> Conversation:
        """``messages``: [{"role": "system" | "user" | "assistant", "content": str}]. ``effort`` ("low" | "medium" |
        "high") is how hard the model thinks, where the provider supports it."""

    def complete(self, messages: list[dict], schema: dict, schema_name: str, effort: str = "medium") -> Completion:
        """One answer without tools: JSON text matching ``schema``."""

    def check(self) -> dict:
        """Confirms the key works and the model exists, without spending tokens."""
