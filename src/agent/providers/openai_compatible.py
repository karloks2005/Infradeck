"""OpenAI, and Google Gemini through its OpenAI-compatible endpoint: both take the same Chat Completions request."""
from __future__ import annotations

from .base import MAX_RETRIES, TIMEOUT_SECONDS, Completion

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class OpenAIProvider:
    """Chat Completions with Structured Outputs, so a reply always parses."""

    def __init__(self, api_key: str, model: str, name: str = "OpenAI", base_url: str | None = None, client=None):
        self.name, self.model = name, model
        if client is None:
            from openai import OpenAI   # imported here so the rest of the app runs without the package
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        self.client = client

    def complete(self, messages: list[dict], schema: dict, schema_name: str, effort: str = "medium") -> Completion:
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
