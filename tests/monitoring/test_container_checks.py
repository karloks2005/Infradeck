import math
import os
import socket
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.collector import collector
from src.backend.routes import monitoring as monitoring_routes
from src.backend import alerts, main, rules
from src.backend.models import Base, ContainerMetric, HostMetric, IncidentAlert, Server, SystemEvent, User

TOKEN = "collector-token"
IMAGE_A, IMAGE_B = "sha256:" + "a" * 64, "sha256:" + "b" * 64


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add(Server(id=5, owner_id=1, hostname="nas", check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKEN)))
    session.commit()
    return session


def _ingest(db, containers=(), **host):
    monitoring_routes.ingest_metrics(main.MetricsIngestRequest(server_id=5, host=host, containers=list(containers)), authorization=f"Bearer {TOKEN}", db=db)


def _c(name, status="running", **extra):
    return {"container_id": f"id-{name}", "name": name, "status": status, **extra}


def _stamp(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _active(db):
    return {alert.rule_key: alert for alert in db.query(IncidentAlert).filter_by(status="active")}


def _events(db, *kinds):
    return [(event.kind, event.detail, event.event_key) for event in db.query(SystemEvent).filter(SystemEvent.kind.in_(kinds or rules.CONTAINER_EVENT_KINDS)).order_by(SystemEvent.id)]


# --- collector: Docker status (#22) -----------------------------------------------------------------

def _clear_docker_env(monkeypatch, tmp_path):
    monkeypatch.delenv("INFRADECK_DOCKER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_SOCKET_PATH", str(tmp_path / "no-such-docker.sock"))


def test_a_host_without_docker_is_not_configured_not_broken(monkeypatch, tmp_path):
    _clear_docker_env(monkeypatch, tmp_path)

    assert collector.collect_docker() == ([], {"docker_status": "not_configured", "docker_reason": None, "docker_latency_ms": None})


def test_a_configured_but_missing_proxy_socket_is_unreachable(monkeypatch, tmp_path):
    monkeypatch.setenv("INFRADECK_DOCKER_HOST", str(tmp_path / "proxy.sock"))

    _containers, status = collector.collect_docker()

    assert (status["docker_status"], status["docker_reason"]) == ("unreachable", "missing")


def test_a_stale_socket_nobody_listens_on_is_reported_as_refused(monkeypatch, tmp_path):
    path = str(tmp_path / "stale.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.close()  # leaves the socket file behind, like a stopped proxy
    monkeypatch.setenv("INFRADECK_DOCKER_HOST", path)

    containers, status = collector.collect_docker()

    assert containers == [] and (status["docker_status"], status["docker_reason"]) == ("unreachable", "refused")


@pytest.mark.parametrize("error, reason", [
    (TimeoutError(), "timeout"), (ConnectionRefusedError(), "refused"), (FileNotFoundError(), "missing"), (PermissionError(), "denied"),
    (OSError("Docker API returned HTTP 502"), "error"), (ValueError("bad json"), "error"), (RuntimeError("secret detail"), "error"),
])
def test_failures_map_to_a_fixed_vocabulary_never_raw_text(error, reason):
    assert collector._docker_failure_reason(error) == reason


def test_a_working_daemon_reports_ok_with_its_latency(monkeypatch, tmp_path):
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    cid = "c" * 64
    answers = {"/containers/json?all=1": [{"Id": cid, "Names": ["/web"]}], f"/containers/{cid}/json": {"Name": "/web", "State": {"Status": "running"}, "Config": {"Image": "nginx"}}}
    monkeypatch.setattr(collector, "_docker_request", lambda path, endpoint: answers.get(endpoint, {}))
    monkeypatch.setenv("INFRADECK_DOCKER_HOST", str(socket_path))

    containers, status = collector.collect_docker()

    assert [c["name"] for c in containers] == ["web"]
    assert status["docker_status"] == "ok" and isinstance(status["docker_latency_ms"], int) and status["docker_latency_ms"] >= 0


def test_a_daemon_error_during_listing_is_unreachable(monkeypatch, tmp_path):
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(collector, "_docker_request", lambda path, endpoint: (_ for _ in ()).throw(OSError("Docker API returned HTTP 502")))
    monkeypatch.setenv("INFRADECK_DOCKER_HOST", str(socket_path))

    containers, status = collector.collect_docker()

    assert containers == [] and (status["docker_status"], status["docker_reason"]) == ("unreachable", "error")


def test_one_container_vanishing_mid_collection_does_not_fail_the_rest(monkeypatch, tmp_path):
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    a, b = "a" * 64, "b" * 64

    def request(path, endpoint):
        if endpoint == "/containers/json?all=1":
            return [{"Id": a, "Names": ["/gone"]}, {"Id": b, "Names": ["/web"]}]
        if endpoint == f"/containers/{a}/json":
            raise OSError("Docker API returned HTTP 502")  # removed between listing and inspecting
        return {"Name": "/web", "State": {"Status": "exited"}}

    monkeypatch.setattr(collector, "_docker_request", request)
    monkeypatch.setenv("INFRADECK_DOCKER_HOST", str(socket_path))

    containers, status = collector.collect_docker()

    assert [c["name"] for c in containers] == ["web"] and status["docker_status"] == "ok"


def test_when_nothing_can_be_inspected_the_daemon_is_failing_not_empty(monkeypatch, tmp_path):
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()

    def request(path, endpoint):
        if endpoint == "/containers/json?all=1":
            return [{"Id": "a" * 64, "Names": ["/web"]}]
        raise OSError("Docker API returned HTTP 502")

    monkeypatch.setattr(collector, "_docker_request", request)
    monkeypatch.setenv("INFRADECK_DOCKER_HOST", str(socket_path))

    containers, status = collector.collect_docker()

    assert containers == [] and status["docker_status"] == "unreachable"


# --- collector: exit status and image ID (#19, #21) -------------------------------------------------

def _collect_one(monkeypatch, tmp_path, **state_and_detail):
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    cid = "d" * 64
    detail = {"Name": "/job", "Config": {"Image": "busybox"}, "State": {"Status": "exited"}, **state_and_detail}
    monkeypatch.setattr(collector, "_docker_request", lambda path, endpoint: [{"Id": cid, "Names": ["/job"]}] if "json?all" in endpoint else detail)
    return collector.collect_containers(str(socket_path))[0]


def test_a_stopped_container_reports_how_it_ended(monkeypatch, tmp_path):
    container = _collect_one(monkeypatch, tmp_path, Image=IMAGE_A, State={"Status": "exited", "ExitCode": 137, "OOMKilled": True, "FinishedAt": "2026-10-04T10:11:12.123456789Z"})

    assert (container["exit_code"], container["oom_killed"], container["finished_at"], container["image_id"]) == (137, True, "2026-10-04T10:11:12Z", IMAGE_A)


def test_a_running_container_does_not_report_a_stale_exit_status(monkeypatch, tmp_path):
    container = _collect_one(monkeypatch, tmp_path, State={"Status": "running", "ExitCode": 1, "OOMKilled": True, "FinishedAt": "2026-10-04T10:11:12Z"})

    assert (container["exit_code"], container["oom_killed"], container["finished_at"]) == (None, None, None)


@pytest.mark.parametrize("state", [
    {"ExitCode": "1"}, {"ExitCode": True}, {"ExitCode": 99999999}, {"ExitCode": -5}, {"ExitCode": 1.5}, {"ExitCode": None},
    {"ExitCode": 1, "OOMKilled": "yes"}, {"ExitCode": 1, "FinishedAt": "0001-01-01T00:00:00Z"}, {"ExitCode": 1, "FinishedAt": "garbage"}, {"ExitCode": 1, "FinishedAt": 12345},
])
def test_malformed_exit_details_become_unknown(monkeypatch, tmp_path, state):
    container = _collect_one(monkeypatch, tmp_path, State={"Status": "exited", **state})

    assert container["exit_code"] in (None, 1) and container["oom_killed"] is None and container["finished_at"] is None


@pytest.mark.parametrize("image_id", ["sha256:abc", "nginx:latest", "sha256:" + "G" * 64, "sha256:" + "a" * 65, "", None, 5, ["x"]])
def test_only_a_well_formed_image_id_is_passed_on(monkeypatch, tmp_path, image_id):
    assert _collect_one(monkeypatch, tmp_path, Image=image_id)["image_id"] is None


# --- backend: Docker health rules (#22) -------------------------------------------------------------

def test_docker_unreachable_alerts_only_after_it_worked_and_only_when_sustained(db):
    _ingest(db, docker_status="ok", docker_latency_ms=20)
    _ingest(db, docker_status="unreachable", docker_reason="refused")
    _ingest(db, docker_status="unreachable", docker_reason="refused")
    assert "system:docker_unreachable" not in _active(db)

    _ingest(db, docker_status="unreachable", docker_reason="refused")
    alert = _active(db)["system:docker_unreachable"]
    assert alert.severity == alerts.CRITICAL and "refused" in alert.description

    _ingest(db, docker_status="ok", docker_latency_ms=15)
    assert "system:docker_unreachable" not in _active(db)


def test_a_host_that_never_had_working_docker_never_alerts(db):
    for _ in range(5):
        _ingest(db, docker_status="unreachable", docker_reason="missing")
    for _ in range(5):
        _ingest(db, docker_status="not_configured")

    assert not [key for key in _active(db) if "docker" in key]


def test_an_open_docker_alert_survives_the_purge_of_the_samples_that_proved_it_worked(db):
    _ingest(db, docker_status="ok", docker_latency_ms=10)
    for _ in range(3):
        _ingest(db, docker_status="unreachable", docker_reason="timeout")
    assert "system:docker_unreachable" in _active(db)

    db.query(HostMetric).filter(HostMetric.docker_status == "ok").delete()
    db.commit()
    _ingest(db, docker_status="unreachable", docker_reason="timeout")

    assert "system:docker_unreachable" in _active(db)


def test_slow_docker_warns_when_sustained_and_clears_when_fast(db):
    for _ in range(2):
        _ingest(db, docker_status="ok", docker_latency_ms=9000)
    assert "system:docker_slow" not in _active(db)
    _ingest(db, docker_status="ok", docker_latency_ms=9000)
    assert _active(db)["system:docker_slow"].severity == alerts.WARNING

    _ingest(db, docker_status="ok", docker_latency_ms=30)
    assert "system:docker_slow" not in _active(db)


def test_missing_docker_data_from_old_collectors_never_alerts(db):
    for _ in range(5):
        _ingest(db, [_c("web")])

    assert not [key for key in _active(db) if "docker" in key]


def test_malformed_docker_fields_are_dropped(db):
    from src.backend.models import HostMetric as Host
    _ingest(db, docker_status="<script>", docker_reason="rm -rf /", docker_latency_ms="fast")
    _ingest(db, docker_status="ok", docker_reason="timeout", docker_latency_ms=10**9)

    rows = db.query(Host).order_by(Host.id).all()
    assert [(r.docker_status, r.docker_reason, r.docker_latency_ms) for r in rows] == [(None, None, None), ("ok", "timeout", None)]


# --- backend: exit codes (#19) ----------------------------------------------------------------------

@pytest.mark.parametrize("code, oom, severity, words", [
    (1, False, alerts.WARNING, "exited with an error"),
    (127, False, alerts.WARNING, "command not found"),
    (137, False, alerts.INFO, "stopped by a signal"),
    (143, False, alerts.INFO, "stopped by a signal"),
    (137, True, alerts.CRITICAL, "running out of memory"),
])
def test_stopped_containers_are_classified_by_how_they_ended(db, code, oom, severity, words):
    _ingest(db, [_c("job", "exited", exit_code=code, oom_killed=oom, finished_at=_stamp(5))])

    alert = _active(db)["container:job:exit"]
    assert alert.severity == severity and words in (alert.title + alert.description)


@pytest.mark.parametrize("container", [
    _c("job", "exited", exit_code=0, oom_killed=False, finished_at="NOW"),
    _c("job", "exited", exit_code=1, finished_at=None),
    _c("job", "exited", exit_code=None, finished_at="NOW"),
    _c("job", "running", exit_code=1, finished_at="NOW"),
    _c("job", "created", exit_code=1, finished_at="NOW"),
])
def test_clean_exits_unknown_and_running_containers_never_alert(db, container):
    if container.get("finished_at") == "NOW":
        container = {**container, "finished_at": _stamp(5)}
    _ingest(db, [container])

    assert "container:job:exit" not in _active(db)


def test_an_old_exit_is_not_an_incident_and_a_restart_clears_it(db):
    _ingest(db, [_c("old", "exited", exit_code=1, finished_at=_stamp(60 * 5))])
    assert "container:old:exit" not in _active(db)

    _ingest(db, [_c("job", "exited", exit_code=1, finished_at=_stamp(5))])
    assert "container:job:exit" in _active(db)
    _ingest(db, [_c("job", "running")])
    assert "container:job:exit" not in _active(db)


# --- backend: what a collector may send about containers --------------------------------------------

def _clean(raw):
    return main._clean_container(raw, datetime.now(timezone.utc))


@pytest.mark.parametrize("raw", [None, 5, "x", [], {}, {"container_id": "a"}, {"status": "running"}, {"container_id": 5, "status": "running"}, {"container_id": "a", "status": 5}, {"container_id": "", "status": "running"}, {"container_id": "a", "status": "  "}])
def test_unusable_container_records_are_dropped(raw):
    assert _clean(raw) is None


def test_container_text_is_made_printable_and_bounded():
    clean = _clean({"container_id": "id\x00" + "x" * 500, "status": "RUNNING\n", "name": "web\x1b[31m" + "n" * 600, "image": "i" * 600, "ports": "p" * 2000, "healthcheck_status": "h" * 200, "started_at": "s" * 200})

    assert len(clean["container_id"]) == 128 and len(clean["name"]) == main.MAX_CONTAINER_NAME and len(clean["image"]) == 255 and len(clean["ports"]) == 512
    assert clean["status"] == "running" and "\x1b" not in clean["name"] and "\x00" not in clean["container_id"]
    assert len(clean["healthcheck_status"]) == 64 and len(clean["started_at"]) == 64


def test_a_missing_name_falls_back_to_the_container_id():
    assert _clean({"container_id": "abcdef0123456789", "status": "running"})["name"] == "abcdef012345"


@pytest.mark.parametrize("bad", ["12", True, -1, math.nan, math.inf, 1e13, None, [1], {"a": 1}])
def test_numeric_fields_must_be_real_in_range_numbers(bad):
    clean = _clean({"container_id": "a", "status": "running", "cpu_pct": bad, "memory_used_mb": bad, "pids": bad, "restart_count": bad, "exit_code": bad})

    assert clean["cpu_pct"] is None and clean["memory_used_mb"] is None and clean["pids"] is None and clean["restart_count"] == 0
    assert clean["exit_code"] is None or clean["exit_code"] == -1


def test_sane_numbers_pass_through():
    clean = _clean({"container_id": "a", "status": "exited", "cpu_pct": 12.5, "memory_pct": 3, "pids": 7, "restart_count": 2, "exit_code": 137, "oom_killed": True, "image_id": IMAGE_A})

    assert (clean["cpu_pct"], clean["memory_pct"], clean["pids"], clean["restart_count"], clean["exit_code"], clean["oom_killed"], clean["image_id"]) == (12.5, 3.0, 7, 2, 137, True, IMAGE_A)


@pytest.mark.parametrize("value", ["garbage", "2026-10-04", "2026-10-04T10:11:12", "2026-10-04T10:11:12.5Z", "1999-12-31T23:59:59Z", "2999-01-01T00:00:00Z", 5, None, ["x"]])
def test_finish_times_must_be_exact_recent_utc_timestamps(value):
    assert _clean({"container_id": "a", "status": "exited", "finished_at": value})["finished_at"] is None


def test_a_valid_finish_time_becomes_an_aware_datetime():
    finished = _clean({"container_id": "a", "status": "exited", "finished_at": "2026-10-04T10:11:12Z"})["finished_at"]

    assert finished == datetime(2026, 10, 4, 10, 11, 12, tzinfo=timezone.utc)


def test_unknown_fields_are_not_passed_to_the_database():
    clean = _clean({"container_id": "a", "status": "running", "id": 99, "server_id": 7, "time": "now", "evil": "x"})

    assert not {"id", "server_id", "time", "evil"} & clean.keys()


def test_the_api_schema_already_refuses_container_entries_that_are_not_objects():
    from pydantic import ValidationError
    for junk in ([None], [5], ["x"], [[1]]):
        with pytest.raises(ValidationError):
            main.MetricsIngestRequest(server_id=5, containers=junk)


def test_hostile_container_records_never_break_ingest_or_other_data(db):
    hostile = [{"container_id": "a" * 1000, "status": "running", "cpu_pct": "NaN", "exit_code": "1", "finished_at": {"x": 1}},
               {"container_id": "ok", "status": "running", "name": "fine", "memory_used_mb": float("inf")},
               {"container_id": "dropped", "status": 5}]
    _ingest(db, hostile, cpu_utilization_pct=12)

    stored = db.query(ContainerMetric).order_by(ContainerMetric.id).all()
    # Unusable records vanish; the odd ones are kept with unusable values turned into "unknown".
    assert [c.name for c in stored] == ["a" * 12, "fine"]
    assert stored[0].cpu_pct is None and stored[0].exit_code is None and stored[0].finished_at is None and stored[1].memory_used_mb is None
    assert db.query(HostMetric).one().cpu_utilization_pct == 12  # the host's own data was still recorded


def test_no_more_than_500_containers_are_accepted_per_check_in(db):
    _ingest(db, [_c(f"c{number}") for number in range(600)])

    assert db.query(ContainerMetric).count() == main.MAX_CONTAINERS_PER_CHECKIN


# --- backend: inventory changes and image drift (#16, #21) ------------------------------------------

def test_the_first_check_in_is_only_a_baseline(db):
    _ingest(db, [_c("web", image_id=IMAGE_A), _c("db")])

    assert _events(db) == []


def test_new_and_missing_containers_are_recorded(db):
    _ingest(db, [_c("web"), _c("db")])
    _ingest(db, [_c("web"), _c("cache", image="redis:7")])

    assert _events(db) == [("container_added", "Container cache appeared (redis:7)", "cache"), ("container_removed", "Container db is no longer present", "db")]


def test_a_removed_container_opens_an_informational_incident_that_clears_with_the_window(db):
    _ingest(db, [_c("web"), _c("db")])
    _ingest(db, [_c("web")])
    assert _active(db)["container:db:removed"].severity == alerts.INFO

    db.query(SystemEvent).update({"time": datetime.now(timezone.utc) - timedelta(hours=3)})
    db.commit()
    _ingest(db, [_c("web")])

    assert "container:db:removed" not in _active(db)


def test_a_container_that_comes_back_clears_its_removal_incident(db):
    _ingest(db, [_c("web"), _c("db")])
    _ingest(db, [_c("web")])
    _ingest(db, [_c("web"), _c("db")])

    assert "container:db:removed" not in _active(db)


def test_containers_are_matched_by_name_so_recreation_is_not_a_change(db):
    _ingest(db, [{"container_id": "old-id", "name": "web", "status": "running", "image_id": IMAGE_A}])
    _ingest(db, [{"container_id": "new-id", "name": "web", "status": "running", "image_id": IMAGE_A}])

    assert _events(db) == []


def test_an_empty_list_never_looks_like_everything_vanished_or_reappeared(db):
    _ingest(db, [_c("web"), _c("db")])
    _ingest(db, [], docker_status="unreachable", docker_reason="refused")
    _ingest(db, [_c("web"), _c("db")])

    assert _events(db) == []


def test_a_changed_image_is_recorded_and_raises_an_informational_incident(db):
    _ingest(db, [_c("web", image_id=IMAGE_A)])
    _ingest(db, [_c("web", image_id=IMAGE_B)])

    [(kind, detail, name)] = _events(db)
    assert (kind, name) == ("image_changed", "web") and "aaaaaaaaaaaa to bbbbbbbbbbbb" in detail
    assert _active(db)["container:web:image"].severity == alerts.INFO


def test_the_same_image_or_an_unknown_one_is_not_a_change(db):
    _ingest(db, [_c("same", image_id=IMAGE_A), _c("unknown")])
    _ingest(db, [_c("same", image_id=IMAGE_A), _c("unknown", image_id=IMAGE_B)])
    _ingest(db, [_c("same", image_id=IMAGE_A), _c("unknown", image_id=None)])

    assert _events(db) == []


def test_a_burst_of_changes_is_capped_per_check_in_with_one_notice(db):
    _ingest(db, [_c("web")])
    _ingest(db, [_c("web")] + [_c(f"ci-{number}") for number in range(25)])

    kinds = [kind for kind, _detail, _name in _events(db)]
    assert kinds.count("container_added") == rules.MAX_CONTAINER_EVENTS_PER_CHECKIN and kinds.count("container_notice") == 1


def test_sustained_churn_is_capped_per_hour(db):
    _ingest(db, [_c("web")])
    for round_number in range(10):
        _ingest(db, [_c("web")] + [_c(f"ci-{round_number}-{number}") for number in range(20)])

    kinds = [kind for kind, _detail, _name in _events(db)]
    assert len(kinds) <= rules.MAX_CONTAINER_EVENTS_PER_HOUR + 1 and kinds.count("container_notice") == 1


def test_container_changes_have_their_own_list_and_do_not_crowd_the_system_row(db, monkeypatch):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    db.add(SystemEvent(server_id=5, time=datetime.now(timezone.utc) - timedelta(days=2), kind="reboot", detail="The host rebooted"))
    db.commit()
    _ingest(db, [_c("web")])
    _ingest(db, [_c("web")] + [_c(f"ci-{number}") for number in range(8)])

    server = monitoring_routes.monitoring_overview(session_id="s", db=db)["servers"][0]

    assert [event["kind"] for event in server["system_events"]] == ["reboot"]
    assert len(server["container_events"]) == 8 and {event["kind"] for event in server["container_events"]} == {"container_added"}


def test_the_container_change_list_is_bounded(db, monkeypatch):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    for number in range(40):
        db.add(SystemEvent(server_id=5, time=datetime.now(timezone.utc) - timedelta(minutes=number), kind="container_added", detail=f"Container c{number} appeared", event_key=f"c{number}"))
    db.commit()

    assert len(monitoring_routes.monitoring_overview(session_id="s", db=db)["servers"][0]["container_events"]) == main.CONTAINER_EVENTS_SHOWN


def test_container_events_are_kept_for_the_history_retention(db):
    now = datetime.now(timezone.utc)
    db.add_all([SystemEvent(server_id=5, time=now - timedelta(days=days), kind=kind, detail="x", event_key="x") for kind in rules.CONTAINER_EVENT_KINDS for days in (40, 400)])
    db.commit()

    assert main.retention.purge(db) == len(rules.CONTAINER_EVENT_KINDS)   # the ones from 400 days ago; a year is the default
    assert db.query(SystemEvent).count() == len(rules.CONTAINER_EVENT_KINDS)


# --- backend: memory leak trends (#20) --------------------------------------------------------------

def _seed_memory(db, name="app", hours=12, per_hour=6, value=lambda hour: 200 + 20 * hour, restart=0, container_id="cid", limit=None, status="running", skip_hours=()):
    """Samples spread over the window, ending a few minutes before now, plus the check-in that makes them current."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    last = None
    for hour in range(hours):
        if hour in skip_hours:
            continue
        for k in range(per_hour):
            last = since + timedelta(minutes=hour * 60 + k * 10 + 5)
            db.add(ContainerMetric(server_id=5, container_id=container_id, name=name, status=status, restart_count=restart, memory_used_mb=float(value(hour + k / per_hour)), memory_limit_mb=limit, time=last))
    db.add(HostMetric(server_id=5, time=last))
    db.commit()
    return now


def _trend(db, now=None):
    main.analyze_memory_trends(db, now=now)
    return {key: alert for key, alert in _active(db).items() if key.startswith("trend:")}


def test_steady_growth_is_flagged_as_a_possible_leak(db):
    now = _seed_memory(db, limit=2048.0)

    alert = _trend(db, now)["trend:app:memory"]

    assert alert.severity == alerts.WARNING and "possible" not in alert.title.lower() and "memory leak" in alert.title
    assert "20.0 MB per hour" in alert.description and "2048 MB limit" in alert.description


def test_flat_noisy_and_sawtooth_memory_is_not_a_leak(db):
    _seed_memory(db, name="flat", value=lambda hour: 300)
    _seed_memory(db, name="sawtooth", value=lambda hour: 200 + 150 * (int(hour) % 3))
    now = _seed_memory(db, name="noisy", value=lambda hour: 300 + (40 if int(hour * 6) % 2 else -40))

    assert _trend(db, now) == {}


def test_a_restart_or_recreation_in_the_window_resets_the_trend(db):
    _seed_memory(db, name="recreated", container_id="one")
    db.query(ContainerMetric).filter(ContainerMetric.name == "recreated", ContainerMetric.memory_used_mb > 300).update({"container_id": "two"})
    _seed_memory(db, name="restarted")
    db.query(ContainerMetric).filter(ContainerMetric.name == "restarted", ContainerMetric.memory_used_mb > 300).update({"restart_count": 1})
    db.commit()

    assert _trend(db) == {}


def test_too_little_history_or_a_slow_or_proportionally_small_rise_is_not_flagged(db):
    _seed_memory(db, name="gappy", skip_hours=(1, 2, 3, 4, 5))
    _seed_memory(db, name="slow", value=lambda hour: 200 + 2 * hour)
    now = _seed_memory(db, name="huge", value=lambda hour: 5000 + 12 * hour)  # 12 MB/h but only ~2.6% over the window

    assert _trend(db, now) == {}


def test_containers_that_are_not_running_now_are_not_analysed(db):
    now = _seed_memory(db, status="exited")

    assert _trend(db, now) == {}


def test_the_flag_clears_when_the_growth_stops(db):
    now = _seed_memory(db)
    assert "trend:app:memory" in _trend(db, now)

    db.query(ContainerMetric).delete()
    db.query(HostMetric).delete()
    db.commit()
    now = _seed_memory(db, value=lambda hour: 300)

    assert _trend(db, now) == {}


def test_analysis_is_bounded_to_100_containers_per_server(db):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=12)
    last = None
    for number in range(120):
        for hour in range(12):
            for k in range(6):
                last = since + timedelta(minutes=hour * 60 + k * 10 + 5)
                db.add(ContainerMetric(server_id=5, container_id=f"id{number}", name=f"app{number:03d}", status="running", restart_count=0, memory_used_mb=float(200 + 20 * (hour + k / 6)), time=last))
    db.add(HostMetric(server_id=5, time=last))
    db.commit()

    assert len(_trend(db, now)) == rules.MAX_TREND_CONTAINERS


def test_a_fit_needs_distinct_points():
    assert rules._linear_fit([(1.0, 5.0), (1.0, 6.0)]) is None
    slope, intercept, r_squared = rules._linear_fit([(0.0, 10.0), (1.0, 20.0), (2.0, 30.0)])
    assert (round(slope, 6), round(intercept, 6), round(r_squared, 6)) == (10.0, 10.0, 1.0)


def test_the_new_thresholds_have_safe_defaults_and_are_validated():
    defaults = rules.DEFAULT_THRESHOLDS
    assert (defaults["docker_slow_ms"], defaults["leak_window_hours"], defaults["leak_min_mb_per_hour"], defaults["leak_min_growth_pct"]) == (2000, 12, 10, 20)

    for bad in ({"docker_slow_ms": 50}, {"docker_slow_ms": 70000}, {"docker_slow_ms": 1500.5}, {"leak_window_hours": 5}, {"leak_window_hours": 73}, {"leak_window_hours": 12.5},
                {"leak_min_mb_per_hour": 0}, {"leak_min_mb_per_hour": 10001}, {"leak_min_growth_pct": 0}, {"leak_min_growth_pct": 101}, {"leak_min_growth_pct": "20"}, {"leak_window_hours": True}):
        with pytest.raises(ValueError):
            rules.validate_thresholds(bad, defaults)

    ok = rules.validate_thresholds({"docker_slow_ms": 5000, "leak_window_hours": 24, "leak_min_mb_per_hour": 2.5, "leak_min_growth_pct": 50}, defaults)
    assert (ok["docker_slow_ms"], ok["leak_window_hours"], ok["leak_min_mb_per_hour"], ok["leak_min_growth_pct"]) == (5000, 24, 2.5, 50)


def test_the_longest_container_name_still_fits_every_incident_key(db):
    name = "n" * 600
    _ingest(db, [_c(name, "exited", exit_code=1, finished_at=_stamp(5), restart_count=0, healthcheck_status="unhealthy")])

    keys = list(_active(db))
    assert keys and max(len(key) for key in keys) < 255


# --- collector keeping up with its check interval (#44) ---------------------------------------------

def test_a_collector_too_slow_for_its_interval_warns_when_sustained_and_clears(db):
    for _ in range(2):
        _ingest(db, collect_duration_ms=55_000)          # 60 s interval, limit 80% = 48 s
    assert "system:collect_slow" not in _active(db)
    _ingest(db, collect_duration_ms=55_000)
    alert = _active(db)["system:collect_slow"]
    assert alert.severity == alerts.WARNING and "55.0 s" in alert.description and "60 s" in alert.description

    _ingest(db, collect_duration_ms=900)
    assert "system:collect_slow" not in _active(db)


def test_collection_within_the_limit_never_warns(db):
    for _ in range(6):
        _ingest(db, collect_duration_ms=47_999)
    assert "system:collect_slow" not in _active(db)


def test_one_slow_check_in_among_fast_ones_does_not_warn(db):
    for duration in (900, 55_000, 900, 55_000, 900):
        _ingest(db, collect_duration_ms=duration)
    assert "system:collect_slow" not in _active(db)


def test_old_collectors_that_do_not_send_the_duration_never_trigger_it(db):
    for _ in range(6):
        _ingest(db)
    assert "system:collect_slow" not in _active(db)
    for duration in (55_000, None, 55_000):
        _ingest(db, collect_duration_ms=duration)
    assert "system:collect_slow" not in _active(db)


def test_the_limit_follows_the_servers_check_interval(db):
    db.get(Server, 5).check_interval_seconds = 15
    db.commit()
    for _ in range(3):
        _ingest(db, collect_duration_ms=13_000)         # fine at 60 s, too slow at 15 s
    assert "system:collect_slow" in _active(db)


def test_the_limit_is_an_editable_validated_threshold():
    assert rules.DEFAULT_THRESHOLDS["collect_slow_pct"] == 80
    for bad in (0, 101, -5, "80", True, None):
        with pytest.raises(ValueError):
            rules.validate_thresholds({"collect_slow_pct": bad}, rules.DEFAULT_THRESHOLDS)
    assert rules.validate_thresholds({"collect_slow_pct": 60}, rules.DEFAULT_THRESHOLDS)["collect_slow_pct"] == 60


@pytest.mark.parametrize("bad", ["12", -1, 10**12, True, 1.5, None, [1], float("nan")])
def test_a_hostile_collection_time_is_dropped_not_stored(db, bad):
    _ingest(db, collect_duration_ms=bad)
    assert db.query(HostMetric).order_by(HostMetric.id.desc()).first().collect_duration_ms is None


def test_a_sane_collection_time_is_stored(db):
    _ingest(db, collect_duration_ms=1234)
    assert db.query(HostMetric).order_by(HostMetric.id.desc()).first().collect_duration_ms == 1234
