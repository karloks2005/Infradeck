import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agent import collector
from src.backend import main
from src.backend.models import Base, IncidentAlert, Server, SystemEvent, User

TOKEN = "collector-token"
BOOT_A, BOOT_B = "boot-a", "boot-b"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add(Server(id=5, owner_id=1, hostname="nas", check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKEN)))
    session.commit()
    return session


def _ingest(db, **system):
    host = {"boot_id": BOOT_A, "oom_kill_total": 0, "kernel_tainted": 0, "ntp_synced": True, "reboot_required": False, **system}
    main.ingest_metrics(main.MetricsIngestRequest(server_id=5, host=host), authorization=f"Bearer {TOKEN}", session_id=None, db=db)


def _active(db):
    return {alert.rule_key: alert for alert in db.query(IncidentAlert).filter_by(status="active")}


def _kinds(db):
    return [event.kind for event in db.query(SystemEvent).order_by(SystemEvent.id)]


def test_first_check_in_records_no_events(db):
    _ingest(db)
    assert _kinds(db) == [] and _active(db) == {}


def test_new_boot_id_records_a_reboot_and_raises_info_alert(db):
    _ingest(db)
    _ingest(db, boot_id=BOOT_B)

    assert _kinds(db) == ["reboot"]
    assert _active(db)["system:reboot"].severity == "INFO"


def test_reboot_alert_clears_after_the_event_window(db):
    _ingest(db)
    _ingest(db, boot_id=BOOT_B)
    for event in db.query(SystemEvent):
        event.time = event.time.replace(year=2000)
    db.commit()
    _ingest(db, boot_id=BOOT_B)

    assert "system:reboot" not in _active(db)


def test_oom_counter_rise_raises_alert_but_a_reboot_resetting_it_does_not(db):
    _ingest(db, oom_kill_total=4)
    _ingest(db, oom_kill_total=6)
    assert _kinds(db) == ["oom_kill"]
    assert _active(db)["system:oom"].severity == "WARNING"

    _ingest(db, boot_id=BOOT_B, oom_kill_total=0)
    assert _kinds(db) == ["oom_kill", "reboot"]


def test_repeated_oom_kills_are_critical(db):
    for total in range(4):
        _ingest(db, oom_kill_total=total)
    assert _active(db)["system:oom"].severity == "CRITICAL"


def test_kernel_fault_taint_alerts_until_reboot_but_module_taint_is_ignored(db):
    _ingest(db, kernel_tainted=1 | 4096)  # proprietary + out-of-tree module only
    assert _active(db) == {} and _kinds(db) == []

    _ingest(db, kernel_tainted=1 | 1 << 4 | 1 << 7)
    assert _kinds(db) == ["kernel_fault"]
    assert "machine check" in db.query(SystemEvent).one().detail
    _ingest(db, kernel_tainted=1 | 1 << 4 | 1 << 7)
    assert "system:kernel" in _active(db) and _kinds(db) == ["kernel_fault"]

    _ingest(db, boot_id=BOOT_B, kernel_tainted=0)
    assert "system:kernel" not in _active(db)


def test_ntp_alert_needs_sustained_unsynced_checks_and_resolves(db):
    _ingest(db, ntp_synced=False)
    _ingest(db, ntp_synced=False)
    assert "system:ntp" not in _active(db)
    _ingest(db, ntp_synced=False)
    assert "system:ntp" in _active(db)
    _ingest(db, ntp_synced=True)
    assert "system:ntp" not in _active(db)


def test_unknown_values_never_alert(db):
    for _ in range(4):
        _ingest(db, boot_id=None, oom_kill_total=None, kernel_tainted=None, ntp_synced=None, reboot_required=None)
    assert _active(db) == {}


def test_malformed_values_from_a_collector_are_dropped(db):
    _ingest(db, oom_kill_total="many", kernel_tainted=True, ntp_synced="yes", reboot_required=1, boot_id="x" * 100)
    from src.backend.models import HostMetric
    row = db.query(HostMetric).one()
    assert (row.oom_kill_total, row.kernel_tainted, row.ntp_synced, row.reboot_required) == (None, None, None, None)
    assert row.boot_id == "x" * 64


def test_reboot_required_raises_info_alert(db):
    _ingest(db, reboot_required=True)
    assert _active(db)["system:reboot_required"].severity == "INFO"
    _ingest(db, reboot_required=False)
    assert "system:reboot_required" not in _active(db)


def test_collector_reads_kernel_state_from_proc(tmp_path, monkeypatch):
    (tmp_path / "sys/kernel/random").mkdir(parents=True)
    (tmp_path / "sys/kernel/random/boot_id").write_text("abc-123\n")
    (tmp_path / "sys/kernel/tainted").write_text("128\n")
    (tmp_path / "vmstat").write_text("nr_free_pages 1\noom_kill 7\n")
    monkeypatch.setattr(collector, "HOST_PROC", tmp_path)
    monkeypatch.setattr(collector, "HOST_ROOT", tmp_path)
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/debian_version").write_text("12\n")

    state = collector.collect_system()
    assert (state["boot_id"], state["kernel_tainted"], state["oom_kill_total"], state["reboot_required"]) == ("abc-123", 128, 7, False)

    (tmp_path / "run").mkdir()
    (tmp_path / "run/reboot-required").touch()
    assert collector.collect_system()["reboot_required"] is True


def test_collector_reports_unknown_when_files_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "HOST_PROC", tmp_path)
    monkeypatch.setattr(collector, "HOST_ROOT", tmp_path)

    state = collector.collect_system()
    assert (state["boot_id"], state["kernel_tainted"], state["oom_kill_total"], state["reboot_required"]) == (None, None, None, None)
