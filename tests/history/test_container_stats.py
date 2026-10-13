"""The Statistics page's views of every container at once, and comparing a range with the one before it."""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agent import collector
from src.backend import history, main, rollups, stats
from src.backend.models import Base, ContainerMetric, HostMetric, Server, User
from tests.fakes import FakeRedis

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add(Server(id=1, owner_id=1, hostname="alpha", check_interval_seconds=60))
    session.commit()
    return session


def seed(db, name, start, count, cpu, memory=100.0, restarts=lambda i: 0, status=lambda i: "running", step=timedelta(minutes=1)):
    for index in range(count):
        db.add(ContainerMetric(server_id=1, container_id=name * 8, name=name, status=status(index), cpu_pct=cpu(index), memory_used_mb=memory, restart_count=restarts(index), time=start + index * step))
    db.commit()


def by_name(result):
    return {item["name"]: item for item in result["containers"]}


def test_a_day_is_summed_from_the_check_ins_and_ranked_by_average_cpu(db):
    seed(db, "web", NOW - timedelta(hours=2), 120, cpu=lambda i: 10.0 if i < 60 else 30.0, memory=200.0, restarts=lambda i: 3 if i < 100 else 5, status=lambda i: "exited" if i < 6 else "running")
    seed(db, "db", NOW - timedelta(hours=2), 120, cpu=lambda i: 80.0 if i == 7 else 1.0, memory=900.0)

    result = stats.container_usage(db, db.get(Server, 1), "24h", NOW)
    usage = by_name(result)

    assert [item["name"] for item in result["containers"]] == ["web", "db"]
    assert (usage["web"]["avg_cpu"], usage["web"]["max_cpu"], usage["web"]["avg_memory_mb"]) == (20.0, 30.0, 200.0)
    assert (usage["web"]["restarts"], usage["web"]["samples"], usage["web"]["down_samples"]) == (2, 120, 6)
    assert usage["db"]["max_cpu"] == 80.0 and usage["db"]["restarts"] == 0
    known = [value for value in usage["web"]["sparkline"] if value is not None]
    assert len(usage["web"]["sparkline"]) == stats.SPARKLINE_POINTS and known[0] == 10.0 and known[-1] == 30.0


def test_a_week_gives_the_same_answer_from_summaries_as_from_check_ins(db):
    start = NOW - timedelta(days=3)
    seed(db, "web", start, 3 * 24 * 4, cpu=lambda i: float(i % 50), step=timedelta(minutes=15))
    before = by_name(stats.container_usage(db, db.get(Server, 1), "7d", NOW))["web"]   # nothing summarised yet
    rollups.run_rollups(db, NOW)
    db.commit()
    after = by_name(stats.container_usage(db, db.get(Server, 1), "7d", NOW))["web"]

    assert after["samples"] == before["samples"] == 3 * 24 * 4
    assert after["avg_cpu"] == pytest.approx(before["avg_cpu"]) and after["max_cpu"] == before["max_cpu"] == 49.0


def test_the_busiest_container_is_named_for_each_period(db):
    seed(db, "web", NOW - timedelta(minutes=30), 30, cpu=lambda i: 5.0)
    seed(db, "backup", NOW - timedelta(minutes=30), 30, cpu=lambda i: 94.0 if 10 <= i < 15 else 1.0)

    result = stats.busiest_containers(db, db.get(Server, 1), "1h", NOW)
    names = [point["name"] for point in result["points"]]

    assert result["bucket_seconds"] == 60 and len(names) == 30
    assert names[9] == "web" and names[10:15] == ["backup"] * 5 and result["points"][10]["cpu"] == 94.0


def test_the_busiest_container_over_a_week_comes_from_hourly_summaries_plus_what_is_not_summarised_yet(db):
    seed(db, "web", NOW - timedelta(days=2), 2 * 24 * 4, cpu=lambda i: 90.0 if i == 0 else 3.0, step=timedelta(minutes=15))
    rollups.run_rollups(db, NOW - timedelta(hours=6))
    db.commit()

    result = stats.busiest_containers(db, db.get(Server, 1), "7d", NOW)
    assert result["bucket_seconds"] == 3600 and len(result["points"]) == 48
    assert result["points"][0] == {"time": (NOW - timedelta(days=2)).isoformat(), "name": "web", "cpu": 90.0}


def test_the_period_before_a_range_can_be_asked_for(db):
    for index in range(120):
        db.add(HostMetric(server_id=1, time=NOW - timedelta(minutes=120 - index, seconds=-30), cpu_utilization_pct=10.0 if index < 60 else 50.0))
    db.commit()
    current = history.history(db, history.HOST, 1, None, "1h", ("cpu",), 60, now=NOW)
    before = history.history(db, history.HOST, 1, None, "1h", ("cpu",), 60, now=history.previous_end("1h", NOW))

    assert {point["cpu"]["avg"] for point in current["points"]} == {50.0} and {point["cpu"]["avg"] for point in before["points"]} == {10.0}
    assert before["end"] == (NOW - timedelta(hours=1)).isoformat() and history.previous_end("all", NOW) is None


def test_the_endpoints_answer_and_refuse_what_makes_no_sense(db, monkeypatch):
    main.app.dependency_overrides[main.get_db] = lambda: db
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1, "role": "user"})
    client = TestClient(main.app)
    try:
        db.add(HostMetric(server_id=1, time=datetime.now(timezone.utc) - timedelta(minutes=90), cpu_utilization_pct=7.0))
        db.commit()
        seed(db, "web", datetime.now(timezone.utc) - timedelta(minutes=10), 5, cpu=lambda i: 4.0)

        assert client.get("/stats/servers/1/containers?range=24h").json()["containers"][0]["name"] == "web"
        assert client.get("/stats/servers/1/busiest?range=1h").json()["points"][0]["name"] == "web"
        previous = client.get("/monitoring/servers/1/history?range=1h&metrics=cpu&previous=true").json()
        assert previous["previous"] is True and [point["cpu"]["avg"] for point in previous["points"]] == [7.0]
        assert client.get("/monitoring/servers/1/history?range=all&previous=true").status_code == 422
        assert client.get("/stats/servers/99/containers").status_code == 404 and client.get("/stats/servers/1/busiest?range=2h").status_code == 422
    finally:
        main.app.dependency_overrides.clear()


def test_container_swap_is_read_from_its_cgroup_and_only_for_real_ids(tmp_path, monkeypatch):
    cid = "ab" * 32
    scope = tmp_path / f"sys/fs/cgroup/system.slice/docker-{cid}.scope"
    scope.mkdir(parents=True)
    (scope / "memory.swap.current").write_text(f"{84 * collector.MiB}\n")
    monkeypatch.setattr(collector, "HOST_ROOT", tmp_path)

    assert collector.container_swap_mb(cid) == 84.0
    assert collector.container_swap_mb("cd" * 32) is None
    assert collector.container_swap_mb("../../etc/passwd") is None


def test_swap_per_container_is_averaged_over_the_range_and_survives_summarising(db):
    start = NOW - timedelta(days=2)
    for index in range(2 * 24 * 4):
        db.add(ContainerMetric(server_id=1, container_id="gitlab00", name="gitlab", status="running", cpu_pct=1.0, memory_used_mb=100.0, swap_mb=1000.0 if index >= 96 else 0.0, restart_count=0, time=start + index * timedelta(minutes=15)))
    db.commit()
    before = by_name(stats.container_usage(db, db.get(Server, 1), "7d", NOW))["gitlab"]
    rollups.run_rollups(db, NOW)
    db.commit()
    after = by_name(stats.container_usage(db, db.get(Server, 1), "7d", NOW))["gitlab"]

    assert (before["avg_swap_mb"], before["max_swap_mb"]) == (500.0, 1000.0)
    assert after["avg_swap_mb"] == pytest.approx(500.0) and after["max_swap_mb"] == 1000.0
    series = history.history(db, history.CONTAINER, 1, "gitlab00", "30d", ("swap_mb",), 60, now=NOW)
    assert series["points"][-1]["swap_mb"] == {"avg": 1000.0, "max": 1000.0}


def test_cpu_spikes_have_a_length_even_when_shorter_than_a_chart_point(db):
    server = db.get(Server, 1)
    for index in range(120):   # one check-in a minute; 100% for minutes 10-17 and 60-61, with one late check-in
        if index == 14:
            continue
        db.add(HostMetric(server_id=1, time=NOW - timedelta(minutes=120 - index), cpu_utilization_pct=100.0 if 10 <= index < 18 or index in (60, 61) else 8.0))
    db.commit()

    result = stats.cpu_spikes(db, server, "24h", NOW)
    assert result["available"] is True
    assert [(spike["seconds"], spike["peak"]) for spike in result["spikes"]] == [(8 * 60, 100.0), (2 * 60, 100.0)]
    assert result["spikes"][0]["time"] == (NOW - timedelta(minutes=110)).isoformat()
    assert stats.cpu_spikes(db, server, "30d", NOW) == {"server_id": 1, "range": "30d", "available": False, "threshold": 90.0, "spikes": []}


def test_installed_updates_become_an_event_once():
    before = {"updates_applied_at": 1000.0, "updates_pending": 41, "updates_manager": "apt"}
    assert main.packages_updated_detail(before, {**before, "updates_applied_at": 5000.0, "updates_pending": 0, "reboot_kernel": "6.8.0-85-generic"}) == "41 packages updated by apt, kernel 6.8.0-85-generic waiting for reboot"
    assert main.packages_updated_detail(before, {**before, "updates_applied_at": 5000.0, "updates_pending": 43}) == "Packages installed or updated by apt"
    assert main.packages_updated_detail(before, before) is None
    assert main.packages_updated_detail({}, {"updates_applied_at": 5000.0}) is None   # the first report is only a baseline
