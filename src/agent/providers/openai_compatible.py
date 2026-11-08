"""Google Gemini through its OpenAI-compatible endpoint, which takes a Chat Completions request. OpenAI itself uses
the Responses API (openai_responses.py), since its reasoning models can't use tools through Chat Completions."""
from __future__ import annotations

import json

from .base import MAX_RETRIES, TIMEOUT_SECONDS, Completion, Conversation, Tool, ToolCall, ToolResult, Turn

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class OpenAIProvider:
    """Chat Completions with Structured Outputs, so a final answer always parses."""

    def __init__(self, api_key: str, model: str, name: str = "OpenAI", base_url: str | None = None, client=None):
        self.name, self.model = name, model
        if client is None:
            from openai import OpenAI   # imported here so the rest of the app runs without the package
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        self.client = client

    def start(self, messages: list[dict], tools: list[Tool], schema: dict, schema_name: str, effort: str = "medium") -> OpenAIConversation:
        return OpenAIConversation(self, messages, tools, schema, schema_name)

    def complete(self, messages: list[dict], schema: dict, schema_name: str, effort: str = "medium") -> Completion:
        return self.start(messages, [], schema, schema_name, effort).step().completion

    def check(self) -> dict:
        model = self.client.models.retrieve(self.model)
        return {"provider": self.name, "model": getattr(model, "id", self.model)}


class OpenAIConversation(Conversation):
    def __init__(self, provider: OpenAIProvider, messages: list[dict], tools: list[Tool], schema: dict, schema_name: str):
        self.provider = provider
        self.history = [dict(message) for message in messages]
        self.response_format = {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
        self.tools = [{"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.schema, "strict": True}} for tool in tools]

    def step(self, allow_tools: bool = True) -> Turn:
        extra = {"tools": self.tools, "tool_choice": "auto" if allow_tools else "none"} if self.tools else {}
        response = self.provider.client.chat.completions.create(model=self.provider.model, messages=list(self.history), response_format=self.response_format, **extra)
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise ValueError(f"The model refused to answer: {message.refusal}")
        raw_calls = getattr(message, "tool_calls", None) or []
        try:
            calls = [ToolCall(call.id, call.function.name, json.loads(call.function.arguments)) for call in raw_calls]
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("The model sent tool arguments that aren't valid JSON.") from exc
        if any(not isinstance(call.arguments, dict) for call in calls):
            raise ValueError("The model sent tool arguments that aren't a JSON object.")
        turn = {"role": "assistant", "content": message.content}
        if raw_calls:
            turn["tool_calls"] = [{"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}} for call in raw_calls]
        self.history.append(turn)
        usage = getattr(response, "usage", None)
        return Turn(Completion(
            text="" if calls else message.content or "",
            model=getattr(response, "model", self.provider.model),
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ), calls)

    def _append_results(self, results: list[ToolResult]) -> None:
        for result in results:
            self.history.append({"role": "tool", "tool_call_id": result.call_id, "content": f"Error: {result.content}" if result.is_error else result.content})
