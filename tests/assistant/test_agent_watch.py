"""Reusing a recent diagnosis (#126) and watching the fix it gave until it holds (#125)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agent import diagnosis, verify, worker
from src.backend import alerts
from src.backend.models import AgentReport, AgentRun, Base, ContainerMetric, HostMetric, IncidentAlert, Server

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Server(id=1, owner_id=1, hostname="alpha", cpu_cores=4, total_ram_gb=16.0, check_interval_seconds=60, last_seen_at=NOW))
    session.add(IncidentAlert(id=31, server_id=1, severity="CRITICAL", title="gitlab restart loop", description="d", status="resolved", rule_key="container:gitlab:restart_loop",
                              created_at=NOW - timedelta(hours=5), resolved_at=NOW - timedelta(hours=4)))
    session.add(IncidentAlert(id=42, server_id=1, severity="CRITICAL", title="gitlab restart loop", description="d", status="active", rule_key="container:gitlab:restart_loop", created_at=NOW - timedelta(minutes=30)))
    session.commit()
    yield session
    session.close()


def add_report(db, incident_id, verify_condition=None, created_at=None):
    body = {"signals": [{"text": "Memory hits the limit.", "holds": True, "evidence": [{"id": "metric:main", "label": "container memory"}]}], "fix_steps": [],
            "verify": verify_condition or {"type": "manual", "target": None, "threshold": None, "minutes": None}, "dropped_signals": 0}
    version = db.query(AgentReport).filter(AgentReport.incident_id == incident_id).count() + 1
    db.add(AgentReport(incident_id=incident_id, version=version, trigger="incident.opened", status="ok", model="gpt-test", confidence="high", summary="Killed for memory.",
                       body=json.dumps(body), evidence="{}", created_at=created_at or NOW))
    db.commit()


def container_samples(db, minutes, restarts=lambda minute: 6, status=lambda minute: "running"):
    for minute in range(minutes, -1, -1):
        db.add(ContainerMetric(server_id=1, container_id="g" * 64, name="gitlab", status=status(minute), restart_count=restarts(minute), memory_used_mb=1000, time=NOW - timedelta(minutes=minute)))
    db.commit()


# --- reuse (#126) -----------------------------------------------------------------------------------------------

def test_the_same_problem_within_a_day_reuses_the_diagnosis(db):
    add_report(db, 31, created_at=NOW - timedelta(hours=5))

    copy = diagnosis.reuse_recent(db, db.get(IncidentAlert, 42), NOW)

    shown = diagnosis.report_json(copy)
    assert copy.trigger == "reused" and shown["summary"] == "Killed for memory." and shown["reused_from"]["reference"] == "INC-0031"
    assert db.query(AgentRun).count() == 0          # no tokens spent


def test_an_older_or_different_diagnosis_is_not_reused(db):
    add_report(db, 31, created_at=NOW - timedelta(hours=25))
    assert diagnosis.reuse_recent(db, db.get(IncidentAlert, 42), NOW) is None

    db.add(IncidentAlert(id=50, server_id=1, severity="CRITICAL", title="redis restart loop", description="d", status="active", rule_key="container:redis:restart_loop"))
    db.commit()
    add_report(db, 31, created_at=NOW - timedelta(hours=1))
    assert diagnosis.reuse_recent(db, db.get(IncidentAlert, 50), NOW) is None   # another container


def test_the_worker_reuses_before_asking_the_model(db, monkeypatch):
    add_report(db, 31, created_at=NOW - timedelta(hours=1))
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(worker, "configured_key", lambda: "sk-test")
    monkeypatch.setattr(worker, "get_provider", lambda: pytest.fail("the model must not be asked"))

    assert worker.handle_incident({"incident_id": 42, "type": "incident.opened"}) == ("reused", "Same diagnosis as INC-0031; no tokens spent")


def test_getting_worse_or_asking_again_is_never_reused(db, monkeypatch):
    add_report(db, 31, created_at=NOW - timedelta(hours=1))
    asked = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(worker, "configured_key", lambda: "sk-test")
    monkeypatch.setattr(worker.diagnosis, "diagnose", lambda db, incident, provider, trigger: asked.append(trigger) or AgentReport(status="failed", error="x"))
    monkeypatch.setattr(worker, "get_provider", lambda: object())

    worker.handle_incident({"incident_id": 42, "type": "incident.worsened"})
    worker.handle_incident({"incident_id": 42, "type": "incident.requested"})
    assert asked == ["incident.worsened", "incident.requested"]


# --- watching (#125) --------------------------------------------------------------------------------------------

RUNNING_15 = {"type": "container_running_for", "target": "gitlab", "threshold": None, "minutes": 15}


def test_a_container_that_ran_long_enough_without_restarting_resolves_the_incident(db):
    add_report(db, 42, RUNNING_15)
    container_samples(db, 20, restarts=lambda minute: 5 if minute > 18 else 6)

    result = verify.evaluate(db, db.get(IncidentAlert, 42), RUNNING_15, NOW)
    assert result["holds"] and result["held_minutes"] == 18 and result["text"] == "gitlab runs 15 min without a restart"
    assert verify.watch(db, NOW)["resolved"] == [42]
    incident = db.get(IncidentAlert, 42)
    assert incident.status == "resolved" and incident.resolution == "Resolved by the agent: gitlab runs 15 min without a restart" and incident.reopen_after is not None


def test_a_recent_restart_keeps_it_open_and_shows_progress(db):
    add_report(db, 42, RUNNING_15)
    container_samples(db, 20, restarts=lambda minute: 5 if minute > 6 else 6)

    result = verify.evaluate(db, db.get(IncidentAlert, 42), RUNNING_15, NOW)
    assert not result["holds"] and result["held_minutes"] == 6
    assert verify.watch(db, NOW)["resolved"] == []


def test_old_data_is_not_proof(db):
    add_report(db, 42, RUNNING_15)
    for minute in range(60, 30, -1):
        db.add(ContainerMetric(server_id=1, container_id="g" * 64, name="gitlab", status="running", restart_count=6, time=NOW - timedelta(minutes=minute)))
    db.commit()

    result = verify.evaluate(db, db.get(IncidentAlert, 42), RUNNING_15, NOW)
    assert result["known"] is False and result["holds"] is False


def test_a_metric_under_its_threshold_long_enough_holds(db):
    db.add(IncidentAlert(id=60, server_id=1, severity="WARNING", title="memory", description="d", status="active", rule_key="host:memory"))
    for minute in range(30, -1, -1):
        db.add(HostMetric(server_id=1, time=NOW - timedelta(minutes=minute), ram_pct=95 if minute > 12 else 70))
    db.commit()
    condition = {"type": "metric_below_for", "target": "metric:main", "threshold": 80, "minutes": 10, "label": "memory in use"}

    result = verify.evaluate(db, db.get(IncidentAlert, 60), condition, NOW)
    assert result["holds"] and result["text"] == "memory in use stays under 80 for 10 min"


def test_the_rule_does_not_reopen_what_the_agent_closed_until_the_hold_ends_or_it_breaks(db):
    add_report(db, 42, RUNNING_15)
    container_samples(db, 20)
    verify.watch(db, NOW)

    assert alerts.raise_alert(db, 1, "container:gitlab:restart_loop", "CRITICAL", "gitlab restart loop", "d") is False   # old restarts still in the window
    db.add(ContainerMetric(server_id=1, container_id="g" * 64, name="gitlab", status="restarting", restart_count=7, time=NOW + timedelta(seconds=30)))
    db.commit()
    assert verify.watch(db, NOW + timedelta(minutes=1))["released"] == [42]
    assert alerts.raise_alert(db, 1, "container:gitlab:restart_loop", "CRITICAL", "gitlab restart loop", "d") is True    # a real new restart opens one


def test_manual_conditions_are_not_watched(db):
    add_report(db, 42)
    container_samples(db, 20)

    assert verify.evaluate(db, db.get(IncidentAlert, 42), {"type": "manual"}, NOW) is None
    assert verify.watch(db, NOW)["resolved"] == []
