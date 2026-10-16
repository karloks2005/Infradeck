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

    def fake_send(api_url, server_id, token, results):
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
