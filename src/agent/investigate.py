"""The agent's investigation (#157): one conversation with the model in which it may look things up with the
read-only tools (tools/) before it answers. The diagnosis and the chat both run through ``investigate``.

- The budget is a number of tool calls. A call past it is answered with BUDGET_USED_UP instead of running. Once the
  budget is used up, the month's spending cap is reached (usage.budget) or the deadline has passed, the next step is
  made with ``allow_tools=False``, so the model has to answer. The list of tools never changes during a conversation:
  that would make the provider's cache and Claude's earlier thinking invalid.
- A budget of 0 offers no tools at all, so the agent answers from what it was given in a single call.
- Every call, run or refused, is written to the audit log (event type ``agent_tool_call``) and kept in ``trail``.
- A ToolError (bad arguments, an unknown server) goes back to the model so it can correct the call. Any other
  failure is logged on the server; the model only hears that the look-up failed.

What a tool returns is masked twice: by tools.run, and by ``Conversation.add_tool_results`` on its way to the model.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from src.backend.auth import record_audit_log

from . import tools, usage
from .bounds import clip
from .masking import mask, mask_text
from .providers import Completion, Conversation, Provider, ToolCall, ToolResult, Turn

logger = logging.getLogger(__name__)

BUDGET_USED_UP = "Tool budget used up; give your final answer now."
LOOKUP_FAILED = "The look-up failed. Answer from what you have, or try another tool."
EVENT_TYPE = "agent_tool_call"
# Model calls allowed beyond one per tool call in the budget: the final answer, and one more in case the model asks
# for a tool where it was told to answer. After that the investigation fails instead of going round in circles.
EXTRA_STEPS = 2

Recorder = Callable[[Completion, int], None]   # every model call: what it returned and how long it took, in ms
Auditor = Callable[[dict], None]               # every tool call, as a trail entry


@dataclass
class Investigation:
    conversation: Conversation
    completion: Completion                    # the final answer
    record: Recorder
    labels: dict[str, str] = field(default_factory=dict)   # fact IDs the tools returned, for citations
    trail: list[dict] = field(default_factory=list)        # each tool call: tool, arguments, status, result

    def ask_again(self, text: str) -> Completion:
        """The answer couldn't be used: say why, in the same conversation and without tools, so the look-ups
        aren't paid for twice."""
        self.conversation.add_user(text)
        self.completion = _step(self.conversation, False, self.record).completion
        return self.completion


def _step(conversation: Conversation, allow_tools: bool, record: Recorder) -> Turn:
    started = time.monotonic()
    turn = conversation.step(allow_tools=allow_tools)
    record(turn.completion, round((time.monotonic() - started) * 1000))
    return turn


def _arguments(call: ToolCall) -> str:
    return clip(json.dumps(mask(call.arguments), ensure_ascii=False, separators=(",", ":")), 500)


def _run(db, call: ToolCall) -> tuple[ToolResult, dict, dict[str, str]]:
    """One allowed call: its result for the model, its trail entry and the facts it found."""
    entry = {"tool": clip(str(call.name), 60), "arguments": _arguments(call)}
    try:
        output = tools.run(db, call.name, call.arguments)
    except tools.ToolError as exc:
        message = mask_text(str(exc))   # it may repeat an argument
        return ToolResult(call.id, message, True), {**entry, "status": "error", "result": message}, {}
    except Exception:
        logger.exception("The agent's tool %s failed.", entry["tool"])
        db.rollback()   # a failed query leaves the session unusable for the next one
        return ToolResult(call.id, LOOKUP_FAILED, True), {**entry, "status": "error", "result": LOOKUP_FAILED}, {}
    return ToolResult(call.id, output.content), {**entry, "status": "ok", "result": output.content}, output.labels


def investigate(db, provider: Provider, messages: list[dict], schema: dict, schema_name: str, *, budget: int, effort: str, record: Recorder,
                audit: Auditor, deadline_seconds: float | None = None) -> Investigation:
    """Ask the model, let it make up to ``budget`` tool calls, and return its final answer. ``effort`` applies when
    no tools are offered; with tools it is always "high". Provider errors are raised as they come."""
    conversation = provider.start(messages, tools.TOOLS if budget else [], schema, schema_name, "high" if budget else effort)
    started = time.monotonic()
    labels, trail, used = {}, [], 0
    for _ in range(budget + EXTRA_STEPS):
        allow = (used < budget and usage.budget(db)["state"] != "paused"
                 and (deadline_seconds is None or time.monotonic() - started < deadline_seconds))
        turn = _step(conversation, allow, record)
        if not turn.tool_calls:
            return Investigation(conversation, turn.completion, record, labels, trail)
        results = []
        for call in turn.tool_calls:
            if allow and used < budget:
                used += 1
                result, entry, found = _run(db, call)
                labels = tools.merge_labels(labels, found)
            else:
                result = ToolResult(call.id, BUDGET_USED_UP, True)
                entry = {"tool": clip(str(call.name), 60), "arguments": _arguments(call), "status": "refused", "result": BUDGET_USED_UP}
            trail.append(entry)
            audit(entry)
            results.append(result)
        conversation.add_tool_results(results)
    raise ValueError("The model kept asking for tools after it was told to answer.")


def audit_log(db, *, title: str, user_id: int | None = None, conversation_id: str | None = None) -> Auditor:
    """Writes each tool call to the audit log, under ``title`` (the incident or the chat question)."""
    def write(entry: dict) -> None:
        record_audit_log(db, user_id=user_id, level="INFO" if entry["status"] == "ok" else "WARN", source="agent", event_type=EVENT_TYPE,
                         message=f"The agent looked up {entry['tool']}" + ("" if entry["status"] == "ok" else f" ({entry['status']})"),
                         task_title=clip(title, 255), conversation_id=conversation_id,
                         steps=[f"Arguments: {entry['arguments']}", f"Result: {entry['status']}" + ("" if entry["status"] == "ok" else f": {clip(entry['result'], 200)}")])
    return write
