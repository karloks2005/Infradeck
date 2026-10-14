"""What the Overview page reads: machine facts from collectors (never trusted), sparklines, the all-clear summary
and a server's event timeline."""
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

from src.collector import collector, system_helper
from src.backend import dashboard, main
from src.backend.models import Base, ContainerMetric, HostMetric, IncidentAlert, PendingCollector, Server, SystemEvent, User
from tests.fakes import FakeRedis

TOKEN = "token-one"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as seed:
        seed.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin", is_active=True))
        seed.add(Server(id=1, owner_id=1, hostname="alpha", check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKEN), machine_id="machine-1", created_at=NOW - timedelta(days=60)))
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
    monkeypatch.setattr(main, "_require_admin", lambda session_id, db: {"id": 1, "role": "admin"})
    monkeypatch.setattr(main, "_is_admin", lambda db, user_id: True)
    client = TestClient(main.app, raise_server_exceptions=False)
    client.Session = Session
    yield client
    main.app.dependency_overrides.clear()


def ingest(client, body):
    return client.post("/monitoring/metrics/ingest", content=json.dumps({"server_id": 1, **body}), headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})


def overview_server(client):
    return client.get("/monitoring/overview").json()["servers"][0]


# --- machine facts --------------------------------------------------------------------------

def test_system_info_keeps_known_fields_and_drops_the_rest(app):
    system = {
        "kernel": "6.8.0-84-generic", "arch": "x86_64", "cpu_model": "Intel Core i5-8500T", "swap_total_gb": 4.0, "updates_pending": 23, "updates_security": 4,
        "auto_updates": True, "helper_installed": False, "reboot_kernel": "6.8.0-85-generic", "clock_offset_ms": 0.4, "secret": "x", "html": "<script>",
        "temperatures": [{"label": "coretemp", "celsius": 52.0}, {"label": "bad", "celsius": 9999}, "junk"],
        "interfaces": [{"name": "eth0", "network": "10.0.0.12/24", "rx_mbps": 1.8, "tx_mbps": float("1e99"), "speed_mbit": 1000, "extra": 1}, {"name": "", "network": "x"}, {"name": "wg0", "network": "not a network"}],
        "mount_devices": {"/": "nvme0n1p2", "/x": 5},
        "firewall_allowed": [{"proto": "tcp", "port": 22, "file": "nftables"}, {"proto": "icmp", "port": 1, "file": "nftables"}, {"proto": "tcp", "port": 80, "file": "/etc/passwd"}],
    }
    assert ingest(app, {"server": {"system": system}, "host": {"cpu_utilization_pct": 5}}).status_code == 200

    stored = overview_server(app)["system"]
    assert stored["kernel"] == "6.8.0-84-generic" and stored["updates_security"] == 4 and stored["auto_updates"] is True and stored["reboot_kernel"] == "6.8.0-85-generic"
    assert "secret" not in stored and "html" not in stored
    assert stored["temperatures"] == [{"label": "coretemp", "celsius": 52.0}]
    assert stored["interfaces"] == [{"name": "eth0", "network": "10.0.0.0/24", "rx_mbps": 1.8, "speed_mbit": 1000}, {"name": "wg0"}]
    assert stored["mount_devices"] == {"/": "nvme0n1p2"}
    assert stored["firewall_allowed"] == [{"proto": "tcp", "port": 22, "file": "nftables"}]


@pytest.mark.parametrize("junk", [None, 5, "text", [], {"kernel": 5, "interfaces": "x", "temperatures": {}, "mount_devices": [], "firewall_allowed": 3, "updates_pending": -1, "auto_updates": "yes"}])
def test_malformed_system_info_never_breaks_a_check_in(app, junk):
    assert ingest(app, {"server": {"system": junk}, "host": {"cpu_utilization_pct": 5}}).status_code == 200
    assert isinstance(overview_server(app)["system"], dict)


def test_containers_carry_their_project_ports_and_volumes(app):
    container = {"container_id": "c" * 64, "name": "gitlab", "status": "running", "project": "gitlab", "ports": "8080->80/tcp", "volumes": "/srv/gitlab/config, /srv/gitlab/data", "started_at": "2026-10-03T09:12:00Z"}
    ingest(app, {"host": {"cpu_utilization_pct": 5}, "containers": [container, {"container_id": "d" * 64, "name": "solo", "status": "running", "project": 7}]})

    by_name = {item["name"]: item for item in overview_server(app)["containers"]}
    assert (by_name["gitlab"]["project"], by_name["gitlab"]["ports"], by_name["gitlab"]["started_at"]) == ("gitlab", "8080->80/tcp", "2026-10-03T09:12:00Z")
    assert by_name["solo"]["project"] is None
    assert app.get(f"/monitoring/servers/1/containers/{'c' * 64}").json()["volumes"] == "/srv/gitlab/config, /srv/gitlab/data"


# --- adding a server ------------------------------------------------------------------------

def test_a_pending_collector_shows_what_it_found_and_can_be_named(app):
    registered = app.post("/monitoring/collectors/register", json={"hostname": "box", "machine_id": "machine-9", "details": {"cpu_cores": 4, "containers": 35, "mounts": 2, "helper_installed": False, "dashboard_host": True, "junk": "x"}})
    assert registered.status_code == 200
    [pending] = app.get("/monitoring/overview").json()["pending_collectors"]
    assert pending["details"] == {"cpu_cores": 4, "containers": 35, "mounts": 2, "helper_installed": False, "dashboard_host": True}

    assert app.post(f"/monitoring/pending-collectors/{pending['id']}/approve", json={"hostname": "alpha"}).status_code == 409  # taken
    assert app.post(f"/monitoring/pending-collectors/{pending['id']}/approve", json={"hostname": "media-box"}).status_code == 200
    with app.Session() as db:
        server = db.query(Server).filter_by(machine_id="machine-9").one()
        assert (server.hostname, server.name_locked) == ("media-box", True)
        token = "token-nine"
        server.collector_token_hash = main._hash_secret(token)
        db.commit()
        server_id = server.id
    # The machine keeps calling itself "box"; the chosen name stays.
    app.post("/monitoring/metrics/ingest", content=json.dumps({"server_id": server_id, "server": {"hostname": "box"}, "host": {"cpu_utilization_pct": 1}}), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with app.Session() as db:
        assert db.get(Server, server_id).hostname == "media-box"


def test_keeping_the_machines_own_name_does_not_lock_it(app):
    app.post("/monitoring/collectors/register", json={"hostname": "box", "machine_id": "machine-9"})
    [pending] = app.get("/monitoring/overview").json()["pending_collectors"]
    app.post(f"/monitoring/pending-collectors/{pending['id']}/approve", json={"hostname": "box"})
    with app.Session() as db:
        assert db.query(Server).filter_by(machine_id="machine-9").one().name_locked is False


# --- sparklines -----------------------------------------------------------------------------

def test_downsample_averages_and_keeps_gaps():
    assert dashboard.downsample([1, 2, 3], limit=5) == [1, 2, 3]
    assert dashboard.downsample([0, 10, None, None, 4, 6], limit=3) == [5, None, 5]


def test_sparklines_cover_recent_samples_only(app):
    with app.Session() as db:
        now = datetime.now(timezone.utc)
        for minutes_ago, cpu in ((90, 99.0), (10, 20.0), (5, 40.0)):
            db.add(HostMetric(server_id=1, time=now - timedelta(minutes=minutes_ago), cpu_utilization_pct=cpu))
            db.add(ContainerMetric(server_id=1, container_id="c1", name="web", status="running", cpu_pct=cpu / 2, time=now - timedelta(minutes=minutes_ago)))
        db.commit()

    assert app.get("/monitoring/sparklines?minutes=30").json()["servers"] == {"1": [20.0, 40.0]}
    assert app.get("/monitoring/servers/1/containers/sparklines?minutes=15").json()["containers"] == {"c1": [10.0, 20.0]}
    assert app.get("/monitoring/servers/99/containers/sparklines").status_code == 404
    live = app.get("/monitoring/servers/1/live?minutes=30").json()["samples"]
    assert [sample["cpu"] for sample in live] == [20.0, 40.0] and set(live[0]) == {"time", "cpu", "ram", "load", "swap", "net_sent_mb", "net_recv_mb"}


# --- the all-clear summary ------------------------------------------------------------------

def test_longest_quiet_stretch_is_the_biggest_gap_between_incidents():
    start, end = NOW - timedelta(days=10), NOW
    spans = [(start + timedelta(days=1), start + timedelta(days=2)), (start + timedelta(days=1, hours=12), start + timedelta(days=3)), (start + timedelta(days=9), end)]
    assert dashboard.longest_quiet_seconds(spans, start, end) == timedelta(days=6).total_seconds()
    assert dashboard.longest_quiet_seconds([], start, end) == timedelta(days=10).total_seconds()
    assert dashboard.longest_quiet_seconds([(start - timedelta(days=1), end + timedelta(days=1))], start, end) == 0


def test_quiet_summary_names_the_last_incident_and_ignores_notices(app):
    with app.Session() as db:
        db.add(IncidentAlert(server_id=1, severity="CRITICAL", title="gitlab is in a restart loop", description="d", status="resolved", rule_key="container:gitlab:restart_loop", created_at=NOW - timedelta(hours=3), resolved_at=NOW - timedelta(hours=2)))
        db.add(IncidentAlert(server_id=1, severity="INFO", title="alpha needs a reboot", description="d", status="resolved", rule_key="system:reboot_required", created_at=NOW - timedelta(hours=1), resolved_at=NOW - timedelta(minutes=5)))
        db.commit()
        summary = dashboard.quiet_summary(db, NOW)

    assert summary["last_incident"]["title"] == "gitlab is in a restart loop" and summary["last_incident"]["hostname"] == "alpha"
    assert summary["quiet_since"] == (NOW - timedelta(hours=2)).isoformat()
    assert summary["longest_quiet_seconds_this_month"] == (NOW - timedelta(hours=3) - NOW.replace(day=1, hour=0)).total_seconds()


def test_overview_reports_quiet_only_when_nothing_needs_attention(app):
    assert app.get("/monitoring/overview").json()["quiet"] is not None
    with app.Session() as db:
        db.add(IncidentAlert(server_id=1, severity="WARNING", title="t", description="d", status="active", rule_key="host:memory"))
        db.add(IncidentAlert(server_id=1, severity="INFO", title="n", description="d", status="active", rule_key="system:reboot_required"))
        db.commit()
    body = app.get("/monitoring/overview").json()
    assert body["quiet"] is None and body["alerts_by_severity"] == {"WARNING": 1, "INFO": 1} and body["servers"][0]["alerts_by_severity"] == {"WARNING": 1, "INFO": 1}
    assert {alert["rule_key"] for alert in body["alerts"]} == {"host:memory", "system:reboot_required"}


# --- a server's timeline --------------------------------------------------------------------

def test_events_merge_recorded_events_with_alerts_newest_first(app):
    with app.Session() as db:
        db.add(SystemEvent(server_id=1, time=NOW - timedelta(hours=5), kind="port_opened", detail="tcp/8081 (all interfaces) started listening"))
        db.add(SystemEvent(server_id=1, time=NOW - timedelta(hours=9), kind="container_added", detail="Container web appeared"))
        db.add(SystemEvent(server_id=1, time=NOW - timedelta(days=20), kind="reboot", detail="old"))
        db.add(IncidentAlert(server_id=1, severity="WARNING", title="Memory usage on alpha is 93%", description="d", status="resolved", rule_key="host:memory", created_at=NOW - timedelta(hours=2), resolved_at=NOW - timedelta(hours=1, minutes=42)))
        db.add(IncidentAlert(server_id=1, severity="CRITICAL", title="Server alpha is offline", description="d", status="resolved", rule_key=main.SERVER_OFFLINE_RULE, created_at=NOW - timedelta(days=3), resolved_at=NOW - timedelta(days=3) + timedelta(hours=5)))
        db.commit()
        events = dashboard.server_events(db, db.get(Server, 1), 7, NOW)

    assert [(event["source"], event["kind"]) for event in events] == [
        ("alert", "alert_resolved"), ("alert", "alert_opened"), ("network", "port_opened"), ("container", "container_added"), ("collector", "collector_resumed"), ("collector", "collector_stopped"),
    ]
    assert events[0]["duration_seconds"] == 18 * 60 and events[1]["resolved"] is True
    assert app.get("/monitoring/servers/1/events?days=7").status_code == 200 and app.get("/monitoring/servers/99/events").status_code == 404


# --- what the collector reads ---------------------------------------------------------------

def test_firewall_allowed_names_only_ports_that_are_listening(tmp_path, monkeypatch):
    (tmp_path / "etc/ufw").mkdir(parents=True)
    (tmp_path / "etc/ufw/user.rules").write_text("-A ufw-user-input -p tcp --dport 22 -j ACCEPT\n-A ufw-user-input -p tcp -m multiport --dports 80,443 -j ACCEPT\n-A ufw-user-input -p tcp --dport 5432 -j DROP\n")
    (tmp_path / "etc/nftables.conf").write_text("table inet filter {\n chain input {\n  udp dport { 1194, 51820 } accept\n  tcp dport 9000-9002 accept\n }\n}\n")
    monkeypatch.setattr(collector, "HOST_ROOT", tmp_path)
    listeners = [{"proto": "tcp", "port": 22, "scope": "any"}, {"proto": "tcp", "port": 443, "scope": "any"}, {"proto": "tcp", "port": 5432, "scope": "any"}, {"proto": "udp", "port": 1194, "scope": "any"}, {"proto": "tcp", "port": 9001, "scope": "any"}]

    assert collector.firewall_allowed(listeners) == [
        {"proto": "tcp", "port": 22, "file": "ufw"}, {"proto": "tcp", "port": 443, "file": "ufw"}, {"proto": "tcp", "port": 9001, "file": "nftables"}, {"proto": "udp", "port": 1194, "file": "nftables"},
    ]
    assert collector.firewall_allowed(None) == []


def test_updates_come_from_the_files_apt_leaves_behind(tmp_path, monkeypatch):
    for directory in ("etc/apt/apt.conf.d", "var/lib/update-notifier", "boot"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "etc/debian_version").write_text("12\n")
    (tmp_path / "var/lib/update-notifier/updates-available").write_text("\n23 updates can be applied immediately.\n4 of these updates are standard security updates.\n")
    (tmp_path / "etc/apt/apt.conf.d/20auto-upgrades").write_text('APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n')
    for name in ("vmlinuz-6.8.0-9-generic", "vmlinuz-6.8.0-84-generic", "vmlinuz-6.8.0-85-generic", "vmlinuz", "initrd.img-6.8.0-99-generic"):
        (tmp_path / "boot" / name).write_text("")
    monkeypatch.setattr(collector, "HOST_ROOT", tmp_path)

    info = collector.collect_updates("6.8.0-84-generic", True)
    assert (info["updates_pending"], info["updates_security"], info["auto_updates"], info["reboot_kernel"]) == (23, 4, True, "6.8.0-85-generic")
    assert "reboot_kernel" not in collector.collect_updates("6.8.0-85-generic", True)
    assert "reboot_kernel" not in collector.collect_updates("6.8.0-84-generic", False)


def test_interfaces_report_their_network_and_traffic_since_the_last_look(tmp_path, monkeypatch):
    net = tmp_path / "1/net"
    net.mkdir(parents=True)
    header = "Inter-|   Receive |  Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
    row = lambda name, rx, tx: f"{name}: {rx} 1 0 0 0 0 0 0 {tx} 1 0 0 0 0 0 0\n"  # noqa: E731
    (net / "route").write_text("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n" "eth0\t00000000\t0100000A\t0003\t0\t0\t0\t00000000\n" "eth0\t0000000A\t00000000\t0001\t0\t0\t0\t00FFFFFF\n")
    monkeypatch.setattr(collector, "HOST_PROC", tmp_path)
    monkeypatch.setattr(collector, "HOST_ROOT", tmp_path)
    collector._interface_previous.clear()

    (net / "dev").write_text(header + row("lo", 5, 5) + row("eth0", 1000, 2000) + row("veth12ab", 9, 9) + row("idle0", 0, 0))
    assert collector.collect_interfaces(now=100.0) == [{"name": "eth0", "network": "10.0.0.0/24"}]
    (net / "dev").write_text(header + row("eth0", 1000 + 4 * collector.MiB, 2000 + 2 * collector.MiB))
    assert collector.collect_interfaces(now=102.0) == [{"name": "eth0", "network": "10.0.0.0/24", "rx_mbps": 2.0, "tx_mbps": 1.0}]
    collector._interface_previous.clear()


# --- data that needs more than the collector has ----------------------------------------------

def _fake_host_proc(tmp_path):
    """A host /proc with sshd listening on tcp/22, a resolver on udp/53 and a process that names itself oddly."""
    net = tmp_path / "1/net"
    net.mkdir(parents=True)
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    (net / "tcp").write_text(header + "   0: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1001 1 x\n"
                             "   1: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 1003 1 x\n"
                             "   2: 0A00000C:D431 0A000015:01BB 01 00000000:00000000 00:00000000 00000000  1000        0 1009 1 x\n")
    (net / "udp").write_text(header + "  10: 3500007F:0035 00000000:0000 07 00000000:00000000 00:00000000 00000000   101        0 1002 2 x 0\n")
    for pid, name, inodes in ((1, "systemd", [5]), (812, "sshd", [1001, 7]), (640, "systemd-resolve", [1002]), (3000, "evil\x1b[31m\nname", [1003]), (3001, "client", [1009])):
        fd = tmp_path / str(pid) / "fd"
        fd.mkdir(parents=True, exist_ok=True)
        (tmp_path / str(pid) / "comm").write_text(name + "\n")
        for number, inode in enumerate(inodes):
            os.symlink(f"socket:[{inode}]", fd / str(number))
        os.symlink("/etc/shadow", fd / "99")
    return tmp_path


def test_port_owners_name_the_process_behind_each_listening_port(tmp_path):
    proc = _fake_host_proc(tmp_path)

    assert system_helper.listening_sockets(proc) == {1001: ("tcp", 22), 1003: ("tcp", 8080), 1002: ("udp", 53)}   # the connected socket is not a listener
    owners = system_helper.port_owners(proc)
    assert owners == [{"proto": "tcp", "port": 22, "process": "sshd"}, {"proto": "udp", "port": 53, "process": "systemd-resolve"}, {"proto": "tcp", "port": 8080, "process": "evil?[31m?name"}]
    assert system_helper.port_owners(tmp_path / "missing") == []


def test_the_collector_only_trusts_a_fresh_well_formed_port_owners_file(tmp_path):
    path = tmp_path / "ports.json"
    now = 1_000_000.0
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    assert collector.collect_port_owners(path, now) is None   # helper not running
    path.write_text(json.dumps({"generated_at": stamp, "listeners": [{"proto": "tcp", "port": 22, "process": "sshd"}, {"proto": "icmp", "port": 1, "process": "x"}, {"proto": "tcp", "port": "22", "process": "x"}, "junk"]}))
    assert collector.collect_port_owners(path, now) == [{"proto": "tcp", "port": 22, "process": "sshd"}]
    assert collector.collect_port_owners(path, now + 3600) is None   # stale
    path.write_text("{not json")
    assert collector.collect_port_owners(path, now) is None
    link = tmp_path / "link.json"
    os.symlink(path, link)
    assert collector.collect_port_owners(link, now) is None   # never follows a symlink


def test_docker_disk_usage_and_port_owners_are_validated_before_they_are_stored(app):
    system = {
        "docker_disk": {"images_gb": 41.2, "images_unused_gb": 38.0, "build_cache_gb": 2.5, "build_cache_unused_gb": float("inf"), "images": 60, "images_unused": 44, "names": ["secret"]},
        "disk_io_errors": 3,
        "listener_owners": [{"proto": "tcp", "port": 22, "process": "sshd"}, {"proto": "tcp", "port": 99999, "process": "x"}, {"proto": "tcp", "port": 80, "process": "a" * 200}, {"proto": "tcp", "port": 81, "process": ""}],
    }
    ingest(app, {"server": {"system": system}, "host": {"cpu_utilization_pct": 5}})
    stored = overview_server(app)["system"]

    assert stored["docker_disk"] == {"images_gb": 41.2, "images_unused_gb": 38.0, "build_cache_gb": 2.5, "images": 60, "images_unused": 44}
    assert stored["disk_io_errors"] == 3
    assert stored["listener_owners"] == [{"proto": "tcp", "port": 22, "process": "sshd"}, {"proto": "tcp", "port": 80, "process": "a" * 64}]
    # A collector without the helper sends no list at all, which is different from an empty one.
    ingest(app, {"server": {"system": {"kernel": "6.8"}}, "host": {"cpu_utilization_pct": 5}})
    assert "listener_owners" not in overview_server(app)["system"]


def test_disk_error_counter_sums_the_disks_that_have_one(tmp_path, monkeypatch):
    for disk, value in (("sda", "0x3"), ("sdb", "0x0a"), ("nvme0n1", None)):
        (tmp_path / "sys/block" / disk / "device").mkdir(parents=True)
        if value:
            (tmp_path / "sys/block" / disk / "device/ioerr_cnt").write_text(value + "\n")
    monkeypatch.setattr(collector, "HOST_ROOT", tmp_path)
    assert collector.disk_io_errors() == 13
    (tmp_path / "sys/block/sda/device/ioerr_cnt").unlink()
    (tmp_path / "sys/block/sdb/device/ioerr_cnt").unlink()
    assert collector.disk_io_errors() is None


def test_docker_disk_usage_is_asked_for_rarely_and_survives_an_old_proxy(monkeypatch):
    calls = []
    answer = {"images_bytes": 2 * collector.GiB, "images_unused_bytes": collector.GiB, "build_cache_bytes": 0, "build_cache_unused_bytes": 0, "images": 5, "images_unused": 2}
    monkeypatch.setattr(collector, "_docker_request", lambda path, endpoint: calls.append(endpoint) or answer)
    monkeypatch.setattr(collector, "_docker_disk", {"value": None, "next": 0.0})

    collector._remember_docker_disk("sock", now=10.0)
    collector._remember_docker_disk("sock", now=20.0)
    assert calls == ["/system/df"] and collector._docker_disk["value"] == {"images_gb": 2.0, "images_unused_gb": 1.0, "build_cache_gb": 0.0, "build_cache_unused_gb": 0.0, "images": 5, "images_unused": 2}

    def refused(path, endpoint):
        raise ValueError("403")
    monkeypatch.setattr(collector, "_docker_request", refused)
    collector._remember_docker_disk("sock", now=10.0 + collector.DOCKER_DISK_EVERY_SECONDS + 1)
    assert collector._docker_disk["value"]["images_gb"] == 2.0   # the last known figure stays
