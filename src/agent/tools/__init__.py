"""The agent's read-only tools (#114): what it may look up in Infradeck's database while it investigates.

Every tool reads data Infradeck already stores. None of them writes, and none contacts a collector or a monitored
machine. The list is closed: ``run`` refuses any other name, and checks the arguments against the tool's schema
before anything is read. What comes back is bounded (a few dozen points per series, the newest entries of lists,
clipped text, about 12 KB in all), masked (masking.py), and carries fact IDs that a diagnosis can cite; ``labels``
says what each ID is. Check targets are always shown without credentials or query string, as non-admins see them,
because the result goes to the model provider; nothing that only admins can see (IP addresses, users, the audit log,
enrolment) is returned, so a tool gives the same answer whoever is asking. Every call also carries a ``reason``, the
question the model wants answered, which only steps.py uses.

The loop that offers these to the model, with its budget and audit log, is investigate.py. See docs/assistant.md.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from ..masking import mask, mask_text
from ..providers.base import Tool
from . import activity, containers, servers
from .common import MAX_DAYS, Facts, ToolError, merge_labels

__all__ = ["TOOLS", "Output", "ToolError", "merge_labels", "run"]

MAX_RESULT_CHARS = 12000
MAX_STRING = 200
MAX_ITEMS = 12


REASON = {"type": "string", "description": "One sentence: the question this call should answer, such as \"Did memory grow in the days before "
           "the restarts?\". Write it before you see the result. It is shown to the people reading your answer."}


def _object(**properties) -> dict:
    """A tool's arguments. Every tool also takes ``reason``, which the tool itself ignores (steps.py shows it)."""
    properties = {**properties, "reason": REASON}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


class _Optional(dict):
    """A text argument that may be left empty ("" or null), which the tool reads as "not given". It is a plain
    string in the schema because strict schemas allow at most 16 nullable or union-typed parameters per request,
    counted over every tool and the answer's schema (Anthropic rejects more); see docs/assistant.md."""


def _optional(description: str) -> dict:
    return _Optional(type="string", description=description)


SERVER = {"type": "string", "description": "The server's hostname, as the evidence names it."}
FROM = _optional("Start of the window, ISO 8601 in UTC (2026-11-07T10:00). Empty: 24 hours before `to`.")
TO = _optional("End of the window, ISO 8601 in UTC. Empty: now.")
AT = _optional("The moment to look at, ISO 8601 in UTC. Empty: now.")
WINDOW_NOTE = (f" Any window within the last {MAX_DAYS} days: every check-in for the last 7 days, hourly averages and peaks before that."
               " Start wide, then narrow down to the interesting hours.")


@dataclass
class Output:
    content: str               # JSON for the model
    labels: dict[str, str]     # fact ID -> what it is, for checking citations and showing them


Handler = Callable[[object, dict, datetime, Facts], dict]

_REGISTRY: dict[str, tuple[Tool, Handler]] = {tool.name: (tool, handler) for tool, handler in [
    (Tool("server_info", "What a server is and how it is doing now: OS, kernel, CPU, memory, collector and Docker versions, last check-in, online or not, "
          "pending updates, uptime, whether it needs a reboot, clock sync, Docker and internet reachability.", _object(server=SERVER)), servers.server_info),
    (Tool("host_metrics", "A server's CPU, memory, swap, load, root disk and more over a window, with min, max, average and up to 40 points per metric. "
          "Each point is the highest value of its stretch, so short peaks stay visible." + WINDOW_NOTE,
          _object(server=SERVER, metrics={"type": "array", "items": {"type": "string", "enum": list(servers.HOST_METRICS)},
                                          "description": "Which metrics. Empty: cpu, ram, swap, load and disk."}, **{"from": FROM, "to": TO})), servers.host_metrics),
    (Tool("list_containers", "Every Docker container on a server at one check-in: status, health, CPU, memory and its limit, restarts, exit code, OOM kill, image. "
          "Not running and the largest come first. Only for the last 7 days.", _object(server=SERVER, at=AT)), containers.list_containers),
    (Tool("container_history", "One container over a window: CPU, memory and restart count as series, how much of the time it was not running, its memory limit, "
          "and (for the last 7 days) each change of status, health, exit code, OOM kill or image." + WINDOW_NOTE,
          _object(server=SERVER, container={"type": "string", "description": "The container's name."}, **{"from": FROM, "to": TO})), containers.container_history),
    (Tool("disk_usage", "Every filesystem of a server (or one): used space and inodes now, usage over the last `days`, growth in GB per day and, when it grows, "
          "in about how many days it fills up.", _object(server=SERVER, mount=_optional("One mount point such as /var. Empty: all of them."),
                                                         days={"type": "integer", "description": f"How many days back to look at, 1 to {MAX_DAYS}."})), servers.disk_usage),
    (Tool("events", "What the server recorded in a window: reboots, OOM kills, kernel faults and errors, image changes, containers added or removed, ports "
          "opened or closed, firewall changes, package updates. The newest 30, oldest first." + WINDOW_NOTE,
          _object(server=SERVER, kinds={"type": "array", "items": {"type": "string", "enum": list(activity.EVENT_KINDS)}, "description": "Which kinds. Empty: all."},
                  **{"from": FROM, "to": TO})), activity.events),
    (Tool("compare_to_baseline", "Whether a value is unusual: a host metric's average over one hour, next to the same hour yesterday and the average of "
          "that hour of the day over the previous 4 weeks.", _object(server=SERVER, metric={"type": "string", "enum": list(servers.BASELINE_METRICS)}, at=AT)),
     servers.compare_to_baseline),
    (Tool("find_incidents", "Earlier incidents on one server or all of them: title, kind, severity, how long they lasted and how they were resolved. Newest "
          "first. The window defaults to the last 30 days.",
          _object(server=_optional("A hostname. Empty: every server."),
                  kind=_optional("Only this kind, as in the evidence's incident `kind` (host:disk, container:restart_loop, …). Empty: every kind."),
                  **{"from": _optional("Start of the window, ISO 8601 in UTC. Empty: 30 days before `to`."), "to": TO})), activity.find_incidents),
    (Tool("fleet_snapshot", "Every server at one moment: online or not, open incidents and the worst of them, availability checks failing from it. "
          "Shows whether a problem is on one machine or many.", _object(at=AT)), activity.fleet_snapshot),
    (Tool("check_results", "Availability checks (HTTP, TCP, TLS, DNS, ping …) over a window: uptime, when they were down, the errors they got and response "
          "times. Give one check by name, or a server for every check that runs from it." + WINDOW_NOTE,
          _object(check=_optional("The check's name."), server=_optional("A hostname: every check run from it."), **{"from": FROM, "to": TO})),
     activity.check_results),
    (Tool("network_state", "A server's listening ports (and whether other machines can reach them), its firewall configuration, and port and firewall changes "
          "in the last 7 days.", _object(server=SERVER)), servers.network_state),
]}
# What the provider gets: the same schemas as plain JSON (the _Optional marker is only for checking arguments here).
TOOLS: list[Tool] = [Tool(tool.name, tool.description, json.loads(json.dumps(tool.schema))) for tool, _ in _REGISTRY.values()]


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _check(schema: dict, value, path: str) -> None:
    """``value`` against the small part of JSON Schema the tools use. Raises ToolError naming the argument."""
    if isinstance(schema, _Optional) and _blank(value):
        return
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            raise ToolError(f"Give exactly these arguments: {', '.join(schema['properties'])}.")
        for key, sub in schema["properties"].items():
            _check(sub, value[key], key)
    elif kind == "array":
        if not isinstance(value, list) or len(value) > MAX_ITEMS:
            raise ToolError(f"{path} must be a list of at most {MAX_ITEMS} items.")
        for item in value:
            _check(schema["items"], item, path)
    elif kind == "string":
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_STRING:
            raise ToolError(f"{path} must be a non-empty text of at most {MAX_STRING} characters.")
        if "enum" in schema and value not in schema["enum"]:
            raise ToolError(f"{path} must be one of: {', '.join(schema['enum'])}.")
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolError(f"{path} must be a whole number.")


def _longest_list(value, best=None):
    """The longest list anywhere in ``value`` (by its JSON size), to shorten first."""
    if isinstance(value, list):
        if len(value) > 1 and (best is None or len(json.dumps(value)) > len(json.dumps(best))):
            best = value
        for item in value:
            best = _longest_list(item, best)
    elif isinstance(value, dict):
        for item in value.values():
            best = _longest_list(item, best)
    return best


def _is_series(items: list) -> bool:
    return all(isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) for item in items)


def _fit(content: dict) -> str:
    """JSON of at most MAX_RESULT_CHARS. Until it fits, the longest list is halved: a series by joining neighbouring
    points (keeping the higher value, so peaks survive), any other list by dropping its oldest half."""
    text = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    while len(text) > MAX_RESULT_CHARS:
        longest = _longest_list(content)
        if longest is None:
            return json.dumps({"truncated": True, "note": "The result was too large. Ask for a narrower window or fewer items."})
        if _is_series(longest):
            longest[:] = [[longest[index][0], max(point[1] for point in longest[index:index + 2])] for index in range(0, len(longest), 2)]
        else:
            del longest[: len(longest) // 2]
        content["truncated"] = True
        text = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return text


def run(db, name: str, arguments, now: datetime | None = None) -> Output:
    """Run one tool. Raises ToolError for a call the model can correct (unknown tool, bad arguments, unknown server)."""
    if name not in _REGISTRY:
        raise ToolError(f"There is no tool named {str(name)[:60]!r}.")
    tool, handler = _REGISTRY[name]
    _check(tool.schema, arguments, "arguments")
    # An optional argument left empty reaches the tool as None, whether the model sent "" or null.
    arguments = {key: None if isinstance(tool.schema["properties"][key], _Optional) and _blank(value) else value for key, value in arguments.items()}
    now = now or datetime.now(timezone.utc)
    facts = Facts()
    text = _fit(mask(handler(db, arguments, now, facts)))
    labels = {mask_text(fact_id): mask_text(label) for fact_id, label in facts.labels.items()}
    # Only facts still in the result can be cited: a list shortened to fit loses its labels too.
    return Output(text, {fact_id: label for fact_id, label in labels.items() if json.dumps(fact_id) in text})
