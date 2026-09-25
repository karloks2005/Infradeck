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
    main.ingest_metrics(main.MetricsIngestRequest(server_id=server_id, server=server, host={"cpu_utilization_pct": 10, "ram_pct": 20, "disk_usage_pct": 30}), authorization=f"Bearer token-{server_id}", session_id=None, db=db)


def test_dashboard_host_is_listed_first_and_flagged(db):
    _ingest(db, 1, dashboard_host=True)
    _ingest(db, 2)

    servers = main.monitoring_overview(session_id="s", db=db)["servers"]

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

    samples = main.server_history(1, minutes=30, session_id="s", db=db)["samples"]

    assert [(s["cpu"], s["ram"], s["disk"]) for s in samples] == [(2, 50, 60), (3, 50, 60)]


def test_history_of_unknown_server_is_404(db):
    with pytest.raises(HTTPException) as exc:
        main.server_history(99, session_id="s", db=db)
    assert exc.value.status_code == 404


def test_collector_sends_flag_only_when_configured(monkeypatch):
    monkeypatch.setenv("HOMELAB_DASHBOARD_HOST", "true")
    assert collector.collect_host()[0]["dashboard_host"] is True

    monkeypatch.delenv("HOMELAB_DASHBOARD_HOST")
    assert "dashboard_host" not in collector.collect_host()[0]
