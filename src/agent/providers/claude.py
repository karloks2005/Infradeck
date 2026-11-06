"""Claude through the Anthropic API, with structured outputs so a reply always parses."""
from __future__ import annotations

from .base import MAX_RETRIES, TIMEOUT_SECONDS, Completion

# Enough for adaptive thinking plus the longest answer the schemas allow, and small enough for a non-streaming request.
MAX_TOKENS = 16000


class ClaudeProvider:
    name = "Anthropic"

    def __init__(self, api_key: str, model: str, client=None):
        self.model = model
        if client is None:
            import anthropic   # imported here so the rest of the app runs without the package
            client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        self.client = client

    def complete(self, messages: list[dict], schema: dict, schema_name: str, effort: str = "medium") -> Completion:
        system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
        turns = [{"role": message["role"], "content": message["content"]} for message in messages if message["role"] != "system"]
        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=turns,
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            cache_control={"type": "ephemeral"},   # the system prompt and earlier turns repeat on every call
        )
        if response.stop_reason == "refusal":
            raise ValueError("The model refused to answer.")
        if response.stop_reason == "max_tokens":
            raise ValueError("The model's answer was cut off before it was complete.")
        usage = response.usage
        return Completion(
            text="".join(block.text for block in response.content if block.type == "text"),
            model=getattr(response, "model", self.model),
            # Cached input is billed differently, but it is still input: counting it keeps the budget on the safe side.
            input_tokens=(usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0),
            output_tokens=usage.output_tokens or 0,
        )

    def check(self) -> dict:
        model = self.client.models.retrieve(self.model)
        return {"provider": self.name, "model": getattr(model, "id", self.model)}
