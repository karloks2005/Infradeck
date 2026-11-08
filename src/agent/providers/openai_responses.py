"""OpenAI through the Responses API: tools, reasoning and a strict JSON answer in one request (#196).

OpenAI's reasoning models (the default, gpt-6-luna, among them) can't use tools through Chat Completions, so OpenAI
has its own provider here; Gemini keeps the OpenAI-compatible Chat Completions endpoint (openai_compatible.py).

Nothing is kept at OpenAI: every request is sent with ``store=False``. The model's reasoning still carries over
from one step to the next, because each response's items, including the reasoning in encrypted form
(``reasoning.encrypted_content``), go back unchanged with the next request.
"""
from __future__ import annotations

import json

from .base import MAX_RETRIES, TIMEOUT_SECONDS, Completion, Conversation, Tool, ToolCall, ToolResult, Turn


class OpenAIResponsesProvider:
    name = "OpenAI"

    def __init__(self, api_key: str, model: str, client=None):
        self.model = model
        if client is None:
            from openai import OpenAI   # imported here so the rest of the app runs without the package
            client = OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        self.client = client

    def start(self, messages: list[dict], tools: list[Tool], schema: dict, schema_name: str, effort: str = "medium") -> ResponsesConversation:
        return ResponsesConversation(self, messages, tools, schema, schema_name, effort)

    def complete(self, messages: list[dict], schema: dict, schema_name: str, effort: str = "medium") -> Completion:
        return self.start(messages, [], schema, schema_name, effort).step().completion

    def check(self) -> dict:
        model = self.client.models.retrieve(self.model)
        return {"provider": self.name, "model": getattr(model, "id", self.model)}


def _item(item) -> dict:
    """An output item as the next request's input, unchanged."""
    return item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else dict(item)


class ResponsesConversation(Conversation):
    def __init__(self, provider: OpenAIResponsesProvider, messages: list[dict], tools: list[Tool], schema: dict, schema_name: str, effort: str):
        self.provider, self.effort = provider, effort
        self.instructions = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
        self.history = [{"role": message["role"], "content": message["content"]} for message in messages if message["role"] != "system"]
        self.text = {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}}
        self.tools = [{"type": "function", "name": tool.name, "description": tool.description, "parameters": tool.schema, "strict": True} for tool in tools]

    def step(self, allow_tools: bool = True) -> Turn:
        extra = {"tools": self.tools, "tool_choice": "auto" if allow_tools else "none"} if self.tools else {}
        response = self.provider.client.responses.create(
            model=self.provider.model, instructions=self.instructions or None, input=list(self.history), text=self.text,
            reasoning={"effort": self.effort}, store=False, include=["reasoning.encrypted_content"], **extra,
        )
        if getattr(response, "status", None) == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            raise ValueError("The model's answer was cut off before it was complete." if reason == "max_output_tokens" else f"The model stopped before answering ({reason or 'incomplete'}).")
        output = list(response.output or [])
        for item in output:
            if item.type == "message" and any(getattr(part, "type", None) == "refusal" for part in item.content or []):
                raise ValueError("The model refused to answer.")
        try:
            calls = [ToolCall(item.call_id, item.name, json.loads(item.arguments)) for item in output if item.type == "function_call"]
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("The model sent tool arguments that aren't valid JSON.") from exc
        if any(not isinstance(call.arguments, dict) for call in calls):
            raise ValueError("The model sent tool arguments that aren't a JSON object.")
        self.history.extend(_item(item) for item in output)   # reasoning included, so the next step goes on from it
        usage = getattr(response, "usage", None)
        return Turn(Completion(
            text="" if calls else response.output_text or "",
            model=getattr(response, "model", self.provider.model),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,   # reasoning tokens included
        ), calls)

    def _append_results(self, results: list[ToolResult]) -> None:
        for result in results:
            self.history.append({"type": "function_call_output", "call_id": result.call_id, "output": f"Error: {result.content}" if result.is_error else result.content})
