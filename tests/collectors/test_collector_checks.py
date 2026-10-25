"""The collector runs availability checks: only well-formed checks from the dashboard, against a real local HTTP
server and TCP port here, and the results it reports never contain a response body."""
import http.server
import socket
import threading
import time
import urllib.error

import pytest

from src.collector import collector

GOOD = {"id": 1, "type": "http", "target": "http://127.0.0.1:1/", "interval": 60, "retry_interval": 20, "timeout": 5, "accepted_codes": ["200-299"], "follow_redirects": True, "ignore_tls": False, "method": "GET"}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        if self.path == "/away":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()
            return
        status = 503 if self.path == "/broken" else 200
        body = b"<html>Welcome to Gitea, secret=hunter2</html>"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def check(**changes):
    return collector.validate_check({**GOOD, **changes})


@pytest.mark.parametrize("changes", [
    {"type": "ping"}, {"id": 0}, {"id": True}, {"interval": 5}, {"retry_interval": 10**6}, {"timeout": 49}, {"target": "file:///etc/passwd"},
    {"target": "ftp://example.com/"}, {"target": "http://exa mple.com/"}, {"target": "http://a\x00b/"}, {"accepted_codes": []}, {"accepted_codes": ["2xx"]},
    {"type": "keyword"}, {"type": "keyword", "keyword": ""}, {"type": "tcp", "target": "host"}, {"type": "tcp", "target": "h;id:22"}, {"type": "tcp", "target": "h:0"},
])
def test_a_check_that_isnt_well_formed_is_not_run(changes):
    assert check(**changes) is None


def test_an_accepted_status_is_up_with_a_response_time(site):
    result = collector.run_http(check(target=f"{site}/ok"))

    assert result["ok"] is True and result["code"] == 200 and result["response_ms"] >= 0 and result["detail"] is None


def test_a_status_outside_the_accepted_codes_is_down_with_the_code(site):
    result = collector.run_http(check(target=f"{site}/broken"))

    assert (result["ok"], result["code"], result["detail"]) == (False, 503, "HTTP 503")
    assert collector.run_http(check(target=f"{site}/broken", accepted_codes=["200-299", "503"]))["ok"] is True


def test_redirects_are_followed_only_when_asked_and_only_to_http(site):
    assert collector.run_http(check(target=f"{site}/redirect"))["code"] == 200
    assert collector.run_http(check(target=f"{site}/redirect", follow_redirects=False))["detail"] == "HTTP 302"
    assert collector.run_http(check(target=f"{site}/away"))["detail"] == "HTTP 302"


def test_a_keyword_check_looks_in_the_page_but_reports_none_of_it(site):
    found = collector.run_check(check(type="keyword", target=f"{site}/ok", keyword="welcome TO gitea"))
    exact = collector.run_check(check(type="keyword", target=f"{site}/ok", keyword="welcome TO gitea", case_sensitive=True))

    assert found["ok"] is True
    assert (exact["ok"], exact["detail"]) == (False, "Keyword not found")
    assert "hunter2" not in str(found) + str(exact)


def test_head_is_used_only_for_plain_http_checks():
    assert check(method="HEAD")["method"] == "HEAD"
    assert check(type="keyword", keyword="x", method="HEAD")["method"] == "GET"


def test_an_unreachable_service_is_down_with_a_short_reason():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert collector.run_http(check(target=f"http://127.0.0.1:{port}/"))["detail"] == "Connection refused"
    assert collector.run_tcp(check(type="tcp", target=f"127.0.0.1:{port}"))["detail"] == "Connection refused"


def test_an_open_tcp_port_is_up(site):
    port = site.rsplit(":", 1)[1]

    result = collector.run_tcp(check(type="tcp", target=f"127.0.0.1:{port}"))

    assert result["ok"] is True and result["response_ms"] >= 0


def test_the_runner_runs_due_checks_and_reports_them(monkeypatch, site):
    sent = []

    def fake_send(api_url, server_id, token, results, tests=None):
        sent.append(list(results))
        return {"checks": [{**GOOD, "target": f"{site}/ok"}], "report_every": 5}

    monkeypatch.setattr(collector, "send_check_results", fake_send)
    runner = collector.CheckRunner("http://dashboard/api", (3, "token"))

    assert runner.report() is True            # the first report has nothing in it and fetches the checks
    runner.run_due()
    deadline = time.monotonic() + 5
    while not runner.pending and time.monotonic() < deadline:
        time.sleep(0.05)
    runner.report()

    assert sent[0] == [] and len(sent[1]) == 1
    assert sent[1][0]["check_id"] == 1 and sent[1][0]["ok"] is True and set(sent[1][0]) <= {"check_id", "time", "ok", "response_ms", "code", "detail", "cert_days"}
    assert runner.next_run[1] > time.monotonic() + 50   # next run after the interval


def test_results_that_couldnt_be_sent_wait_for_the_next_report(monkeypatch):
    runner = collector.CheckRunner("http://dashboard/api", (3, "token"))
    runner.pending.extend([{"check_id": 1, "ok": True, "time": "t"}] * 3)

    def failing(*args):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(collector, "send_check_results", failing)

    assert runner.report() is False
    assert len(runner.pending) == 3


def test_checks_from_the_dashboard_are_validated_and_capped():
    runner = collector.CheckRunner("http://dashboard/api", (3, "token"))

    runner.set_checks([{**GOOD, "id": index} for index in range(1, 80)] + [{**GOOD, "id": 999, "target": "file:///etc/shadow"}, "junk"])

    assert len(runner.checks) == collector.MAX_CHECKS and 999 not in runner.checks
    runner.set_checks(None)
    assert runner.checks == {} and runner.next_run == {}


def test_a_test_from_the_form_runs_once_at_once_and_goes_back_on_its_own(monkeypatch, site):
    sent = []
    answers = iter([{"checks": [], "tests": [{**GOOD, "id": 41, "target": f"{site}/broken"}], "idle_every": 10}, {"checks": [], "tests": [], "idle_every": 10}])

    def fake_send(api_url, server_id, token, results, tests=None):
        sent.append((list(results), list(tests or [])))
        return next(answers)

    monkeypatch.setattr(collector, "send_check_results", fake_send)
    runner = collector.CheckRunner("http://dashboard/api", (3, "token"))

    runner.report()
    deadline = time.monotonic() + 5
    while not runner.test_results and time.monotonic() < deadline:
        time.sleep(0.05)
    runner.report()

    assert runner.idle_every == 10 and runner.checks == {}     # a test is not a check: it isn't kept or repeated
    assert sent[1][0] == [] and [(item["check_id"], item["ok"], item["detail"]) for item in sent[1][1]] == [(41, False, "HTTP 503")]


def test_a_test_that_isnt_well_formed_is_not_run(monkeypatch):
    runner = collector.CheckRunner("http://dashboard/api", (3, "token"))
    submitted = []
    monkeypatch.setattr(runner.pool, "submit", lambda *args: submitted.append(args) or type("Future", (), {"add_done_callback": lambda self, done: None})())

    runner.run_tests([{**GOOD, "target": "file:///etc/shadow"}, "junk", None] + [{**GOOD, "id": index} for index in range(1, 10)])

    assert len(submitted) == 2   # only the first five are looked at, and three of those aren't checks


# --- TLS, DNS, JSON and ping validation + parsing ----------------------------------------------------

import struct  # noqa: E402


@pytest.mark.parametrize("raw, expect", [
    ({"type": "tls", "target": "mail.example.com:993", "warn_days": 30}, {"host": "mail.example.com", "port": 993, "warn_days": 30}),
    ({"type": "tls", "target": "example.com", "warn_days": 14}, {"host": "example.com", "port": 443}),
    ({"type": "ping", "target": "10.0.0.1"}, {"host": "10.0.0.1"}),
    ({"type": "dns", "target": "deck.home.lan", "record_type": "A", "resolver": "10.0.0.12", "expected": "x"}, {"record_type": "A", "resolver": "10.0.0.12"}),
    ({"type": "json", "target": "http://h/api", "accepted_codes": ["200-299"], "json_path": "a.b", "json_op": "equals", "json_expect": "ok"}, {"json_path": "a.b", "json_op": "equals", "method": "GET"}),
])
def test_validate_check_accepts_the_new_types(raw, expect):
    result = collector.validate_check({"id": 1, "interval": 60, "retry_interval": 20, "timeout": 10, **raw})
    assert result is not None
    for key, value in expect.items():
        assert result[key] == value


@pytest.mark.parametrize("raw", [
    {"type": "ping", "target": "10.0.0.1:22"}, {"type": "ping", "target": "a b"},
    {"type": "tls", "target": "10.0.0.1:70000"}, {"type": "tls", "target": "example.com"},   # missing warn_days
    {"type": "dns", "target": "deck.home.lan", "record_type": "SRV"}, {"type": "dns", "target": "deck.home.lan", "record_type": "A", "resolver": "nope"},
    {"type": "json", "target": "http://h/api", "accepted_codes": ["200"], "json_path": "a", "json_op": "bad", "json_expect": "x"},
])
def test_validate_check_rejects_malformed_new_types(raw):
    base = {"id": 1, "interval": 60, "retry_interval": 20, "timeout": 10}
    if raw["type"] == "tls" and "warn_days" not in raw:
        pass   # tls without warn_days should be rejected
    assert collector.validate_check({**base, **raw}) is None


def test_json_walk_reaches_nested_values_and_raises_when_missing():
    data = {"data": {"status": "ok"}, "items": [{"ok": True}, {"ok": False}]}
    assert collector._json_walk(data, "data.status") == "ok"
    assert collector._json_walk(data, "items[1].ok") is False
    assert collector._json_walk(data, "items.0.ok") is True
    with pytest.raises((KeyError, IndexError, TypeError)):
        collector._json_walk(data, "data.missing")


@pytest.mark.parametrize("value, op, expect, result", [
    ("ok", "equals", "OK", True), (True, "equals", "true", True), (5, "equals", "6", False),
    ("hello world", "contains", "world", True), (5, "gt", "3", True), (5, "lt", "3", False),
    ("not a number", "gt", "3", False), (None, "exists", "", True),
])
def test_json_match_compares_safely(value, op, expect, result):
    assert collector._json_match(value, op, expect) is result


def _dns_packet(ip="93.184.216.34", rcode=0, ancount=1):
    header = struct.pack(">HHHHHH", 0x1234, 0x8180 | rcode, 1, ancount, 0, 0)
    question = b"\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
    answer = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 300, 4) + bytes(int(part) for part in ip.split("."))
    return header + question + (answer if ancount else b"")


def test_parse_dns_reads_an_a_record_and_flags_nxdomain():
    assert collector._parse_dns(_dns_packet(), 1) == ["93.184.216.34"]
    assert collector._parse_dns(_dns_packet(ancount=0), 1) == []
    with pytest.raises(collector._DnsError):
        collector._parse_dns(_dns_packet(rcode=3), 1)


def test_system_resolver_reads_resolv_conf(tmp_path, monkeypatch):
    conf = tmp_path / "resolv.conf"
    conf.write_text("# comment\nnameserver 10.0.0.12\nnameserver 1.1.1.1\n")
    monkeypatch.setattr(collector, "Path", lambda _p: conf)
    assert collector._system_resolver() == "10.0.0.12"


def test_ping_returns_a_clean_result_whether_or_not_icmp_is_permitted():
    result = collector.run_ping({"host": "127.0.0.1", "timeout": 2})
    assert isinstance(result["ok"], bool) and set(result) == {"ok", "response_ms", "code", "detail", "cert_days"}
    if not result["ok"]:
        assert result["detail"]


def test_run_check_dispatches_by_type(monkeypatch):
    monkeypatch.setattr(collector, "run_tls", lambda c: {"ok": True, "response_ms": 1, "code": None, "detail": None, "cert_days": 40})
    out = collector.run_check({"id": 7, "type": "tls", "host": "h", "port": 443, "timeout": 5, "warn_days": 14})
    assert out["check_id"] == 7 and out["ok"] is True and out["cert_days"] == 40


def test_validate_check_rejects_credentials_in_a_url():
    base = {"id": 1, "interval": 60, "retry_interval": 20, "timeout": 10, "accepted_codes": ["200-299"]}
    assert collector.validate_check({**base, "type": "http", "target": "https://user:pass@host/"}) is None
    assert collector.validate_check({**base, "type": "http", "target": "https://host/?token=x"}) is not None
