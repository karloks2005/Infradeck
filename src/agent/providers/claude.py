"""Claude through the Anthropic API, with structured outputs so a final answer always parses."""
from __future__ import annotations

from .base import MAX_RETRIES, TIMEOUT_SECONDS, Completion, Conversation, Tool, ToolCall, ToolResult, Turn

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

    def start(self, messages: list[dict], tools: list[Tool], schema: dict, schema_name: str, effort: str = "medium") -> ClaudeConversation:
        return ClaudeConversation(self, messages, tools, schema, effort)

    def complete(self, messages: list[dict], schema: dict, schema_name: str, effort: str = "medium") -> Completion:
        return self.start(messages, [], schema, schema_name, effort).step().completion

    def check(self) -> dict:
        model = self.client.models.retrieve(self.model)
        return {"provider": self.name, "model": getattr(model, "id", self.model)}


class ClaudeConversation(Conversation):
    def __init__(self, provider: ClaudeProvider, messages: list[dict], tools: list[Tool], schema: dict, effort: str):
        self.provider, self.schema, self.effort = provider, schema, effort
        self.system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
        self.history = [{"role": message["role"], "content": message["content"]} for message in messages if message["role"] != "system"]
        # Not strict: Anthropic compiles strict tools and the strict answer schema into one grammar with an unpublished
        # size limit, and the agent's tools with the diagnosis schema are over it (#199). The answer stays strict, and
        # the backend checks every call's arguments itself (tools._check), so a malformed call goes back as an error.
        self.tools = [{"name": tool.name, "description": tool.description, "input_schema": tool.schema} for tool in tools]

    def step(self, allow_tools: bool = True) -> Turn:
        extra = {"tools": self.tools, "tool_choice": {"type": "auto" if allow_tools else "none"}} if self.tools else {}
        response = self.provider.client.messages.create(
            model=self.provider.model,
            max_tokens=MAX_TOKENS,
            system=self.system,
            messages=list(self.history),
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": self.schema}},
            cache_control={"type": "ephemeral"},   # the system prompt, tools and earlier turns repeat on every call
            **extra,
        )
        if response.stop_reason == "refusal":
            raise ValueError("The model refused to answer.")
        if response.stop_reason == "max_tokens":
            raise ValueError("The model's answer was cut off before it was complete.")
        self.history.append({"role": "assistant", "content": response.content})   # unchanged, thinking blocks included
        calls = [ToolCall(block.id, block.name, block.input) for block in response.content if block.type == "tool_use"]
        usage = response.usage
        text = "" if calls else "".join(block.text for block in response.content if block.type == "text")
        return Turn(Completion(
            text=text,
            model=getattr(response, "model", self.provider.model),
            # Cached input is billed differently, but it is still input: counting it keeps the budget on the safe side.
            input_tokens=(usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0),
            output_tokens=usage.output_tokens or 0,
        ), calls)

    def _append_results(self, results: list[ToolResult]) -> None:
        # All results of one turn go back in a single message.
        self.history.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": result.call_id, "content": result.content, **({"is_error": True} if result.is_error else {})}
            for result in results
        ]})
