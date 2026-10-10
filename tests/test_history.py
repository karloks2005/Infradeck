import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.backend import history, main, rollups
from src.backend.models import AppSetting, Base, ContainerMetric, HostMetric, HostRollup, MountMetric, Server, User

NOW = datetime(2026, 3, 11, 12, 30, tzinfo=timezone.utc)   # a Wednesday


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add_all(Server(id=n, owner_id=1, hostname=f"srv{n}", check_interval_seconds=60) for n in (1, 2))
    session.commit()
    session.engine = engine
    return session


def seed(db, start, count, step=timedelta(minutes=1), server=1, cpu=lambda i: float(i % 100), **extra):
    db.add_all(HostMetric(server_id=server, time=start + step * i, cpu_utilization_pct=cpu(i), ram_pct=50.0, **extra) for i in range(count))
    db.commit()


def ask(db, range_name, metrics=("cpu",), server=1, spec=history.HOST, key=None, now=NOW):
    return history.history(db, spec, server, key, range_name, metrics, 60, now=now)


def times(result):
    return [datetime.fromisoformat(point["time"]) for point in result["points"]]


# --- short ranges come from the check-in samples -------------------------------------------------

def test_the_last_hour_is_one_point_per_check_in_with_its_average_and_maximum(db):
    seed(db, NOW - timedelta(minutes=90), 91)

    result = ask(db, "1h", ("cpu", "ram"))

    assert result["source"] == "detailed" and result["bucket_seconds"] == 60 and result["range"] == "1h"
    assert len(result["points"]) == 61      # both ends of the hour are included
    point = result["points"][-1]
    assert point["samples"] == 1 and point["cpu"]["avg"] == point["cpu"]["max"] and point["ram"] == {"avg": 50.0, "max": 50.0}
    assert times(result) == sorted(times(result)) and all(moment.tzinfo is not None for moment in times(result))


def test_a_week_of_samples_is_grouped_into_at_most_five_hundred_points_without_losing_anything(db):
    seed(db, NOW - timedelta(days=7), 7 * 24 * 60 + 1)

    result = ask(db, "7d")

    assert result["source"] == "detailed" and result["bucket_seconds"] == 1800 and len(result["points"]) <= history.MAX_POINTS
    assert sum(point["samples"] for point in result["points"]) == 7 * 24 * 60 + 1
    first = result["points"][0]
    assert first["cpu"]["max"] >= first["cpu"]["avg"]
    raw = [row[0] for row in db.query(HostMetric.cpu_utilization_pct).filter(HostMetric.time >= NOW - timedelta(days=7))]
    total = sum(point["cpu"]["avg"] * point["samples"] for point in result["points"])
    assert total / len(raw) == pytest.approx(sum(raw) / len(raw))
    assert max(point["cpu"]["max"] for point in result["points"]) == max(raw)


def test_grouping_keeps_the_exact_average_and_maximum_of_each_group(db):
    seed(db, NOW - timedelta(hours=23), 1)                                # makes the data span a whole day
    start = NOW - timedelta(hours=5)
    for minute, cpu in ((0, 10.0), (1, 20.0), (2, 90.0), (6, 40.0)):    # the first three share a five-minute group
        seed(db, start + timedelta(minutes=minute), 1, cpu=lambda i, cpu=cpu: cpu)

    result = ask(db, "24h")

    grouped = [point for point in result["points"] if point["samples"] > 1]
    assert result["bucket_seconds"] == 300 and len(grouped) == 1
    assert grouped[0]["cpu"] == {"avg": pytest.approx(40.0), "max": 90.0} and grouped[0]["samples"] == 3


def test_a_gap_stays_a_gap(db):
    seed(db, NOW - timedelta(hours=20), 10)
    seed(db, NOW - timedelta(hours=2), 10)

    result = ask(db, "24h")

    moments = times(result)
    assert max(b - a for a, b in zip(moments, moments[1:])) > timedelta(hours=15)   # nothing invented in between
    assert sum(point["samples"] for point in result["points"]) == 20


def test_a_metric_that_was_never_reported_is_null_not_zero(db):
    seed(db, NOW - timedelta(hours=1), 5)

    point = ask(db, "1h", ("cpu", "swap", "docker_ms"))["points"][-1]

    assert point["swap"] is None and point["docker_ms"] is None and point["cpu"] is not None


def test_a_server_without_data_gives_an_empty_series(db):
    for name in history.RANGES:
        result = ask(db, name, server=2)
        assert result["points"] == [] and result["data_since"] is None


# --- long ranges come from the summaries, topped up with the newest samples -----------------------

def test_thirty_days_use_hourly_summaries_and_match_the_samples(db):
    seed(db, NOW - timedelta(days=10), 10 * 24 * 6, step=timedelta(minutes=10))
    rollups.run_rollups(db, NOW)

    result = ask(db, "30d")

    assert result["source"] == "hour" and result["bucket_seconds"] == 3600
    assert sum(point["samples"] for point in result["points"]) == 10 * 24 * 6
    first = result["points"][0]
    raw = [row[0] for row in db.query(HostMetric.cpu_utilization_pct).filter(HostMetric.time >= datetime.fromisoformat(first["time"]), HostMetric.time < datetime.fromisoformat(first["time"]) + timedelta(hours=1))]
    assert first["cpu"] == {"avg": pytest.approx(sum(raw) / len(raw)), "max": max(raw)} and first["samples"] == len(raw)


def test_the_hour_that_has_not_been_summarised_yet_is_still_in_the_answer(db):
    seed(db, NOW - timedelta(days=3), 3 * 24 * 60)
    rollups.run_rollups(db, NOW)
    seed(db, NOW - timedelta(minutes=20), 20, cpu=lambda i: 1000.0)     # after the last closed hour

    result = ask(db, "30d")

    last = result["points"][-1]
    assert datetime.fromisoformat(last["time"]) == rollups.floor(rollups.HOUR, NOW) and last["cpu"]["max"] == 1000.0


@pytest.mark.parametrize("range_name", ["30d", "90d", "1y", "all"])
def test_the_answer_does_not_depend_on_how_far_the_rollup_job_has_got(db, monkeypatch, range_name):
    seed(db, NOW - timedelta(days=25), 25 * 24 * 4, step=timedelta(minutes=15), cpu=lambda i: float((i * 7) % 101))
    answers = [ask(db, range_name)]                                    # no summaries at all yet
    monkeypatch.setitem(rollups.MAX_BUCKETS_PER_RUN, rollups.HOUR, 200)
    monkeypatch.setitem(rollups.MAX_BUCKETS_PER_RUN, rollups.DAY, 5)
    for _ in range(3):
        rollups.run_rollups(db, NOW)
        answers.append(ask(db, range_name))                            # partly caught up
    monkeypatch.undo()
    for _ in range(20):
        if not any(rollups.run_rollups(db, NOW).values()):
            break
    answers.append(ask(db, range_name))                                # fully caught up

    assert len({repr(answer["points"]) for answer in answers}) == 1
    assert sum(point["samples"] for point in answers[0]["points"]) > 0


def test_every_sample_is_counted_exactly_once_in_the_all_time_view(db):
    seed(db, NOW - timedelta(days=75), 75 * 24 * 2, step=timedelta(minutes=30))
    rollups.run_rollups(db, NOW)
    for _ in range(10):
        rollups.run_rollups(db, NOW)

    result = ask(db, "all")

    assert result["source"] == "month" and result["start"] == result["data_since"]
    assert sum(point["samples"] for point in result["points"]) == 75 * 24 * 2
    assert [datetime.fromisoformat(point["time"]).day for point in result["points"]] == [1, 1, 1, 1]   # Dec, Jan, Feb, Mar (partial)


def test_weekly_points_start_on_mondays_and_the_current_week_is_partial(db):
    seed(db, NOW - timedelta(days=30), 30 * 24, step=timedelta(hours=1))
    for _ in range(10):
        rollups.run_rollups(db, NOW)

    result = ask(db, "1y")

    assert result["source"] == "week" and result["bucket_seconds"] == 7 * 86400
    assert all(moment.weekday() == 0 and moment.hour == 0 for moment in times(result))
    assert datetime.fromisoformat(result["points"][-1]["time"]) == rollups.floor(rollups.WEEK, NOW)
    assert sum(point["samples"] for point in result["points"]) == 30 * 24


def test_a_long_series_is_thinned_to_the_point_limit(db):
    start = rollups.floor(rollups.HOUR, NOW - timedelta(days=30))
    db.add_all(HostRollup(server_id=1, tier="hour", bucket_start=start + timedelta(hours=i), samples=60, avg_cpu=float(i % 50), n_cpu=60, max_cpu=float(i % 50) + 5) for i in range(720))
    db.add(AppSetting(key="rollup_done_hour", value=(start + timedelta(hours=720)).isoformat()))
    db.commit()

    result = ask(db, "30d", now=start + timedelta(hours=720))

    assert len(result["points"]) <= history.MAX_POINTS and result["bucket_seconds"] == 7200
    assert sum(point["samples"] for point in result["points"]) == 720 * 60
    assert result["points"][0]["cpu"] == {"avg": pytest.approx(0.5), "max": 6.0}   # hours 0 and 1: avg (0 + 1) / 2, max of 5 and 6


# --- when the history starts ----------------------------------------------------------------------

def test_history_starts_at_the_first_check_in_not_at_a_bucket_boundary(db):
    first = datetime(2026, 3, 9, 9, 17, tzinfo=timezone.utc)
    seed(db, first, 3 * 24 * 60)
    rollups.run_rollups(db, NOW)                     # hour and day summaries now start earlier, at 09:00 and midnight

    assert history.data_since(db, history.HOST, 1, None) == first
    assert datetime.fromisoformat(ask(db, "all")["data_since"]) == first


def test_when_old_samples_are_gone_the_oldest_summary_that_reaches_back_decides(db):
    first = datetime(2026, 3, 9, 9, 17, tzinfo=timezone.utc)
    seed(db, first, 3 * 24 * 60)
    rollups.run_rollups(db, NOW)

    db.query(HostMetric).filter(HostMetric.time < datetime(2026, 3, 10, tzinfo=timezone.utc)).delete()
    db.commit()
    assert history.data_since(db, history.HOST, 1, None) == datetime(2026, 3, 9, 9, tzinfo=timezone.utc)     # hourly rows still reach 09:00

    db.query(HostRollup).filter(HostRollup.tier == "hour").delete()
    db.commit()
    assert history.data_since(db, history.HOST, 1, None) == datetime(2026, 3, 9, tzinfo=timezone.utc)        # only the daily row remains


def test_a_series_with_no_data_has_no_start(db):
    assert history.data_since(db, history.HOST, 2, None) is None


# --- containers and mounts ---------------------------------------------------------------------

def test_a_container_series_has_cpu_memory_limit_and_down_counts(db):
    for minute in range(10):
        db.add(ContainerMetric(server_id=1, time=NOW - timedelta(minutes=minute), container_id="abc", name="web", status="running" if minute < 7 else "exited",
                               cpu_pct=float(minute * 10), memory_used_mb=100.0 + minute, memory_limit_mb=512.0, restart_count=minute // 5))
    db.add(ContainerMetric(server_id=1, time=NOW, container_id="other", name="db", status="running", cpu_pct=99.0))
    db.commit()

    result = history.history(db, history.CONTAINER, 1, "abc", "1h", ("cpu", "memory_mb", "memory_limit_mb", "restarts"), 60, now=NOW)

    assert sum(point["samples"] for point in result["points"]) == 10 and sum(point["down"] for point in result["points"]) == 3
    top = max(result["points"], key=lambda point: point["cpu"]["max"])
    assert top["cpu"]["max"] == 90.0 and top["memory_limit_mb"] == {"max": 512.0} and top["restarts"] == {"max": 1}   # limit and restarts have no average


def test_a_mount_series_is_picked_by_path(db):
    for mount, used in (("/", 10.0), ("/data", 90.0)):
        db.add(MountMetric(server_id=1, time=NOW - timedelta(minutes=1), mount=mount, used_pct=used, used_gb=used, total_gb=100.0))
    db.commit()

    result = history.history(db, history.MOUNT, 1, "/data", "1h", ("used_pct", "used_gb", "total_gb"), 60, now=NOW)

    point = result["points"][0]
    assert point["used_pct"] == {"avg": 90.0, "max": 90.0} and point["used_gb"] == {"avg": 90.0} and point["total_gb"] == {"max": 100.0}


@pytest.mark.parametrize("range_name", ["30d", "all"])
def test_container_summaries_agree_with_the_samples(db, range_name):
    for i in range(3 * 24 * 12):
        db.add(ContainerMetric(server_id=1, time=NOW - timedelta(days=3) + timedelta(minutes=5 * i), container_id="abc", name="web", status="running", cpu_pct=float(i % 40), memory_used_mb=200.0))
    db.commit()
    before = history.history(db, history.CONTAINER, 1, "abc", range_name, ("cpu", "memory_mb"), 60, now=NOW)
    for _ in range(10):
        rollups.run_rollups(db, NOW)

    after = history.history(db, history.CONTAINER, 1, "abc", range_name, ("cpu", "memory_mb"), 60, now=NOW)

    assert after["points"] == before["points"] and sum(point["samples"] for point in after["points"]) == 3 * 24 * 12


# --- the endpoints -------------------------------------------------------------------------------

@pytest.fixture
def client(db, monkeypatch):
    main.app.dependency_overrides[main.get_db] = lambda: db
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1, "role": "user"})
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_the_endpoint_returns_the_series_for_a_range(db, client):
    seed(db, datetime.now(timezone.utc) - timedelta(minutes=30), 30)

    response = client.get("/monitoring/servers/1/history?range=1h&metrics=cpu,ram")

    body = response.json()
    assert response.status_code == 200 and body["server_id"] == 1 and body["range"] == "1h" and body["metrics"] == ["cpu", "ram"]
    assert len(body["points"]) >= 29 and set(body["points"][0]) == {"time", "samples", "cpu", "ram"}


def test_without_a_range_the_original_answer_is_unchanged(db, client):
    seed(db, datetime.now(timezone.utc) - timedelta(minutes=10), 5)

    body = client.get("/monitoring/servers/1/history?minutes=30").json()

    assert set(body) == {"server_id", "samples"} and set(body["samples"][0]) == {"time", "cpu", "ram", "disk"}


@pytest.mark.parametrize("query", ["range=2h", "range=", "range=ALL", "range=1h&metrics=", "range=1h&metrics=cpu,,ram", "range=1h&metrics=password",
                                   "range=1h&metrics=cpu;drop", "range=1h&metrics=" + "cpu," * 100, "range=1h&metrics=cpu,ram_pct"])
def test_unknown_ranges_and_metrics_are_refused(client, query):
    assert client.get(f"/monitoring/servers/1/history?{query}").status_code == 422


def test_unknown_servers_mounts_and_containers_are_not_found(db, client):
    seed(db, datetime.now(timezone.utc) - timedelta(minutes=5), 3)
    assert client.get("/monitoring/servers/99/history?range=1h").status_code == 404
    assert client.get("/monitoring/servers/1/mounts/history?mount=/data").status_code == 404
    assert client.get("/monitoring/servers/1/mounts/history?mount=data").status_code == 404     # not an absolute path
    assert client.get("/monitoring/servers/1/mounts/history").status_code == 422
    assert client.get("/monitoring/servers/1/containers/nope/history?range=7d").status_code == 404
    assert client.get("/monitoring/servers/99/containers/nope/history").status_code == 404
    assert client.get("/monitoring/servers/1/containers/" + "a" * 129 + "/history").status_code == 422


def test_the_container_and_mount_endpoints_return_their_series(db, client):
    when = datetime.now(timezone.utc) - timedelta(minutes=2)
    db.add(ContainerMetric(server_id=1, time=when, container_id="abc", name="web", status="running", cpu_pct=12.0, memory_used_mb=100.0))
    db.add(MountMetric(server_id=1, time=when, mount="/data", used_pct=80.0))
    db.commit()

    container = client.get("/monitoring/servers/1/containers/abc/history?range=1h").json()
    mount = client.get("/monitoring/servers/1/mounts/history?mount=/data&range=1h&metrics=used_pct").json()

    assert container["name"] == "web" and container["container_id"] == "abc" and container["points"][0]["cpu"]["avg"] == 12.0
    assert mount["mount"] == "/data" and mount["points"][0]["used_pct"]["avg"] == 80.0


def test_signing_in_is_required(db, monkeypatch):
    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        client = TestClient(main.app)
        for path in ("/monitoring/servers/1/history?range=1h", "/monitoring/servers/1/mounts/history?mount=/", "/monitoring/servers/1/containers/abc/history"):
            assert client.get(path).status_code == 401
    finally:
        main.app.dependency_overrides.clear()
