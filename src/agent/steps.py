"""The agent's investigation as people see it (#158): each tool call it made, in plain words, without the raw result.

A step has three parts, and each comes from somewhere different:

- ``reason``: why the agent made the call, in its own words. The model writes it as an argument of the call
  (every tool has one), before it has seen any result, so it states an intention, never a finding.
- ``asked``: what it looked up, written here from the call's arguments ("Memory on web1"). The window or moment
  is left to ``from``/``to``/``at`` so the page can show it in the viewer's clock.
- ``found``: one line of facts written here from the result: numbers and names the tool returned, such as
  "memory 41–96 %, average 63 %". It never says what they mean; that is the diagnosis's job, and the diagnosis
  has to cite the facts it rests on.

Nothing here calls the model. A result that doesn't have the expected shape gives "Done" rather than a guess. See
docs/agent-investigation.md.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable

from .bounds import clip
from .tools.servers import HOST_METRICS

MAX_STEPS = 30
REFUSED = "Not run: the look-up budget was used up."
DONE = "Done."


def _number(value) -> str:
    """12 · 3.4 · 0.42: whole numbers stay whole, small ones keep a decimal or two. Anything else is not a
    number to show, so the whole line falls back to DONE (describe catches the ValueError)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("not a number")
    if abs(value) >= 100 or float(value).is_integer():
        return str(round(value))
    return f"{value:.1f}".rstrip("0").rstrip(".") if abs(value) >= 1 else f"{value:.2f}".rstrip("0").rstrip(".")


def _with_unit(value, unit: str) -> str:
    return f"{_number(value)} {unit}" if unit else _number(value)


def _count(number: int, one: str, many: str | None = None) -> str:
    return f"{number} {one if number == 1 else many or one + 's'}"


def _first_up(text: str) -> str:
    return text[:1].upper() + text[1:]


def _listed(items: list[str], limit: int = 3) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _metric_name(series: dict) -> str:
    return str(series.get("label", "")).split(" · ")[0] or "value"


def _range(series: dict) -> str:
    """"memory 41–96 %, average 63 %" from a series' stats."""
    stats, unit = series.get("stats") or {}, series.get("unit") or ""
    low, high = stats.get("min"), stats.get("max")
    if low == high:
        return f"{_metric_name(series)} {_with_unit(high, unit)} throughout"
    return f"{_metric_name(series)} {_number(low)}–{_with_unit(high, unit)}, average {_with_unit(stats.get('avg'), unit)}"


# --- what was asked, from the arguments ---------------------------------------------------------------------------

def _metric_names(names: list) -> str:
    labels = [HOST_METRICS[name][0] for name in names if name in HOST_METRICS]
    return _first_up(_listed(labels, 4)) if labels else "CPU, memory, swap, load and root disk"


ASKED: dict[str, Callable[[dict], str]] = {
    "server_info": lambda a: f"Machine facts for {a.get('server')}",
    "host_metrics": lambda a: f"{_metric_names(a.get('metrics') or [])} on {a.get('server')}",
    "list_containers": lambda a: f"Every container on {a.get('server')}",
    "container_history": lambda a: f"Container {a.get('container')} on {a.get('server')}",
    "disk_usage": lambda a: f"{'Filesystem ' + str(a['mount']) if a.get('mount') else 'Filesystems'} on {a.get('server')}, last {_count(a.get('days') or 0, 'day')}",
    "events": lambda a: f"Events on {a.get('server')}" + (f" ({_listed([_kind(kind) for kind in a['kinds']], 4)})" if a.get("kinds") else ""),
    "compare_to_baseline": lambda a: f"{_first_up(HOST_METRICS.get(a.get('metric'), (str(a.get('metric')),))[0])} on {a.get('server')} against its usual level for that hour",
    "find_incidents": lambda a: "Earlier incidents" + (f" on {a['server']}" if a.get("server") else " on every server") + (f" of kind {a['kind']}" if a.get("kind") else ""),
    "fleet_snapshot": lambda a: "Every server at one moment",
    "check_results": lambda a: f"Availability check {a['check']}" if a.get("check") else f"Availability checks run from {a.get('server')}",
    "network_state": lambda a: f"Open ports and firewall on {a.get('server')}",
}


# --- what was found, from the result ------------------------------------------------------------------------------

def _server_info(r: dict) -> str:
    parts = ["online" if r.get("online") else "offline"]
    parts += [part for part in (r.get("os"), r.get("cpu_cores") and _count(r["cpu_cores"], "core"), r.get("memory_gb") and f"{_number(r['memory_gb'])} GB memory") if part]
    if r.get("updates_pending"):
        parts.append(_count(r["updates_pending"], "update") + " pending")
    if r.get("reboot_required"):
        parts.append("needs a reboot")
    return ", ".join(parts)


def _host_metrics(r: dict) -> str:
    series = r.get("series") or []
    if not series:
        return r.get("note") or "No data in that window."
    lines = [_range(item) for item in series[:3]]
    return "; ".join(lines) + (f"; and {len(series) - 3} more" if len(series) > 3 else "")


def _list_containers(r: dict) -> str:
    containers = r.get("containers") or []
    if not containers:
        return r.get("note") or "No containers."
    total = len(containers) + (r.get("more_containers") or 0)
    stopped = [item.get("name") for item in containers if item.get("status") != "running"]
    killed = sum(1 for item in containers if item.get("oom_killed"))
    parts = [_count(total, "container"), f"{total - len(stopped)} running"]
    if stopped:
        parts.append("not running: " + _listed(stopped))
    if killed:
        parts.append(f"{killed} killed for running out of memory")
    return ", ".join(parts)


def _container_history(r: dict) -> str:
    series = {str(item.get("id", "")).rsplit(".", 1)[-1]: item for item in r.get("series") or []}
    parts = []
    memory = series.get("memory_mb")
    if memory:
        limit = r.get("memory_limit_mb")
        parts.append(f"memory up to {_with_unit(memory['stats'].get('max'), 'MB')}" + (f" of {_with_unit(limit, 'MB')}" if limit else ""))
    cpu = series.get("cpu")
    if cpu:
        parts.append(f"CPU up to {_with_unit(cpu['stats'].get('max'), '%')}")
    restarts = series.get("restarts")
    if restarts:
        added = (restarts["stats"].get("last") or 0) - (restarts["stats"].get("first") or 0)
        if added > 0:
            parts.append(f"restarted {_number(added)}×")
    if r.get("not_running_pct"):
        parts.append(f"not running {_number(r['not_running_pct'])} % of the time")
    if r.get("recreated"):
        parts.append("recreated")
    changes = r.get("changes") or []
    if changes:
        parts.append(_count(len(changes), "change") + f", the last: {clip(changes[-1].get('change'), 80)}")
    return ", ".join(parts) or "No data in that window."


def _disk_usage(r: dict) -> str:
    lines = []
    for mount in (r.get("mounts") or [])[:2]:
        line = f"{mount.get('mount')} {_with_unit(mount.get('used_pct'), '%')} used"
        growth = mount.get("growth_gb_per_day")
        if growth is not None:
            line += ", not growing" if abs(growth) < 0.05 else f", {'+' if growth > 0 else ''}{_number(growth)} GB a day"
        if mount.get("full_in_days") is not None:
            line += f", full in about {_count(round(mount['full_in_days']), 'day')}"
        lines.append(line)
    more = len(r.get("mounts") or []) - 2 + (r.get("more_mounts") or 0)
    return ("; ".join(lines) + (f"; and {more} more" if more > 0 else "")) if lines else "No filesystems reported."


def _kind(kind: str) -> str:
    return str(kind).replace("oom_kill", "OOM kill").replace("_", " ")


def _events(r: dict) -> str:
    total = r.get("total") or 0
    if not total:
        return "No events in that window."
    listed = r.get("events") or []
    kinds = Counter(_kind(event.get("kind", "event")) for event in listed)
    summary = ", ".join(f"{count} {kind}" for kind, count in kinds.most_common(4))
    if not summary:
        return _count(total, "event")
    # Only the newest are returned, so the counts by kind are of those, and the line says so.
    return _count(total, "event") + (f"; of the newest {len(listed)}: {summary}" if len(listed) < total else f": {summary}")


def _compare_to_baseline(r: dict) -> str:
    unit = r.get("unit") or ""
    if r.get("value") is None:
        return "No data for that hour."
    line = f"{_with_unit(r['value'], unit)} that hour"
    if r.get("usual_for_this_hour") is not None:
        line += f", usually {_with_unit(r['usual_for_this_hour'], unit)}"
        if r.get("difference_pct") is not None:
            line += f" ({'+' if r['difference_pct'] >= 0 else ''}{_number(r['difference_pct'])} %)"
    else:
        line += ", no earlier weeks to compare with"
    return line


def _find_incidents(r: dict) -> str:
    total = r.get("total") or 0
    listed = r.get("incidents") or []
    if not total:
        return "No incidents in that window."
    newest = listed[0] if listed else {}
    return _count(total, "incident") + (f", the newest {newest.get('reference')}: {clip(newest.get('title'), 80)}" if newest else "")


def _fleet_snapshot(r: dict) -> str:
    servers = r.get("servers") or []
    offline = [item.get("server") for item in servers if not item.get("online")]
    troubled = [item.get("server") for item in servers if item.get("open_incidents")]
    failing = [item.get("server") for item in servers if item.get("failing_checks")]
    parts = [_count(len(servers) + (r.get("more_servers") or 0), "server")]
    parts.append("offline: " + _listed(offline) if offline else "all online")
    if troubled:
        parts.append("open incidents on " + _listed(troubled))
    if failing:
        parts.append("failing checks on " + _listed(failing))
    return ", ".join(parts)


def _check_results(r: dict) -> str:
    checks = r.get("checks") or []
    if not checks:
        return r.get("note") or "No availability checks."
    lines = []
    for check in checks[:2]:
        line = f"{check.get('name')} " + (f"{_number(check['uptime_pct'])} % up" if check.get("uptime_pct") is not None else "no results")
        if check.get("down"):
            line += f", down {_count(check['down'], 'time')}"
            if check.get("errors"):
                line += f" ({clip(check['errors'][0].get('detail'), 60)})"
        lines.append(line)
    return "; ".join(lines) + (f"; and {len(checks) - 2} more" if len(checks) > 2 else "")


def _network_state(r: dict) -> str:
    listening = r.get("listening") or []
    total = len(listening) + (r.get("more_listening") or 0)
    open_to_others = sum(1 for item in listening if item.get("reachable_from") == "other machines")
    firewalls = [item.get("name") for item in r.get("firewall") or [] if item.get("enabled")]
    changes = len(r.get("changes_last_7_days") or [])
    parts = [_count(total, "port") + " listening", f"{open_to_others} reachable from other machines",
             ("firewall: " + _listed(firewalls)) if firewalls else "no firewall enabled", _count(changes, "change") + " in the last 7 days"]
    return ", ".join(parts)


FOUND: dict[str, Callable[[dict], str]] = {
    "server_info": _server_info, "host_metrics": _host_metrics, "list_containers": _list_containers, "container_history": _container_history,
    "disk_usage": _disk_usage, "events": _events, "compare_to_baseline": _compare_to_baseline, "find_incidents": _find_incidents,
    "fleet_snapshot": _fleet_snapshot, "check_results": _check_results, "network_state": _network_state,
}


# --- one step -----------------------------------------------------------------------------------------------------

def _json(text) -> dict:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def describe(entry: dict) -> dict:
    """One trail entry (investigate.py) as people see it. Never raises: an odd entry gives a plain step."""
    tool = str(entry.get("tool") or "unknown")
    arguments = _json(entry.get("arguments"))
    status = entry.get("status") if entry.get("status") in ("ok", "error", "refused") else "error"
    result = _json(entry.get("result")) if status == "ok" else {}
    try:
        asked = ASKED[tool](arguments) if tool in ASKED and arguments else _first_up(tool.replace("_", " "))
    except (KeyError, TypeError, ValueError, AttributeError):
        asked = _first_up(tool.replace("_", " "))
    if status == "refused":
        found = REFUSED
    elif status == "error":
        found = clip(entry.get("result") or "The look-up failed.", 200)
    else:
        try:
            found = FOUND[tool](result) if tool in FOUND else DONE
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            found = DONE
        if result.get("truncated"):
            found += " (the result was shortened to fit)"
    window = {"from": result.get("from") or arguments.get("from"), "to": result.get("to") or arguments.get("to"), "at": result.get("at") or result.get("hour") or arguments.get("at")}
    return {"tool": tool, "status": status, "reason": clip(entry.get("reason") or arguments.get("reason") or "", 200) or None,
            "asked": clip(asked, 200), "found": clip(found, 300),
            **{key: value if isinstance(value, str) and value.strip() else None for key, value in window.items()}}


def describe_all(trail) -> list[dict]:
    return [describe(entry) for entry in (trail or [])[:MAX_STEPS] if isinstance(entry, dict)]
