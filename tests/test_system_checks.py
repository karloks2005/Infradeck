import os
from datetime import datetime, timedelta, timezone

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


# --- the System row's event list ---------------------------------------------------------------

def _event(db, kind, detail, minutes_ago):
    db.add(SystemEvent(server_id=5, time=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago), kind=kind, detail=detail))
    db.commit()


def _shown(db, monkeypatch):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    return main.monitoring_overview(session_id="s", db=db)["servers"][0]["system_events"]


def test_repeated_kernel_errors_collapse_into_one_row_with_a_count(db, monkeypatch):
    for minutes_ago in (3000, 2000, 100):
        _event(db, "kernel_error", "ACPI BIOS Error (bug): Could not resolve symbol", minutes_ago)
    _event(db, "kernel_error", "i915: Unclaimed access detected", 50)

    rows = {row["detail"]: row for row in _shown(db, monkeypatch)}

    assert len(rows) == 2
    acpi = rows["ACPI BIOS Error (bug): Could not resolve symbol"]
    assert acpi["count"] == 3
    # The row's time is the latest occurrence; the first one is kept for the tooltip.
    assert datetime.fromisoformat(acpi["time"]) > datetime.fromisoformat(acpi["first_time"])
    assert rows["i915: Unclaimed access detected"]["count"] == 1


def test_reboots_and_oom_kills_are_never_collapsed(db, monkeypatch):
    for minutes_ago in (500, 100):
        _event(db, "reboot", "The host rebooted after a clean shutdown", minutes_ago)
        _event(db, "oom_kill", "The kernel OOM killer stopped java (pid 1)", minutes_ago + 1)

    rows = _shown(db, monkeypatch)

    assert sorted((row["kind"], row["count"]) for row in rows) == [("oom_kill", 1), ("oom_kill", 1), ("reboot", 1), ("reboot", 1)]


def test_noisy_kernel_errors_cannot_crowd_out_real_events(db, monkeypatch):
    _event(db, "oom_kill", "The kernel OOM killer stopped java (pid 1)", 60 * 24 * 6)
    for number in range(40):
        _event(db, "kernel_error", f"driver message {number}", number + 1)

    rows = _shown(db, monkeypatch)

    assert [row["kind"] for row in rows].count("oom_kill") == 1
    assert [row["kind"] for row in rows].count("kernel_error") == main.SYSTEM_EVENTS_SHOWN


def test_events_are_listed_newest_first_across_kinds(db, monkeypatch):
    _event(db, "reboot", "The host rebooted", 300)
    _event(db, "kernel_error", "late message", 10)
    _event(db, "oom_kill", "The kernel OOM killer stopped x (pid 2)", 100)

    assert [row["kind"] for row in _shown(db, monkeypatch)] == ["kernel_error", "oom_kill", "reboot"]


def test_events_older_than_seven_days_are_not_shown(db, monkeypatch):
    _event(db, "reboot", "The host rebooted", 60 * 24 * 8)
    _event(db, "kernel_error", "old message", 60 * 24 * 8)

    assert _shown(db, monkeypatch) == []
