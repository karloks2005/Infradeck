"""Read-only host and Docker metrics collector for a registered Homelab server.

Only HOMELAB_API_URL is required. On first start the collector asks to join and
appears on the dashboard under "New servers detected"; once someone clicks Add
it receives its token (no restart) and saves it under HOMELAB_STATE_DIR.
HOMELAB_JOIN_TOKEN (from the dashboard's Connect a server command) proves who asked it to join.
HOMELAB_ENROLL_KEY skips the approval step for automated installs, and
HOMELAB_SERVER_ID / HOMELAB_COLLECTOR_TOKEN still work as explicit credentials.
Run with ``python -m src.agent.collector`` or as a standalone script (standard
library only; it ships as a container image). Docker access is optional; when
the socket is unavailable, host metrics continue to be sent.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import http.client
import json
import os
import platform
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

MiB = 1024 * 1024
GiB = 1024**3
# In a container the host is mounted at /host/proc and /hostfs; fall back to our own view otherwise.
HOST_PROC = Path(os.getenv("HOST_PROC") or ("/host/proc" if Path("/host/proc").is_dir() else "/proc"))
HOST_ROOT = Path(os.getenv("HOST_ROOT") or ("/hostfs" if Path("/hostfs").is_dir() else "/"))


def _cpu_times() -> tuple[int, int]:
    values = [int(value) for value in (HOST_PROC / "stat").read_text().splitlines()[0].split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _memory() -> dict[str, float]:
    values: dict[str, int] = {}
    for line in (HOST_PROC / "meminfo").read_text().splitlines():
        key, raw = line.split(":", 1)
        parts = raw.split()
        if parts:
            values[key] = int(parts[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "ram_used_gb": round((total - available) / GiB, 3) if total else 0,
        "ram_available_gb": round(available / GiB, 3),
        "ram_pct": round((total - available) * 100 / total, 2) if total else 0,
        "swap_pct": round((swap_total - swap_free) * 100 / swap_total, 2) if swap_total else 0,
    }


def _network_totals() -> tuple[int, int]:
    sent = received = 0
    for line in (HOST_PROC / "net/dev").read_text().splitlines()[2:]:
        if ":" not in line:
            continue
        interface, raw = line.split(":", 1)
        if interface.strip() == "lo":
            continue
        fields = [int(value) for value in raw.split()]
        if len(fields) >= 9:
            received += fields[0]
            sent += fields[8]
    return sent, received


# Filesystems worth reporting: anything backed by a block device, plus these pooled/network ones.
NON_DEVICE_FS_TYPES = {"zfs", "nfs", "nfs4", "cifs", "smb3", "fuse.mergerfs", "fuse.sshfs"}
SKIPPED_FS_TYPES = {"squashfs", "overlay", "tmpfs", "devtmpfs"}
SKIPPED_MOUNT_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/snap", "/var/snap", "/var/lib/docker", "/var/lib/containerd", "/var/lib/kubelet")


def _decode_mount_path(path: str) -> str:
    """/proc mounts escapes spaces, tabs, newlines and backslashes as octal."""
    for escaped, char in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        path = path.replace(escaped, char)
    return path


def _mount_entries() -> list[tuple[str, str, str]]:
    """(source, mount point, fs type) as the host sees them."""
    # PID 1's table is the host's; "self" would be this container's own mounts.
    for candidate in (HOST_PROC / "1/mounts", HOST_PROC / "self/mounts"):
        try:
            lines = candidate.read_text().splitlines()
        except OSError:
            continue
        return [(fields[0], _decode_mount_path(fields[1]), fields[2]) for fields in (line.split() for line in lines) if len(fields) >= 3]
    return []


def collect_mounts() -> list[dict[str, Any]]:
    """Usage of every real filesystem on the host, one entry per device."""
    by_device: dict[tuple[str, int], dict[str, Any]] = {}
    for source, mount, fs_type in _mount_entries():
        if fs_type in SKIPPED_FS_TYPES or not (source.startswith("/dev/") or fs_type in NON_DEVICE_FS_TYPES):
            continue
        if mount != "/" and mount.startswith(SKIPPED_MOUNT_PREFIXES):
            continue
        local_path = HOST_ROOT / mount.lstrip("/")
        # A mount made after the collector started is only an empty directory here; skip it rather
        # than report the parent filesystem under its name.
        if mount != "/" and not os.path.ismount(local_path):
            continue
        try:
            stats = os.statvfs(local_path)
        except OSError:
            continue
        total = stats.f_blocks * stats.f_frsize
        if not total:
            continue
        used = total - stats.f_bfree * stats.f_frsize
        # Space available to non-root users is what fills up, so percentages use it like `df` does.
        usable = used + stats.f_bavail * stats.f_frsize
        entry = {
            "mount": mount[:255],
            "fs_type": fs_type[:64],
            "total_gb": round(total / GiB, 2),
            "used_gb": round(used / GiB, 2),
            "used_pct": round(100 * used / usable, 2) if usable else 0,
            "inodes_pct": round(100 * (stats.f_files - stats.f_ffree) / stats.f_files, 2) if stats.f_files else None,
        }
        # Bind mounts and btrfs subvolumes show the same device several times; keep its shortest path.
        key = (source, total)
        if key not in by_device or len(mount) < len(by_device[key]["mount"]):
            by_device[key] = entry
    return sorted(by_device.values(), key=lambda entry: entry["mount"])


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _oom_kill_total() -> int | None:
    """Processes the kernel OOM killer has stopped since boot (needs Linux 4.13+)."""
    for line in (_read_text(HOST_PROC / "vmstat") or "").splitlines():
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    return None


class _Timex(ctypes.Structure):
    _fields_ = [
        ("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long), ("maxerror", ctypes.c_long),
        ("esterror", ctypes.c_long), ("status", ctypes.c_int), ("constant", ctypes.c_long), ("precision", ctypes.c_long),
        ("tolerance", ctypes.c_long), ("time_sec", ctypes.c_long), ("time_usec", ctypes.c_long), ("tick", ctypes.c_long),
        ("ppsfreq", ctypes.c_long), ("jitter", ctypes.c_long), ("shift", ctypes.c_int), ("stabil", ctypes.c_long),
        ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long), ("stbcnt", ctypes.c_long),
        ("tai", ctypes.c_int), ("padding", ctypes.c_int * 11),
    ]


_TIME_ERROR = 5


def _ntp_synced() -> bool | None:
    """Whether the kernel clock is disciplined by a time service; None when it can't be read."""
    # A read-only adjtimex (modes=0) covers chrony, ntpd and timesyncd alike. Docker's default
    # seccomp profile may refuse it, in which case timesyncd's marker file is the fallback.
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        state = libc.adjtimex(ctypes.byref(_Timex()))
        if state >= 0:
            return state != _TIME_ERROR
    except (OSError, AttributeError):
        pass
    if (HOST_ROOT / "run/systemd/timesyncd/synchronized").exists():
        return True
    return None


def _reboot_required() -> bool | None:
    """Debian and Ubuntu flag pending reboots with a file; other distros aren't checked, so None."""
    if (HOST_ROOT / "run/reboot-required").exists() or (HOST_ROOT / "var/run/reboot-required").exists():
        return True
    return False if (HOST_ROOT / "etc/debian_version").exists() else None


HELPER_FILE_MAX_BYTES = 256 * 1024
HELPER_MAX_AGE_SECONDS = 300


def _helper_path() -> Path:
    return Path(os.getenv("HOMELAB_HELPER_FILE", "/helper/system.json"))


def collect_helper(boot_id: str | None, path: Path | None = None, now: float | None = None) -> dict[str, Any]:
    """Summary from the optional system helper; {} when it isn't installed, stale or from another boot.

    The file is treated as untrusted input: symlinks are refused, size is capped, and only
    expected fields are passed on (the backend validates them again).
    """
    path = path or _helper_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return {}
    try:
        raw = os.read(fd, HELPER_FILE_MAX_BYTES + 1)
    except OSError:
        return {}
    finally:
        os.close(fd)
    try:
        data = json.loads(raw) if len(raw) <= HELPER_FILE_MAX_BYTES else None
        generated = datetime.fromisoformat(data["generated_at"]).timestamp()
    except (ValueError, KeyError, TypeError):
        return {}
    if not isinstance(data, dict) or data.get("boot_id") != boot_id or abs((now if now is not None else time.time()) - generated) > HELPER_MAX_AGE_SECONDS:
        return {}
    events = data.get("events")
    return {"last_shutdown": data.get("last_shutdown"), "helper_events": events if isinstance(events, list) else []}


def collect_system() -> dict[str, Any]:
    """Kernel-reported system state; the optional helper adds what needs log access."""
    tainted = _read_text(HOST_PROC / "sys/kernel/tainted")
    boot_id = _read_text(HOST_PROC / "sys/kernel/random/boot_id")
    return {
        **collect_helper(boot_id),
        "boot_id": boot_id,
        "oom_kill_total": _oom_kill_total(),
        "kernel_tainted": int(tainted) if tainted and tainted.isdigit() else None,
        "ntp_synced": _ntp_synced(),
        "reboot_required": _reboot_required(),
    }


def collect_host() -> tuple[dict[str, Any], dict[str, Any]]:
    before_total, before_idle = _cpu_times()
    time.sleep(0.15)
    after_total, after_idle = _cpu_times()
    elapsed, idle = after_total - before_total, after_idle - before_idle
    cpu_pct = round(100 * (elapsed - idle) / elapsed, 2) if elapsed else 0
    memory = _memory()
    disk = os.statvfs(HOST_ROOT)
    inode_total = disk.f_files
    inode_pct = (100 * (inode_total - disk.f_ffree) / inode_total) if inode_total else None
    disk_used = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
    disk_usable = disk_used + disk.f_bavail * disk.f_frsize
    # Same formula as `df` and the per-mount figures.
    disk_pct = (100 * disk_used / disk_usable) if disk_usable else 0
    try:
        loads = os.getloadavg()
    except (AttributeError, OSError):
        loads = (0.0, 0.0, 0.0)
    sent, received = _network_totals()
    uptime = float((HOST_PROC / "uptime").read_text().split()[0])
    host = {
        "cpu_utilization_pct": cpu_pct,
        "load_1m": round(loads[0], 2), "load_5m": round(loads[1], 2), "load_15m": round(loads[2], 2),
        **memory,
        "disk_usage_pct": round(disk_pct, 2),
        "disk_inodes_pct": round(inode_pct, 2) if inode_pct is not None else None,
        "net_bytes_sent_mb": round(sent / MiB, 3),
        "net_bytes_recv_mb": round(received / MiB, 3),
        "system_uptime_seconds": round(uptime, 1),
        "mounts": collect_mounts(),
        **collect_system(),
    }
    server = {
        "hostname": _host_hostname(),
        "os_info": _host_os_info(),
        "cpu_cores": _host_cpu_cores(),
        "total_ram_gb": round(_memory_total_gb(), 3),
    }
    # Set only on the collector in the dashboard's own compose stack; the dashboard's top charts follow it.
    if os.getenv("HOMELAB_DASHBOARD_HOST", "").lower() in {"1", "true", "yes"}:
        server["dashboard_host"] = True
    return server, host


def _memory_total_gb() -> float:
    for line in (HOST_PROC / "meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024 / GiB
    return 0.0


def _host_hostname() -> str:
    override = os.getenv("HOMELAB_SERVER_NAME")
    if override:
        return override
    try:
        return (HOST_ROOT / "etc/hostname").read_text().strip() or platform.node()
    except OSError:
        return platform.node()


def _host_os_info() -> str:
    try:
        for line in (HOST_ROOT / "etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


def _host_cpu_cores() -> int:
    try:
        count = sum(line.startswith("processor:") for line in (HOST_PROC / "cpuinfo").read_text().splitlines())
        if count:
            return count
    except OSError:
        pass
    return os.cpu_count() or 1


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 3):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self.socket_path)
        self.sock = connection


def _docker_request(socket_path: str, endpoint: str) -> Any:
    connection = _UnixHTTPConnection(socket_path)
    connection.request("GET", endpoint, headers={"Host": "localhost", "Connection": "close"})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    if response.status != 200:
        raise OSError(f"Docker API returned HTTP {response.status}")
    return json.loads(body or b"null")


_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_DOCKER_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
# Containers that have stopped (or are between restarts) carry a meaningful exit status.
_STOPPED = {"exited", "dead", "restarting"}


def _docker_time(value: Any) -> str | None:
    """Docker's nanosecond timestamp cut to whole seconds, or None for the "never" value (year 1)."""
    match = _DOCKER_TIME.match(value) if isinstance(value, str) else None
    return f"{match.group(0)}Z" if match and int(match.group(0)[:4]) >= 2000 else None


def _docker_failure_reason(error: BaseException) -> str:
    """A coarse, fixed-vocabulary reason, never the raw error text."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionRefusedError):
        return "refused"
    if isinstance(error, FileNotFoundError):
        return "missing"
    if isinstance(error, PermissionError):
        return "denied"
    return "error"


def collect_containers(socket_path: str | None = None, info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every container with its state and usage. ``info`` receives how long listing them took."""
    # The read-only proxy is preferred: it keeps the Docker socket, which is root on the host, out of this container.
    socket_path = socket_path or os.getenv("HOMELAB_DOCKER_HOST") or os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
    if not Path(socket_path).exists():
        return []
    listing_started = time.monotonic()
    containers = _docker_request(socket_path, "/containers/json?all=1")
    if info is not None:
        info["latency_ms"] = int((time.monotonic() - listing_started) * 1000)
    result = []
    for item in containers:
        container_id = item.get("Id", "")
        if not container_id:
            continue
        try:
            detail = _docker_request(socket_path, f"/containers/{container_id}/json")
        except (OSError, ValueError, http.client.HTTPException):
            # A container removed between listing and inspecting it must not fail the whole collection.
            continue
        state = detail.get("State") or {}
        status = state.get("Status") or item.get("State") or "unknown"
        health = (state.get("Health") or {}).get("Status")
        usage_stats = _container_usage(socket_path, container_id) if status == "running" else {}
        stopped = str(status).lower() in _STOPPED
        exit_code = state.get("ExitCode")
        image_id = detail.get("Image")
        result.append({
            "container_id": container_id,
            "name": (detail.get("Name") or (item.get("Names") or [container_id[:12]])[0]).lstrip("/"),
            "image": detail.get("Config", {}).get("Image") or item.get("Image"),
            "status": status.lower(),
            "restart_count": int(detail.get("RestartCount", 0)),
            "healthcheck_status": health,
            "started_at": state.get("StartedAt") if status == "running" else None,
            "ports": _format_ports((detail.get("NetworkSettings") or {}).get("Ports")),
            "exit_code": exit_code if stopped and isinstance(exit_code, int) and not isinstance(exit_code, bool) and -1 <= exit_code <= 65535 else None,
            "oom_killed": state.get("OOMKilled") if stopped and isinstance(state.get("OOMKilled"), bool) else None,
            "finished_at": _docker_time(state.get("FinishedAt")) if stopped else None,
            "image_id": image_id if isinstance(image_id, str) and _IMAGE_ID.fullmatch(image_id) else None,
            **usage_stats,
        })
    if containers and not result:
        # Listing worked but nothing could be inspected: the daemon is failing, not empty.
        raise OSError("no container could be inspected")
    return result


def collect_docker(socket_path: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Containers plus a verdict on the Docker API: ``ok``, ``unreachable`` or ``not_configured``.

    "No Docker on this host" is not an error, and an empty list is not proof of a problem, so the
    backend only alerts on ``unreachable`` for a host that had Docker working before.
    """
    explicit = socket_path or os.getenv("HOMELAB_DOCKER_HOST")
    target = explicit or os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
    if not Path(target).exists():
        # A proxy that is configured but missing is a failure; a host that simply has no Docker socket is not.
        if explicit:
            return [], {"docker_status": "unreachable", "docker_reason": "missing", "docker_latency_ms": None}
        return [], {"docker_status": "not_configured", "docker_reason": None, "docker_latency_ms": None}
    info: dict[str, Any] = {}
    try:
        containers = collect_containers(target, info)
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(f"Docker metrics unavailable ({type(error).__name__}); sending host metrics only", flush=True)
        return [], {"docker_status": "unreachable", "docker_reason": _docker_failure_reason(error), "docker_latency_ms": info.get("latency_ms")}
    return containers, {"docker_status": "ok", "docker_reason": None, "docker_latency_ms": info.get("latency_ms")}


def _format_ports(ports: dict[str, Any] | None) -> str | None:
    mappings = []
    for container_port, bindings in (ports or {}).items():
        for binding in bindings or []:
            mappings.append(f"{binding.get('HostPort')}->{container_port}")
    return ", ".join(sorted(set(mappings)))[:512] or None


def _container_usage(socket_path: str, container_id: str) -> dict[str, Any]:
    try:
        stats = _docker_request(socket_path, f"/containers/{container_id}/stats?stream=false")
    except (OSError, ValueError):
        return {}
    usage: dict[str, Any] = {}
    try:
        cpu = stats.get("cpu_stats") or {}
        previous = stats.get("precpu_stats") or {}
        cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (previous.get("cpu_usage") or {}).get("total_usage", 0)
        system_delta = cpu.get("system_cpu_usage", 0) - previous.get("system_cpu_usage", 0)
        # cgroup v2 omits percpu_usage, so prefer online_cpus like `docker stats` does.
        cores = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
        if cpu_delta > 0 and system_delta > 0:
            usage["cpu_pct"] = round(cpu_delta / system_delta * cores * 100, 2)

        memory = stats.get("memory_stats") or {}
        # Page cache is reclaimable; subtract it the same way `docker stats` does.
        details = memory.get("stats") or {}
        used = memory.get("usage", 0) - details.get("inactive_file", details.get("total_inactive_file", 0))
        limit = memory.get("limit", 0)
        usage["memory_used_mb"] = round(max(used, 0) / MiB, 2)
        usage["memory_limit_mb"] = round(limit / MiB, 2) if limit else None
        usage["memory_pct"] = round(max(used, 0) * 100 / limit, 2) if limit else None

        networks = (stats.get("networks") or {}).values()
        usage["net_rx_mb"] = round(sum(n.get("rx_bytes", 0) for n in networks) / MiB, 2)
        usage["net_tx_mb"] = round(sum(n.get("tx_bytes", 0) for n in networks) / MiB, 2)

        io_entries = (stats.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []
        usage["block_read_mb"] = round(sum(e.get("value", 0) for e in io_entries if str(e.get("op", "")).lower() == "read") / MiB, 2)
        usage["block_write_mb"] = round(sum(e.get("value", 0) for e in io_entries if str(e.get("op", "")).lower() == "write") / MiB, 2)

        usage["pids"] = (stats.get("pids_stats") or {}).get("current")
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return usage


def send_payload(api_url: str, server_id: int, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/monitoring/metrics/ingest",
        data=json.dumps({"server_id": server_id, **payload}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())


def _machine_id() -> str | None:
    override = os.getenv("HOMELAB_MACHINE_ID")
    if override:
        return override.strip()
    for candidate in ("etc/machine-id", "var/lib/dbus/machine-id"):
        try:
            value = (HOST_ROOT / candidate).read_text().strip()
        except OSError:
            continue
        if value:
            return value
    return None


def _credentials_path() -> Path:
    return Path(os.getenv("HOMELAB_STATE_DIR", "/var/lib/homelab-collector")) / "credentials.json"


def load_credentials(path: Path | None = None) -> tuple[int, str] | None:
    path = path or _credentials_path()
    try:
        data = json.loads(path.read_text())
        return int(data["server_id"]), str(data["collector_token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_credentials(server_id: int, token: str, path: Path | None = None) -> None:
    path = path or _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"server_id": server_id, "collector_token": token}))
    temporary.chmod(0o600)
    temporary.replace(path)


def enroll(api_url: str, enroll_key: str) -> tuple[int, str]:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/monitoring/collectors/enroll",
        data=json.dumps({"enroll_key": enroll_key, "hostname": _host_hostname(), "machine_id": _machine_id()}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode())
    return int(data["server_id"]), str(data["collector_token"])


def _post_json(url: str, payload: dict[str, Any], bearer: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """The backend's explanation, e.g. that a join token has expired, without trusting its size or shape."""
    try:
        detail = json.loads(exc.read(2048)).get("detail")
        return " ".join(str(detail).split())[:200] if detail else "no details"
    except (ValueError, AttributeError, OSError):
        return "no details"


def request_approval(api_url: str) -> tuple[int, str]:
    """Ask to join, then wait until someone clicks Add on the dashboard. No restart needed."""
    base = api_url.rstrip("/")
    while True:
        try:
            registration = _post_json(f"{base}/monitoring/collectors/register", {"hostname": _host_hostname(), "machine_id": _machine_id(), "os_info": _host_os_info(), "join_token": os.getenv("HOMELAB_JOIN_TOKEN", "").strip() or None})
        except urllib.error.HTTPError as exc:
            print(f"Registration refused (HTTP {exc.code}): {_error_detail(exc)}; retrying in 5 minutes", flush=True)
            time.sleep(300)
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"Could not reach {base} ({type(exc).__name__}): {exc}; retrying", flush=True)
            time.sleep(15)
            continue

        secret = registration["request_secret"]
        poll_seconds = max(2, int(registration.get("poll_interval_seconds", 5)))
        print(f"Waiting for approval: open the dashboard and click Add on '{_host_hostname()}' (pairing code {registration['pairing_code']})", flush=True)
        last_status = "pending"
        while True:
            time.sleep(poll_seconds if last_status == "pending" else 60)
            try:
                result = _post_json(f"{base}/monitoring/collectors/register/poll", {}, bearer=secret)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    print("Registration request expired; registering again", flush=True)
                    break
                print(f"Approval check failed (HTTP {exc.code})", flush=True)
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                print(f"Approval check failed ({type(exc).__name__}): {exc}", flush=True)
                continue
            status = result.get("status")
            if status == "approved":
                return int(result["server_id"]), str(result["collector_token"])
            if status != last_status:
                print("Dismissed on the dashboard; it can still be added from there" if status == "dismissed" else "Waiting for approval again", flush=True)
                last_status = status


def resolve_credentials(api_url: str) -> tuple[int, str]:
    """Explicit env credentials win, then saved credentials, then an enrollment key, then dashboard approval."""
    if os.getenv("HOMELAB_SERVER_ID") and os.getenv("HOMELAB_COLLECTOR_TOKEN"):
        return int(os.environ["HOMELAB_SERVER_ID"]), os.environ["HOMELAB_COLLECTOR_TOKEN"]
    saved = load_credentials()
    if saved:
        return saved
    credentials = None
    enroll_key = os.getenv("HOMELAB_ENROLL_KEY", "").strip()
    if enroll_key:
        try:
            credentials = enroll(api_url, enroll_key)
        except urllib.error.HTTPError as exc:
            print(f"Enrollment key rejected (HTTP {exc.code}); falling back to dashboard approval", flush=True)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            print(f"Enrollment failed ({type(exc).__name__}): {exc}; falling back to dashboard approval", flush=True)
    if credentials is None:
        credentials = request_approval(api_url)
    try:
        save_credentials(*credentials)
    except OSError as exc:
        print(f"Could not save credentials ({exc}); mount a volume at {_credentials_path().parent} so restarts don't need approval again", flush=True)
    print(f"Registered as server {credentials[0]}", flush=True)
    return credentials


def run_collector() -> None:
    api_url = os.getenv("HOMELAB_API_URL", "http://localhost:8000")
    server_id, token = resolve_credentials(api_url)
    interval = max(15, int(os.getenv("HOMELAB_CHECK_INTERVAL_SECONDS", "60")))
    print(f"Collector started for server {server_id}; API={api_url}", flush=True)
    while True:
        started = time.monotonic()
        try:
            server, host = collect_host()
            containers, docker = collect_docker()
            host.update(docker)
            response = send_payload(api_url, server_id, token, {"server": server, "host": host, "containers": containers})
            interval = max(15, int(response.get("interval_seconds", interval)))
            print(f"Sent host metrics and {len(containers)} container records; next check in {interval}s", flush=True)
            delay = max(0, interval - (time.monotonic() - started))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 404) and not os.getenv("HOMELAB_SERVER_ID"):
                # Removed from the dashboard: ask to join again. This needs a fresh Add, so a
                # deleted server never comes back on its own.
                print(f"Backend no longer accepts server {server_id} (HTTP {exc.code}); requesting approval again", flush=True)
                _credentials_path().unlink(missing_ok=True)
                server_id, token = request_approval(api_url)
                try:
                    save_credentials(server_id, token)
                except OSError:
                    pass
                delay = 0
            elif exc.code in (401, 404):
                print(f"Backend rejected server {server_id} (HTTP {exc.code}); check HOMELAB_SERVER_ID / HOMELAB_COLLECTOR_TOKEN", flush=True)
                delay = 300
            else:
                print(f"Send failed (HTTP {exc.code})", flush=True)
                delay = min(60, max(15, interval))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, http.client.HTTPException) as exc:
            print(f"Collection/send failed ({type(exc).__name__}): {exc}", flush=True)
            delay = min(60, max(15, interval))
        time.sleep(delay)


if __name__ == "__main__":
    run_collector()
