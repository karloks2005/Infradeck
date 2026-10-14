"""The language model behind the agent, behind one small interface so another provider can be added later.

A provider turns a list of chat messages into one reply that matches a JSON schema, and reports how many tokens
that took. It never sees the database or the servers; the agent decides what goes into the messages.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

DEFAULT_MODEL = "gpt-5-mini"
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

    def complete(self, messages: list[dict], schema: dict, schema_name: str) -> Completion:
        """``messages``: [{"role": "system" | "user" | "assistant", "content": str}]. Returns JSON text matching ``schema``."""

    def check(self) -> dict:
        """Confirms the key works and the model exists, without spending tokens."""


def configured_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def configured_model() -> str:
    return os.getenv("AGENT_MODEL", "").strip() or DEFAULT_MODEL


class OpenAIProvider:
    """The OpenAI API through Chat Completions, with Structured Outputs so a reply always parses."""

    name = "OpenAI"

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None):
        key = api_key or configured_key()
        if not key and client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        self.model = model or configured_model()
        if client is None:
            from openai import OpenAI   # imported here so the rest of the app runs without the package
            client = OpenAI(api_key=key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        self.client = client

    def complete(self, messages: list[dict], schema: dict, schema_name: str) -> Completion:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        )
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ValueError(f"The model refused to answer: {choice.message.refusal}")
        usage = getattr(response, "usage", None)
        return Completion(
            text=choice.message.content or "",
            model=getattr(response, "model", self.model),
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def check(self) -> dict:
        model = self.client.models.retrieve(self.model)
        return {"provider": self.name, "model": getattr(model, "id", self.model)}


def get_provider() -> Provider:
    return OpenAIProvider()
