"""The agent's read-only tools (#114): a closed list with strict schemas, arguments checked before anything is read,
every check-in for the last week and hourly summaries before that, bounded and masked results with fact IDs, check
targets without secrets, no IP addresses, and nothing ever written."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agent import chat, diagnosis, tools
from src.agent.tools import common, servers
from src.backend import history, rollups
from src.backend.models import (AppSetting, Base, Check, CheckResult, ContainerMetric, FirewallState, HostMetric, HostRollup, IncidentAlert, ListenerState,
                                MountMetric, Server, SystemEvent)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
SPIKE = NOW - timedelta(hours=5)


def run(db, name, arguments, now):
    """tools.run with the ``reason`` every call carries (the tools ignore it)."""
    return tools.run(db, name, {**arguments, "reason": "Test question?"} if isinstance(arguments, dict) else arguments, now=now)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    system = {"kernel": "6.8.0", "collector_version": "0.10.0", "updates_pending": 4, "updates_security": 1}
    session.add(Server(id=1, owner_id=1, hostname="web1", ip_address="10.0.0.5", os_info="Debian 12", cpu_cores=4, total_ram_gb=16.0, check_interval_seconds=600,
                       last_seen_at=NOW, system_info=json.dumps(system)))
    session.add(Server(id=2, owner_id=1, hostname="db1", ip_address="10.0.0.6", check_interval_seconds=600, last_seen_at=NOW - timedelta(days=2)))
    # Every 10 minutes for two days: CPU around 10 % with one peak, the root disk filling up by 1 GB a day.
    for step in range(2 * 24 * 6):
        moment = NOW - timedelta(minutes=10 * step)
        session.add(HostMetric(server_id=1, time=moment, cpu_utilization_pct=97.0 if moment == SPIKE else 10 + step % 3, ram_pct=50, swap_pct=5, load_1m=0.4, disk_usage_pct=60))
        session.add(MountMetric(server_id=1, time=moment, mount="/", fs_type="ext4", total_gb=100, used_gb=60 - step * 10 / 1440, used_pct=60 - step * 10 / 1440, inodes_pct=10))
    # Hourly summaries before the last week: CPU averaging 5 % at 12:00 UTC, 30 % at other hours.
    for hours in range(8 * 24, 40 * 24):
        start = rollups.floor(rollups.HOUR, NOW) - timedelta(hours=hours)
        average = 5.0 if start.hour == 12 else 30.0
        session.add(HostRollup(server_id=1, tier=rollups.HOUR, bucket_start=start, samples=6, avg_cpu=average, n_cpu=6, max_cpu=average + 10))
    session.add(AppSetting(key=rollups.WATERMARK_PREFIX + rollups.HOUR, value=(NOW - timedelta(days=7)).isoformat()))
    # nginx: running, then killed for memory and restarted.
    for step in range(12):
        moment = NOW - timedelta(minutes=10 * (11 - step))
        session.add(ContainerMetric(server_id=1, container_id="n" * 64, name="nginx", image="nginx:1.27", status="running", restart_count=0 if step < 6 else 1,
                                    cpu_pct=2, memory_used_mb=100 + step, memory_limit_mb=256, exit_code=137 if step >= 6 else None, oom_killed=step >= 6, time=moment))
        session.add(ContainerMetric(server_id=1, container_id="w" * 64, name="worker", image="app:2", status="exited", restart_count=0, time=moment))
    session.add(SystemEvent(id=500, server_id=1, time=NOW - timedelta(hours=1), kind="oom_kill", detail="Killed nginx DATABASE_URL=postgres://app:hunter2@db/app"))
    session.add(SystemEvent(id=501, server_id=1, time=NOW - timedelta(hours=2), kind="port_opened", detail="Listener opened: tcp/8080 on all addresses"))
    for index in range(40):
        session.add(SystemEvent(id=600 + index, server_id=1, time=NOW - timedelta(hours=3, minutes=index), kind="packages_updated", detail=f"{index} packages updated"))
    session.add(IncidentAlert(id=31, server_id=1, severity="CRITICAL", title="Disk usage on web1:/ is 95%", description="d", status="resolved", rule_key="host:disk:/",
                              created_at=NOW - timedelta(days=10), resolved_at=NOW - timedelta(days=10) + timedelta(hours=2), resolution="Marked resolved by Ana"))
    session.add(IncidentAlert(id=32, server_id=1, severity="WARNING", title="Container nginx restarted", description="d", status="active", rule_key="container:nginx:restart_loop",
                              created_at=NOW - timedelta(hours=1)))
    session.add(IncidentAlert(id=33, server_id=2, severity="CRITICAL", title="db1 is offline", description="d", status="active", rule_key="server_offline", created_at=NOW - timedelta(days=2)))
    session.add(Check(id=7, name="Shop", type="http", target="https://shop:s3cret@shop.example.org/health?token=abc123", server_ids="[1]", interval_seconds=60, retries=0,
                      state="up", created_at=NOW - timedelta(days=30)))
    for minute in range(120):
        failing = 30 <= minute < 35
        session.add(CheckResult(check_id=7, server_id=1, time=NOW - timedelta(minutes=120 - minute), ok=not failing, response_ms=None if failing else 40 + minute % 5,
                                detail="HTTP 503" if failing else None))
    session.add(ListenerState(server_id=1, proto="tcp", port=443, scope="any", first_seen=NOW - timedelta(days=20), last_seen=NOW))
    session.add(ListenerState(server_id=1, proto="tcp", port=5432, scope="loopback", first_seen=NOW - timedelta(days=20), last_seen=NOW))
    session.add(FirewallState(server_id=1, name="ufw-conf", sha="abc", rules=3, enabled=True, updated_at=NOW - timedelta(days=1)))
    session.commit()
    yield session
    session.close()


def call(db, name, **arguments):
    output = run(db, name, arguments, now=NOW)
    return json.loads(output.content), output.labels


WINDOW = {"from": None, "to": None}
EVERY_CALL = [
    ("server_info", {"server": "web1"}),
    ("host_metrics", {"server": "web1", "metrics": [], **WINDOW}),
    ("list_containers", {"server": "web1", "at": None}),
    ("container_history", {"server": "web1", "container": "nginx", **WINDOW}),
    ("disk_usage", {"server": "web1", "mount": None, "days": 2}),
    ("events", {"server": "web1", "kinds": [], **WINDOW}),
    ("compare_to_baseline", {"server": "web1", "metric": "cpu", "at": None}),
    ("find_incidents", {"server": None, "kind": None, **WINDOW}),
    ("fleet_snapshot", {"at": None}),
    ("check_results", {"check": "Shop", "server": None, **WINDOW}),
    ("network_state", {"server": "web1"}),
]


# --- the list and its arguments ---------------------------------------------------------------------------------

def _strict(schema: dict) -> bool:
    if "anyOf" in schema:
        return all(_strict(option) for option in schema["anyOf"])
    if schema["type"] == "object":
        return schema["additionalProperties"] is False and schema["required"] == list(schema["properties"]) and all(_strict(sub) for sub in schema["properties"].values())
    if schema["type"] == "array":
        return _strict(schema["items"])
    return True


def test_every_tool_is_listed_once_with_a_strict_schema():
    names = [tool.name for tool in tools.TOOLS]
    assert sorted(names) == sorted(name for name, _ in EVERY_CALL) and len(names) == 11
    assert all(_strict(tool.schema) for tool in tools.TOOLS)
    assert set(servers.HOST_METRICS) <= set(history.HOST_METRICS)


def _unions(schema) -> int:
    """Parameters that may hold more than one type (anyOf, or a list of types), as Anthropic counts them."""
    if isinstance(schema, dict):
        return int("anyOf" in schema or isinstance(schema.get("type"), list)) + sum(_unions(value) for value in schema.values())
    if isinstance(schema, list):
        return sum(_unions(value) for value in schema)
    return 0


@pytest.mark.parametrize("answer", [diagnosis.SCHEMA, chat.REPLY_SCHEMA], ids=["diagnosis", "chat"])
def test_no_request_has_more_union_typed_parameters_than_the_provider_accepts(answer):
    # Anthropic rejects strict schemas with more than 16 nullable or union-typed parameters over all tools and the
    # answer's schema of one request (#196), so the tools themselves have none.
    assert sum(_unions(tool.schema) for tool in tools.TOOLS) == 0
    assert _unions(answer) <= 16


def test_an_optional_argument_left_empty_or_null_means_not_given(db):
    empty = run(db, "host_metrics", {"server": "web1", "metrics": ["cpu"], "from": "", "to": " "}, now=NOW)
    null = run(db, "host_metrics", {"server": "web1", "metrics": ["cpu"], "from": None, "to": None}, now=NOW)

    assert empty.content == null.content and json.loads(empty.content)["to"] == "2026-11-07T12:00+00:00"
    left_out = run(db, "host_metrics", {"server": "web1", "metrics": ["cpu"]}, now=NOW)   # a model without strict tools may omit them
    assert left_out.content == null.content
    with pytest.raises(tools.ToolError, match="non-empty"):
        run(db, "server_info", {"server": ""}, now=NOW)   # a required one may not be empty
    with pytest.raises(tools.ToolError, match="Give exactly"):
        run(db, "host_metrics", {"server": "web1", "from": None, "to": None}, now=NOW)   # nor left out


def test_an_unknown_tool_is_refused(db):
    with pytest.raises(tools.ToolError, match="no tool named"):
        run(db, "run_shell", {"command": "rm -rf /"}, now=NOW)


@pytest.mark.parametrize("name, arguments, message", [
    ("events", {"server": "web1"}, "exactly these arguments"),
    ("events", {"server": "web1", "kinds": [], "from": None, "to": None, "sql": "drop table"}, "exactly these arguments"),
    ("events", {"server": 1, "kinds": [], "from": None, "to": None}, "server must be"),
    ("events", {"server": "web1", "kinds": ["everything"], "from": None, "to": None}, "kinds must be one of"),
    ("events", {"server": "web1", "kinds": ["reboot"] * 13, "from": None, "to": None}, "at most 12"),
    ("events", {"server": "x" * 201, "kinds": [], "from": None, "to": None}, "at most 200"),
    ("events", {"server": "web1", "kinds": [], "from": "yesterday", "to": None}, "ISO 8601"),
    ("events", {"server": "web1", "kinds": [], "from": "2026-11-07T10:00", "to": "2026-11-07T09:00"}, "earlier than"),
    ("events", {"server": "web1", "kinds": [], "from": "2026-01-01T00:00", "to": None}, "last 90 days"),
    ("disk_usage", {"server": "web1", "mount": None, "days": True}, "whole number"),
    ("disk_usage", {"server": "web1", "mount": None, "days": 365}, "between 1 and 90"),
    ("compare_to_baseline", {"server": "web1", "metric": "temperature", "at": None}, "one of"),
    ("list_containers", {"server": "web1", "at": "2026-10-01T00:00"}, "kept for 7 days"),
    ("check_results", {"check": "Shop", "server": "web1", "from": None, "to": None}, "not both"),
    ("check_results", {"check": None, "server": None, "from": None, "to": None}, "not both"),
    ("check_results", {"check": "Bank", "server": None, "from": None, "to": None}, "Checks: Shop"),
    ("container_history", {"server": "web1", "container": "redis", "from": None, "to": None}, "nginx, worker"),
])
def test_bad_arguments_are_refused_with_a_reason_the_model_can_act_on(db, name, arguments, message):
    with pytest.raises(tools.ToolError, match=message):
        run(db, name, arguments, now=NOW)


def test_an_unknown_server_is_answered_with_the_known_ones(db):
    with pytest.raises(tools.ToolError, match="Servers: db1, web1"):
        run(db, "server_info", {"server": "mail"}, now=NOW)
    assert call(db, "server_info", server="WEB1")[0]["hostname"] == "web1"


# --- what each tool returns -------------------------------------------------------------------------------------

def test_host_metrics_reads_every_check_in_of_the_last_week_and_keeps_the_peak(db):
    content, labels = call(db, "host_metrics", server="web1", metrics=["cpu"], **{"from": "2026-11-07T00:00", "to": "2026-11-07T12:00"})
    cpu = content["series"][0]
    assert cpu["id"] == "metric:web1.cpu" and cpu["source"] == "check-ins"
    assert cpu["stats"]["max"] == 97.0 and max(value for _, value in cpu["points"]) == 97.0
    assert len(cpu["points"]) <= 40
    assert labels["metric:web1.cpu"] == "CPU · web1 · 2026-11-07 00:00–12:00 UTC"


def test_the_window_defaults_to_the_last_24_hours_and_empty_metrics_to_the_usual_five(db):
    content, _ = call(db, "host_metrics", server="web1", metrics=[], **WINDOW)
    assert (content["from"], content["to"]) == ("2026-11-06T12:00+00:00", "2026-11-07T12:00+00:00")
    assert [series["id"] for series in content["series"]] == [f"metric:web1.{name}" for name in history.DEFAULT_HOST_METRICS]


def test_older_windows_are_read_from_the_hourly_summaries(db):
    content, _ = call(db, "host_metrics", server="web1", metrics=["cpu"], **{"from": "2026-10-01T00:00", "to": "2026-10-20T00:00"})
    cpu = content["series"][0]
    assert cpu["source"] == "hourly summaries"
    assert cpu["stats"]["min"] == 5.0 and cpu["stats"]["max"] == 40.0 and len(cpu["points"]) <= 40


def test_no_data_in_the_window_says_so(db):
    content, labels = call(db, "host_metrics", server="db1", metrics=[], **WINDOW)
    assert content["series"] == [] and "No check-ins" in content["note"] and labels == {}


def test_server_info_has_the_machine_facts_and_no_ip_address(db):
    content, labels = call(db, "server_info", server="web1")
    assert content["online"] is True and content["kernel"] == "6.8.0" and content["security_updates_pending"] == 1
    assert labels == {"server:web1": "web1: machine facts"}
    assert "10.0.0.5" not in json.dumps(content)


def test_list_containers_puts_the_ones_not_running_first(db):
    content, labels = call(db, "list_containers", server="web1", at=None)
    assert [item["name"] for item in content["containers"]] == ["worker", "nginx"]
    assert content["containers"][1]["oom_killed"] is True and "container:web1/nginx" in labels


def test_container_history_reports_restarts_and_oom_kills(db):
    content, labels = call(db, "container_history", server="web1", container="nginx", **WINDOW)
    assert content["changes"] == [{"time": "2026-11-07T11:10+00:00", "change": "restarted 1×, exit code 137, killed for running out of memory"}]
    assert {"metric:web1/nginx.cpu", "metric:web1/nginx.memory_mb", "metric:web1/nginx.restarts", "container:web1/nginx"} <= set(labels)
    assert content["memory_limit_mb"] == 256 and content["not_running_pct"] == 0.0


def test_disk_usage_estimates_growth_and_when_it_fills_up(db):
    content, labels = call(db, "disk_usage", server="web1", mount="/", days=2)
    root = content["mounts"][0]
    assert root["growth_gb_per_day"] == pytest.approx(1.0, abs=0.05)
    assert root["full_in_days"] == pytest.approx(40, abs=2)
    assert "metric:web1.disk:/" in labels
    with pytest.raises(tools.ToolError, match="no mount '/data'"):
        run(db, "disk_usage", {"server": "web1", "mount": "/data", "days": 2}, now=NOW)


def test_events_show_the_newest_thirty_oldest_first_and_filter_by_kind(db):
    content, labels = call(db, "events", server="web1", kinds=[], **WINDOW)
    assert content["total"] == 42 and content["shown"] == 30
    times = [event["time"] for event in content["events"]]
    assert times == sorted(times) and content["events"][-1]["id"] == "event:500"
    assert set(labels) == {event["id"] for event in content["events"]}
    only, _ = call(db, "events", server="web1", kinds=["oom_kill", "port_opened"], **WINDOW)
    assert [event["id"] for event in only["events"]] == ["event:501", "event:500"]


def test_compare_to_baseline_sets_an_hour_against_the_same_hour_before(db):
    content, labels = call(db, "compare_to_baseline", server="web1", metric="cpu", at="2026-10-20T12:30")
    assert content["hour"] == "2026-10-20T12:00+00:00" and content["value"] == 5.0
    assert content["same_hour_yesterday"] == 5.0 and content["usual_for_this_hour"] == 5.0 and content["difference"] == 0.0
    later, _ = call(db, "compare_to_baseline", server="web1", metric="cpu", at=None)
    assert later["usual_for_this_hour"] == 5.0 and later["value"] > later["usual_for_this_hour"]
    assert "baseline:web1.cpu" in labels


def test_find_incidents_filters_by_server_and_kind(db):
    content, labels = call(db, "find_incidents", server="web1", kind="host:disk", **WINDOW)
    assert [item["id"] for item in content["incidents"]] == ["incident:31"]
    assert content["incidents"][0]["lasted_minutes"] == 120 and content["incidents"][0]["resolution"] == "Marked resolved by Ana"
    assert labels == {"incident:31": "INC-0031 · Disk usage on web1:/ is 95%"}
    everything, _ = call(db, "find_incidents", server=None, kind=None, **WINDOW)
    assert [item["id"] for item in everything["incidents"]] == ["incident:32", "incident:33", "incident:31"]


def test_fleet_snapshot_shows_every_server_now_and_at_an_earlier_moment(db):
    content, labels = call(db, "fleet_snapshot", at=None)
    by_name = {item["server"]: item for item in content["servers"]}
    assert by_name["web1"]["online"] is True and by_name["db1"]["online"] is False
    assert by_name["db1"]["worst"] == "CRITICAL" and by_name["web1"]["open_incidents"] == 1
    assert by_name["web1"]["failing_checks"] == []
    assert {"fleet:web1", "fleet:db1", "incident:32", "incident:33"} <= set(labels)
    during, _ = call(db, "fleet_snapshot", at="2026-11-07T10:32")
    assert {item["server"]: item for item in during["servers"]}["web1"]["failing_checks"] == ["Shop"]
    long_ago, _ = call(db, "fleet_snapshot", at="2026-10-01T00:00")
    assert long_ago["servers"][0]["failing_checks"] is None and "hourly totals" in long_ago["note"]


def test_check_results_show_downtime_and_errors_without_the_targets_secrets(db):
    content, labels = call(db, "check_results", check="shop", server=None, **WINDOW)
    shop = content["checks"][0]
    assert shop["target"] == "https://shop.example.org/health?…"
    assert "s3cret" not in json.dumps(content) and "abc123" not in json.dumps(content)
    assert shop["down"] == 5 and shop["down_periods"] == [{"from": "2026-11-07T10:30+00:00", "to": "2026-11-07T10:35+00:00"}]
    assert shop["errors"] == [{"detail": "HTTP 503", "count": 5}] and shop["runs_from"] == ["web1"]
    assert labels["uptime:7"].startswith("check Shop")
    by_server, _ = call(db, "check_results", check=None, server="web1", **WINDOW)
    assert [check["name"] for check in by_server["checks"]] == ["Shop"]


def test_network_state_lists_ports_firewall_and_recent_changes(db):
    content, labels = call(db, "network_state", server="web1")
    assert [(item["port"], item["reachable_from"]) for item in content["listening"]] == [(443, "other machines"), (5432, "this machine only")]
    assert content["firewall"] == [{"name": "ufw-conf", "rules": 3, "enabled": True, "changed_at": "2026-11-06T12:00+00:00"}]
    assert [event["id"] for event in content["changes_last_7_days"]] == ["event:501"] and "network:web1" in labels


# --- rules every result follows ---------------------------------------------------------------------------------

@pytest.mark.parametrize("name, arguments", EVERY_CALL)
def test_every_result_is_bounded_masked_free_of_ip_addresses_and_labelled(db, name, arguments):
    output = run(db, name, arguments, now=NOW)
    assert len(output.content) <= tools.MAX_RESULT_CHARS
    assert "hunter2" not in output.content and "10.0.0." not in output.content
    cited = set()

    def ids(value):
        if isinstance(value, dict):
            if isinstance(value.get("id"), str):
                cited.add(value["id"])
            for item in value.values():
                ids(item)
        elif isinstance(value, list):
            for item in value:
                ids(item)
    ids(json.loads(output.content))
    assert cited == set(output.labels)


def test_a_result_too_large_is_shortened_and_loses_the_labels_of_what_was_cut(db, monkeypatch):
    monkeypatch.setattr(tools, "MAX_RESULT_CHARS", 1500)
    content, labels = call(db, "events", server="web1", kinds=[], **WINDOW)
    assert content["truncated"] is True and len(content["events"]) < 30
    assert set(labels) == {event["id"] for event in content["events"]}
    assert content["events"][-1]["id"] == "event:500"   # the oldest went first


def test_a_long_series_is_thinned_keeping_its_peak(db, monkeypatch):
    monkeypatch.setattr(tools, "MAX_RESULT_CHARS", 900)
    content, _ = call(db, "host_metrics", server="web1", metrics=["cpu"], **{"from": "2026-11-07T00:00", "to": "2026-11-07T12:00"})
    points = content["series"][0]["points"]
    assert content["truncated"] is True and len(points) < 40 and max(value for _, value in points) == 97.0


def test_the_same_question_gives_the_same_ids_and_a_second_window_joins_the_label(db):
    first = run(db, "host_metrics", {"server": "web1", "metrics": ["cpu"], "from": "2026-11-07T00:00", "to": "2026-11-07T06:00"}, now=NOW)
    again = run(db, "host_metrics", {"server": "web1", "metrics": ["cpu"], "from": "2026-11-07T00:00", "to": "2026-11-07T06:00"}, now=NOW)
    later = run(db, "host_metrics", {"server": "web1", "metrics": ["cpu"], "from": "2026-11-07T06:00", "to": "2026-11-07T12:00"}, now=NOW)
    assert first.content == again.content and set(first.labels) == set(later.labels) == {"metric:web1.cpu"}
    merged = common.merge_labels(tools.merge_labels({}, first.labels), later.labels)
    assert merged["metric:web1.cpu"] == "CPU · web1 · 2026-11-07 00:00–06:00 UTC; CPU · web1 · 2026-11-07 06:00–12:00 UTC"
    assert common.merge_labels(merged, first.labels) == merged


def test_tools_never_write_to_the_database(db):
    for name, arguments in EVERY_CALL:
        run(db, name, arguments, now=NOW)
        assert not db.new and not db.dirty and not db.deleted, name
