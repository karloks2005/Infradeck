import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend.routes import monitoring as monitoring_routes
from src.backend import alerts, main
from src.backend.models import Base, IncidentAlert, Server, User

TOKEN = "collector-token"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add(Server(id=5, owner_id=1, hostname="nas", check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKEN)))
    session.commit()
    return session


def _ingest(db, containers):
    monitoring_routes.ingest_metrics(main.MetricsIngestRequest(server_id=5, containers=containers), authorization=f"Bearer {TOKEN}", session_id=None, db=db)


def _container(name, status="running", health=None, restarts=0):
    return {"container_id": f"id-{name}", "name": name, "status": status, "healthcheck_status": health, "restart_count": restarts}


def _active(db):
    return db.query(IncidentAlert).filter_by(status="active").all()


def test_raise_alert_dedupes_and_refreshes(db):
    assert alerts.raise_alert(db, 5, "disk", alerts.WARNING, "Disk 86%", "first") is True
    assert alerts.raise_alert(db, 5, "disk", alerts.CRITICAL, "Disk 96%", "second") is False

    [alert] = _active(db)
    assert (alert.severity, alert.title, alert.description) == (alerts.CRITICAL, "Disk 96%", "second")


def test_raise_alert_rejects_unknown_severity(db):
    with pytest.raises(ValueError):
        alerts.raise_alert(db, 5, "disk", "SEVERE", "t", "d")


def test_resolved_rule_can_fire_again_as_new_alert(db):
    alerts.raise_alert(db, 5, "disk", alerts.WARNING, "t", "d")
    assert alerts.resolve_alerts(db, 5, ["disk"]) == 1
    assert alerts.raise_alert(db, 5, "disk", alerts.WARNING, "t", "d") is True

    assert db.query(IncidentAlert).count() == 2
    assert db.query(IncidentAlert).filter_by(status="resolved").one().resolved_at is not None


def test_unhealthy_container_alerts_once_and_resolves(db):
    for _ in range(3):
        _ingest(db, [_container("web", health="unhealthy")])

    [alert] = _active(db)
    assert alert.rule_key == "container:web:unhealthy"
    assert alert.severity == alerts.CRITICAL

    _ingest(db, [_container("web", health="healthy")])
    assert _active(db) == []


def test_restart_loop_alerts_once_when_count_climbs(db):
    for restarts in range(0, 5):
        _ingest(db, [_container("db", status="restarting", restarts=restarts)])

    [alert] = _active(db)
    assert alert.rule_key == "container:db:restart_loop"
    assert "restarted 4 times" in alert.description


def test_restart_loop_ignores_old_restarts_and_status_alone(db):
    """A high restart count from long ago, or one restarting sample, is not a loop."""
    for _ in range(3):
        _ingest(db, [_container("db", status="restarting", restarts=40)])

    assert _active(db) == []


def test_removed_container_alert_resolves_when_others_report(db):
    _ingest(db, [_container("web", health="unhealthy"), _container("db")])
    _ingest(db, [_container("db")])

    # The unhealthy incident is gone with the container; what remains is only the informational removal notice.
    assert [(alert.rule_key, alert.severity) for alert in _active(db)] == [("container:web:removed", alerts.INFO)]


def test_empty_container_list_keeps_container_alerts(db):
    """No containers means Docker was unreachable, not that every problem cleared."""
    _ingest(db, [_container("web", health="unhealthy")])
    _ingest(db, [])

    assert len(_active(db)) == 1


def test_container_sync_leaves_other_rules_alone(db):
    alerts.raise_alert(db, 5, alerts.SERVER_OFFLINE_RULE, alerts.CRITICAL, "offline", "d")
    alerts.sync_alerts(db, 5, alerts.CONTAINER_RULE_PREFIX, {})

    assert len(_active(db)) == 1


def test_overview_lists_critical_first_with_hostname_and_true_count(db, monkeypatch):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    monkeypatch.setattr(main, "OVERVIEW_ALERT_LIMIT", 2)
    alerts.raise_alert(db, 5, "a", alerts.CRITICAL, "old critical", "d")
    db.commit()
    alerts.raise_alert(db, 5, "b", alerts.INFO, "new info", "d")
    alerts.raise_alert(db, 5, "c", alerts.WARNING, "new warning", "d")
    db.commit()

    overview = monitoring_routes.monitoring_overview(session_id="s", db=db)

    assert overview["alert_count"] == 3
    assert [a["title"] for a in overview["alerts"]] == ["old critical", "new warning"]
    assert overview["alerts"][0]["hostname"] == "nas"
