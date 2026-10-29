import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.collector import collector
from src.backend.routes import monitoring as monitoring_routes
from src.backend import main
from src.backend.models import Base, HostMetric, Server, User


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    now = datetime.now(timezone.utc)
    for server_id, name, age in ((1, "local", 3), (2, "nas", 2), (3, "pi", 1)):
        session.add(Server(id=server_id, owner_id=1, hostname=name, check_interval_seconds=60, created_at=now - timedelta(days=age), collector_token_hash=main._hash_secret(f"token-{server_id}")))
    session.commit()
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    return session


def _ingest(db, server_id, **server):
    monitoring_routes.ingest_metrics(main.MetricsIngestRequest(server_id=server_id, server=server, host={"cpu_utilization_pct": 10, "ram_pct": 20, "disk_usage_pct": 30}), authorization=f"Bearer token-{server_id}", session_id=None, db=db)


def test_dashboard_host_is_listed_first_and_flagged(db):
    _ingest(db, 1, dashboard_host=True)
    _ingest(db, 2)

    servers = monitoring_routes.monitoring_overview(session_id="s", db=db)["servers"]

    assert [(s["hostname"], s["is_dashboard_host"]) for s in servers] == [("local", True), ("pi", False), ("nas", False)]


def test_only_latest_claimant_is_dashboard_host_and_flag_can_be_dropped(db):
    _ingest(db, 1, dashboard_host=True)
    _ingest(db, 2, dashboard_host=True)
    assert [s.id for s in db.query(Server).filter_by(is_dashboard_host=True)] == [2]

    _ingest(db, 2)
    assert db.query(Server).filter_by(is_dashboard_host=True).count() == 0


def test_history_returns_recent_samples_oldest_first(db):
    now = datetime.now(timezone.utc)
    for minutes_ago, cpu in ((90, 1), (20, 2), (5, 3)):
        db.add(HostMetric(server_id=1, time=now - timedelta(minutes=minutes_ago), cpu_utilization_pct=cpu, ram_pct=50, disk_usage_pct=60))
    db.add(HostMetric(server_id=2, time=now, cpu_utilization_pct=99))
    db.commit()

    samples = monitoring_routes.server_history(1, minutes=30, session_id="s", db=db)["samples"]

    assert [(s["cpu"], s["ram"], s["disk"]) for s in samples] == [(2, 50, 60), (3, 50, 60)]


def test_history_of_unknown_server_is_404(db):
    with pytest.raises(HTTPException) as exc:
        monitoring_routes.server_history(99, session_id="s", db=db)
    assert exc.value.status_code == 404


def test_collector_sends_flag_only_when_configured(monkeypatch):
    monkeypatch.setenv("INFRADECK_DASHBOARD_HOST", "true")
    assert collector.collect_host()[0]["dashboard_host"] is True

    monkeypatch.delenv("INFRADECK_DASHBOARD_HOST")
    assert "dashboard_host" not in collector.collect_host()[0]


def _ingest_containers(db, server_id, names):
    containers = [{"container_id": f"id-{name}", "name": name, "status": "running"} for name in names]
    monitoring_routes.ingest_metrics(main.MetricsIngestRequest(server_id=server_id, host={"cpu_utilization_pct": 1}, containers=containers), authorization=f"Bearer token-{server_id}", session_id=None, db=db)


def _overview_server(db, server_id):
    return next(s for s in monitoring_routes.monitoring_overview(session_id="s", db=db)["servers"] if s["id"] == server_id)


def test_overview_lists_only_containers_from_latest_check_in(db):
    _ingest_containers(db, 2, ["web", "old"])
    _ingest_containers(db, 2, ["web", "db"])

    assert sorted(c["name"] for c in _overview_server(db, 2)["containers"]) == ["db", "web"]


def test_overview_lists_no_containers_when_latest_check_in_had_none(db):
    _ingest_containers(db, 2, ["web"])
    db.query(main.ContainerMetric).update({"time": datetime.now(timezone.utc) - timedelta(minutes=5)})
    db.commit()
    _ingest_containers(db, 2, [])

    assert _overview_server(db, 2)["containers"] == []


def test_overview_counts_active_alerts_per_server(db):
    from src.backend import alerts
    alerts.raise_alert(db, 2, "a", alerts.WARNING, "t", "d")
    alerts.raise_alert(db, 2, "b", alerts.WARNING, "t", "d")
    alerts.raise_alert(db, 3, "c", alerts.WARNING, "t", "d")
    alerts.resolve_alerts(db, 3, ["c"])
    db.commit()

    assert _overview_server(db, 2)["active_alert_count"] == 2
    assert _overview_server(db, 3)["active_alert_count"] == 0
