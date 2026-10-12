import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agent import collector
from src.backend import main
from src.backend.models import Base, ContainerMetric, Server, User


@pytest.fixture(autouse=True)
def _fresh_collector_state():
    collector._cpu_previous.clear()
    collector._one_shot_unsupported = False
    yield
    collector._cpu_previous.clear()
    collector._one_shot_unsupported = False


def _one_shot(total, system, cpus=4, **extra):
    """Docker's one-shot answer: current counters and no earlier reading."""
    return {"cpu_stats": {"cpu_usage": {"total_usage": total}, "system_cpu_usage": system, "online_cpus": cpus}, "precpu_stats": {}, **extra}


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"),
        User(id=2, name="Other", email="other@example.com", password_hash="x"),
    ])
    session.add(Server(id=10, owner_id=1, hostname="nas"))
    now = datetime.now(timezone.utc)
    for index in range(3):
        session.add(ContainerMetric(server_id=10, container_id="abc", name="web", status="running", cpu_pct=float(index), memory_used_mb=100.0 + index, memory_limit_mb=1024.0, pids=4, time=now - timedelta(minutes=3 - index)))
    session.commit()
    return session


def test_container_detail_returns_latest_and_chronological_history(monkeypatch):
    session = _session()
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})

    result = main.container_detail(10, "abc", limit=60, session_id="s", db=session)

    assert result["name"] == "web"
    assert result["latest"]["cpu_pct"] == 2.0
    assert result["latest"]["memory_limit_mb"] == 1024.0
    assert [sample["cpu_pct"] for sample in result["history"]] == [0.0, 1.0, 2.0]


def test_container_detail_is_visible_to_regular_users(monkeypatch):
    session = _session()
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 2})

    assert main.container_detail(10, "abc", limit=60, session_id="s", db=session)["name"] == "web"


def test_container_detail_returns_404_for_unknown_server(monkeypatch):
    session = _session()
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})

    with pytest.raises(HTTPException) as exc:
        main.container_detail(99, "abc", limit=60, session_id="s", db=session)
    assert exc.value.status_code == 404


def test_container_usage_parses_docker_stats(monkeypatch):
    stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 1000, "online_cpus": 4},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 600},
        "memory_stats": {"usage": 300 * collector.MiB, "limit": 1000 * collector.MiB, "stats": {"inactive_file": 100 * collector.MiB}},
        "networks": {"eth0": {"rx_bytes": 2 * collector.MiB, "tx_bytes": collector.MiB}},
        "blkio_stats": {"io_service_bytes_recursive": [{"op": "read", "value": 5 * collector.MiB}, {"op": "write", "value": 3 * collector.MiB}]},
        "pids_stats": {"current": 7},
    }
    monkeypatch.setattr(collector, "_docker_request", lambda socket_path, endpoint: stats)

    usage = collector._container_usage("/sock", "abc")

    assert usage["cpu_pct"] == 200.0
    assert usage["memory_used_mb"] == 200.0
    assert usage["memory_pct"] == 20.0
    assert (usage["net_rx_mb"], usage["net_tx_mb"]) == (2.0, 1.0)
    assert (usage["block_read_mb"], usage["block_write_mb"]) == (5.0, 3.0)
    assert usage["pids"] == 7


def test_the_quick_one_shot_request_is_tried_first(monkeypatch):
    asked = []
    monkeypatch.setattr(collector, "_docker_request", lambda socket_path, endpoint: asked.append(endpoint) or _one_shot(100, 1000))

    collector._container_usage("/sock", "abc")

    assert asked == ["/containers/abc/stats?stream=false&one-shot=true"]


def test_cpu_is_the_change_since_the_previous_check_in_and_the_first_has_none(monkeypatch):
    readings = iter([_one_shot(100, 1000), _one_shot(300, 1400), _one_shot(300, 1800)])
    monkeypatch.setattr(collector, "_docker_request", lambda socket_path, endpoint: next(readings))

    first = collector._container_usage("/sock", "abc")
    second = collector._container_usage("/sock", "abc")
    idle = collector._container_usage("/sock", "abc")

    assert "cpu_pct" not in first            # unknown, not zero
    assert second["cpu_pct"] == 200.0        # 200 of 400 CPU time across 4 cores
    assert "cpu_pct" not in idle             # no CPU used at all gives no figure, as before


def test_containers_do_not_share_cpu_history(monkeypatch):
    readings = {"a": iter([_one_shot(100, 1000), _one_shot(200, 1400)]), "b": iter([_one_shot(5000, 1000), _one_shot(5040, 1400)])}
    monkeypatch.setattr(collector, "_docker_request", lambda socket_path, endpoint: next(readings[endpoint.split("/")[2]]))

    for name in ("a", "b"):
        collector._container_usage("/sock", name)

    assert collector._container_usage("/sock", "a")["cpu_pct"] == 100.0
    assert collector._container_usage("/sock", "b")["cpu_pct"] == 40.0


def test_a_counter_that_goes_backwards_skips_that_sample_and_recovers(monkeypatch):
    readings = iter([_one_shot(900, 5000), _one_shot(50, 5400), _one_shot(150, 5800)])
    monkeypatch.setattr(collector, "_docker_request", lambda socket_path, endpoint: next(readings))

    collector._container_usage("/sock", "abc")
    restarted = collector._container_usage("/sock", "abc")
    after = collector._container_usage("/sock", "abc")

    assert "cpu_pct" not in restarted        # the container restarted
    assert after["cpu_pct"] == 100.0         # measured from the new baseline


def test_the_normal_request_is_used_when_one_shot_is_refused_and_remembered(monkeypatch):
    classic = {"cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 1000, "online_cpus": 4}, "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 600}}
    asked = []

    def fake(socket_path, endpoint):
        asked.append(endpoint)
        if "one-shot" in endpoint:
            raise OSError("Docker API returned HTTP 403")
        return classic

    monkeypatch.setattr(collector, "_docker_request", fake)

    assert collector._container_usage("/sock", "abc")["cpu_pct"] == 200.0
    assert collector._one_shot_unsupported
    asked.clear()
    assert collector._container_usage("/sock", "abc")["cpu_pct"] == 200.0
    assert asked == ["/containers/abc/stats?stream=false"]   # no wasted one-shot attempt next time


def test_a_container_that_vanished_does_not_make_one_shot_look_unsupported(monkeypatch):
    def gone(socket_path, endpoint):
        raise OSError("Docker API returned HTTP 404")

    monkeypatch.setattr(collector, "_docker_request", gone)

    assert collector._container_usage("/sock", "abc") == {}
    assert not collector._one_shot_unsupported


@pytest.mark.parametrize("bad", ["12", None, True, [1], {"a": 1}, float("nan")])
def test_hostile_cpu_counters_give_no_figure_and_no_crash(monkeypatch, bad):
    readings = iter([_one_shot(100, 1000), _one_shot(bad, 1400), _one_shot(200, bad), _one_shot(300, 1800)])
    monkeypatch.setattr(collector, "_docker_request", lambda socket_path, endpoint: next(readings))

    results = [collector._container_usage("/sock", "abc") for _ in range(3)]

    assert all("cpu_pct" not in usage for usage in results[:2])


def test_cpu_state_for_containers_that_are_gone_is_dropped(monkeypatch):
    collector._cpu_previous.update({"gone" + "0" * 8: (1, 1), "kept": (2, 2)})

    def fake(socket_path, endpoint):
        if endpoint.startswith("/containers/json"):
            return [{"Id": "kept", "Names": ["/kept"], "Image": "x", "State": "running"}]
        if endpoint.endswith("/json"):
            return {"Id": "kept", "Name": "/kept", "State": {"Status": "running"}, "Config": {"Image": "x"}}
        return _one_shot(10, 100)

    monkeypatch.setattr(collector, "_docker_request", fake)
    monkeypatch.setattr(collector.Path, "exists", lambda self: True)

    collector.collect_containers("/sock")

    assert list(collector._cpu_previous) == ["kept"]


def test_format_ports_lists_published_bindings():
    ports = {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}, {"HostIp": "::", "HostPort": "8080"}], "443/tcp": None}
    assert collector._format_ports(ports) == "8080->80/tcp"
