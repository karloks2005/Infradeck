"""Read-only Docker API proxy for the collector.

The Docker socket is all-or-nothing: whatever can talk to it can start a privileged container,
which is root on the host. Only this proxy mounts the socket. It listens on a Unix socket in a
volume that only the collector shares (no network port at all), and answers exactly six kinds of request:

    GET /containers/json?all=1
    GET /containers/<id>/json
    GET /containers/<id>/stats?stream=false
    GET /containers/<id>/stats?stream=false&one-shot=true
    GET /version
    GET /system/df

The one-shot request returns immediately instead of waiting a second for Docker to take a second CPU reading.

Everything else is refused, and the answers are cut down to the fields the collector uses: the
inspect output is where a container's environment variables (often secrets) live, and they never
leave this process. Paths are matched literally, with no decoding or normalisation, so there is
nothing for traversal or encoding tricks to work on.

Standard library only. Run with ``python -m src.collector.docker_proxy`` or as a standalone script.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable

# Installs from before the rename to Infradeck set HOMELAB_* variables; they keep working unchanged.
for _name, _value in list(os.environ.items()):
    if _name.startswith("HOMELAB_"):
        os.environ.setdefault("INFRADECK_" + _name[len("HOMELAB_"):], _value)

SOCKET_PATH = os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
LISTEN_PATH = os.getenv("INFRADECK_DOCKER_PROXY_SOCKET", "/run/docker-proxy/docker.sock")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
UPSTREAM_TIMEOUT_SECONDS = 10
# A stalled client is dropped after this long without sending anything, and only this many connections are served
# at once (the collector sends one request at a time), so no client can tie the proxy up.
CLIENT_TIMEOUT_SECONDS = 30
MAX_CONNECTIONS = 16

_ID = r"[0-9a-f]{12,64}"
# Matched against the whole request target, query string included.
_LIST = re.compile(r"/containers/json\?all=1")
_INSPECT = re.compile(rf"/containers/{_ID}/json")
_STATS = re.compile(rf"/containers/{_ID}/stats\?stream=false(&one-shot=true)?")
_VERSION = re.compile(r"/version")
_DISK_USAGE = re.compile(r"/system/df")
# The one label that passes: which compose project a container belongs to, so the dashboard can group them.
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


def _pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: source[key] for key in keys if isinstance(source, dict) and key in source}


def project_list(data: Any) -> list[dict[str, Any]]:
    return [_pick(item, ("Id", "Names", "Image", "State")) for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def project_inspect(data: Any) -> dict[str, Any]:
    """The inspect fields the collector reads. Config.Env, every label but the compose project, and the rest are dropped.

    Of a mount only where it comes from and where it lands passes (paths and volume names, never contents or options).
    """
    if not isinstance(data, dict):
        return {}
    state = data.get("State") if isinstance(data.get("State"), dict) else {}
    health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
    network = data.get("NetworkSettings") if isinstance(data.get("NetworkSettings"), dict) else {}
    config = data.get("Config") if isinstance(data.get("Config"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    mounts = data.get("Mounts") if isinstance(data.get("Mounts"), list) else []
    return {
        # Image is the ID of the image the container runs. State.Error is free text, so it stays out.
        **_pick(data, ("Id", "Name", "RestartCount", "Image")),
        "Config": {**_pick(config, ("Image",)), "Labels": _pick(labels, (COMPOSE_PROJECT_LABEL,))},
        "Mounts": [_pick(mount, ("Type", "Name", "Source", "Destination")) for mount in mounts if isinstance(mount, dict)],
        "State": {**_pick(state, ("Status", "StartedAt", "ExitCode", "OOMKilled", "FinishedAt")), "Health": _pick(health, ("Status",))},
        "NetworkSettings": _pick(network, ("Ports",)),
    }


def project_version(data: Any) -> dict[str, Any]:
    """Only the daemon's version numbers; the rest of /version describes the host."""
    return _pick(data, ("Version", "ApiVersion"))


def _size(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 2**62 else 0


def project_disk_usage(data: Any) -> dict[str, int]:
    """How much disk Docker's images and build cache take and how much of it nothing uses: six numbers.

    /system/df lists every image, container, volume and build step by name; none of those leave the proxy.
    """
    if not isinstance(data, dict):
        return {}
    images = [item for item in data.get("Images") or [] if isinstance(item, dict)] if isinstance(data.get("Images"), list) else []
    cache = [item for item in data.get("BuildCache") or [] if isinstance(item, dict)] if isinstance(data.get("BuildCache"), list) else []
    unused = [item for item in images if item.get("Containers") == 0]
    return {
        "images_bytes": _size(data.get("LayersSize")),
        # What only that image holds: layers it shares with an image still in use stay after a prune.
        "images_unused_bytes": sum(max(0, _size(item.get("Size")) - _size(item.get("SharedSize"))) for item in unused),
        "images_unused": len(unused),
        "images": len(images),
        "build_cache_bytes": sum(_size(item.get("Size")) for item in cache if not item.get("Shared")),
        "build_cache_unused_bytes": sum(_size(item.get("Size")) for item in cache if not item.get("Shared") and not item.get("InUse")),
    }


def project_stats(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return _pick(data, ("cpu_stats", "precpu_stats", "memory_stats", "networks", "blkio_stats", "pids_stats"))


def route(method: str, target: str) -> tuple[str, Callable[[Any], Any]] | None:
    """The upstream request and response filter for an allowed request, or None to refuse it."""
    if method != "GET" or not target.isascii() or not target.isprintable():
        return None
    for pattern, project in ((_LIST, project_list), (_INSPECT, project_inspect), (_STATS, project_stats), (_VERSION, project_version), (_DISK_USAGE, project_disk_usage)):
        if pattern.fullmatch(target):
            return target, project
    return None


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost", timeout=UPSTREAM_TIMEOUT_SECONDS)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "infradeck-docker-proxy"
    socket_path = SOCKET_PATH
    timeout = CLIENT_TIMEOUT_SECONDS

    def _send(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        allowed = route(self.command, self.path)
        if allowed is None:
            print(f"Refused {self.command} {self.path[:80]!r}", flush=True)
            self._send(403, b"Forbidden")
            return
        target, project = allowed
        try:
            connection = _UnixHTTPConnection(self.socket_path)
            try:
                connection.request("GET", target, headers={"Host": "localhost", "Connection": "close"})
                response = connection.getresponse()
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
            finally:
                connection.close()
            if status != 200 or len(body) > MAX_RESPONSE_BYTES:
                self._send(502, b"Upstream error")
                return
            self._send(200, json.dumps(project(json.loads(body))).encode(), "application/json")
        except (OSError, ValueError, http.client.HTTPException):
            self._send(502, b"Upstream error")

    # Every method goes through the same check, so anything but an allowed GET is refused.
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - quiet by default; refusals are logged above
        pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request: Any, client_address: Any) -> None:
        """Closes a connection beyond MAX_CONNECTIONS straight away instead of starting a thread for it."""
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_server(listen_path: str = LISTEN_PATH, socket_path: str = SOCKET_PATH) -> socketserver.ThreadingUnixStreamServer:
    """Listen on a Unix socket only readable and writable by its owner (and group)."""
    listen = Path(listen_path)
    listen.parent.mkdir(parents=True, exist_ok=True)
    listen.unlink(missing_ok=True)
    handler = type("BoundProxyHandler", (ProxyHandler,), {"socket_path": socket_path})
    previous = os.umask(0o117)  # created as 0660, never briefly world-accessible
    try:
        server = _Server(str(listen), handler)
    finally:
        os.umask(previous)
    return server


if __name__ == "__main__":
    print(f"Docker read-only proxy listening on {LISTEN_PATH}, upstream {SOCKET_PATH}", flush=True)
    make_server().serve_forever()
