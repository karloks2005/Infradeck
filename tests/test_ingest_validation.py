"""What a collector reports is never trusted: numbers, names and filesystems are checked before they are stored,
and a collector can't flood the database or take another server's place."""
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

from src.backend import history, main, rollups
from src.backend.models import Base, HostMetric, IncidentAlert, MountMetric, Server, User
from tests.fakes import FakeRedis

TOKENS = {1: "token-one", 2: "token-two"}


@pytest.fixture
def app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as seed:
        seed.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin", is_active=True))
        for server_id, name in ((1, "alpha"), (2, "bravo")):
            seed.add(Server(id=server_id, owner_id=1, hostname=name, check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKENS[server_id]), machine_id=f"machine-{server_id}"))
        seed.commit()

    def get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[main.get_db] = get_db
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1, "role": "admin"})
    client = TestClient(main.app, raise_server_exceptions=False)
    client.Session = Session
    yield client
    main.app.dependency_overrides.clear()


def ingest(client, server_id, body, token=None, headers=None):
    payload = body if isinstance(body, str) else json.dumps({"server_id": server_id, **body})
    return client.post("/monitoring/metrics/ingest", content=payload, headers={"Authorization": f"Bearer {token or TOKENS[server_id]}", "Content-Type": "application/json", **(headers or {})})


def latest(client, server_id=1):
    with client.Session() as db:
        return db.query(HostMetric).filter_by(server_id=server_id).order_by(HostMetric.id.desc()).first()


def server(client, server_id=1):
    with client.Session() as db:
        return db.get(Server, server_id)


# --- numbers --------------------------------------------------------------------------------------

def test_a_check_in_with_infinity_does_not_break_the_pages_for_everyone(app):
    ingest(app, 1, '{"server_id": 1, "host": {"cpu_utilization_pct": 12.5}}')

    response = ingest(app, 1, '{"server_id": 1, "host": {"ram_pct": Infinity, "load_1m": -Infinity, "swap_pct": NaN}}')

    assert response.status_code == 200
    assert latest(app).ram_pct is None and latest(app).load_1m is None and latest(app).swap_pct is None
    for path in ("/monitoring/overview", "/monitoring/servers/1/history?range=1h&metrics=ram", "/monitoring/servers/1/history?minutes=30", "/stats/servers/1/heatmap", "/stats/fleet?range=24h"):
        assert app.get(path).status_code == 200, path


@pytest.mark.parametrize("key", sorted(main.HOST_NUMBER_LIMITS))
@pytest.mark.parametrize("bad", ["1e308", "-1", "2e12", "\"12\"", "true", "null", "[1]", "{\"a\": 1}", "1" + "0" * 400])
def test_every_host_number_outside_its_range_or_of_the_wrong_type_is_stored_as_unknown(app, key, bad):
    response = ingest(app, 1, '{"server_id": 1, "host": {"%s": %s}}' % (key, bad))

    assert response.status_code == 200
    assert getattr(latest(app), key) is None


def test_sensible_numbers_are_stored_exactly(app):
    ingest(app, 1, {"host": {"cpu_utilization_pct": 41.5, "load_1m": 0.52, "ram_pct": 63.25, "ram_used_gb": 12.5, "disk_usage_pct": 100, "net_bytes_sent_mb": 123456.789, "system_uptime_seconds": 86400.5}})

    row = latest(app)
    assert (row.cpu_utilization_pct, row.load_1m, row.ram_pct, row.ram_used_gb, row.disk_usage_pct, row.net_bytes_sent_mb, row.system_uptime_seconds) == (41.5, 0.52, 63.25, 12.5, 100.0, 123456.789, 86400.5)


@pytest.mark.parametrize("key", ["oom_kill_total", "kernel_tainted"])
@pytest.mark.parametrize("bad", [2**31, 2**40, -1, 10**30])
def test_counters_too_big_for_the_database_are_unknown_instead_of_an_error(app, key, bad):
    response = ingest(app, 1, {"host": {key: bad}})

    assert response.status_code == 200 and getattr(latest(app), key) is None


def test_the_summaries_survive_whatever_was_reported(app):
    for body in ('{"server_id": 1, "host": {"cpu_utilization_pct": 1e200}}', '{"server_id": 1, "host": {"cpu_utilization_pct": 1e200}}', '{"server_id": 1, "host": {"cpu_utilization_pct": 50}}'):
        ingest(app, 1, body)
    with app.Session() as db:
        db.query(HostMetric).update({HostMetric.time: datetime.now(timezone.utc) - timedelta(days=2)})
        db.commit()
        rollups.run_rollups(db)
        assert [row.avg_cpu for row in db.query(rollups.HostRollup).filter_by(tier="hour")] == [50.0]   # the absurd values were never stored


# --- names and descriptions of the server -----------------------------------------------------------

def test_a_server_description_is_kept_only_if_it_is_sensible(app):
    ingest(app, 1, {"server": {"hostname": "alpha-new\x00\x07", "ip_address": "10.0.0.5", "os_info": "Debian 13\n(trixie)", "cpu_cores": 8, "total_ram_gb": 31.4}, "host": {}})

    row = server(app)
    assert (row.hostname, row.ip_address, row.os_info, row.cpu_cores, row.total_ram_gb) == ("alpha-new??", "10.0.0.5", "Debian 13?(trixie)", 8, 31.4)


@pytest.mark.parametrize("reported", [
    {"cpu_cores": -5}, {"cpu_cores": 0}, {"cpu_cores": 10**9}, {"cpu_cores": True}, {"cpu_cores": "8"}, {"cpu_cores": 4.5},
    {"total_ram_gb": -1}, {"total_ram_gb": 1e30}, {"total_ram_gb": "lots"}, {"total_ram_gb": True},
    {"ip_address": "not an address"}, {"ip_address": 12345}, {"ip_address": "999.1.1.1"}, {"hostname": ""}, {"hostname": "   "}, {"hostname": 12}, {"hostname": None}, {"os_info": 5},
])
def test_a_bad_server_description_leaves_what_was_known(app, reported):
    before = server(app)
    keep = (before.hostname, before.ip_address, before.os_info, before.cpu_cores, before.total_ram_gb)

    assert ingest(app, 1, {"server": reported, "host": {}}).status_code == 200

    after = server(app)
    assert (after.hostname, after.ip_address, after.os_info, after.cpu_cores, after.total_ram_gb) == keep


def test_long_names_are_cut_to_what_fits(app):
    ingest(app, 1, {"server": {"hostname": "h" * 5000, "os_info": "o" * 5000}, "host": {}})

    row = server(app)
    assert len(row.hostname) == 255 and len(row.os_info) == 255


def test_a_collector_cannot_take_another_servers_name(app):
    ingest(app, 1, {"server": {"hostname": "BRAVO"}, "host": {}})     # bravo is server 2's name, whatever the case

    assert server(app, 1).hostname == "alpha"


def test_only_a_collector_on_the_internal_listener_can_claim_to_be_the_dashboards_machine(app, monkeypatch):
    monkeypatch.setattr(main, "COLLECTOR_ACCESS", "strict")
    ingest(app, 2, {"server": {"dashboard_host": True}, "host": {}}, headers={"X-Collector-Via": "internal"})
    assert server(app, 2).is_dashboard_host is True

    ingest(app, 1, {"server": {"dashboard_host": True}, "host": {}}, headers={"X-Collector-Via": "vpn"})     # a remote collector

    assert server(app, 1).is_dashboard_host is False and server(app, 2).is_dashboard_host is True


def test_in_the_development_stack_the_claim_is_accepted_as_before(app):
    ingest(app, 1, {"server": {"dashboard_host": True}, "host": {}})

    assert server(app, 1).is_dashboard_host is True


# --- filesystems -------------------------------------------------------------------------------------

def mounts(client, server_id=1):
    with client.Session() as db:
        return db.query(MountMetric).filter_by(server_id=server_id).order_by(MountMetric.id).all()


def test_at_most_sixty_four_filesystems_are_stored_per_check_in(app):
    many = [{"mount": f"/m{i}", "used_pct": 99.0, "total_gb": 10, "used_gb": 9.9} for i in range(3000)]

    assert ingest(app, 1, {"host": {"mounts": many}}).status_code == 200

    assert len(mounts(app)) == main.MAX_MOUNTS_PER_CHECKIN == 64


def test_a_filesystem_is_stored_once_and_only_with_known_good_fields(app):
    ingest(app, 1, {"host": {"mounts": [
        {"mount": "/data", "fs_type": "ext4", "total_gb": 100, "used_gb": 40, "used_pct": 40, "inodes_pct": 5, "id": 999, "server_id": 2, "time": "2001-01-01T00:00:00", "extra": "x"},
        {"mount": "/data", "used_pct": 1},
    ]}})

    [row] = mounts(app)
    assert (row.mount, row.fs_type, row.total_gb, row.used_pct, row.server_id) == ("/data", "ext4", 100.0, 40.0, 1) and row.id != 999 and row.time.year != 2001
    assert mounts(app, 2) == []


@pytest.mark.parametrize("mount", [{"mount": "relative/path"}, {"mount": ""}, {"mount": 5}, {"mount": None}, {"nothing": 1}, "text", 7, None, ["/x"]])
def test_filesystem_records_that_are_not_usable_are_skipped(app, mount):
    assert ingest(app, 1, {"host": {"mounts": [mount, {"mount": "/ok", "used_pct": 1}]}}).status_code == 200

    assert [row.mount for row in mounts(app)] == ["/ok"]


@pytest.mark.parametrize("bad", ["1e308", "-1", "101", "\"x\"", "true", "null", "Infinity", "NaN"])
def test_filesystem_numbers_outside_their_range_are_unknown(app, bad):
    ingest(app, 1, '{"server_id": 1, "host": {"mounts": [{"mount": "/data", "used_pct": %s, "inodes_pct": %s, "total_gb": %s, "used_gb": %s}]}}' % (bad, bad, bad, bad))

    [row] = mounts(app)
    assert (row.used_pct, row.inodes_pct) == (None, None)
    assert (row.total_gb, row.used_gb) == ((101.0, 101.0) if bad == "101" else (None, None))   # 101 is a fine size in GB, only a bad percentage


def test_mount_names_are_made_printable_and_bounded(app):
    ingest(app, 1, {"host": {"mounts": [{"mount": "/" + "d" * 600 + "\x00", "fs_type": "t" * 300}]}})

    [row] = mounts(app)
    assert len(row.mount) == main.MAX_MOUNT_NAME == 200 and len(row.fs_type) == 64 and "\x00" not in row.mount


# --- how often, and how many --------------------------------------------------------------------------

@pytest.fixture
def spaced(monkeypatch):
    monkeypatch.setattr(main, "INGEST_MIN_SPACING_RATIO", 0.25)


def test_a_check_in_that_follows_the_last_one_too_closely_is_refused(app, spaced):
    assert ingest(app, 1, {"host": {}}).status_code == 200

    again = ingest(app, 1, {"host": {}})

    assert again.status_code == 429 and again.headers["Retry-After"] == "15"      # a quarter of the 60 s interval
    assert ingest(app, 2, {"host": {}}).status_code == 200                         # another server has its own slot


def test_the_next_check_in_is_accepted_when_the_slot_has_passed(app, spaced):
    ingest(app, 1, {"host": {}})
    main.redis_client.delete("homelab:ingest:1")                                    # the key expires after the spacing

    assert ingest(app, 1, {"host": {}}).status_code == 200


def test_the_spacing_is_never_shorter_than_the_minimum(app, spaced):
    with app.Session() as db:
        db.get(Server, 1).check_interval_seconds = 15
        db.commit()
    ingest(app, 1, {"host": {}})

    assert ingest(app, 1, {"host": {}}).headers["Retry-After"] == "5"


def test_someone_without_the_token_cannot_use_up_a_servers_slot(app, spaced, monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda session_id, db: (_ for _ in ()).throw(main.HTTPException(status_code=401, detail="no")))

    assert ingest(app, 1, {"host": {}}, token="wrong").status_code == 401

    assert ingest(app, 1, {"host": {}}).status_code == 200                          # the real collector is not held up


def test_a_redis_outage_does_not_stop_check_ins(app, spaced, monkeypatch):
    class Down:
        def set(self, *args, **kwargs):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(main, "redis_client", Down())

    assert ingest(app, 1, {"host": {}}).status_code == 200 and ingest(app, 1, {"host": {}}).status_code == 200


def test_one_server_with_a_flood_of_alerts_cannot_hide_everyone_elses(app):
    with app.Session() as db:
        db.add_all(IncidentAlert(server_id=1, severity="CRITICAL", title=f"flood {i}", description="d", rule_key=f"host:disk:/m{i}", status="active") for i in range(100))
        db.add(IncidentAlert(server_id=2, severity="WARNING", title="the real problem", description="d", rule_key="host:memory", status="active"))
        db.commit()

    overview = app.get("/monitoring/overview").json()

    titles = [alert["title"] for alert in overview["alerts"]]
    assert "the real problem" in titles and sum(1 for alert in overview["alerts"] if alert["server_id"] == 1) == main.OVERVIEW_ALERTS_PER_SERVER
    assert overview["alert_count"] == 101                                          # the true total is still shown


def test_one_query_never_reads_more_raw_rows_than_the_backstop(app, monkeypatch):
    with app.Session() as db:
        now = datetime.now(timezone.utc)
        db.add_all(HostMetric(server_id=1, time=now - timedelta(minutes=i), cpu_utilization_pct=1.0) for i in range(500))
        db.commit()
        monkeypatch.setattr(history, "MAX_RAW_ROWS", 100)

        result = history.history(db, history.HOST, 1, None, "24h", ("cpu",), 60)

    assert sum(point["samples"] for point in result["points"]) == 100
