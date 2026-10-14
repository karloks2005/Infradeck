import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.collector import collector
from src.backend import alerts, main, rules
from src.backend.models import Base, FirewallState, HostMetric, IncidentAlert, ListenerState, Server, SystemEvent, User

TOKEN = "collector-token"


# --- collector: host network totals ----------------------------------------------------------------

def _dev_line(name, received, sent):
    return f"{name:>8}: {received} 1 0 0 0 0 0 0 {sent} 1 0 0 0 0 0 0"


DEV_HEADER = "Inter-|   Receive                            |  Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"


def _fake_host(tmp_path, monkeypatch, host_dev=None, own_dev=None, physical=("eno2", "wlo1")):
    proc, root = tmp_path / "proc", tmp_path / "root"
    (proc / "1/net").mkdir(parents=True)
    (proc / "net").mkdir()
    if host_dev is not None:
        (proc / "1/net/dev").write_text(DEV_HEADER + "\n".join(host_dev) + "\n")
    if own_dev is not None:
        (proc / "net/dev").write_text(DEV_HEADER + "\n".join(own_dev) + "\n")
    for name in ("lo", "docker0", "veth123", "eno2", "wlo1"):
        (root / "sys/class/net" / name).mkdir(parents=True)
        if name in physical:
            (root / "sys/class/net" / name / "device").mkdir()
    monkeypatch.setattr(collector, "HOST_PROC", proc)
    monkeypatch.setattr(collector, "HOST_ROOT", root)
    return proc, root


def test_network_totals_come_from_the_hosts_physical_interfaces(tmp_path, monkeypatch):
    _fake_host(tmp_path, monkeypatch,
               host_dev=[_dev_line("lo", 999, 999), _dev_line("eno2", 1000, 400), _dev_line("wlo1", 500, 100), _dev_line("docker0", 777, 777), _dev_line("veth123", 888, 888)],
               own_dev=[_dev_line("lo", 5, 5), _dev_line("eth0", 7, 3)])

    # The collector container's own eth0 is never used, and Docker's virtual interfaces would double count.
    assert collector._network_totals() == (500, 1500)


def test_without_sysfs_virtual_interfaces_are_skipped_by_name(tmp_path, monkeypatch):
    _proc, root = _fake_host(tmp_path, monkeypatch, host_dev=[_dev_line("lo", 9, 9), _dev_line("eno2", 100, 50), _dev_line("veth9", 70, 70), _dev_line("br-abc", 60, 60), _dev_line("docker0", 40, 40)])
    import shutil
    shutil.rmtree(root / "sys")

    assert collector._network_totals() == (50, 100)


def test_inside_a_container_an_unreadable_host_table_is_unknown_not_the_containers_own(tmp_path, monkeypatch):
    _fake_host(tmp_path, monkeypatch, host_dev=None, own_dev=[_dev_line("eth0", 7, 3)])

    assert collector._network_totals() is None


def test_only_a_collector_on_the_host_itself_may_fall_back_to_proc_net(monkeypatch):
    monkeypatch.setattr(collector, "HOST_PROC", Path("/host/proc"))
    assert collector._host_net_dirs() == [Path("/host/proc/1/net")]
    monkeypatch.setattr(collector, "HOST_PROC", Path("/proc"))
    assert collector._host_net_dirs() == [Path("/proc/1/net"), Path("/proc/net")]


def test_garbage_and_oversized_network_tables_are_survived(tmp_path, monkeypatch):
    proc, _root = _fake_host(tmp_path, monkeypatch, host_dev=[_dev_line("eno2", 100, 50), "garbage without a colon", "eno2: not numbers at all", _dev_line("wlo1", 1, 1)])
    assert collector._network_totals() == (51, 101)

    (proc / "1/net/dev").write_bytes(b"x" * (collector.MAX_NET_TABLE_BYTES + 10))
    assert collector._network_totals() is None


# --- collector: listening ports ---------------------------------------------------------------------

TCP_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
ZERO6 = "0" * 32


def _sock(address, port, state, remote="00000000:0000"):
    return f"   0: {address}:{port:04X} {remote} {state} 00000000:00000000 00:00000000 00000000     0        0 1 1 0"


def _net_tables(tmp_path, monkeypatch, **tables):
    proc = tmp_path / "proc"
    (proc / "1/net").mkdir(parents=True)
    for name, lines in tables.items():
        (proc / "1/net" / name).write_text(TCP_HEADER + "\n".join(lines) + "\n")
    monkeypatch.setattr(collector, "HOST_PROC", proc)


def test_listeners_are_read_with_their_scope(tmp_path, monkeypatch):
    _net_tables(tmp_path, monkeypatch,
                tcp=[_sock("0100007F", 5432, "0A"), _sock("00000000", 80, "0A"), _sock("0100A8C0", 8080, "0A"), _sock("00000000", 443, "01", "0100A8C0:C350")],
                tcp6=[_sock(ZERO6, 22, "0A"), _sock("00000000000000000000000001000000", 631, "0A"), _sock("0000000000000000FFFF00000100007F", 9000, "0A")],
                udp=[_sock("00000000", 5353, "07"), _sock("00000000", 5355, "01", "0100A8C0:0035")], udp6=[])

    found = {(item["proto"], item["port"], item["scope"]) for item in collector.collect_listeners()}

    assert found == {("tcp", 5432, "loopback"), ("tcp", 80, "any"), ("tcp", 8080, "specific"), ("tcp", 22, "any"), ("tcp", 631, "loopback"), ("tcp", 9000, "loopback"), ("udp", 5353, "any")}


def test_the_same_port_on_ipv4_and_ipv6_is_one_listener(tmp_path, monkeypatch):
    _net_tables(tmp_path, monkeypatch, tcp=[_sock("00000000", 80, "0A")], tcp6=[_sock(ZERO6, 80, "0A")], udp=[], udp6=[])

    assert collector.collect_listeners() == [{"proto": "tcp", "port": 80, "scope": "any"}]


@pytest.mark.parametrize("proto, port, scope, tracked", [
    ("tcp", 80, "any", True), ("tcp", 5432, "loopback", True), ("udp", 5353, "any", True), ("udp", 53, "loopback", True),
    ("tcp", 45983, "loopback", False), ("udp", 40000, "any", False), ("udp", 40000, "specific", False),
    ("tcp", 40000, "any", True), ("tcp", 40000, "specific", True),
])
def test_ephemeral_ports_are_skipped_but_exposed_high_tcp_ports_are_not(proto, port, scope, tracked):
    assert collector.listener_tracked(proto, port, scope) is tracked
    assert rules.listener_tracked(proto, port, scope) is tracked


def test_missing_or_garbage_tables_are_unknown_or_ignored(tmp_path, monkeypatch):
    _net_tables(tmp_path, monkeypatch)
    assert collector.collect_listeners() is None  # nothing readable

    proc = tmp_path / "proc"
    for name in ("tcp6", "udp", "udp6"):
        (proc / "1/net" / name).write_text(TCP_HEADER)
    (proc / "1/net/tcp").write_text(TCP_HEADER + "\n".join(["garbage", "", "1: nonsense", "0: ZZZ:GG 00000000:0000 0A", "0: 0100007F:0000 00000000:0000 0A", "0: 0100007F:FFFFF 00000000:0000 0A", _sock("00000000", 22, "0A")]) + "\n")
    assert collector.collect_listeners() == [{"proto": "tcp", "port": 22, "scope": "any"}]


def test_one_unreadable_table_makes_the_whole_answer_unknown_not_partial(tmp_path, monkeypatch):
    _net_tables(tmp_path, monkeypatch, tcp=[_sock("00000000", 22, "0A")], tcp6=[], udp=[])  # udp6 is missing

    assert collector.collect_listeners() is None


def test_a_table_longer_than_the_cap_is_unknown_because_a_truncated_read_could_fake_closed_ports(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "MAX_NET_TABLE_LINES", 100)
    _net_tables(tmp_path, monkeypatch, tcp=[_sock("00000000", 22, "0A")] + [_sock("0100A8C0", 40000 + number, "01", "0100A8C0:C350") for number in range(150)], tcp6=[], udp=[], udp6=[])

    assert collector.collect_listeners() is None


def test_a_busy_hosts_connection_table_does_not_hide_its_listeners(tmp_path, monkeypatch):
    connections = [_sock("0100A8C0", 20000 + number, "01", "0100A8C0:C350") for number in range(5000)]
    _net_tables(tmp_path, monkeypatch, tcp=[_sock("00000000", 443, "0A")] + connections, tcp6=[], udp=[], udp6=[])

    assert collector.collect_listeners() == [{"proto": "tcp", "port": 443, "scope": "any"}]


def test_a_container_never_reports_its_own_sockets_as_the_hosts(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "net/tcp").write_text(TCP_HEADER + _sock("00000000", 9999, "0A") + "\n")
    monkeypatch.setattr(collector, "HOST_PROC", proc)  # a container's /proc/net only has the container's sockets

    assert collector.collect_listeners() is None


def test_the_listener_report_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "MAX_NET_TABLE_LINES", 10_000)
    _net_tables(tmp_path, monkeypatch, tcp=[_sock("00000000", port, "0A") for port in range(1, 700)], tcp6=[], udp=[], udp6=[])

    assert len(collector.collect_listeners()) == collector.MAX_LISTENERS


# --- collector: connectivity ------------------------------------------------------------------------

def test_connectivity_is_off_unless_targets_are_configured(monkeypatch):
    monkeypatch.delenv("INFRADECK_CONNECTIVITY_TARGETS", raising=False)
    assert collector.collect_connectivity() == {"internet_status": None, "internet_latency_ms": None}


@pytest.mark.parametrize("value, expected", [
    ("1.1.1.1:443", [("1.1.1.1", 443)]),
    ("example.com:443, 9.9.9.9:53 ,example.com:443", [("example.com", 443), ("9.9.9.9", 53)]),
    ("a.com:1,b.com:2,c.com:3,d.com:4,e.com:5,f.com:6,g.com:7", [("a.com", 1), ("b.com", 2), ("c.com", 3), ("d.com", 4), ("e.com", 5)]),
])
def test_valid_targets_are_parsed_deduplicated_and_capped(monkeypatch, value, expected):
    monkeypatch.setenv("INFRADECK_CONNECTIVITY_TARGETS", value)
    assert collector._connectivity_targets() == expected


@pytest.mark.parametrize("bad", ["", "nohost", ":443", "host:", "host:0", "host:65536", "host:abc", "host:-1", "http://example.com:443", "exa mple.com:443", "example.com;rm -rf /:443",
                                 "999.1.1.1:443", "1.1.1:443", "[::1]:443", "-bad.com:443", "a" * 300 + ".com:443", "user@host:443", "host:443/path", "host\n:443"])
def test_malformed_targets_are_ignored(monkeypatch, bad):
    monkeypatch.setenv("INFRADECK_CONNECTIVITY_TARGETS", bad)
    assert collector._connectivity_targets() == []


@pytest.mark.parametrize("results, status", [
    ([{"dns": None, "tcp": True}, {"dns": True, "tcp": True}], "ok"),
    ([{"dns": None, "tcp": True}, {"dns": True, "tcp": False}], "degraded"),
    ([{"dns": False, "tcp": False}, {"dns": False, "tcp": False}], "dns_failed"),
    ([{"dns": None, "tcp": False}, {"dns": True, "tcp": False}], "down"),
    ([{"dns": False, "tcp": False}, {"dns": None, "tcp": False}], "down"),
])
def test_results_are_summarised(results, status):
    assert collector.summarise_connectivity(results) == status


def test_a_reachable_target_reports_ok_with_its_latency(monkeypatch):
    monkeypatch.setenv("INFRADECK_CONNECTIVITY_TARGETS", "example.com:443,1.1.1.1:443")
    monkeypatch.setattr(collector.socket, "getaddrinfo", lambda host, port, **kw: [(None, None, None, "", ("93.184.216.34", port))])

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(collector.socket, "create_connection", lambda address, timeout: Connection())

    result = collector.collect_connectivity()

    assert result["internet_status"] == "ok" and isinstance(result["internet_latency_ms"], int)


def test_unresolvable_names_are_reported_as_dns_failure(monkeypatch):
    monkeypatch.setenv("INFRADECK_CONNECTIVITY_TARGETS", "example.com:443")
    monkeypatch.setattr(collector.socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("no dns")))

    assert collector.collect_connectivity()["internet_status"] == "dns_failed"


def test_a_hung_probe_cannot_stall_the_check_in(monkeypatch):
    monkeypatch.setenv("INFRADECK_CONNECTIVITY_TARGETS", "1.1.1.1:443")
    monkeypatch.setattr(collector, "CONNECTIVITY_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(collector.socket, "create_connection", lambda address, timeout: time.sleep(3))

    started = time.monotonic()
    result = collector.collect_connectivity()

    assert time.monotonic() - started < 1.5 and result["internet_status"] == "down"


def test_probes_only_connect_and_never_send_data(monkeypatch):
    monkeypatch.setenv("INFRADECK_CONNECTIVITY_TARGETS", "1.1.1.1:443")
    sent = []

    class Connection:
        def close(self):
            pass

        def send(self, data):
            sent.append(data)

        sendall = send

    monkeypatch.setattr(collector.socket, "create_connection", lambda address, timeout: Connection())
    collector.collect_connectivity()

    assert sent == []


# --- collector: firewall files ----------------------------------------------------------------------

def _firewall_root(tmp_path, monkeypatch, files):
    root = tmp_path / "root"
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(collector, "HOST_ROOT", root)
    return root


UFW_RULES = "*filter\n:ufw-user-input - [0:0]\n-A ufw-user-input -p tcp --dport 22 -j ACCEPT\n-A ufw-user-input -p tcp --dport 8443 -s 10.0.0.0/8 -j ACCEPT\nCOMMIT\n"


def test_firewall_files_are_reduced_to_a_hash_a_rule_count_and_the_ufw_flag(tmp_path, monkeypatch):
    _firewall_root(tmp_path, monkeypatch, {"etc/ufw/user.rules": UFW_RULES, "etc/ufw/ufw.conf": "# comment\nENABLED=yes\nLOGLEVEL=low\n", "etc/nftables.conf": "#!/usr/sbin/nft -f\n\nflush ruleset\ntable inet filter {\n}\n"})

    report = collector.collect_firewall()

    by_name = {item["name"]: item for item in report["files"]}
    assert by_name["ufw"]["rules"] == 2 and by_name["nftables"]["rules"] == 3 and report["ufw_enabled"] is True
    assert all(len(item["sha"]) == 16 for item in report["files"])


def test_the_firewall_report_never_contains_the_rules_themselves(tmp_path, monkeypatch):
    _firewall_root(tmp_path, monkeypatch, {"etc/ufw/user.rules": UFW_RULES + "# secret-internal-subnet 10.99.0.0/16\n"})

    dumped = json.dumps(collector.collect_firewall())

    assert "10.0.0.0" not in dumped and "8443" not in dumped and "secret-internal-subnet" not in dumped


def test_ufw_disabled_is_reported_and_changes_the_fingerprint(tmp_path, monkeypatch):
    _firewall_root(tmp_path, monkeypatch, {"etc/ufw/ufw.conf": "ENABLED=yes\n"})
    enabled = collector.collect_firewall()
    _firewall_root(tmp_path, monkeypatch, {"etc/ufw/ufw.conf": "ENABLED=no\n"})
    disabled = collector.collect_firewall()

    assert (enabled["ufw_enabled"], disabled["ufw_enabled"]) == (True, False) and enabled["files"][0]["sha"] != disabled["files"][0]["sha"]


def test_no_known_firewall_file_means_none(tmp_path, monkeypatch):
    _firewall_root(tmp_path, monkeypatch, {})
    assert collector.collect_firewall() is None


def test_symlinked_oversized_and_binary_firewall_files_are_handled_safely(tmp_path, monkeypatch):
    root = _firewall_root(tmp_path, monkeypatch, {"etc/ufw/user6.rules": "x", "etc/iptables/rules.v6": b"\xff\xfe\x00" * 10})
    secret = tmp_path / "secret.txt"
    secret.write_text("root:hash")
    (root / "etc/ufw/user.rules").symlink_to(secret)  # must not be followed
    (root / "etc/nftables.conf").write_bytes(b"#" * (collector.MAX_FIREWALL_FILE_BYTES + 1))  # too large

    names = {item["name"] for item in collector.collect_firewall()["files"]}

    assert names == {"ufw6", "ip6tables"}


# --- backend ---------------------------------------------------------------------------------------

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
    main.ingest_metrics(main.MetricsIngestRequest(server_id=5, host=host), authorization=f"Bearer {TOKEN}", session_id=None, db=db)


def _l(proto, port, scope="any"):
    return {"proto": proto, "port": port, "scope": scope}


BASE_PORTS = [_l("tcp", 22), _l("tcp", 5432, "loopback")]


def _active(db):
    return {alert.rule_key: alert for alert in db.query(IncidentAlert).filter_by(status="active")}


def _events(db, *kinds):
    return [(event.kind, event.detail, event.event_key) for event in db.query(SystemEvent).filter(SystemEvent.kind.in_(kinds or rules.NETWORK_EVENT_KINDS)).order_by(SystemEvent.id)]


@pytest.mark.parametrize("raw", [None, 5, "x", {}, {"proto": "tcp"}])
def test_a_listener_report_must_be_a_list(raw):
    assert rules.validate_listeners(raw) is None


def test_listener_entries_are_validated_deduplicated_and_filtered():
    raw = [_l("tcp", 80), _l("tcp", 80), _l("sctp", 80), _l("tcp", 0), _l("tcp", 65536), _l("tcp", "80"), _l("tcp", True), _l("tcp", 1.5), _l("tcp", 80, "everywhere"),
           {"proto": "tcp"}, None, 5, "x", _l("tcp", 45000, "loopback"), _l("udp", 45000), _l("udp", 53, "specific")]

    assert rules.validate_listeners(raw) == [("tcp", 80, "any"), ("udp", 53, "specific")]


def test_the_listener_list_is_capped():
    assert len(rules.validate_listeners([_l("tcp", port) for port in range(1, 2000)])) == rules.MAX_LISTENERS


def test_firewall_reports_are_validated():
    sha = "0123456789abcdef"
    assert rules.validate_firewall(None) == {"files": {}, "ufw_enabled": None}
    assert rules.validate_firewall("x") is None and rules.validate_firewall({"files": "x"}) is None and rules.validate_firewall({}) is None
    good = {"files": [{"name": "ufw", "sha": sha, "rules": 3}], "ufw_enabled": True}
    assert rules.validate_firewall(good) == {"files": {"ufw": (sha, 3)}, "ufw_enabled": True}
    junk = {"files": [{"name": "passwd", "sha": sha, "rules": 1}, {"name": "ufw", "sha": "NOT-HEX", "rules": 1}, {"name": "ufw6", "sha": sha, "rules": -1}, {"name": "ufw6", "sha": sha, "rules": "5"},
                      {"name": "ufw6", "sha": sha, "rules": True}, {"name": "nftables", "sha": sha, "rules": 10**9}, {"name": "iptables", "sha": sha, "rules": 2}, {"name": "iptables", "sha": sha, "rules": 9}, 5, None], "ufw_enabled": "yes"}
    assert rules.validate_firewall(junk) == {"files": {"iptables": (sha, 2)}, "ufw_enabled": None}


def test_the_first_listener_report_is_only_a_baseline(db):
    _ingest(db, listeners=BASE_PORTS)

    assert _events(db) == [] and db.query(ListenerState).count() == 2


def test_a_new_exposed_listener_is_recorded_and_raises_a_warning(db):
    _ingest(db, listeners=BASE_PORTS)
    _ingest(db, listeners=BASE_PORTS + [_l("tcp", 8080)])

    assert _events(db) == [("port_opened", "New listener: tcp/8080 (all interfaces)", "tcp/8080/any")]
    alert = _active(db)["network:port:tcp/8080"]
    assert alert.severity == alerts.WARNING and "8080" in alert.title


def test_a_new_loopback_listener_is_an_event_without_an_incident(db):
    _ingest(db, listeners=BASE_PORTS)
    _ingest(db, listeners=BASE_PORTS + [_l("tcp", 6379, "loopback")])

    assert [kind for kind, _d, _k in _events(db)] == ["port_opened"] and not [key for key in _active(db) if key.startswith("network:port")]


def test_a_closed_listener_is_recorded_and_clears_its_incident(db):
    _ingest(db, listeners=BASE_PORTS)
    _ingest(db, listeners=BASE_PORTS + [_l("tcp", 8080)])
    _ingest(db, listeners=BASE_PORTS)

    assert [kind for kind, _d, _k in _events(db)] == ["port_opened", "port_closed"]
    assert "network:port:tcp/8080" not in _active(db) and db.query(ListenerState).count() == 2


def test_the_listener_incident_clears_with_the_event_window(db):
    _ingest(db, listeners=BASE_PORTS)
    _ingest(db, listeners=BASE_PORTS + [_l("tcp", 8080)])
    db.query(SystemEvent).update({"time": datetime.now(timezone.utc) - timedelta(hours=3)})
    db.commit()
    _ingest(db, listeners=BASE_PORTS + [_l("tcp", 8080)])

    assert "network:port:tcp/8080" not in _active(db)


def test_unchanged_listeners_create_no_events_or_duplicate_rows(db):
    for _ in range(3):
        _ingest(db, listeners=BASE_PORTS)

    assert _events(db) == [] and db.query(ListenerState).count() == 2


def test_an_empty_or_missing_listener_report_never_looks_like_everything_closed(db):
    _ingest(db, listeners=BASE_PORTS)
    _ingest(db, listeners=[])
    _ingest(db, listeners=None)
    _ingest(db)  # an old collector that doesn't send the field
    _ingest(db, listeners="garbage")

    assert _events(db) == [] and db.query(ListenerState).count() == 2


def test_a_burst_of_new_listeners_is_capped_with_one_notice(db):
    _ingest(db, listeners=BASE_PORTS)
    _ingest(db, listeners=BASE_PORTS + [_l("tcp", port) for port in range(9000, 9030)])

    kinds = [kind for kind, _d, _k in _events(db)]
    assert kinds.count("port_opened") == rules.MAX_NETWORK_EVENTS_PER_CHECKIN and kinds.count("network_notice") == 1


def test_sustained_port_flapping_is_capped_per_hour(db):
    _ingest(db, listeners=BASE_PORTS)
    for round_number in range(12):
        extra = [_l("tcp", 9000 + number + round_number * 100) for number in range(15)]
        _ingest(db, listeners=BASE_PORTS + extra)

    kinds = [kind for kind, _d, _k in _events(db)]
    assert len(kinds) <= rules.MAX_NETWORK_EVENTS_PER_HOUR + 1 and kinds.count("network_notice") == 1


def test_listener_state_is_removed_with_the_server(db, monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda session_id, db: {"id": 1})
    _ingest(db, listeners=BASE_PORTS, firewall={"files": [{"name": "ufw", "sha": "0123456789abcdef", "rules": 1}], "ufw_enabled": None})

    main.delete_monitored_server(5, session_id="s", db=db)

    assert db.query(ListenerState).count() == 0 and db.query(FirewallState).count() == 0


def _fw(*files, enabled=None):
    return {"files": [{"name": name, "sha": sha, "rules": count} for name, sha, count in files], "ufw_enabled": enabled}


SHA_A, SHA_B, SHA_C = "a" * 16, "b" * 16, "c" * 16


def test_the_first_firewall_report_is_only_a_baseline(db):
    _ingest(db, firewall=_fw(("ufw", SHA_A, 4), ("ufw-conf", SHA_B, 0), enabled=True))

    assert _events(db) == [] and db.query(FirewallState).count() == 2 and not [k for k in _active(db) if k.startswith("network:")]


def test_unchanged_firewall_files_report_nothing(db):
    for _ in range(3):
        _ingest(db, firewall=_fw(("ufw", SHA_A, 4)))

    assert _events(db) == []


def test_changed_rules_are_informational_and_fewer_rules_are_a_warning(db):
    _ingest(db, firewall=_fw(("ufw", SHA_A, 4)))
    _ingest(db, firewall=_fw(("ufw", SHA_B, 6)))
    assert _events(db) == [("firewall_changed", "ufw IPv4 rules changed (4 to 6 rules)", "ufw")]
    assert _active(db)["network:firewall"].severity == alerts.INFO

    _ingest(db, firewall=_fw(("ufw", SHA_C, 2)))
    assert [kind for kind, _d, _k in _events(db)][-1] == "firewall_weakened"
    assert _active(db)["network:firewall"].severity == alerts.WARNING


def test_disabling_ufw_is_a_warning_and_enabling_it_is_informational(db):
    _ingest(db, firewall=_fw(("ufw-conf", SHA_A, 0), enabled=True))
    _ingest(db, firewall=_fw(("ufw-conf", SHA_B, 0), enabled=False))
    assert _events(db) == [("firewall_weakened", "ufw was enabled and is now disabled", "ufw-conf")]
    assert _active(db)["network:firewall"].severity == alerts.WARNING

    _ingest(db, firewall=_fw(("ufw-conf", SHA_C, 0), enabled=True))
    assert _events(db)[-1][:2] == ("firewall_changed", "ufw was disabled and is now enabled")


def test_a_firewall_file_disappearing_is_a_warning_and_one_appearing_is_informational(db):
    _ingest(db, firewall=_fw(("ufw", SHA_A, 4), ("nftables", SHA_B, 9)))
    _ingest(db, firewall=_fw(("nftables", SHA_B, 9), ("iptables", SHA_C, 1)))

    assert _events(db) == [("firewall_changed", "iptables rules.v4 appeared", "iptables"), ("firewall_weakened", "ufw IPv4 rules is no longer present", "ufw")]
    assert db.query(FirewallState).count() == 2


def test_all_firewall_files_vanishing_is_noticed(db):
    _ingest(db, firewall=_fw(("ufw", SHA_A, 4)))
    _ingest(db, firewall=None)

    assert [kind for kind, _d, _k in _events(db)] == ["firewall_weakened"] and db.query(FirewallState).count() == 0


def test_missing_or_malformed_firewall_reports_are_ignored(db):
    _ingest(db, firewall=_fw(("ufw", SHA_A, 4)))
    _ingest(db)
    _ingest(db, firewall="garbage")
    _ingest(db, firewall={"files": "x"})
    _ingest(db, firewall=_fw(("passwd", SHA_B, 1), ("ufw", "not-a-hash", 1)))  # valid shape, but no usable entry

    assert [kind for kind, _d, _k in _events(db)] == ["firewall_weakened"]  # only the last one: the real file is now absent


def test_the_firewall_incident_clears_with_the_event_window(db):
    _ingest(db, firewall=_fw(("ufw", SHA_A, 4)))
    _ingest(db, firewall=_fw(("ufw", SHA_B, 5)))
    db.query(SystemEvent).update({"time": datetime.now(timezone.utc) - timedelta(hours=3)})
    db.commit()
    _ingest(db, firewall=_fw(("ufw", SHA_B, 5)))

    assert "network:firewall" not in _active(db)


def test_sustained_internet_loss_warns_and_recovery_clears(db):
    for _ in range(2):
        _ingest(db, internet_status="down")
    assert "network:internet" not in _active(db)
    _ingest(db, internet_status="down")
    assert _active(db)["network:internet"].severity == alerts.WARNING

    _ingest(db, internet_status="ok", internet_latency_ms=20)
    assert "network:internet" not in _active(db)


def test_sustained_dns_failure_is_its_own_warning(db):
    for _ in range(3):
        _ingest(db, internet_status="dns_failed")

    assert "network:dns" in _active(db) and "network:internet" not in _active(db)


def test_degraded_unchecked_and_malformed_internet_status_never_alerts(db):
    for _ in range(4):
        _ingest(db, internet_status="degraded")
    for _ in range(4):
        _ingest(db, internet_status=None)
    for _ in range(4):
        _ingest(db, internet_status="<script>", internet_latency_ms="fast")

    assert not [key for key in _active(db) if key.startswith("network:")]
    assert db.query(HostMetric).filter(HostMetric.internet_status == "<script>").count() == 0


def test_internet_latency_must_be_a_sane_number(db):
    _ingest(db, internet_status="ok", internet_latency_ms=10**9)
    _ingest(db, internet_status="ok", internet_latency_ms=42)

    assert [row.internet_latency_ms for row in db.query(HostMetric).order_by(HostMetric.id)] == [None, 42]


def test_the_overview_shows_listeners_firewall_and_network_events(db, monkeypatch):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    _ingest(db, listeners=BASE_PORTS, firewall=_fw(("ufw-conf", SHA_A, 0), enabled=False), internet_status="ok", internet_latency_ms=12)
    _ingest(db, listeners=BASE_PORTS + [_l("tcp", 8080)], firewall=_fw(("ufw-conf", SHA_A, 0), enabled=False), internet_status="ok", internet_latency_ms=12)

    server = main.monitoring_overview(session_id="s", db=db)["servers"][0]

    assert [(item["proto"], item["port"], item["scope"]) for item in server["network"]["listeners"]] == [("tcp", 22, "any"), ("tcp", 5432, "loopback"), ("tcp", 8080, "any")]
    assert server["network"]["firewall"][0]["label"] == "ufw settings" and server["network"]["firewall"][0]["enabled"] is False
    assert [event["kind"] for event in server["network"]["events"]] == ["port_opened"]
    assert (server["host"]["internet_status"], server["host"]["internet_latency_ms"]) == ("ok", 12)


def test_network_events_have_their_own_list_and_do_not_crowd_the_other_rows(db, monkeypatch):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    db.add(SystemEvent(server_id=5, time=datetime.now(timezone.utc) - timedelta(days=2), kind="reboot", detail="The host rebooted"))
    db.commit()
    _ingest(db, listeners=BASE_PORTS)
    _ingest(db, listeners=BASE_PORTS + [_l("tcp", port) for port in range(9000, 9008)])

    server = main.monitoring_overview(session_id="s", db=db)["servers"][0]

    assert [event["kind"] for event in server["system_events"]] == ["reboot"] and server["container_events"] == []
    assert len(server["network"]["events"]) == 8


def test_the_network_lists_are_bounded(db, monkeypatch):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    now = datetime.now(timezone.utc)
    for number in range(300):
        db.add(ListenerState(server_id=5, proto="tcp", port=1000 + number, scope="any", first_seen=now, last_seen=now))
    for number in range(40):
        db.add(SystemEvent(server_id=5, time=now - timedelta(minutes=number), kind="port_opened", detail=f"New listener: tcp/{number}", event_key=f"tcp/{number}/any"))
    db.commit()

    network = main.monitoring_overview(session_id="s", db=db)["servers"][0]["network"]

    assert len(network["listeners"]) == main.LISTENERS_SHOWN and len(network["events"]) == main.NETWORK_EVENTS_SHOWN


def test_network_events_follow_the_normal_retention(db):
    old = datetime.now(timezone.utc) - timedelta(days=40)
    db.add_all([SystemEvent(server_id=5, time=old, kind=kind, detail="x", event_key="x") for kind in rules.NETWORK_EVENT_KINDS])
    db.commit()

    assert main.purge_old_metrics(db) == len(rules.NETWORK_EVENT_KINDS)


def test_crafted_event_keys_cannot_raise_incidents_for_closed_or_loopback_ports(db):
    now = datetime.now(timezone.utc)
    db.add_all([
        SystemEvent(server_id=5, time=now, kind="port_opened", detail="x", event_key="tcp/8080/any"),  # not actually open
        SystemEvent(server_id=5, time=now, kind="port_opened", detail="x", event_key="garbage"),
        SystemEvent(server_id=5, time=now, kind="port_opened", detail="x", event_key="tcp/notaport/any"),
    ])
    db.add_all([ListenerState(server_id=5, proto="tcp", port=5432, scope="loopback", first_seen=now, last_seen=now), ListenerState(server_id=5, proto="tcp", port=22, scope="any", first_seen=now, last_seen=now)])
    db.add(SystemEvent(server_id=5, time=now, kind="port_opened", detail="x", event_key="tcp/5432/loopback"))
    db.commit()

    _ingest(db, listeners=BASE_PORTS)

    assert not [key for key in _active(db) if key.startswith("network:port")]
