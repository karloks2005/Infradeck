"""A notification connects to the address that was checked, not to whatever the channel's host name resolves to a
moment later, so DNS rebinding can't turn a public-looking channel into a request to this machine or the LAN."""
import http.server
import socket
import threading

import pytest

from src.backend import notify


class _Hook(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):  # noqa: N802
        self.seen.append(self.headers["Host"])
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def hook():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Hook.seen = []
    yield server.server_address[1]
    server.shutdown()


def test_the_post_goes_to_the_checked_address_without_resolving_the_name_again(monkeypatch, hook):
    real = socket.getaddrinfo

    def no_second_lookup(host, *args, **kwargs):
        assert host != "rebind.example", "the host name was resolved again at connect time"
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", no_second_lookup)

    worked, status = notify._post(f"http://rebind.example:{hook}/hook", b"{}", {"Content-Type": "application/json"}, address="127.0.0.1")

    assert (worked, status) == (True, "204")
    assert _Hook.seen == [f"rebind.example:{hook}"]   # the name still goes in the Host header


def test_a_name_that_changes_its_answer_is_resolved_once_and_the_checked_answer_is_used(monkeypatch):
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    lookups, used = [], []
    monkeypatch.setattr(notify, "resolve", lambda host, port: lookups.append(host) or next(answers))
    monkeypatch.setattr(notify, "_post", lambda url, data, headers, address: used.append(address) or (True, "200"))

    assert notify.send("webhook", "http://rebind.example/hook", "Disk full", "body") == (True, "200")

    assert lookups == ["rebind.example"] and used == ["93.184.216.34"]


def test_https_keeps_the_host_name_for_sni_and_the_certificate_check():
    connection = notify._PinnedHTTPS("93.184.216.34").connection("ntfy.example", 443)

    assert connection.host == "ntfy.example"


def test_a_proxy_from_the_environment_is_not_used(monkeypatch, hook):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")   # nothing listens there; using it would fail

    assert notify._post(f"http://rebind.example:{hook}/hook", b"{}", {}, address="127.0.0.1") == (True, "204")
