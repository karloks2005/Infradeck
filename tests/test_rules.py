import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend import main, rules
from src.backend.alerts import CRITICAL, WARNING
from src.backend.models import AuditLog, Base, IncidentAlert, MountMetric, Server, User

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
    alert = _active(db)["host:disk:/"]
    assert alert.severity == WARNING
    assert alert.title == "Disk usage on nas:/ is 90%"


def test_single_spike_does_not_alert(db):
    for value in (40, 99, 40, 99, 40):
        _ingest(db, ram_pct=value)

    assert _active(db) == {}


def test_open_alert_stays_open_until_value_drops_and_escalates_only_when_sustained(db):
    for _ in range(3):
        _ingest(db, disk_usage_pct=90)
    _ingest(db, disk_usage_pct=97)
    assert _active(db)["host:disk:/"].severity == WARNING

    _ingest(db, disk_usage_pct=97)
    _ingest(db, disk_usage_pct=97)
    assert _active(db)["host:disk:/"].severity == CRITICAL

    _ingest(db, disk_usage_pct=88)
    assert _active(db)["host:disk:/"].severity == WARNING

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


def _mount(mount, used_pct, inodes_pct=10.0, total_gb=2000.0):
    return {"mount": mount, "fs_type": "ext4", "total_gb": total_gb, "used_gb": total_gb * used_pct / 100, "used_pct": used_pct, "inodes_pct": inodes_pct}


def test_each_mount_gets_its_own_disk_alert(db):
    for _ in range(3):
        _ingest(db, disk_usage_pct=40, mounts=[_mount("/", 40), _mount("/mnt/data", 96), _mount("/mnt/backup", 88, inodes_pct=99)])

    active = _active(db)
    assert set(active) == {"host:disk:/mnt/data", "host:disk:/mnt/backup", "host:inodes:/mnt/backup"}
    assert active["host:disk:/mnt/data"].severity == CRITICAL
    assert active["host:disk:/mnt/data"].title == "Disk usage on nas:/mnt/data is 96%"
    assert "(1.9 TB of 2.0 TB used)" in active["host:disk:/mnt/data"].description
    assert active["host:disk:/mnt/backup"].severity == WARNING


def test_unmounted_disk_alert_resolves(db):
    for _ in range(3):
        _ingest(db, mounts=[_mount("/", 40), _mount("/mnt/data", 96)])
    _ingest(db, mounts=[_mount("/", 40)])

    assert _active(db) == {}


def test_mount_needs_sustained_breach_of_its_own(db):
    _ingest(db, mounts=[_mount("/", 40)])
    _ingest(db, mounts=[_mount("/", 40)])
    _ingest(db, mounts=[_mount("/", 40), _mount("/mnt/new", 99)])

    assert _active(db) == {}


def test_mount_rows_are_stored_and_bad_entries_ignored(db):
    _ingest(db, mounts=[_mount("/", 40), {"mount": ""}, "junk", {"used_pct": 5}])

    assert [row.mount for row in db.query(MountMetric)] == ["/"]


def test_mount_path_with_braces_does_not_break_titles(db):
    for _ in range(3):
        _ingest(db, mounts=[_mount("/mnt/{odd}", 99)])

    assert _active(db)["host:disk:/mnt/{odd}"].title == "Disk usage on nas:/mnt/{odd} is 99%"


def test_overview_shows_latest_mounts_and_disk_thresholds(db, monkeypatch):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    _ingest(db, mounts=[_mount("/", 10)])
    _ingest(db, mounts=[_mount("/", 20), _mount("/mnt/data", 30)])

    overview = main.monitoring_overview(session_id="s", db=db)

    assert [(m["mount"], m["used_pct"]) for m in overview["servers"][0]["mounts"]] == [("/", 20), ("/mnt/data", 30)]
    assert overview["disk_thresholds"]["disk_critical_pct"] == rules.DEFAULT_THRESHOLDS["disk_critical_pct"]
