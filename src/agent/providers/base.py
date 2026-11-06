"""What every provider returns and offers. A provider never sees the database or the servers; the agent decides
what goes into the messages, and masks them first (masking.py)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

TIMEOUT_SECONDS = 60
MAX_RETRIES = 2


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class Provider(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict], schema: dict, schema_name: str, effort: str = "medium") -> Completion:
        """``messages``: [{"role": "system" | "user" | "assistant", "content": str}]. Returns JSON text matching ``schema``.
        ``effort`` ("low" | "medium" | "high") is how hard the model thinks, where the provider supports it."""

    def check(self) -> dict:
        """Confirms the key works and the model exists, without spending tokens."""
