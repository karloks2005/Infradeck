import json
import os
import struct
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agent import collector, system_helper as helper
from src.backend import main
from src.backend.models import Base, IncidentAlert, Server, SystemEvent, User

TOKEN = "collector-token"
BOOT = "boot-a"
NOW = 1_000_000.0


def _klog(*lines):
    return "\n".join(lines) + "\n"


def _parse(text, boot_id=BOOT):
    return helper.parse_kernel_log(text, boot_id, monotonic_now=500.0, wall_now=NOW)


# --- kernel log parsing -------------------------------------------------------------------

def test_oom_kill_is_parsed_with_process_name_and_wall_time():
    [event] = _parse(_klog("<3>[  400.000000] Out of memory: Killed process 4321 (java) total-vm:100kB"))

    assert (event["kind"], event["key"]) == ("oom_kill", "boot-a:oom:400.000000")
    assert event["detail"] == "The kernel OOM killer stopped java (pid 4321)"
    # 100 s before the snapshot: monotonic 500 vs 400.
    assert datetime.fromisoformat(event["time"]) == datetime.fromtimestamp(NOW - 100, timezone.utc)


def test_cgroup_oom_is_recognised():
    [event] = _parse(_klog("<3>[   10.500000] Memory cgroup out of memory: Killed process 77 (postgres) total-vm:1kB"))
    assert event["kind"] == "oom_kill" and "postgres" in event["detail"]


def test_kernel_errors_are_kept_but_info_lines_are_not():
    events = _parse(_klog("<6>[    1.000000] eth0: link up", "<4>[    2.000000] a warning", "<3>[    3.000000] nvme0: I/O error, dev nvme0n1", "<0>[    4.000000] kernel panic"))
    assert [event["detail"] for event in events] == ["nvme0: I/O error, dev nvme0n1", "kernel panic"]
    assert {event["kind"] for event in events} == {"kernel_error"}


def test_hostile_log_content_is_neutralised():
    name = "x\x1b[31m;$(reboot)\x00" + "A" * 500
    [oom] = _parse(_klog(f"<3>[    5.000000] Out of memory: Killed process 1 ({name})"))
    [err] = _parse(_klog("<3>[    6.000000] bad \x1b[2J\x07 line " + "B" * 1000))

    assert all(char.isprintable() for char in oom["detail"] + err["detail"])
    assert len(oom["detail"]) < 150 and len(err["detail"]) <= helper.MAX_TEXT


def test_malformed_and_untimestamped_lines_are_ignored():
    assert _parse(_klog("garbage", "<3>no timestamp here", "<x>[1.000000] y", " continuation line", "<3>[abc] z", "")) == []


def test_output_is_capped_to_the_newest_events():
    lines = [f"<3>[{n:>8}.000000] error number {n}" for n in range(1, 40)]
    events = _parse(_klog(*lines))
    assert len(events) == helper.MAX_ERROR_EVENTS and events[-1]["detail"] == "error number 39"


# --- wtmp ---------------------------------------------------------------------------------

def _record(kind, user, when):
    return helper._UTMP.pack(kind, 0, b"~", b"~~", user.encode(), b"", 0, 0, 0, int(when), 0, 0, 0, 0, 0)


def _wtmp(tmp_path, *records):
    path = tmp_path / "wtmp"
    path.write_bytes(b"".join(_record(*record) for record in records))
    return path


def test_utmp_layout_is_384_bytes():
    assert helper._UTMP.size == 384


def test_clean_shutdown_is_detected(tmp_path):
    path = _wtmp(tmp_path, (2, "reboot", 100), (1, "shutdown", 900), (2, "reboot", 1000))
    assert helper.classify_last_shutdown(helper.read_wtmp_records(path), boot_time=1002) == "clean"


def test_missing_shutdown_record_is_unclean_when_shutdowns_were_recorded_before(tmp_path):
    path = _wtmp(tmp_path, (2, "reboot", 100), (1, "shutdown", 200), (2, "reboot", 300), (2, "reboot", 1000))
    assert helper.classify_last_shutdown(helper.read_wtmp_records(path), boot_time=1000) == "unclean"


def test_never_recorded_shutdowns_are_unknown_not_unclean(tmp_path):
    path = _wtmp(tmp_path, (2, "reboot", 100), (2, "reboot", 300), (2, "reboot", 1000))
    assert helper.classify_last_shutdown(helper.read_wtmp_records(path), boot_time=1000) == "unknown"


def test_unknown_when_wtmp_is_missing_short_or_out_of_date(tmp_path):
    assert helper.read_wtmp_records(tmp_path / "nope") == []
    assert helper.classify_last_shutdown([], 1000) == "unknown"
    one_boot = helper.read_wtmp_records(_wtmp(tmp_path, (2, "reboot", 1000)))
    assert helper.classify_last_shutdown(one_boot, 1000) == "unknown"
    stale = helper.read_wtmp_records(_wtmp(tmp_path, (1, "shutdown", 10), (2, "reboot", 100), (2, "reboot", 200)))
    assert helper.classify_last_shutdown(stale, boot_time=5000) == "unknown"


def test_truncated_or_symlinked_wtmp_is_safe(tmp_path):
    path = _wtmp(tmp_path, (2, "reboot", 1))
    path.write_bytes(path.read_bytes() + b"junk")
    assert len(helper.read_wtmp_records(path)) == 1
    link = tmp_path / "link"
    link.symlink_to(path)
    assert helper.read_wtmp_records(link) == []


def test_snapshot_is_written_atomically_with_a_fixed_schema(tmp_path):
    out = tmp_path / "system.json"
    helper.write_snapshot({"version": 1, "events": []}, out)
    assert json.loads(out.read_text()) == {"version": 1, "events": []}
    assert not (tmp_path / "system.json.tmp").exists() and out.stat().st_mode & 0o777 == 0o644


# --- collector reading the helper's file --------------------------------------------------

def _helper_file(tmp_path, **overrides):
    data = {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "boot_id": BOOT, "last_shutdown": "clean",
            "events": [{"kind": "oom_kill"}], **overrides}
    path = tmp_path / "system.json"
    path.write_text(json.dumps(data))
    return path


def test_collector_uses_a_fresh_file_from_the_current_boot(tmp_path):
    assert collector.collect_helper(BOOT, _helper_file(tmp_path)) == {"last_shutdown": "clean", "helper_events": [{"kind": "oom_kill"}]}


@pytest.mark.parametrize("overrides", [{"boot_id": "other"}, {"generated_at": "2001-01-01T00:00:00+00:00"}, {"generated_at": "nonsense"}])
def test_collector_ignores_stale_or_foreign_files(tmp_path, overrides):
    assert collector.collect_helper(BOOT, _helper_file(tmp_path, **overrides)) == {}


def test_collector_refuses_missing_symlinked_oversized_and_garbage_files(tmp_path):
    assert collector.collect_helper(BOOT, tmp_path / "missing.json") == {}
    real = _helper_file(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert collector.collect_helper(BOOT, link) == {}
    big = tmp_path / "big.json"
    big.write_text(" " * (collector.HELPER_FILE_MAX_BYTES + 10))
    assert collector.collect_helper(BOOT, big) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("[1,2")
    assert collector.collect_helper(BOOT, bad) == {}
    arr = tmp_path / "arr.json"
    arr.write_text("[]")
    assert collector.collect_helper(BOOT, arr) == {}


# --- backend ingest -----------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add(Server(id=5, owner_id=1, hostname="nas", check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKEN)))
    session.commit()
    return session


def _ingest(db, **host):
    host = {"boot_id": BOOT, "oom_kill_total": 0, "kernel_tainted": 0, "ntp_synced": True, "reboot_required": False, **host}
    main.ingest_metrics(main.MetricsIngestRequest(server_id=5, host=host), authorization=f"Bearer {TOKEN}", session_id=None, db=db)


def _event(kind="oom_kill", key="boot-a:oom:1.000000", minutes_ago=5, detail="The kernel OOM killer stopped java (pid 1)"):
    return {"kind": kind, "key": key, "detail": detail, "time": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()}


def _events(db):
    return [(e.kind, e.event_key) for e in db.query(SystemEvent).order_by(SystemEvent.id)]


def _alerts(db):
    return {a.rule_key: a for a in db.query(IncidentAlert).filter_by(status="active")}


def test_helper_events_are_stored_once_even_when_republished(db):
    _ingest(db, helper_events=[_event()], last_shutdown="unknown")
    _ingest(db, helper_events=[_event()], last_shutdown="unknown")

    assert _events(db) == [("oom_kill", "boot-a:oom:1.000000")]
    assert "java" in _alerts(db)["system:oom"].description


def test_with_the_helper_the_oom_counter_does_not_add_a_second_event(db):
    _ingest(db, oom_kill_total=0, helper_events=[], last_shutdown="unknown")
    _ingest(db, oom_kill_total=1, helper_events=[_event()], last_shutdown="unknown")

    assert _events(db) == [("oom_kill", "boot-a:oom:1.000000")]


@pytest.mark.parametrize("bad", [
    {"kind": "reboot", "key": "k", "detail": "d", "time": "2026-01-01T00:00:00+00:00"},  # kind not allowed from a helper
    _event(key="has spaces"), _event(key="x" * 200), _event(minutes_ago=60 * 24 * 30), _event(minutes_ago=-600),
    {**_event(), "time": "garbage"}, {**_event(), "detail": 5}, "string", None, 12,
])
def test_malformed_helper_events_are_dropped(db, bad):
    _ingest(db, helper_events=[bad], last_shutdown="unknown")
    assert _events(db) == []


def test_helper_event_count_and_text_are_bounded(db):
    many = [_event(key=f"boot-a:err:{n}", kind="kernel_error", detail="e" * 5000) for n in range(500)]
    _ingest(db, helper_events=many, last_shutdown="unknown")

    rows = db.query(SystemEvent).all()
    assert len(rows) == main.MAX_HELPER_EVENTS_PER_CHECKIN == 20 and all(len(row.detail) <= 300 for row in rows)   # the first 50 offered, 20 stored per check-in


def test_unclean_shutdown_makes_the_reboot_critical(db):
    _ingest(db)
    _ingest(db, boot_id="boot-b", helper_events=[], last_shutdown="unclean")

    alert = _alerts(db)["system:reboot"]
    assert alert.severity == "CRITICAL" and "unclean" in alert.title
    assert db.query(SystemEvent).one().detail.startswith("The host restarted after an unclean shutdown")


def test_clean_shutdown_keeps_the_reboot_informational(db):
    _ingest(db)
    _ingest(db, boot_id="boot-b", helper_events=[], last_shutdown="clean")

    assert _alerts(db)["system:reboot"].severity == "INFO"
    assert "clean shutdown" in db.query(SystemEvent).one().detail


def test_helper_arriving_after_the_reboot_was_noticed_updates_the_event_and_alert(db):
    _ingest(db)
    _ingest(db, boot_id="boot-b")  # collector saw the new boot before the helper had written anything
    assert _alerts(db)["system:reboot"].severity == "INFO"

    _ingest(db, boot_id="boot-b", helper_events=[], last_shutdown="unclean")

    assert _alerts(db)["system:reboot"].severity == "CRITICAL"


def test_invalid_last_shutdown_values_are_dropped(db):
    from src.backend.models import HostMetric
    _ingest(db, last_shutdown="<script>")
    assert db.query(HostMetric).one().last_shutdown is None
