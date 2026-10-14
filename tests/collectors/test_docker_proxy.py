import http.client
import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from src.collector import collector, docker_proxy as proxy

CID = "a" * 64
SECRET = "SUPER_SECRET_TOKEN_123"

INSPECT = {
    "Id": CID, "Name": "/web", "RestartCount": 2, "Image": "sha256:" + "ab" * 32,
    "Config": {"Image": "nginx:1", "Env": [f"DB_PASSWORD={SECRET}"], "Labels": {"k": "v", "secret": SECRET, "com.docker.compose.project": "shop"}, "Cmd": ["nginx"]},
    "State": {"Status": "running", "StartedAt": "2026-01-01T00:00:00Z", "Pid": 99, "ExitCode": 137, "OOMKilled": True, "FinishedAt": "2026-01-02T03:04:05.123456789Z", "Error": f"failed: {SECRET}", "Health": {"Status": "healthy", "Log": [{"Output": SECRET}]}},
    "NetworkSettings": {"Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}, "SecondaryIPAddresses": None, "Networks": {"x": {"Gateway": "1"}}},
    "Mounts": [{"Type": "bind", "Source": "/srv/web", "Destination": "/data", "Mode": SECRET, "RW": True}, {"Type": "volume", "Name": "web_cache", "Source": "/var/lib/docker/volumes/web_cache/_data", "Destination": "/cache", "Driver": "local"}, "junk"], "HostConfig": {"Privileged": True}, "GraphDriver": {"Data": {}},
}
LISTING = [{"Id": CID, "Names": ["/web"], "Image": "nginx:1", "State": "running", "Labels": {"secret": SECRET}, "Mounts": []}]
STATS = {"cpu_stats": {"cpu_usage": {"total_usage": 10}, "system_cpu_usage": 100, "online_cpus": 2}, "precpu_stats": {"cpu_usage": {"total_usage": 5}, "system_cpu_usage": 50},
         "memory_stats": {"usage": 1048576, "limit": 2097152, "stats": {"inactive_file": 0}}, "networks": {"eth0": {"rx_bytes": 1, "tx_bytes": 2}},
         "blkio_stats": {"io_service_bytes_recursive": []}, "pids_stats": {"current": 3}, "name": "/web", "id": CID}


# What Docker returns for one-shot: the same readings but no earlier one to compare with.
ONE_SHOT_STATS = {**STATS, "precpu_stats": {}}


@pytest.fixture(autouse=True)
def _fresh_collector_state():
    collector._cpu_previous.clear()
    collector._one_shot_unsupported = False
    yield
    collector._cpu_previous.clear()
    collector._one_shot_unsupported = False


# --- routing: the allow-list ---------------------------------------------------------------

@pytest.mark.parametrize("target", [
    "/containers/json?all=1", f"/containers/{CID}/json", f"/containers/{CID}/stats?stream=false", f"/containers/{CID[:12]}/json",
    f"/containers/{CID}/stats?stream=false&one-shot=true", "/version", "/system/df",
])
def test_the_six_read_requests_are_allowed(target):
    assert proxy.route("GET", target) is not None


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "get", "CONNECT"])
def test_only_get_is_allowed(method):
    assert proxy.route(method, "/containers/json?all=1") is None


@pytest.mark.parametrize("target", [
    # other endpoints, including the ones that read container contents or start things
    "/containers/json",   # only the full list the collector asks for (?all=1) is allowed
    "/", "/version?x=1", "/v1.43/version", "/info", "/images/json", "/volumes", "/networks", "/system/df?type=image", "/system/info", "/events", "/_ping", "/containers", "/containers/",
    f"/containers/{CID}/export", f"/containers/{CID}/archive?path=/etc/passwd", f"/containers/{CID}/logs", f"/containers/{CID}/top",
    f"/containers/{CID}/attach", f"/containers/{CID}/exec", f"/containers/{CID}/start", f"/containers/{CID}/kill", f"/containers/{CID}/changes",
    "/containers/create", "/containers/prune", "/exec/abc/start", "/build", "/commit", "/swarm", "/secrets", "/configs",
    # query abuse
    "/containers/json?all=1&filters=x", "/containers/json?all=0", "/containers/json?all=1&all=1", "/containers/json?size=1", "/containers/json?",
    f"/containers/{CID}/stats", f"/containers/{CID}/stats?stream=true", f"/containers/{CID}/stats?stream=false&one-shot=1", f"/containers/{CID}/json?size=1",
    # one-shot: exactly one spelling, in exactly one order
    f"/containers/{CID}/stats?stream=false&one-shot=false", f"/containers/{CID}/stats?stream=false&one-shot=", f"/containers/{CID}/stats?stream=false&one-shot",
    f"/containers/{CID}/stats?one-shot=true&stream=false", f"/containers/{CID}/stats?one-shot=true", f"/containers/{CID}/stats?stream=true&one-shot=true",
    f"/containers/{CID}/stats?stream=false&one-shot=true&one-shot=true", f"/containers/{CID}/stats?stream=false&one-shot=true&stream=true",
    f"/containers/{CID}/stats?stream=false&one-shot=True", f"/containers/{CID}/stats?stream=false&one-shot=TRUE", f"/containers/{CID}/stats?stream=false&One-Shot=true",
    f"/containers/{CID}/stats?stream=false&one-shot=true&", f"/containers/{CID}/stats?stream=false&one-shot=true&x=1", f"/containers/{CID}/stats?stream=false&&one-shot=true",
    f"/containers/{CID}/stats?stream=false&one-shot=true#f", f"/containers/{CID}/stats?stream=false&one-shot=true ", f"/containers/{CID}/stats?stream=false&one-shot=%74rue",
    # traversal, encoding, padding
    f"/containers/{CID}/../../images/json", "/containers/../images/json", f"/containers/{CID}/json/..", "//containers/json", "/containers//json",
    "/containers/json/", f"/containers/{CID}/json/", "/containers/%6Ason", "/containers/json%00", f"/containers/{CID}%2fjson", "/%2e%2e/containers/json",
    "/containers/json#frag", "/containers/json ", " /containers/json", "/containers/json\r\nX: y", "/containers/json\n", "/containers/jsón",
    "/v1.43/containers/json", "http://localhost/containers/json", "*",
    # ids that are not plain hex
    "/containers/web/json", "/containers/ABC123ABC123/json", "/containers/abc/json", f"/containers/{'a' * 65}/json", "/containers/zzzzzzzzzzzz/json", "/containers/;rm/json",
])
def test_everything_else_is_refused(target):
    assert proxy.route("GET", target) is None


# --- response filtering --------------------------------------------------------------------

def test_inspect_output_drops_environment_and_everything_unneeded():
    result = proxy.project_inspect(INSPECT)

    assert SECRET not in json.dumps(result)
    assert result == {
        "Id": CID, "Name": "/web", "RestartCount": 2, "Image": "sha256:" + "ab" * 32, "Config": {"Image": "nginx:1", "Labels": {"com.docker.compose.project": "shop"}},
        # Of a mount only where it comes from and where it lands; of the labels only the compose project.
        "Mounts": [{"Type": "bind", "Source": "/srv/web", "Destination": "/data"}, {"Type": "volume", "Name": "web_cache", "Source": "/var/lib/docker/volumes/web_cache/_data", "Destination": "/cache"}],
        "State": {"Status": "running", "StartedAt": "2026-01-01T00:00:00Z", "ExitCode": 137, "OOMKilled": True, "FinishedAt": "2026-01-02T03:04:05.123456789Z", "Health": {"Status": "healthy"}},
        "NetworkSettings": {"Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}},
    }


def test_version_output_keeps_only_the_version_numbers():
    assert proxy.project_version({"Version": "27.3.1", "ApiVersion": "1.47", "KernelVersion": "6.8.0", "Os": "linux", "Components": [{"Details": {"x": SECRET}}]}) == {"Version": "27.3.1", "ApiVersion": "1.47"}
    assert proxy.project_version(None) == {}


def test_disk_usage_is_reduced_to_totals_with_no_names():
    usage = {
        "LayersSize": 9_000, "Images": [
            {"Id": "sha256:a", "RepoTags": [SECRET], "Size": 5_000, "SharedSize": 1_000, "Containers": 2},
            {"Id": "sha256:b", "RepoTags": ["old:1"], "Size": 4_000, "SharedSize": 1_000, "Containers": 0},
            {"Id": "sha256:c", "Size": 500, "SharedSize": 900, "Containers": 0}, "junk",
        ],
        "Containers": [{"Names": [SECRET]}], "Volumes": [{"Name": SECRET, "UsageData": {"Size": 7}}],
        "BuildCache": [{"ID": SECRET, "Size": 300, "InUse": True, "Shared": False}, {"Size": 200, "InUse": False, "Shared": False}, {"Size": 999, "InUse": False, "Shared": True}],
    }
    result = proxy.project_disk_usage(usage)

    assert SECRET not in json.dumps(result)
    assert result == {"images_bytes": 9_000, "images_unused_bytes": 3_000, "images_unused": 2, "images": 3, "build_cache_bytes": 500, "build_cache_unused_bytes": 200}
    assert proxy.project_disk_usage([]) == {} and proxy.project_disk_usage({"Images": "x", "LayersSize": -5})["images_bytes"] == 0


def test_list_and_stats_output_are_cut_down():
    assert SECRET not in json.dumps(proxy.project_list(LISTING))
    assert proxy.project_list(LISTING) == [{"Id": CID, "Names": ["/web"], "Image": "nginx:1", "State": "running"}]
    assert set(proxy.project_stats(STATS)) == {"cpu_stats", "precpu_stats", "memory_stats", "networks", "blkio_stats", "pids_stats"}


@pytest.mark.parametrize("junk", [None, 5, "x", [], {}, [1, "a", None], {"State": "bad", "Config": 3, "NetworkSettings": []}])
def test_unexpected_upstream_shapes_do_not_crash_the_filters(junk):
    proxy.project_list(junk), proxy.project_inspect(junk), proxy.project_stats(junk)


# --- end to end: collector -> proxy -> fake Docker daemon ----------------------------------

class _FakeDocker(BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):
        _FakeDocker.seen.append(self.path)
        routes = {"/containers/json?all=1": LISTING, f"/containers/{CID}/json": INSPECT, f"/containers/{CID}/stats?stream=false": STATS,
                  f"/containers/{CID}/stats?stream=false&one-shot=true": ONE_SHOT_STATS}
        body = json.dumps(routes.get(self.path)).encode() if self.path in routes else b"nope"
        self.send_response(200 if self.path in routes else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_DELETE = do_GET

    def log_message(self, *args):
        pass


@pytest.fixture
def stack(tmp_path):
    socket_path = str(tmp_path / "docker.sock")
    daemon = socketserver.ThreadingUnixStreamServer(socket_path, _FakeDocker)
    daemon.daemon_threads = True
    _FakeDocker.seen = []
    listen_path = str(tmp_path / "proxy" / "docker.sock")
    server = proxy.make_server(listen_path, socket_path)
    for item in (daemon, server):
        threading.Thread(target=item.serve_forever, daemon=True).start()
    yield listen_path, daemon
    for item in (server, daemon):
        item.shutdown()
        item.server_close()


def _raw(listen_path, method, path):
    connection = collector._UnixHTTPConnection(listen_path)
    connection.request(method, path)
    response = connection.getresponse()
    return response.status, response.read()


def test_collector_works_through_the_proxy_and_never_sees_secrets(stack):
    port, _ = stack

    [container] = collector.collect_containers(port)

    assert (container["name"], container["image"], container["status"], container["restart_count"], container["healthcheck_status"]) == ("web", "nginx:1", "running", 2, "healthy")
    assert container["ports"] == "8080->80/tcp" and container["memory_used_mb"] == 1.0 and container["pids"] == 3
    assert container["project"] == "shop" and container["volumes"] == "/srv/web, web_cache"
    assert SECRET not in json.dumps(container)


def test_the_one_shot_request_is_passed_on_and_answers_are_cut_down(stack):
    port, _ = stack

    status, body = _raw(port, "GET", f"/containers/{CID}/stats?stream=false&one-shot=true")

    assert status == 200 and _FakeDocker.seen == [f"/containers/{CID}/stats?stream=false&one-shot=true"]
    assert set(json.loads(body)) == {"cpu_stats", "precpu_stats", "memory_stats", "networks", "blkio_stats", "pids_stats"}


def test_cpu_comes_from_the_change_between_two_collections_through_the_proxy(stack):
    port, _ = stack

    [first] = collector.collect_containers(port)
    [second] = collector.collect_containers(port)

    assert "cpu_pct" not in first and "cpu_pct" not in second  # the fake daemon reports identical counters twice
    assert collector._cpu_previous == {CID: (10, 100)} and not collector._one_shot_unsupported
    assert f"/containers/{CID}/stats?stream=false" not in _FakeDocker.seen  # only the one-shot form was used


def test_refused_requests_never_reach_the_docker_daemon(stack):
    port, _ = stack
    attacks = [("POST", "/containers/create"), ("DELETE", f"/containers/{CID}"), ("GET", f"/containers/{CID}/archive?path=/etc"), ("GET", "/containers/../info"),
               ("GET", f"/containers/{CID}/json?size=1"),
               ("GET", f"/containers/{CID}/stats?one-shot=true&stream=false"), ("GET", f"/containers/{CID}/stats?stream=false&one-shot=false"), ("POST", f"/containers/{CID}/start"), ("GET", "/containers/%6Ason")]
    for method, path in attacks:
        assert _raw(port, method, path)[0] == 403, (method, path)

    assert _FakeDocker.seen == []


def test_upstream_failures_become_a_generic_502(stack):
    port, daemon = stack
    status, body = _raw(port, "GET", f"/containers/{'b' * 64}/json")  # fake daemon answers 404
    assert (status, body) == (502, b"Upstream error")

    daemon.shutdown()
    daemon.server_close()
    assert _raw(port, "GET", "/containers/json?all=1")[0] == 502


def test_oversized_upstream_answers_are_rejected(stack, monkeypatch):
    port, _ = stack
    monkeypatch.setattr(proxy, "MAX_RESPONSE_BYTES", 10)
    assert _raw(port, "GET", "/containers/json?all=1")[0] == 502


def test_proxy_socket_is_not_world_accessible_and_replaces_a_stale_one(tmp_path):
    listen = tmp_path / "run" / "docker.sock"
    listen.parent.mkdir()
    listen.write_text("stale")
    server = proxy.make_server(str(listen), str(tmp_path / "unused"))
    try:
        assert listen.stat().st_mode & 0o777 == 0o660
    finally:
        server.server_close()


def test_collector_still_supports_the_plain_socket_path(tmp_path):
    assert collector.collect_containers(str(tmp_path / "missing.sock")) == []
