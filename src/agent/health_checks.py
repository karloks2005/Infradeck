"""Fixed, read-only health checks exposed to the AI service.

This module deliberately does not invoke a shell. New checks must be added to
``HealthCheckRunner.ALLOWED_CHECKS`` so the agent cannot select arbitrary work.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib import error, request


class AdminNotifier:
    """Optionally delivers critical findings to an administrator webhook."""

    def notify(self, finding: dict[str, Any]) -> dict[str, str]:
        webhook_url = os.getenv("ADMIN_ALERT_WEBHOOK_URL")
        if not webhook_url:
            return {"status": "not_configured"}
        webhook_request = request.Request(
            webhook_url,
            data=json.dumps({"source": "homelab-health", "finding": finding}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(webhook_request, timeout=5) as response:  # nosec B310 - admin-controlled URL
                return {"status": "sent"} if 200 <= response.status < 300 else {"status": "failed", "detail": f"HTTP {response.status}"}
        except (error.URLError, TimeoutError, OSError) as exc:
            return {"status": "failed", "detail": type(exc).__name__}


class HealthCheckRunner:
    """A closed registry of safe, predefined health checks."""

    ALLOWED_CHECKS = frozenset({"server_health", "container_health", "daily_checkup"})

    def __init__(self, notifier: AdminNotifier | None = None):
        self.notifier = notifier or AdminNotifier()
        self._checks: dict[str, Callable[[], dict[str, Any]]] = {
            "server_health": self.server_health,
            "container_health": self.container_health,
            "daily_checkup": self.daily_checkup,
        }

    def run(self, check_name: str) -> dict[str, Any]:
        """Run one check from the closed registry, with no user/model arguments."""
        if check_name not in self.ALLOWED_CHECKS:
            raise ValueError("Requested health check is not allowed.")
        return self._checks[check_name]()

    def server_health(self) -> dict[str, Any]:
        disk = shutil.disk_usage("/")
        disk_percent = round((disk.used / disk.total) * 100, 1) if disk.total else 0.0
        meminfo = self._read_meminfo()
        available_kib, total_kib = meminfo.get("MemAvailable", 0), meminfo.get("MemTotal", 0)
        memory_percent = round((1 - available_kib / total_kib) * 100, 1) if total_kib else None
        load_1m = round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None
        status = "critical" if disk_percent >= 95 or (memory_percent is not None and memory_percent >= 95) else "warning" if disk_percent >= 85 or (memory_percent is not None and memory_percent >= 85) else "ok"
        return {"check": "server_health", "status": status, "observed_at": int(time.time()), "metrics": {"load_1m": load_1m, "disk_used_percent": disk_percent, "memory_used_percent": memory_percent}}

    def container_health(self) -> dict[str, Any]:
        """Inspect Docker's local Unix socket without calling the Docker CLI."""
        try:
            containers = self._docker_get("/containers/json?all=1")
        except (OSError, ValueError) as exc:
            return {"check": "container_health", "status": "unknown", "observed_at": int(time.time()), "detail": f"Docker API unavailable: {type(exc).__name__}", "containers": []}
        unhealthy, stopped = [], []
        for container in containers:
            name = (container.get("Names") or [container.get("Id", "unknown")[:12]])[0].lstrip("/")
            if "(unhealthy)" in str(container.get("Status", "")).lower():
                unhealthy.append(name)
            elif str(container.get("State", "unknown")) != "running":
                stopped.append(name)
        status = "critical" if unhealthy else "warning" if stopped else "ok"
        return {"check": "container_health", "status": status, "observed_at": int(time.time()), "containers": {"total": len(containers), "unhealthy": unhealthy, "not_running": stopped}}

    def daily_checkup(self) -> dict[str, Any]:
        """Run both checks and alert the configured admin for critical findings."""
        results = [self.server_health(), self.container_health()]
        critical = [result for result in results if result["status"] == "critical"]
        alert = self.notifier.notify({"checks": critical}) if critical else {"status": "not_needed"}
        overall = "critical" if critical else "warning" if any(r["status"] in {"warning", "unknown"} for r in results) else "ok"
        return {"check": "daily_checkup", "status": overall, "observed_at": int(time.time()), "results": results, "alert": alert}

    @staticmethod
    def _read_meminfo() -> dict[str, int]:
        try:
            lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        return {key.rstrip(":"): int(value.split()[0]) for line in lines if ":" in line for key, value in [line.split(":", 1)] if value.split() and value.split()[0].isdigit()}

    @staticmethod
    def _docker_get(path: str) -> list[dict[str, Any]]:
        """Minimal Docker Engine API GET over its local Unix-domain socket."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock"))
            client.sendall(f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode("ascii"))
            chunks = []
            while chunk := client.recv(65536):
                chunks.append(chunk)
        header, _, body = b"".join(chunks).partition(b"\r\n\r\n")
        if b" 200 " not in header.split(b"\r\n", 1)[0]:
            raise OSError("Docker API returned a non-success response")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Docker API returned an unexpected payload")
        return payload
