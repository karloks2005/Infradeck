import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend import main, rules
from src.backend.alerts import CRITICAL, WARNING
from src.backend.models import AuditLog, Base, IncidentAlert, Server, User

TOKEN = "collector-token"


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add(Server(id=5, owner_id=1, hostname="nas", cpu_cores=4, check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKEN)))
    session.commit()
    monkeypatch.setattr(main, "_require_admin", lambda session_id, db: {"id": 1})
    monkeypatch.setattr(main, "record_audit_log", lambda db, **kw: db.add(AuditLog(user_id=kw["user_id"], level=kw["level"], message=kw["message"], source=kw["source"])))
    return session


def _ingest(db, **host):
    main.ingest_metrics(main.MetricsIngestRequest(server_id=5, host=host), authorization=f"Bearer {TOKEN}", session_id=None, db=db)


def _active(db):
    return {a.rule_key: a for a in db.query(IncidentAlert).filter_by(status="active")}


def test_host_alert_needs_sustained_breach(db):
    _ingest(db, disk_usage_pct=90)
    _ingest(db, disk_usage_pct=90)
    assert _active(db) == {}

    _ingest(db, disk_usage_pct=90)
    alert = _active(db)["host:disk"]
    assert alert.severity == WARNING
    assert alert.title == "Disk usage on nas is 90%"


def test_single_spike_does_not_alert(db):
    for value in (40, 99, 40, 99, 40):
        _ingest(db, ram_pct=value)

    assert _active(db) == {}


def test_open_alert_stays_open_until_value_drops_and_escalates_only_when_sustained(db):
    for _ in range(3):
        _ingest(db, disk_usage_pct=90)
    _ingest(db, disk_usage_pct=97)
    assert _active(db)["host:disk"].severity == WARNING

    _ingest(db, disk_usage_pct=97)
    _ingest(db, disk_usage_pct=97)
    assert _active(db)["host:disk"].severity == CRITICAL

    _ingest(db, disk_usage_pct=88)
    assert _active(db)["host:disk"].severity == WARNING

    _ingest(db, disk_usage_pct=50)
    assert _active(db) == {}


def test_load_is_compared_per_core(db):
    for _ in range(3):
        _ingest(db, load_5m=5.0)  # 1.25 per core on 4 cores
    assert _active(db) == {}

    for _ in range(3):
        _ingest(db, load_5m=9.0)  # 2.25 per core
    assert _active(db)["host:load"].severity == CRITICAL


def test_swap_only_warns(db):
    for _ in range(3):
        _ingest(db, swap_pct=100)

    assert _active(db)["host:swap"].severity == WARNING


def test_missing_metric_does_not_alert(db):
    for _ in range(3):
        _ingest(db)

    assert _active(db) == {}


def test_saved_thresholds_apply(db):
    main.update_admin_settings(main.AdminSettingsRequest(alert_thresholds={"sustained_checks": 1, "memory_warning_pct": 50}), session_id="s", db=db)

    _ingest(db, ram_pct=60)

    assert _active(db)["host:memory"].severity == WARNING


def test_threshold_update_is_validated_and_audited(db):
    for bad in ({"disk_warning_pct": 99}, {"disk_warning_pct": 0}, {"sustained_checks": 1.5}, {"nope": 1}, {"memory_warning_pct": "90"}):
        with pytest.raises(HTTPException) as exc:
            main.update_admin_settings(main.AdminSettingsRequest(alert_thresholds=bad), session_id="s", db=db)
        assert exc.value.status_code == 400

    result = main.update_admin_settings(main.AdminSettingsRequest(alert_thresholds={"disk_critical_pct": 98}), session_id="s", db=db)

    assert result["alert_thresholds"] == {**rules.DEFAULT_THRESHOLDS, "disk_critical_pct": 98}
    assert db.query(AuditLog).one().message == "Changed alert thresholds: disk_critical_pct=98"


def test_corrupt_saved_thresholds_fall_back_to_defaults(db):
    main._set_setting(db, main.ALERT_THRESHOLDS_KEY, "not json")
    db.commit()

    assert main.get_alert_thresholds(db) == rules.DEFAULT_THRESHOLDS
