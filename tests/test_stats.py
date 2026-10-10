import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.backend import main, stats
from src.backend.models import Base, HostMetric, IncidentAlert, Server, User
from tests.fakes import FakeRedis

NOW = datetime(2026, 3, 11, 12, 30, tzinfo=timezone.utc)   # a Wednesday


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add_all(Server(id=n, owner_id=1, hostname=f"srv{n}", check_interval_seconds=60) for n in (1, 2))
    session.commit()
    return session


def incident(db, opened, severity="WARNING", rule="host:disk:/", resolved=None, server=1):
    db.add(IncidentAlert(server_id=server, severity=severity, title="t", description="d", rule_key=rule, created_at=opened,
                         status="resolved" if resolved else "active", resolved_at=resolved))
    db.commit()


# --- grouping incidents by what they are about ------------------------------------------------------

@pytest.mark.parametrize("key, label", [
    ("host:disk:/", "Disk space"), ("host:disk:/media/data", "Disk space"), ("host:memory", "Memory"), ("host:inodes:/", "Inodes"),
    ("system:oom", "Out-of-memory kills"), ("system:docker_slow", "Docker slow"), ("system:collect_slow", "Collector too slow"),
    ("server_offline", "Server offline"), ("trend:memory_leak:abc", "Possible memory leaks"), ("network:listener:tcp:22", "Network"),
    ("container:abc:restart_loop", "Containers"), (None, "Other"), ("", "Other"), ("something:else", "Other"), ("host", "Other"), (":" * 50, "Other"),
])
def test_rule_keys_become_readable_groups(key, label):
    assert stats.incident_kind(key) == label


def test_long_or_odd_rule_keys_never_leak_through():
    assert stats.incident_kind("x" * 5000) == "Other" and stats.incident_kind("host:unknown_metric") == "Other"


# --- incident statistics ------------------------------------------------------------------------

def test_incidents_are_counted_per_day_and_severity_for_a_week(db):
    incident(db, NOW - timedelta(days=1, hours=3), "CRITICAL")
    incident(db, NOW - timedelta(days=1, hours=1), "WARNING")
    incident(db, NOW - timedelta(days=1, hours=2), "WARNING", rule="system:oom")
    incident(db, NOW - timedelta(days=3), "INFO", rule="system:reboot")

    result = stats.incident_stats(db, "7d", NOW)

    assert result["bucket"] == "day" and result["opened"] == 4
    assert result["by_bucket"] == [
        {"time": "2026-03-08T00:00:00+00:00", "critical": 0, "warning": 0, "info": 1},
        {"time": "2026-03-10T00:00:00+00:00", "critical": 1, "warning": 2, "info": 0},
    ]
    assert result["by_kind"][0] == {"kind": "Disk space", "count": 2}
    assert sorted(item["kind"] for item in result["by_kind"]) == ["Disk space", "Out-of-memory kills", "Reboots"]


def test_the_bucket_size_follows_the_range(db):
    incident(db, NOW - timedelta(minutes=30))
    assert [stats.incident_stats(db, name, NOW)["bucket"] for name in ("1h", "6h", "24h", "7d", "30d", "90d", "1y", "all")] == ["hour", "hour", "hour", "day", "day", "week", "week", "month"]
    assert [stats.incident_stats(db, "1y", NOW)["by_bucket"][0]["time"]][0] == "2026-03-09T00:00:00+00:00"     # weeks start on Monday
    assert stats.incident_stats(db, "all", NOW)["by_bucket"][0]["time"] == "2026-03-01T00:00:00+00:00"


def test_incidents_older_than_the_range_are_left_out(db):
    incident(db, NOW - timedelta(days=40))
    incident(db, NOW - timedelta(days=2))

    assert stats.incident_stats(db, "7d", NOW)["opened"] == 1
    assert stats.incident_stats(db, "90d", NOW)["opened"] == 2
    assert stats.incident_stats(db, "all", NOW)["opened"] == 2


def test_mean_time_to_resolve_uses_only_resolved_incidents(db):
    incident(db, NOW - timedelta(hours=5), resolved=NOW - timedelta(hours=4))               # 60 minutes
    incident(db, NOW - timedelta(hours=5), resolved=NOW - timedelta(hours=4, minutes=20))  # 40 minutes
    incident(db, NOW - timedelta(hours=2))                                                  # still open

    result = stats.incident_stats(db, "24h", NOW)

    assert (result["opened"], result["resolved"], result["mean_minutes_to_resolve"]) == (3, 2, 50.0)


def test_open_incidents_are_counted_by_severity_whatever_the_range(db):
    incident(db, NOW - timedelta(days=200), "CRITICAL")
    incident(db, NOW - timedelta(days=1), "WARNING")
    incident(db, NOW - timedelta(days=1), "WARNING", resolved=NOW)

    result = stats.incident_stats(db, "24h", NOW)

    assert result["open_now"] == {"critical": 1, "warning": 1, "info": 0}   # the 200-day-old one still counts as open
    assert result["opened"] == 2                                             # but only two opened within the day


def test_an_install_without_incidents_gives_empty_answers(db):
    for name in ("1h", "30d", "all"):
        result = stats.incident_stats(db, name, NOW)
        assert result["opened"] == 0 and result["by_bucket"] == [] and result["by_kind"] == [] and result["mean_minutes_to_resolve"] is None
        assert result["open_now"] == {"critical": 0, "warning": 0, "info": 0}


def test_an_unknown_severity_is_counted_as_info_not_dropped(db):
    incident(db, NOW - timedelta(hours=1), "SURPRISE")
    assert stats.incident_stats(db, "24h", NOW)["by_bucket"][0]["info"] == 1


def test_the_amount_of_data_read_is_capped_and_says_so(db, monkeypatch):
    monkeypatch.setattr(stats, "MAX_INCIDENTS_READ", 3)
    for hours in range(6):
        incident(db, NOW - timedelta(hours=hours))

    result = stats.incident_stats(db, "24h", NOW)

    assert result["truncated"] is True and result["opened"] == 3


# --- availability ------------------------------------------------------------------------------

def checkins(db, server, start, count, step=timedelta(minutes=1)):
    db.add_all(HostMetric(server_id=server, time=start + step * i, cpu_utilization_pct=1.0) for i in range(count))
    db.commit()


def test_a_server_that_always_reported_is_fully_available(db):
    checkins(db, 1, NOW - timedelta(hours=6), 361)

    [first, second] = stats.availability(db, "6h", NOW)

    assert first["server_id"] == 1 and first["percent"] == 100.0 and first["checkins"] == 361
    assert second["server_id"] == 2 and second["percent"] is None and second["checkins"] == 0   # never reported: unknown, not 0%


def test_a_server_that_was_silent_for_half_the_time_is_half_available(db):
    checkins(db, 1, NOW - timedelta(hours=6), 181)
    checkins(db, 1, NOW - timedelta(minutes=1), 1)    # reported at the start and now, nothing in between

    assert stats.availability(db, "6h", NOW)[0]["percent"] == pytest.approx(51.0, abs=2)


def test_a_new_server_is_not_blamed_for_the_time_before_it_existed(db):
    checkins(db, 1, NOW - timedelta(hours=1), 61)

    assert stats.availability(db, "30d", NOW)[0]["percent"] == 100.0


def test_availability_is_exact_even_after_the_history_has_been_summarised(db):
    from src.backend import rollups
    checkins(db, 1, NOW - timedelta(days=3, hours=-9, minutes=-17), 3 * 24 * 60 - 9 * 60 - 17)    # starts at 09:17 on the first day
    rollups.run_rollups(db, NOW)

    for name in ("7d", "30d", "all"):
        assert stats.availability(db, name, NOW)[0]["percent"] == 100.0


def test_availability_never_exceeds_one_hundred_percent(db):
    checkins(db, 1, NOW - timedelta(hours=1), 600, step=timedelta(seconds=6))   # far more often than the 60 s interval

    assert stats.availability(db, "6h", NOW)[0]["percent"] == 100.0


def test_availability_works_for_every_range_without_data(db):
    for name in ("1h", "6h", "24h", "7d", "30d", "90d", "1y", "all"):
        assert [item["percent"] for item in stats.availability(db, name, NOW)] == [None, None]


# --- the endpoint ---------------------------------------------------------------------------------

@pytest.fixture
def client(db, monkeypatch):
    main.app.dependency_overrides[main.get_db] = lambda: db
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1, "role": "user"})
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_the_endpoint_returns_incidents_and_availability(db, client):
    incident(db, datetime.now(timezone.utc) - timedelta(hours=2), "CRITICAL")

    body = client.get("/stats/fleet?range=24h").json()

    assert set(body) == {"incidents", "availability"} and body["incidents"]["opened"] == 1 and body["incidents"]["open_now"]["critical"] == 1
    assert [item["hostname"] for item in body["availability"]] == ["srv1", "srv2"]


def test_answers_are_cached_for_a_minute_per_range_only(db, client, monkeypatch):
    calls = []
    real = stats.fleet_stats
    monkeypatch.setattr(stats, "fleet_stats", lambda db, range_name: calls.append(range_name) or real(db, range_name))

    client.get("/stats/fleet?range=7d"); client.get("/stats/fleet?range=7d"); client.get("/stats/fleet?range=30d")

    assert calls == ["7d", "30d"]
    assert sorted(key for key in main.redis_client.keys("*")) == ["homelab:stats:fleet:30d", "homelab:stats:fleet:7d"]


def test_a_broken_cache_does_not_break_the_page(db, client, monkeypatch):
    class Broken:
        def get(self, *args, **kwargs): raise ConnectionError("redis is down")
        def set(self, *args, **kwargs): raise ConnectionError("redis is down")

    monkeypatch.setattr(main, "redis_client", Broken())

    assert client.get("/stats/fleet?range=24h").status_code == 200


@pytest.mark.parametrize("query", ["range=2h", "range=", "range=ALL", "range=1h;drop", "range=" + "a" * 5000])
def test_unknown_ranges_are_refused_and_never_become_cache_keys(db, client, query):
    assert client.get(f"/stats/fleet?{query}").status_code == 422
    assert main.redis_client.keys("*") == []


def test_signing_in_is_required(db, monkeypatch):
    main.app.dependency_overrides[main.get_db] = lambda: db
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    try:
        assert TestClient(main.app).get("/stats/fleet?range=24h").status_code == 401
    finally:
        main.app.dependency_overrides.clear()


# --- when a server is busy: hour of day by weekday ----------------------------------------------------

def server_row(db, server_id=1):
    return db.get(Server, server_id)


def test_the_heatmap_puts_each_hour_in_its_weekday_and_hour_cell(db):
    monday_ten = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)           # a Monday
    db.add_all(HostMetric(server_id=1, time=monday_ten + timedelta(minutes=minute), cpu_utilization_pct=value) for minute, value in ((0, 20.0), (1, 60.0)))
    db.add(HostMetric(server_id=1, time=datetime(2026, 3, 8, 23, 30, tzinfo=timezone.utc), cpu_utilization_pct=5.0))    # Sunday 23:30
    db.commit()

    result = stats.hour_weekday_heatmap(db, server_row(db), "cpu", 0, NOW)

    assert result["cells"][0][10] == 40.0 and result["samples"][0][10] == 2
    assert result["cells"][6][23] == 5.0
    assert result["cells"][2][2] is None and len(result["cells"]) == 7 and all(len(row) == 24 for row in result["cells"])


def test_the_heatmap_follows_the_viewers_time_zone(db):
    when = datetime(2026, 3, 8, 23, 30, tzinfo=timezone.utc)                  # Sunday 23:30 UTC
    db.add(HostMetric(server_id=1, time=when, cpu_utilization_pct=5.0))
    db.commit()

    east = stats.hour_weekday_heatmap(db, server_row(db), "cpu", 120, NOW)    # UTC+2: already Monday 01:30
    west = stats.hour_weekday_heatmap(db, server_row(db), "cpu", -300, NOW)   # UTC-5: Sunday 18:30

    assert east["cells"][0][1] == 5.0 and west["cells"][6][18] == 5.0


def test_the_heatmap_ignores_samples_older_than_thirty_days_and_unknowns(db):
    db.add(HostMetric(server_id=1, time=NOW - timedelta(days=40), cpu_utilization_pct=99.0))
    db.add(HostMetric(server_id=1, time=NOW - timedelta(days=2), ram_pct=50.0))             # no CPU reading
    db.commit()

    assert all(cell is None for row in stats.hour_weekday_heatmap(db, server_row(db), "cpu", 0, NOW)["cells"] for cell in row)


def test_the_heatmap_is_the_same_before_and_after_summarising(db):
    from src.backend import rollups
    db.add_all(HostMetric(server_id=1, time=NOW - timedelta(days=10) + timedelta(minutes=10 * i), cpu_utilization_pct=float(i % 90)) for i in range(10 * 24 * 6))
    db.commit()
    before = stats.hour_weekday_heatmap(db, server_row(db), "cpu", 60, NOW)
    for _ in range(10):
        rollups.run_rollups(db, NOW)

    assert stats.hour_weekday_heatmap(db, server_row(db), "cpu", 60, NOW) == before


def test_the_heatmap_endpoint_validates_everything_and_caches_per_server_metric_and_offset(db, client):
    assert client.get("/stats/servers/1/heatmap?metric=cpu&offset=60").status_code == 200
    assert client.get("/stats/servers/1/heatmap?metric=cpu&offset=60").status_code == 200
    assert main.redis_client.keys("*") == ["homelab:stats:heatmap:1:cpu:60"]
    for query in ("metric=disk", "metric=cpu;x", "offset=900", "offset=-900", "offset=abc", "offset=1.5"):
        assert client.get(f"/stats/servers/1/heatmap?{query}").status_code == 422
    assert client.get("/stats/servers/999/heatmap").status_code == 404
    assert main.redis_client.keys("*") == ["homelab:stats:heatmap:1:cpu:60"]       # refused requests left nothing behind


def test_the_heatmap_endpoint_needs_a_session(db, monkeypatch):
    main.app.dependency_overrides[main.get_db] = lambda: db
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    try:
        assert TestClient(main.app).get("/stats/servers/1/heatmap").status_code == 401
    finally:
        main.app.dependency_overrides.clear()
