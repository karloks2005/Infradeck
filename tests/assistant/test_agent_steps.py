"""The agent's look-ups as people see them (#158): each tool call in plain words, with the model's reason, what it
asked for and one line of facts from the result, written by steps.py without the model. The results are real ones,
from the tools' own test database (test_agent_tools.py)."""
import json

import pytest

from src.agent import steps, tools
from tests.assistant.test_agent_tools import EVERY_CALL, NOW, db, run  # noqa: F401  (db is a fixture)


def step_for(db, name, arguments, reason="Why?"):
    output = run(db, name, arguments, now=NOW)
    return steps.describe({"tool": name, "arguments": json.dumps(arguments), "status": "ok", "result": output.content, "reason": reason})


WINDOW = {"from": "2026-11-06T12:00", "to": "2026-11-07T12:00"}


@pytest.mark.parametrize("name, arguments, asked, found", [
    ("server_info", {"server": "web1"}, "Machine facts for web1", "online, Debian 12, 4 cores, 16 GB memory, 4 updates pending"),
    ("host_metrics", {"server": "web1", "metrics": ["cpu", "ram"], **WINDOW}, "CPU, memory on web1", "CPU 10–97 %, average 11.6 %; memory 50 % throughout"),
    ("list_containers", {"server": "web1", "at": None}, "Every container on web1", "2 containers, 1 running, not running: worker, 1 killed for running out of memory"),
    ("container_history", {"server": "web1", "container": "nginx", **WINDOW}, "Container nginx on web1", "memory up to 110 MB of 256 MB, CPU up to 2 %, restarted 1×"),
    ("disk_usage", {"server": "web1", "mount": None, "days": 2}, "Filesystems on web1, last 2 days", "/ 60 % used, +1 GB a day, full in about 40 days"),
    ("events", {"server": "web1", "kinds": [], **WINDOW}, "Events on web1", "42 events; of the newest 30: 28 packages updated, 1 port opened, 1 OOM kill"),
    ("events", {"server": "web1", "kinds": ["oom_kill"], **WINDOW}, "Events on web1 (OOM kill)", "1 event: 1 OOM kill"),
    ("compare_to_baseline", {"server": "web1", "metric": "cpu", "at": None}, "CPU on web1 against its usual level for that hour", "10 % that hour, usually 5 % (+100 %)"),
    ("find_incidents", {"server": None, "kind": None, **WINDOW}, "Earlier incidents on every server", "1 incident, the newest INC-0032: Container nginx restarted"),
    ("fleet_snapshot", {"at": None}, "Every server at one moment", "2 servers, offline: db1, open incidents on db1, web1"),
    ("check_results", {"check": "Shop", "server": None, **WINDOW}, "Availability check Shop", "Shop 95.8 % up, down 5 times (HTTP 503)"),
    ("network_state", {"server": "web1"}, "Open ports and firewall on web1", "2 ports listening, 1 reachable from other machines, firewall: ufw-conf, 1 change in the last 7 days"),
])
def test_each_tool_is_told_in_plain_words(db, name, arguments, asked, found):
    step = step_for(db, name, arguments)

    assert step["asked"] == asked and step["found"].startswith(found) and step["status"] == "ok" and step["reason"] == "Why?"


def test_every_tool_has_a_way_to_say_what_it_asked_and_found():
    assert set(steps.ASKED) == set(steps.FOUND) == {tool.name for tool in tools.TOOLS}


@pytest.mark.parametrize("name, arguments", EVERY_CALL)
def test_a_step_carries_no_raw_result_and_no_secrets(db, name, arguments):
    step = step_for(db, name, arguments)

    assert set(step) == {"tool", "status", "reason", "asked", "found", "from", "to", "at"}
    assert "hunter2" not in json.dumps(step) and "10.0.0." not in json.dumps(step) and len(step["found"]) <= 300


def test_the_window_is_left_for_the_page_to_show_in_the_viewers_clock(db):
    metrics = step_for(db, "host_metrics", {"server": "web1", "metrics": ["cpu"], "from": None, "to": None})
    containers = step_for(db, "list_containers", {"server": "web1", "at": None})

    assert (metrics["from"], metrics["to"], metrics["at"]) == ("2026-11-06T12:00+00:00", "2026-11-07T12:00+00:00", None)   # null resolved to the last 24 hours
    assert containers["at"] == "2026-11-07T12:00+00:00" and "12:00" not in metrics["asked"]


def test_a_failed_or_refused_call_says_why():
    failed = steps.describe({"tool": "server_info", "arguments": '{"server":"nas2"}', "status": "error", "result": "No server named 'nas2'. Servers: web1."})
    refused = steps.describe({"tool": "events", "arguments": '{"server":"web1","kinds":[],"from":null,"to":null}', "status": "refused", "result": "Tool budget used up"})

    assert (failed["asked"], failed["found"]) == ("Machine facts for nas2", "No server named 'nas2'. Servers: web1.")
    assert refused["found"] == steps.REFUSED and refused["reason"] is None
    empty = steps.describe({"tool": "events", "arguments": '{"server":"web1","kinds":[],"from":"","to":""}', "status": "error", "result": "x"})
    assert (empty["from"], empty["to"]) == (None, None)   # an empty window is "not given", as the tools read it


@pytest.mark.parametrize("entry", [
    {"tool": "host_metrics", "arguments": '{"server":"web1"', "status": "ok", "result": "{not json"},     # cut off, unreadable
    {"tool": "host_metrics", "arguments": "{}", "status": "ok", "result": '{"series":[{"stats":"odd"}]}'},  # an unexpected shape
    {"tool": "made_up", "arguments": "[]", "status": "weird", "result": None},
    {},
])
def test_an_odd_entry_gives_a_plain_step_instead_of_failing(entry):
    step = steps.describe(entry)

    assert step["asked"] and step["found"] and step["status"] in ("ok", "error", "refused")


def test_an_unusable_result_says_done_rather_than_guessing():
    step = steps.describe({"tool": "disk_usage", "arguments": '{"server":"web1","mount":null,"days":2}', "status": "ok", "result": '{"mounts":[{"used_pct":"lots"}]}'})

    assert step["found"] == steps.DONE   # never a made-up number


def test_a_disk_that_barely_changes_is_not_growing():
    result = {"mounts": [{"mount": "/boot", "used_pct": 42.1, "growth_gb_per_day": 0.01, "full_in_days": None},
                         {"mount": "/srv", "used_pct": 70, "growth_gb_per_day": -0.4, "full_in_days": None}]}

    step = steps.describe({"tool": "disk_usage", "arguments": '{"server":"web1","mount":null,"days":7}', "status": "ok", "result": json.dumps(result)})

    assert step["found"] == "/boot 42.1 % used, not growing; /srv 70 % used, -0.4 GB a day"


def test_a_shortened_result_says_so(db):
    output = run(db, "server_info", {"server": "web1"}, now=NOW)
    result = {**json.loads(output.content), "truncated": True}

    step = steps.describe({"tool": "server_info", "arguments": '{"server":"web1"}', "status": "ok", "result": json.dumps(result)})

    assert step["found"].endswith("(the result was shortened to fit)")


def test_only_so_many_steps_are_described():
    trail = [{"tool": "fleet_snapshot", "arguments": '{"at":null}', "status": "refused", "result": ""}] * 40

    assert len(steps.describe_all(trail)) == steps.MAX_STEPS and steps.describe_all(None) == []
