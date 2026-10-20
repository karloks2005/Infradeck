"""Read-only host and Docker metrics collector for a registered Infradeck server.

Only INFRADECK_API_URL is required. On first start the collector asks to join and
appears on the dashboard under "New servers detected"; once someone clicks Add
it receives its token (no restart) and saves it under INFRADECK_STATE_DIR.
INFRADECK_JOIN_TOKEN (from the dashboard's Connect a server command) proves who asked it to join.
INFRADECK_ENROLL_KEY skips the approval step for automated installs, and
INFRADECK_SERVER_ID / INFRADECK_COLLECTOR_TOKEN still work as explicit credentials.
Run with ``python -m src.collector.collector`` or as a standalone script (standard
library only; it ships as a container image). Docker access is optional; when
the socket is unavailable, host metrics continue to be sent.
"""
from __future__ import annotations

import collections
import concurrent.futures
import ctypes
import ctypes.util
import hashlib
import http.client
import json
import math
import os
import platform
import re
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MiB = 1024 * 1024
GiB = 1024**3
# In a container the host is mounted at /host/proc and /hostfs; fall back to our own view otherwise.
# Installs from before the rename to Infradeck set HOMELAB_* variables; they keep working unchanged.
for _name, _value in list(os.environ.items()):
    if _name.startswith("HOMELAB_"):
        os.environ.setdefault("INFRADECK_" + _name[len("HOMELAB_"):], _value)

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


def _host_net_dirs() -> list[Path]:
    """Where the host's network tables are. PID 1's view is the host's; a container's own /proc/net is not.

    Only a collector running directly on the host may fall back to /proc/net. Inside a container that
    fallback would silently report the container's own interfaces and sockets as the host's.
    """
    return [HOST_PROC / "1/net"] + ([HOST_PROC / "net"] if HOST_PROC == Path("/proc") else [])


def _read_net_table(name: str) -> list[str] | None:
    """The lines of a host network table (e.g. ``tcp``, ``dev``), or None if it can't be read."""
    for directory in _host_net_dirs():
        try:
            with open(directory / name, "rb") as handle:
                data = handle.read(MAX_NET_TABLE_BYTES + 1)
        except OSError:
            continue
        if len(data) <= MAX_NET_TABLE_BYTES:
            return data.decode("utf-8", errors="replace").splitlines()
    return None


MAX_NET_TABLE_BYTES = 8 * 1024 * 1024
# Name prefixes of interfaces that are never physical, used only when sysfs can't say.
_VIRTUAL_INTERFACES = ("lo", "veth", "docker", "br-", "virbr", "tun", "tap", "wg", "dummy", "ifb", "vboxnet", "vmnet")


def _physical_interfaces() -> set[str] | None:
    """Interfaces backed by real hardware (they have a ``device`` entry in sysfs), or None if sysfs isn't visible."""
    base = HOST_ROOT / "sys/class/net"
    try:
        return {entry.name for entry in base.iterdir() if (entry / "device").exists()}
    except OSError:
        return None


def _network_totals() -> tuple[int, int] | None:
    """Bytes sent and received over the host's physical interfaces, or None if the host's table is unreadable.

    Virtual interfaces (Docker bridges, veth pairs, loopback) are left out: their traffic is already
    counted on the physical interface and would be counted twice.
    """
    lines = _read_net_table("dev")
    if lines is None:
        return None
    physical = _physical_interfaces()
    sent = received = 0
    for line in lines[2:]:
        if ":" not in line:
            continue
        interface, raw = line.split(":", 1)
        interface = interface.strip()
        if (interface not in physical) if physical else interface.startswith(_VIRTUAL_INTERFACES):
            continue
        try:
            fields = [int(value) for value in raw.split()]
        except ValueError:
            continue
        if len(fields) >= 9:
            received += fields[0]
            sent += fields[8]
    return sent, received


EPHEMERAL_PORT_START = 32768  # Linux's default ephemeral range starts here
MAX_LISTENERS = 512
MAX_NET_TABLE_LINES = 500_000
_ZERO_V6 = "0" * 32


def _listener_scope(address: str) -> str | None:
    """loopback, any (all interfaces) or specific, from a hex address in /proc/net/tcp(6) byte order."""
    if len(address) == 8:  # IPv4: stored little-endian, so the first octet is the last byte
        return "any" if address == "00000000" else "loopback" if address.endswith("7F") else "specific"
    if len(address) == 32:
        if address == _ZERO_V6:
            return "any"
        if address == "00000000000000000000000001000000":
            return "loopback"
        if address.startswith("0000000000000000FFFF0000"):  # IPv4-mapped IPv6
            return _listener_scope(address[24:])
        return "specific"
    return None


def listener_tracked(proto: str, port: int, scope: str) -> bool:
    """Ephemeral ports are skipped: loopback ones, and every UDP one, are programs talking out, not services."""
    return not (port >= EPHEMERAL_PORT_START and (scope == "loopback" or proto == "udp"))


def _scan_listeners(table: str, proto: str) -> set[tuple[str, int, str]] | None:
    """Listeners in one host network table, streamed so a busy server's huge connection table costs no memory.

    None if the table can't be read or is longer than MAX_NET_TABLE_LINES: a truncated read could miss
    listeners and make them look closed, so it is treated as unknown instead.
    """
    for directory in _host_net_dirs():
        try:
            handle = open(directory / table, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        found: set[tuple[str, int, str]] = set()
        with handle:
            try:
                for number, line in enumerate(handle):
                    if number > MAX_NET_TABLE_LINES:
                        return None
                    if number:
                        found |= _parse_listener_line(line, proto)
            except OSError:
                continue
        return found
    return None


def _parse_listener_line(line: str, proto: str) -> set[tuple[str, int, str]]:
    fields = line.split()
    if len(fields) < 4:
        return set()
    address, _, port_hex = fields[1].partition(":")
    # TCP LISTEN is state 0A; a bound, unconnected UDP socket is state 07 with no remote address.
    listening = fields[3] == "0A" if proto == "tcp" else fields[3] == "07" and fields[2].startswith(("00000000:0000", _ZERO_V6 + ":0000"))
    if not listening:
        return set()
    try:
        port = int(port_hex, 16)
    except ValueError:
        return set()
    scope = _listener_scope(address)
    return {(proto, port, scope)} if scope and 1 <= port <= 65535 and listener_tracked(proto, port, scope) else set()


def collect_listeners() -> list[dict[str, Any]] | None:
    """What is listening on the host (protocol, port, scope), read passively from /proc. None if unreadable.

    No process names are collected: matching sockets to other users' processes needs privileges the
    collector deliberately doesn't have. If any table is unreadable the whole answer is unknown, so a
    partial picture can never be mistaken for ports having closed.
    """
    found: set[tuple[str, int, str]] = set()
    for table, proto in (("tcp", "tcp"), ("tcp6", "tcp"), ("udp", "udp"), ("udp6", "udp")):
        listeners = _scan_listeners(table, proto)
        if listeners is None:
            return None
        found |= listeners
    return [{"proto": proto, "port": port, "scope": scope} for proto, port, scope in sorted(found)[:MAX_LISTENERS]]


_HOSTNAME = re.compile(r"(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")
_IPV4 = re.compile(r"(\d{1,3})(\.\d{1,3}){3}")
MAX_CONNECTIVITY_TARGETS = 5
CONNECT_TIMEOUT_SECONDS = 3.0
CONNECTIVITY_DEADLINE_SECONDS = 6.0


def _connectivity_targets() -> list[tuple[str, int]]:
    """``host:port`` entries from INFRADECK_CONNECTIVITY_TARGETS (hostnames or IPv4 only). Empty means the check is off."""
    targets: list[tuple[str, int]] = []
    for entry in os.getenv("INFRADECK_CONNECTIVITY_TARGETS", "").split(","):
        host, _, port = entry.strip().rpartition(":")
        # A name whose last label is all digits isn't a valid hostname; it is a mistyped IP address.
        valid_host = _IPV4.fullmatch(host) and all(int(part) <= 255 for part in host.split(".")) or (_HOSTNAME.fullmatch(host) and not host.rsplit(".", 1)[-1].isdigit())
        if valid_host and port.isdigit() and 1 <= int(port) <= 65535 and (host, int(port)) not in targets:
            targets.append((host, int(port)))
        elif entry.strip():
            print(f"Ignoring invalid connectivity target {entry.strip()[:60]!r}", flush=True)
    return targets[:MAX_CONNECTIVITY_TARGETS]


def _probe(host: str, port: int, results: dict[tuple[str, int], dict[str, Any]]) -> None:
    """Resolve the name (if it is one) and open a TCP connection. Nothing is sent over it."""
    outcome: dict[str, Any] = {"dns": None if _IPV4.fullmatch(host) else False, "tcp": False, "ms": None}
    results[(host, port)] = outcome  # visible even if this thread outlives the deadline
    started = time.monotonic()
    try:
        address = host if outcome["dns"] is None else socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4][0]
        if outcome["dns"] is not None:
            outcome["dns"] = True
        socket.create_connection((address, port), CONNECT_TIMEOUT_SECONDS).close()
        outcome["tcp"], outcome["ms"] = True, int((time.monotonic() - started) * 1000)
    except (OSError, IndexError):
        pass


def summarise_connectivity(results: list[dict[str, Any]]) -> str:
    """ok (all reachable), degraded (some), dns_failed (names don't resolve, nothing else wrong) or down."""
    if all(result["tcp"] for result in results):
        return "ok"
    if any(result["tcp"] for result in results):
        return "degraded"
    return "dns_failed" if all(result["dns"] is False for result in results) else "down"


def collect_connectivity() -> dict[str, Any]:
    """Can this host reach the internet? Off unless INFRADECK_CONNECTIVITY_TARGETS is set on this collector.

    The targets come from the collector's own environment, never from the dashboard, so a compromised
    dashboard can't point these probes anywhere.
    """
    targets = _connectivity_targets()
    if not targets:
        return {"internet_status": None, "internet_latency_ms": None}
    results: dict[tuple[str, int], dict[str, Any]] = {}
    threads = [threading.Thread(target=_probe, args=(host, port, results), daemon=True) for host, port in targets]
    deadline = time.monotonic() + CONNECTIVITY_DEADLINE_SECONDS
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    outcomes = [dict(results.get(target) or {"dns": False, "tcp": False, "ms": None}) for target in targets]
    latencies = sorted(outcome["ms"] for outcome in outcomes if outcome["ms"] is not None)
    return {"internet_status": summarise_connectivity(outcomes), "internet_latency_ms": latencies[len(latencies) // 2] if latencies else None}


# Firewall configuration files that are looked at, by a fixed name. Only a hash and a rule count leave the host.
FIREWALL_FILES = (("ufw", "etc/ufw/user.rules"), ("ufw6", "etc/ufw/user6.rules"), ("ufw-conf", "etc/ufw/ufw.conf"),
                  ("nftables", "etc/nftables.conf"), ("iptables", "etc/iptables/rules.v4"), ("ip6tables", "etc/iptables/rules.v6"))
MAX_FIREWALL_FILE_BYTES = 1024 * 1024


def _read_regular_file(path: Path, limit: int) -> bytes | None:
    """File contents up to ``limit`` bytes; None for symlinks, non-files, unreadable or larger files."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        data = os.read(descriptor, limit + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    return data if len(data) <= limit else None


def collect_firewall() -> dict[str, Any] | None:
    """A fingerprint of the host's firewall configuration files; never their contents (which reveal the network layout).

    This sees configuration, not the live ruleset: reading that needs CAP_NET_ADMIN, which could also change
    the firewall, so the collector doesn't have it. Returns None if no known firewall file exists.
    """
    files: list[dict[str, Any]] = []
    ufw_enabled: bool | None = None
    for name, relative in FIREWALL_FILES:
        data = _read_regular_file(HOST_ROOT / relative, MAX_FIREWALL_FILE_BYTES)
        if data is None:
            continue
        text = data.decode("utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines()]
        if name == "ufw-conf":
            for line in lines:
                if line.upper().startswith("ENABLED="):
                    ufw_enabled = line.split("=", 1)[1].strip().lower() == "yes"
            rules = 0
        elif name in ("nftables",):
            rules = sum(1 for line in lines if line and not line.startswith("#"))
        else:
            rules = sum(1 for line in lines if line.startswith("-A "))
        files.append({"name": name, "sha": hashlib.sha256(data).hexdigest()[:16], "rules": rules})
    return {"files": files, "ufw_enabled": ufw_enabled} if files else None


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


_STA_NANO = 0x2000


def _read_clock() -> tuple[int, float] | None:
    """(kernel clock state, offset from the time source in milliseconds) from a read-only adjtimex, or None."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        timex = _Timex()
        state = libc.adjtimex(ctypes.byref(timex))
    except (OSError, AttributeError):
        return None
    if state < 0:
        return None
    # The offset is in nanoseconds when the kernel says so, microseconds otherwise.
    return state, timex.offset / (1_000_000 if timex.status & _STA_NANO else 1_000)


def _ntp_synced() -> bool | None:
    """Whether the kernel clock is disciplined by a time service; None when it can't be read."""
    # A read-only adjtimex (modes=0) covers chrony, ntpd and timesyncd alike. Docker's default
    # seccomp profile may refuse it, in which case timesyncd's marker file is the fallback.
    clock = _read_clock()
    if clock is not None:
        return clock[0] != _TIME_ERROR
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
    return Path(os.getenv("INFRADECK_HELPER_FILE", "/helper/system.json"))


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


COLLECTOR_VERSION = "0.10.0"
MAX_INTERFACES = 16
MAX_TEMPERATURES = 8
_KERNEL_IMAGE = re.compile(r"vmlinuz-(\d[\w.+~-]*)")
_UPDATES_PENDING = re.compile(r"(\d+) (?:updates?|packages?) can be (?:applied|updated|installed)")
_UPDATES_SECURITY = re.compile(r"(\d+) (?:of these updates|additional security updates?|updates?) (?:is a|are|can be applied with)[^\n]*security", re.IGNORECASE)
# Counters from the previous check-in, per interface: (received bytes, sent bytes, monotonic time).
_interface_previous: dict[str, tuple[int, int, float]] = {}


def _version_key(version: str) -> list[tuple[int, Any]]:
    """Sorts 6.8.0-9 before 6.8.0-85: digit runs compare as numbers."""
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", version) if part]


def _mtime(relative: str) -> float | None:
    try:
        return (HOST_ROOT / relative).stat().st_mtime
    except OSError:
        return None


def _cpu_model() -> str | None:
    for line in (_read_text(HOST_PROC / "cpuinfo") or "").splitlines():
        if line.lower().startswith(("model name", "hardware")) and ":" in line:
            return " ".join(line.split(":", 1)[1].split())[:128] or None
    return None


def _swap_total_gb() -> float | None:
    for line in (_read_text(HOST_PROC / "meminfo") or "").splitlines():
        if line.startswith("SwapTotal:"):
            return round(int(line.split()[1]) * 1024 / GiB, 2)
    return None


def _timezone() -> str | None:
    name = _read_text(HOST_ROOT / "etc/timezone")
    if not name:
        try:
            name = os.readlink(HOST_ROOT / "etc/localtime").partition("zoneinfo/")[2]
        except OSError:
            name = None
    return name[:64] if name else None


def _time_service() -> str | None:
    for service, marker in (("chrony", "run/chrony"), ("systemd-timesyncd", "run/systemd/timesync"), ("ntpd", "var/lib/ntp"), ("chrony", "var/lib/chrony")):
        if (HOST_ROOT / marker).exists():
            return service
    return None


def collect_updates(kernel: str | None, reboot_required: bool | None) -> dict[str, Any]:
    """Pending package updates, from the files apt leaves behind (Debian and Ubuntu); {} elsewhere.

    Nothing is run on the host, so the numbers are as old as apt's last own check.
    """
    if not (HOST_ROOT / "etc/debian_version").exists():
        return {}
    info: dict[str, Any] = {"updates_manager": "apt", "updates_checked_at": _mtime("var/lib/apt/periodic/update-success-stamp"), "updates_applied_at": _mtime("var/log/apt/history.log")}
    summary = _read_regular_file(HOST_ROOT / "var/lib/update-notifier/updates-available", 16 * 1024)
    if summary is not None:
        text = summary.decode("utf-8", errors="replace")
        pending, security = _UPDATES_PENDING.search(text), _UPDATES_SECURITY.search(text)
        info["updates_pending"] = int(pending.group(1)) if pending else 0
        info["updates_security"] = int(security.group(1)) if security else 0
    settings = _read_regular_file(HOST_ROOT / "etc/apt/apt.conf.d/20auto-upgrades", 16 * 1024)
    if settings is not None:
        info["auto_updates"] = bool(re.search(r'Unattended-Upgrade\s+"1"', settings.decode("utf-8", errors="replace")))
    if reboot_required and kernel:
        try:
            installed = [match.group(1) for entry in (HOST_ROOT / "boot").iterdir() if (match := _KERNEL_IMAGE.fullmatch(entry.name))]
        except OSError:
            installed = []
        newest = max(installed, key=_version_key, default=None)
        if newest and _version_key(newest) > _version_key(kernel):
            info["reboot_kernel"] = newest[:64]
    return info


def collect_temperatures() -> list[dict[str, Any]]:
    """Hardware temperature sensors the kernel exposes in sysfs; [] when sysfs isn't visible or has none."""
    found: list[dict[str, Any]] = []
    try:
        sensors = sorted((HOST_ROOT / "sys/class/hwmon").iterdir())
    except OSError:
        return found
    for sensor in sensors:
        label, reading = _read_text(sensor / "name"), _read_text(sensor / "temp1_input")
        if label and reading and reading.lstrip("-").isdigit() and -50_000 <= int(reading) <= 200_000:
            found.append({"label": label[:32], "celsius": round(int(reading) / 1000, 1)})
    return found[:MAX_TEMPERATURES]


def _interface_networks() -> dict[str, str]:
    """The directly connected IPv4 network of each interface, from the host's routing table."""
    networks: dict[str, str] = {}
    for line in (_read_net_table("route") or [])[1:]:
        fields = line.split()
        if len(fields) < 8 or fields[2] != "00000000" or fields[1] == "00000000":
            continue  # routes through a gateway and the default route say nothing about the interface's own network
        try:
            destination = socket.inet_ntoa(bytes.fromhex(fields[1])[::-1])
            prefix = bin(int.from_bytes(bytes.fromhex(fields[7])[::-1], "big")).count("1")
        except (ValueError, OSError):
            continue
        networks.setdefault(fields[0], f"{destination}/{prefix}")
    return networks


def collect_interfaces(now: float | None = None) -> list[dict[str, Any]]:
    """The host's network interfaces with their current traffic; veth pairs (one per container) are left out."""
    lines = _read_net_table("dev")
    if lines is None:
        return []
    now = time.monotonic() if now is None else now
    networks = _interface_networks()
    interfaces, seen = [], set()
    for line in lines[2:]:
        name, _, raw = line.partition(":")
        name = name.strip()
        if not raw or name == "lo" or name.startswith("veth"):
            continue
        try:
            fields = [int(value) for value in raw.split()]
        except ValueError:
            continue
        if len(fields) < 9:
            continue
        seen.add(name)
        received, sent = fields[0], fields[8]
        entry: dict[str, Any] = {"name": name[:16], "network": networks.get(name)}
        previous = _interface_previous.get(name)
        if previous and now > previous[2] and received >= previous[0] and sent >= previous[1]:
            elapsed = now - previous[2]
            entry["rx_mbps"] = round((received - previous[0]) / MiB / elapsed, 3)
            entry["tx_mbps"] = round((sent - previous[1]) / MiB / elapsed, 3)
        speed = _read_text(HOST_ROOT / "sys/class/net" / name / "speed")
        if speed and speed.isdigit() and int(speed) > 0:
            entry["speed_mbit"] = int(speed)
        _interface_previous[name] = (received, sent, now)
        # Interfaces that carry an address or have ever moved data; idle bridges without either are noise.
        if entry["network"] or received or sent:
            interfaces.append(entry)
    for stale in set(_interface_previous) - seen:
        del _interface_previous[stale]
    interfaces.sort(key=lambda item: (item["network"] is None, item["name"]))
    return interfaces[:MAX_INTERFACES]


_PORT_LIST = r"\{?\s*((?:\d{1,5}(?:\s*[-:]\s*\d{1,5})?\s*,?\s*)+)\}?"
_NFT_ACCEPT = re.compile(rf"\b(tcp|udp)\s+dport\s+{_PORT_LIST}[^\n;]*\baccept\b")
_IPTABLES_ACCEPT = re.compile(r"^-A \S*(?:input|INPUT)\S*\s.*-p (tcp|udp)\b.*--dports? ([\d,:]+).*-j ACCEPT", re.MULTILINE)


def _ports_in(spec: str) -> set[int]:
    ports: set[int] = set()
    for part in re.split(r"[,\s]+", spec.strip()):
        low, _, high = part.replace(":", "-").partition("-")
        if low.isdigit() and (not high or high.isdigit()):
            first, last = int(low), int(high or low)
            if 1 <= first <= last <= 65535 and last - first <= 4096:
                ports.update(range(first, last + 1))
    return ports


def firewall_allowed(listeners: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Which of the listening ports a firewall configuration file explicitly accepts, and in which file.

    Only ports the host is already reported to listen on are named, so this adds nothing about the rest of the
    ruleset. Like the fingerprint, it reads configuration, not the live rules.
    """
    wanted = {(item["proto"], item["port"]) for item in listeners or []}
    allowed: dict[tuple[str, int], str] = {}
    for name, relative in FIREWALL_FILES:
        data = _read_regular_file(HOST_ROOT / relative, MAX_FIREWALL_FILE_BYTES) if name != "ufw-conf" else None
        if data is None:
            continue
        text = data.decode("utf-8", errors="replace")
        for proto, spec in (_NFT_ACCEPT if name == "nftables" else _IPTABLES_ACCEPT).findall(text):
            for port in _ports_in(spec):
                if (proto, port) in wanted:
                    allowed.setdefault((proto, port), name)
    return [{"proto": proto, "port": port, "file": name} for (proto, port), name in sorted(allowed.items())]


def disk_io_errors() -> int | None:
    """I/O errors the kernel has counted on SCSI and SATA disks since boot, or None when no disk exposes a counter
    (NVMe drives don't)."""
    total, found = 0, False
    try:
        disks = list((HOST_ROOT / "sys/block").iterdir())
    except OSError:
        return None
    for disk in disks:
        raw = _read_text(disk / "device/ioerr_cnt")
        if raw:
            try:
                total += int(raw, 16)
                found = True
            except ValueError:
                continue
    return total if found else None


PORT_OWNERS_MAX_AGE_SECONDS = 300
MAX_PORT_OWNERS = 512


def _port_owners_path() -> Path:
    return Path(os.getenv("INFRADECK_PORT_OWNERS_FILE", "/helper/ports.json"))


def collect_port_owners(path: Path | None = None, now: float | None = None) -> list[dict[str, Any]] | None:
    """Which process listens on each port, from the optional port-owners helper; None when it isn't running.

    Read like the system helper's file: no symlinks, a size cap, and nothing older than a few minutes.
    """
    path = path or _port_owners_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        raw = os.read(fd, HELPER_FILE_MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        data = json.loads(raw) if len(raw) <= HELPER_FILE_MAX_BYTES else None
        generated = datetime.fromisoformat(data["generated_at"]).timestamp()
    except (ValueError, KeyError, TypeError):
        return None
    if abs((now if now is not None else time.time()) - generated) > PORT_OWNERS_MAX_AGE_SECONDS or not isinstance(data.get("listeners"), list):
        return None
    owners = []
    for item in data["listeners"][:MAX_PORT_OWNERS]:
        if isinstance(item, dict) and item.get("proto") in ("tcp", "udp") and isinstance(item.get("port"), int) and isinstance(item.get("process"), str):
            owners.append({"proto": item["proto"], "port": item["port"], "process": item["process"][:64]})
    return owners


def collect_system_info(listeners: list[dict[str, Any]] | None, reboot_required: bool | None, mounts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Facts about the machine for the dashboard's System and Network tabs. Everything is read from /proc, sysfs and
    a few world-readable files; what can't be read is simply left out."""
    kernel = _read_text(HOST_PROC / "sys/kernel/osrelease")
    clock = _read_clock()
    reported = {mount["mount"] for mount in mounts or []}
    info: dict[str, Any] = {
        "kernel": kernel[:64] if kernel else None,
        "arch": platform.machine()[:16] or None,
        "cpu_model": _cpu_model(),
        "swap_total_gb": _swap_total_gb(),
        "timezone": _timezone(),
        "time_service": _time_service(),
        "clock_offset_ms": round(clock[1], 3) if clock else None,
        "collector_version": COLLECTOR_VERSION,
        "docker_version": _docker_version["value"],
        "helper_installed": _helper_path().exists(),
        "temperatures": collect_temperatures(),
        "interfaces": collect_interfaces(),
        "mount_devices": {mount[:255]: source.rsplit("/", 1)[-1][:64] for source, mount, _ in _mount_entries() if source.startswith("/dev/") and mount in reported},
        "firewall_allowed": firewall_allowed(listeners),
        "docker_disk": _docker_disk["value"],
        "disk_io_errors": disk_io_errors(),
        "listener_owners": collect_port_owners(),
        **collect_updates(kernel, reboot_required),
    }
    return {key: value for key, value in info.items() if value is not None}


def registration_details() -> dict[str, Any]:
    """What a collector found on its machine, shown next to the Add button before anyone approves it."""
    socket_path = os.getenv("INFRADECK_DOCKER_HOST") or os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
    try:
        containers = len(_docker_request(socket_path, "/containers/json?all=1")) if Path(socket_path).exists() else None
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        containers = None
    return {
        "cpu_cores": _host_cpu_cores(), "containers": containers, "mounts": len(collect_mounts()),
        "helper_installed": _helper_path().exists(),
        "dashboard_host": os.getenv("INFRADECK_DASHBOARD_HOST", "").lower() in {"1", "true", "yes"},
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
    network = _network_totals()
    uptime = float((HOST_PROC / "uptime").read_text().split()[0])
    host = {
        "cpu_utilization_pct": cpu_pct,
        "load_1m": round(loads[0], 2), "load_5m": round(loads[1], 2), "load_15m": round(loads[2], 2),
        **memory,
        "disk_usage_pct": round(disk_pct, 2),
        "disk_inodes_pct": round(inode_pct, 2) if inode_pct is not None else None,
        "net_bytes_sent_mb": round(network[0] / MiB, 3) if network else None,
        "net_bytes_recv_mb": round(network[1] / MiB, 3) if network else None,
        "system_uptime_seconds": round(uptime, 1),
        "mounts": collect_mounts(),
        "listeners": collect_listeners(),
        "firewall": collect_firewall(),
        **collect_connectivity(),
        **collect_system(),
    }
    server = {
        "hostname": _host_hostname(),
        "os_info": _host_os_info(),
        "cpu_cores": _host_cpu_cores(),
        "total_ram_gb": round(_memory_total_gb(), 3),
        "system": collect_system_info(host["listeners"], host.get("reboot_required"), host["mounts"]),
    }
    # Set only on the collector in the dashboard's own compose stack; the dashboard's top charts follow it.
    if os.getenv("INFRADECK_DASHBOARD_HOST", "").lower() in {"1", "true", "yes"}:
        server["dashboard_host"] = True
    return server, host


def _memory_total_gb() -> float:
    for line in (HOST_PROC / "meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024 / GiB
    return 0.0


def _host_hostname() -> str:
    override = os.getenv("INFRADECK_SERVER_NAME")
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
    socket_path = socket_path or os.getenv("INFRADECK_DOCKER_HOST") or os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
    if not Path(socket_path).exists():
        return []
    listing_started = time.monotonic()
    containers = _docker_request(socket_path, "/containers/json?all=1")
    if info is not None:
        info["latency_ms"] = int((time.monotonic() - listing_started) * 1000)
    result = []
    for stale in set(_cpu_previous) - {item.get("Id") for item in containers}:
        del _cpu_previous[stale]
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
            "project": _compose_project(detail),
            "volumes": _format_volumes(detail.get("Mounts")),
            "exit_code": exit_code if stopped and isinstance(exit_code, int) and not isinstance(exit_code, bool) and -1 <= exit_code <= 65535 else None,
            "oom_killed": state.get("OOMKilled") if stopped and isinstance(state.get("OOMKilled"), bool) else None,
            "finished_at": _docker_time(state.get("FinishedAt")) if stopped else None,
            "image_id": image_id if isinstance(image_id, str) and _IMAGE_ID.fullmatch(image_id) else None,
            **usage_stats,
            **({"swap_mb": swap} if status == "running" and (swap := container_swap_mb(container_id)) is not None else {}),
        })
    if containers and not result:
        # Listing worked but nothing could be inspected: the daemon is failing, not empty.
        raise OSError("no container could be inspected")
    return result


# The Docker daemon's version, asked for rarely: it only changes when Docker is upgraded.
_docker_version: dict[str, Any] = {"value": None, "countdown": 0}
DOCKER_VERSION_EVERY = 120


def _remember_docker_version(socket_path: str) -> None:
    if _docker_version["countdown"] > 0:
        _docker_version["countdown"] -= 1
        return
    _docker_version["countdown"] = DOCKER_VERSION_EVERY
    try:
        version = _docker_request(socket_path, "/version").get("Version")
    except (OSError, ValueError, AttributeError, http.client.HTTPException):
        return  # an older proxy image refuses the request; the version just stays unknown
    _docker_version["value"] = version[:32] if isinstance(version, str) and version else None


# What Docker's images and build cache take on disk. Asking makes the daemon walk every layer, which can take
# seconds on a big host, so it is asked for rarely and never delays a check-in by more than its own timeout.
_docker_disk: dict[str, Any] = {"value": None, "next": 0.0}
DOCKER_DISK_EVERY_SECONDS = 30 * 60
_DOCKER_DISK_KEYS = ("images_bytes", "images_unused_bytes", "build_cache_bytes", "build_cache_unused_bytes")


def _remember_docker_disk(socket_path: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    if now < _docker_disk["next"]:
        return
    _docker_disk["next"] = now + DOCKER_DISK_EVERY_SECONDS
    try:
        usage = _docker_request(socket_path, "/system/df")
    except (OSError, ValueError, http.client.HTTPException):
        return  # refused by an older proxy image, too slow or too large: stays unknown
    if not isinstance(usage, dict) or not all(isinstance(usage.get(key), int) for key in _DOCKER_DISK_KEYS):
        return
    _docker_disk["value"] = {
        **{key.replace("_bytes", "_gb"): round(usage[key] / GiB, 2) for key in _DOCKER_DISK_KEYS},
        "images": usage.get("images") if isinstance(usage.get("images"), int) else None,
        "images_unused": usage.get("images_unused") if isinstance(usage.get("images_unused"), int) else None,
    }


def collect_docker(socket_path: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Containers plus a verdict on the Docker API: ``ok``, ``unreachable`` or ``not_configured``.

    "No Docker on this host" is not an error, and an empty list is not proof of a problem, so the
    backend only alerts on ``unreachable`` for a host that had Docker working before.
    """
    explicit = socket_path or os.getenv("INFRADECK_DOCKER_HOST")
    target = explicit or os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
    if not Path(target).exists():
        # A proxy that is configured but missing is a failure; a host that simply has no Docker socket is not.
        if explicit:
            return [], {"docker_status": "unreachable", "docker_reason": "missing", "docker_latency_ms": None}
        return [], {"docker_status": "not_configured", "docker_reason": None, "docker_latency_ms": None}
    info: dict[str, Any] = {}
    try:
        containers = collect_containers(target, info)
        _remember_docker_version(target)
        _remember_docker_disk(target)
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(f"Docker metrics unavailable ({type(error).__name__}); sending host metrics only", flush=True)
        return [], {"docker_status": "unreachable", "docker_reason": _docker_failure_reason(error), "docker_latency_ms": info.get("latency_ms")}
    return containers, {"docker_status": "ok", "docker_reason": None, "docker_latency_ms": info.get("latency_ms")}


COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


def container_swap_mb(container_id: str) -> float | None:
    """How much of a container's memory is swapped out, from its cgroup (v2), or None when that can't be read.

    Docker's stats don't report it. The cgroup files are world-readable and sit under the host's /sys, at a path
    that depends on whether Docker uses the systemd or the cgroupfs driver.
    """
    if not _CONTAINER_ID.fullmatch(container_id):
        return None
    for relative in (f"sys/fs/cgroup/system.slice/docker-{container_id}.scope/memory.swap.current", f"sys/fs/cgroup/docker/{container_id}/memory.swap.current"):
        raw = _read_text(HOST_ROOT / relative)
        if raw and raw.isdigit():
            return round(int(raw) / MiB, 2)
    return None


def _compose_project(detail: dict[str, Any]) -> str | None:
    """The compose project a container belongs to, or None for one started on its own."""
    labels = (detail.get("Config") or {}).get("Labels")
    project = labels.get(COMPOSE_PROJECT_LABEL) if isinstance(labels, dict) else None
    return project[:128] if isinstance(project, str) and project else None


def _format_volumes(mounts: Any) -> str | None:
    """Where a container's data lives on the host: bind-mount paths and volume names, never what is in them."""
    sources = set()
    for mount in mounts if isinstance(mounts, list) else []:
        if not isinstance(mount, dict):
            continue
        source = mount.get("Name") if mount.get("Type") == "volume" else mount.get("Source")
        if isinstance(source, str) and source:
            sources.add(source)
    return ", ".join(sorted(sources))[:512] or None


def _format_ports(ports: dict[str, Any] | None) -> str | None:
    mappings = []
    for container_port, bindings in (ports or {}).items():
        for binding in bindings or []:
            mappings.append(f"{binding.get('HostPort')}->{container_port}")
    return ", ".join(sorted(set(mappings)))[:512] or None


# Cumulative CPU counters (container, whole system) from the previous check-in, per container. The one-shot stats
# call returns at once but carries no earlier reading, so CPU % is the change since the last check-in. The first
# check-in after a start has no figure for a container (unknown, not zero).
_cpu_previous: dict[str, tuple[float, float]] = {}
# Set once the proxy or daemon refuses the one-shot request while the normal one works (an older proxy image).
_one_shot_unsupported = False


def _fetch_stats(socket_path: str, container_id: str) -> Any:
    """One container's stats: the quick one-shot request, falling back to the normal one that waits about a second."""
    global _one_shot_unsupported
    base = f"/containers/{container_id}/stats?stream=false"
    tried_one_shot = not _one_shot_unsupported
    if tried_one_shot:
        try:
            return _docker_request(socket_path, f"{base}&one-shot=true")
        except (OSError, ValueError):
            pass
    stats = _docker_request(socket_path, base)
    if tried_one_shot:
        _one_shot_unsupported = True
    return stats


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _cpu_percent(container_id: str, stats: dict[str, Any]) -> float | None:
    cpu = stats.get("cpu_stats") or {}
    previous = stats.get("precpu_stats") or {}
    total = _number((cpu.get("cpu_usage") or {}).get("total_usage"))
    system = _number(cpu.get("system_cpu_usage"))
    before_total = _number((previous.get("cpu_usage") or {}).get("total_usage"))
    before_system = _number(previous.get("system_cpu_usage"))
    if total is not None and system is not None:
        remembered = _cpu_previous.get(container_id)
        _cpu_previous[container_id] = (total, system)
        if not before_system:
            before_total, before_system = remembered if remembered else (None, None)
    if None in (total, system, before_total, before_system):
        return None
    cpu_delta, system_delta = total - before_total, system - before_system
    # A counter that went backwards means the container restarted; skip that sample.
    if cpu_delta <= 0 or system_delta <= 0:
        return None
    # cgroup v2 omits percpu_usage, so prefer online_cpus like `docker stats` does.
    cores = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    return round(cpu_delta / system_delta * cores * 100, 2)


def _container_usage(socket_path: str, container_id: str) -> dict[str, Any]:
    try:
        stats = _fetch_stats(socket_path, container_id)
    except (OSError, ValueError):
        return {}
    usage: dict[str, Any] = {}
    try:
        cpu_pct = _cpu_percent(container_id, stats)
        if cpu_pct is not None:
            usage["cpu_pct"] = cpu_pct

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


# ---------- availability checks ----------
# The dashboard asks this collector to try services every so often and reports each run: an HTTP(s) URL, a keyword in
# a page, a value in a JSON reply, a TCP port, a TLS certificate's expiry, a DNS record, or an ICMP ping. Only what
# the dashboard sends is run, after checking it here too: http/https URLs and host/port pairs, GET or HEAD without
# custom headers or body, at most 1 MiB of a response read and none of it sent back. No shell is ever involved; ping
# uses an unprivileged ICMP datagram socket (no raw sockets, no CAP_NET_RAW), DNS a plain UDP query built by hand.

CHECK_TYPES = ("http", "keyword", "tcp", "tls", "dns", "json", "ping")
URL_CHECK_TYPES = ("http", "keyword", "json")
DNS_RECORD_NUMS = {"A": 1, "NS": 2, "CNAME": 5, "MX": 15, "TXT": 16, "AAAA": 28}
JSON_OPS = ("equals", "contains", "gt", "lt", "exists")
DEFAULT_TLS_PORT = 443
MAX_CHECKS = 50
MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL = 20, 86400
MAX_CHECK_TIMEOUT = 48
MAX_BODY_BYTES = 1024 * 1024
MAX_REDIRECTS = 10
MAX_PENDING_RESULTS = 500
CHECK_WORKERS = 8
IDLE_REPORT_SECONDS = 60
_CHECK_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\]")
_CHECK_CODES = re.compile(r"[1-5]\d\d(-[1-5]\d\d)?")


def _whole(value: Any, low: int, high: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def _check_host_port(target: str, default_port: int | None) -> tuple[str, int] | None:
    """(host, port) from "host:port", "[ipv6]:port", or a bare host with ``default_port``; None if unusable."""
    if target.startswith("["):
        inner, _, rest = target[1:].partition("]")
        if not _is_ip_literal(inner):
            return None
        if rest == "":
            port = default_port
        elif rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
        else:
            return None
        host = inner
    else:
        head, sep, tail = target.rpartition(":")
        if sep and tail.isdigit() and head:
            host, port = head, int(tail)
        elif not sep:
            host, port = target, default_port
        else:
            return None
        if not _CHECK_HOST.fullmatch(host):
            return None
    if port is None or not 1 <= int(port) <= 65535:
        return None
    return host, int(port)


def _is_ip_literal(text: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, text)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, text)
        return True
    except OSError:
        return False


def validate_check(raw: Any) -> dict[str, Any] | None:
    """One check as the dashboard sent it, reduced to known fields of the right type and range; None if unusable."""
    if not isinstance(raw, dict) or raw.get("type") not in CHECK_TYPES or not isinstance(raw.get("target"), str):
        return None
    check_id = _whole(raw.get("id"), 1, 2**31 - 1)
    interval = _whole(raw.get("interval"), MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL)
    retry_interval = _whole(raw.get("retry_interval"), MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL)
    timeout = _whole(raw.get("timeout"), 1, MAX_CHECK_TIMEOUT)
    if None in (check_id, interval, retry_interval, timeout):
        return None
    kind, target = raw["type"], raw["target"].strip()
    check: dict[str, Any] = {"id": check_id, "type": kind, "target": target, "interval": interval, "retry_interval": retry_interval, "timeout": timeout}
    if kind in ("tcp", "tls"):
        parsed = _check_host_port(target, DEFAULT_TLS_PORT if kind == "tls" else None)
        if not parsed:
            return None
        check.update(host=parsed[0], port=parsed[1])
        if kind == "tls":
            warn_days = _whole(raw.get("warn_days"), 1, 3650)
            if warn_days is None:
                return None
            check["warn_days"] = warn_days
        return check
    if kind == "ping":
        if not (_is_ip_literal(target.strip("[]")) or _CHECK_HOST.fullmatch(target)):
            return None
        check["host"] = target.strip("[]")
        return check
    if kind == "dns":
        if not _CHECK_HOST.fullmatch(target) or raw.get("record_type") not in DNS_RECORD_NUMS:
            return None
        resolver = str(raw.get("resolver") or "").strip()
        if resolver and not _is_ip_literal(resolver):
            return None
        expected = raw.get("expected")
        check.update(record_type=raw["record_type"], resolver=resolver, expected=expected if isinstance(expected, str) else "")
        return check
    # url types: http, keyword, json
    parts = urllib.parse.urlsplit(target)
    if parts.scheme not in ("http", "https") or not parts.hostname or len(target) > 2048 or any(char.isspace() or not char.isprintable() for char in target):
        return None
    codes = raw.get("accepted_codes")
    if not isinstance(codes, list) or not 1 <= len(codes) <= 10 or not all(isinstance(code, str) and _CHECK_CODES.fullmatch(code) for code in codes):
        return None
    check.update(accepted_codes=codes, follow_redirects=raw.get("follow_redirects") is True, ignore_tls=raw.get("ignore_tls") is True,
                 method="HEAD" if raw.get("method") == "HEAD" and kind == "http" else "GET")
    if kind == "keyword":
        keyword = raw.get("keyword")
        if not isinstance(keyword, str) or not 1 <= len(keyword) <= 200:
            return None
        check.update(keyword=keyword, case_sensitive=raw.get("case_sensitive") is True)
    if kind == "json":
        path, operator, expect = raw.get("json_path"), raw.get("json_op"), raw.get("json_expect")
        if not isinstance(path, str) or not 1 <= len(path) <= 200 or operator not in JSON_OPS:
            return None
        if operator != "exists" and not (isinstance(expect, str) and expect):
            return None
        check.update(json_path=path, json_op=operator, json_expect=expect if isinstance(expect, str) else "", method="GET")
    return check


def _code_accepted(code: int, accepted: list[str]) -> bool:
    for item in accepted:
        low, _, high = item.partition("-")
        if int(low) <= code <= int(high or low):
            return True
    return False


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follows at most MAX_REDIRECTS redirects, and only to http and https, or none at all."""
    max_redirections = MAX_REDIRECTS

    def __init__(self, follow: bool) -> None:
        self.follow = follow

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not self.follow or urllib.parse.urlsplit(newurl).scheme not in ("http", "https"):
            return None   # urllib then raises HTTPError with the 3xx code, which the accepted codes decide on
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _cert_days(response: Any) -> int | None:
    """Days until the server's TLS certificate expires, when it was verified."""
    try:
        cert = response.fp.raw._sock.getpeercert()
        expires = ssl.cert_time_to_seconds(cert["notAfter"])
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None
    return int((expires - time.time()) // 86400)


def _failure(exc: BaseException, timeout: int) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(exc, (TimeoutError, socket.timeout)):
        return f"Timed out after {timeout} s"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return f"TLS certificate not valid: {reason.verify_message}"[:200]
    if isinstance(reason, ssl.SSLError):
        return "TLS handshake failed"
    if isinstance(reason, socket.gaierror):
        return "Host name not found"
    if isinstance(reason, ConnectionRefusedError):
        return "Connection refused"
    if isinstance(reason, ConnectionResetError):
        return "Connection reset"
    if isinstance(reason, OSError) and reason.strerror:
        return reason.strerror[:200]
    return type(reason).__name__


def run_http(check: dict[str, Any]) -> dict[str, Any]:
    """One run of an HTTP(s) or keyword check: {ok, response_ms, code, detail, cert_days}."""
    context = ssl.create_default_context()
    if check["ignore_tls"]:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), _Redirects(check["follow_redirects"]))
    request = urllib.request.Request(check["target"], method=check["method"], headers={"User-Agent": f"Infradeck-Check/{COLLECTOR_VERSION}", "Accept": "*/*"})
    started = time.monotonic()
    result: dict[str, Any] = {"ok": False, "response_ms": None, "code": None, "detail": None, "cert_days": None}
    body = b""
    try:
        with opener.open(request, timeout=check["timeout"]) as response:
            result["code"] = response.status
            if not check["ignore_tls"]:
                result["cert_days"] = _cert_days(response)
            if check["type"] in ("keyword", "json"):
                deadline = started + check["timeout"]
                while len(body) < MAX_BODY_BYTES and time.monotonic() < deadline:
                    chunk = response.read(min(65536, MAX_BODY_BYTES - len(body)))
                    if not chunk:
                        break
                    body += chunk
    except urllib.error.HTTPError as exc:
        result["code"] = exc.code
        exc.close()
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        result["detail"] = _failure(exc, check["timeout"])
        return result
    result["response_ms"] = int((time.monotonic() - started) * 1000)
    if not _code_accepted(result["code"], check["accepted_codes"]):
        result["detail"] = f"HTTP {result['code']}"
        return result
    if check["type"] == "keyword":
        text = body.decode("utf-8", errors="replace")
        found = check["keyword"] in text if check["case_sensitive"] else check["keyword"].casefold() in text.casefold()
        if not found:
            result["detail"] = "Keyword not found"
            return result
    if check["type"] == "json":
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            result["detail"] = "The reply is not JSON"
            return result
        try:
            value = _json_walk(data, check["json_path"])
        except (KeyError, IndexError, TypeError):
            result["detail"] = f"No value at {check['json_path']}"[:200]
            return result
        if not _json_match(value, check["json_op"], check.get("json_expect", "")):
            result["detail"] = f"{check['json_path']} is {json.dumps(value)[:80]}"[:200]
            return result
    result["ok"] = True
    return result


def _json_walk(data: Any, path: str) -> Any:
    """The value at a dotted/indexed path like ``data.status`` or ``items[0].ok``; raises if it isn't there."""
    current = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            current = current[int(part[1:-1])]
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(part)
    return current


def _json_match(value: Any, operator: str, expect: str) -> bool:
    if operator == "exists":
        return True
    if isinstance(value, bool):
        shown = "true" if value else "false"
    elif value is None:
        shown = "null"
    elif isinstance(value, (dict, list)):
        shown = json.dumps(value, separators=(",", ":"))
    else:
        shown = str(value)
    if operator == "equals":
        return shown.strip().casefold() == expect.strip().casefold()
    if operator == "contains":
        return expect in shown
    try:
        return (float(shown) > float(expect)) if operator == "gt" else (float(shown) < float(expect))
    except ValueError:
        return False


def run_tls(check: dict[str, Any]) -> dict[str, Any]:
    """Connect, read the server's certificate, and be down when it is invalid, expired, or close to expiring."""
    context = ssl.create_default_context()
    started = time.monotonic()
    try:
        with socket.create_connection((check["host"], check["port"]), timeout=check["timeout"]) as sock:
            with context.wrap_socket(sock, server_hostname=check["host"]) as tls:
                cert = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return {"ok": False, "response_ms": None, "code": None, "detail": f"Certificate not valid: {getattr(exc, 'verify_message', None) or exc}"[:200], "cert_days": None}
    except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
        return {"ok": False, "response_ms": None, "code": None, "detail": _failure(exc, check["timeout"]), "cert_days": None}
    response_ms = int((time.monotonic() - started) * 1000)
    try:
        days = int((ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) // 86400)
    except (KeyError, ValueError, TypeError):
        return {"ok": False, "response_ms": response_ms, "code": None, "detail": "The certificate has no expiry date", "cert_days": None}
    ok = days >= check.get("warn_days", 14)
    detail = None if ok else (f"Certificate expired {abs(days)} d ago" if days < 0 else f"Certificate expires in {days} d")
    return {"ok": ok, "response_ms": response_ms, "code": None, "detail": detail, "cert_days": days}


def _system_resolver() -> str:
    try:
        for line in Path("/etc/resolv.conf").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "nameserver" and _is_ip_literal(parts[1]):
                return parts[1]
    except OSError:
        pass
    return ""


def _skip_name(data: bytes, off: int) -> int:
    while off < len(data):
        length = data[off]
        if length == 0:
            return off + 1
        if length & 0xC0 == 0xC0:   # a compression pointer ends the name
            return off + 2
        off += 1 + length
    return off


def _read_name(data: bytes, off: int) -> str:
    labels, hops = [], 0
    while off < len(data) and hops < 64:
        length = data[off]
        if length == 0:
            break
        if length & 0xC0 == 0xC0:
            off = ((length & 0x3F) << 8) | data[off + 1]
            hops += 1
            continue
        labels.append(data[off + 1:off + 1 + length].decode("ascii", "replace"))
        off += 1 + length
    return ".".join(labels)


def _render_rdata(want_type: int, data: bytes, off: int, rdlen: int) -> str:
    rdata = data[off:off + rdlen]
    if want_type == 1 and rdlen == 4:
        return socket.inet_ntop(socket.AF_INET, rdata)
    if want_type == 28 and rdlen == 16:
        return socket.inet_ntop(socket.AF_INET6, rdata)
    if want_type in (2, 5):   # NS, CNAME
        return _read_name(data, off)
    if want_type == 15 and rdlen >= 3:   # MX: preference + exchange
        return f"{struct.unpack('>H', rdata[:2])[0]} {_read_name(data, off + 2)}"
    if want_type == 16:   # TXT: one or more length-prefixed strings
        parts, index = [], 0
        while index < rdlen:
            piece = rdata[index + 1:index + 1 + rdata[index]]
            parts.append(piece.decode("utf-8", "replace"))
            index += 1 + rdata[index]
        return "".join(parts)
    return ""


class _DnsError(Exception):
    """A DNS reply that says the lookup failed, with a message safe to show."""


def _dns_query(name: str, want_type: int, resolver: str, timeout: int) -> list[str]:
    tid = os.urandom(2)
    header = tid + struct.pack(">HHHHH", 0x0100, 1, 0, 0, 0)   # one question, recursion desired
    question = b"".join(bytes([len(label)]) + label for label in (part.encode("idna") for part in name.split(".") if part)) + b"\x00"
    packet = header + question + struct.pack(">HH", want_type, 1)
    family = socket.AF_INET6 if ":" in resolver else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (resolver, 53))
        deadline = time.monotonic() + timeout
        data = b""
        while time.monotonic() < deadline:
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            data, _ = sock.recvfrom(4096)
            if len(data) >= 12 and data[:2] == tid:
                break
    if len(data) < 12 or data[:2] != tid:
        raise _DnsError("No answer from the DNS server")
    return _parse_dns(data, want_type)


def _parse_dns(data: bytes, want_type: int) -> list[str]:
    flags, qd, an = struct.unpack(">HHH", data[2:8])
    rcode = flags & 0x0F
    if rcode == 3:
        raise _DnsError("The name does not exist (NXDOMAIN)")
    if rcode != 0:
        raise _DnsError(f"The DNS server returned an error (rcode {rcode})")
    off = 12
    for _ in range(qd):
        off = _skip_name(data, off) + 4
    answers = []
    for _ in range(an):
        off = _skip_name(data, off)
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
        off += 10
        if rtype == want_type:
            rendered = _render_rdata(want_type, data, off, rdlen)
            if rendered:
                answers.append(rendered)
        off += rdlen
    return answers


def run_dns(check: dict[str, Any]) -> dict[str, Any]:
    """Ask a DNS server for a record and be down when it doesn't resolve or doesn't match the expected value."""
    resolver = check.get("resolver") or _system_resolver()
    if not resolver:
        return {"ok": False, "response_ms": None, "code": None, "detail": "No DNS server to ask", "cert_days": None}
    want_type = DNS_RECORD_NUMS[check["record_type"]]
    started = time.monotonic()
    try:
        answers = _dns_query(check["target"].rstrip("."), want_type, resolver, check["timeout"])
    except _DnsError as exc:
        return {"ok": False, "response_ms": None, "code": None, "detail": str(exc)[:200], "cert_days": None}
    except (OSError, UnicodeError, struct.error) as exc:
        return {"ok": False, "response_ms": None, "code": None, "detail": _failure(exc, check["timeout"]), "cert_days": None}
    response_ms = int((time.monotonic() - started) * 1000)
    if not answers:
        return {"ok": False, "response_ms": response_ms, "code": None, "detail": f"No {check['record_type']} record", "cert_days": None}
    expected = (check.get("expected") or "").strip()
    if expected and not any(expected.casefold() == answer.casefold() or (check["record_type"] == "TXT" and expected in answer) for answer in answers):
        return {"ok": False, "response_ms": response_ms, "code": None, "detail": f"Resolved to {', '.join(answers)}"[:200], "cert_days": None}
    return {"ok": True, "response_ms": response_ms, "code": None, "detail": None, "cert_days": None}


def _icmp_checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(">%dH" % (len(data) // 2), data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def run_ping(check: dict[str, Any]) -> dict[str, Any]:
    """One ICMP echo over an unprivileged datagram socket (no raw socket, no shell). The container allows this only
    for its own group via net.ipv4.ping_group_range; without that the result says ICMP isn't permitted."""
    try:
        family, _, _, _, sockaddr = socket.getaddrinfo(check["host"], None)[0]
    except (socket.gaierror, IndexError):
        return {"ok": False, "response_ms": None, "code": None, "detail": "Host name not found", "cert_days": None}
    address = sockaddr[0]
    if family == socket.AF_INET6:
        proto, echo_type, reply_type = socket.IPPROTO_ICMPV6, 128, 129
    else:
        proto, echo_type, reply_type = socket.IPPROTO_ICMP, 8, 0
    identifier = os.getpid() & 0xFFFF
    body = struct.pack(">BBHHH", echo_type, 0, 0, identifier, 1) + b"infradeck"
    packet = struct.pack(">BBHHH", echo_type, 0, _icmp_checksum(body), identifier, 1) + b"infradeck"
    started = time.monotonic()
    try:
        with socket.socket(family, socket.SOCK_DGRAM, proto) as sock:
            sock.settimeout(check["timeout"])
            sock.sendto(packet, (address, 0))
            deadline = time.monotonic() + check["timeout"]
            while time.monotonic() < deadline:
                sock.settimeout(max(0.05, deadline - time.monotonic()))
                reply, _ = sock.recvfrom(1024)
                if reply and reply[0] == reply_type:
                    return {"ok": True, "response_ms": int((time.monotonic() - started) * 1000), "code": None, "detail": None, "cert_days": None}
    except PermissionError:
        return {"ok": False, "response_ms": None, "code": None, "detail": "ICMP is not permitted on this collector (ping_group_range)", "cert_days": None}
    except (OSError, struct.error) as exc:
        return {"ok": False, "response_ms": None, "code": None, "detail": _failure(exc, check["timeout"]), "cert_days": None}
    return {"ok": False, "response_ms": None, "code": None, "detail": "No reply", "cert_days": None}


def run_tcp(check: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with socket.create_connection((check["host"], check["port"]), timeout=check["timeout"]):
            pass
    except OSError as exc:
        return {"ok": False, "response_ms": None, "code": None, "detail": _failure(exc, check["timeout"]), "cert_days": None}
    return {"ok": True, "response_ms": int((time.monotonic() - started) * 1000), "code": None, "detail": None, "cert_days": None}


_RUNNERS = {"tcp": lambda c: run_tcp(c), "tls": lambda c: run_tls(c), "dns": lambda c: run_dns(c), "ping": lambda c: run_ping(c)}


def run_check(check: dict[str, Any]) -> dict[str, Any]:
    try:
        outcome = _RUNNERS.get(check["type"], run_http)(check)
    except Exception as exc:  # one broken check must never stop the others
        outcome = {"ok": False, "response_ms": None, "code": None, "detail": f"Check failed: {type(exc).__name__}", "cert_days": None}
    return {"check_id": check["id"], "time": datetime.now(timezone.utc).isoformat(), **{key: value for key, value in outcome.items() if value is not None or key == "ok"}}


def send_check_results(api_url: str, server_id: int, token: str, results: list[dict[str, Any]], tests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/monitoring/metrics/checks",
        data=json.dumps({"server_id": server_id, "results": results, "tests": tests or []}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read(MAX_BODY_BYTES).decode())


MAX_TESTS = 5


class CheckRunner(threading.Thread):
    """Runs the checks the dashboard gave this collector, each on its interval (the retry interval after a
    failure), and reports the results every few seconds; with nothing to report it asks for its checks every
    10 s (as the dashboard says), so a test from the New check form is picked up quickly. A test runs once, at
    once, and its result goes back with the next report. Results that couldn't be sent wait, up to the newest 500."""

    def __init__(self, api_url: str, credentials: tuple[int, str]) -> None:
        super().__init__(name="checks", daemon=True)
        self.api_url = api_url
        self.credentials = credentials   # the main loop replaces this after joining again
        self.checks: dict[int, dict[str, Any]] = {}
        self.next_run: dict[int, float] = {}
        self.running: set[int] = set()
        self.pending: collections.deque = collections.deque(maxlen=MAX_PENDING_RESULTS)
        self.lock = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=CHECK_WORKERS, thread_name_prefix="check")
        self.report_every = 5
        self.idle_every = IDLE_REPORT_SECONDS
        self.test_results: collections.deque = collections.deque(maxlen=MAX_TESTS * 2)

    def run_tests(self, raw: Any) -> None:
        """Run each test once, right away; its result is reported apart from the checks' results."""
        for test in (validate_check(item) for item in (raw if isinstance(raw, list) else [])[:MAX_TESTS]):
            if test:
                self.pool.submit(run_check, test).add_done_callback(lambda future: self._tested(future))

    def _tested(self, future: concurrent.futures.Future) -> None:
        with self.lock:
            self.test_results.append(future.result())

    def set_checks(self, raw: Any) -> None:
        valid = [check for check in (validate_check(item) for item in (raw if isinstance(raw, list) else [])[:MAX_CHECKS]) if check]
        with self.lock:
            new = {check["id"]: check for check in valid}
            for check_id, check in new.items():
                if self.checks.get(check_id) != check:
                    self.next_run[check_id] = time.monotonic()   # new or changed: run it now
            self.checks = new
            for check_id in list(self.next_run):
                if check_id not in new:
                    del self.next_run[check_id]

    def _done(self, check: dict[str, Any], future: concurrent.futures.Future) -> None:
        result = future.result()
        with self.lock:
            self.running.discard(check["id"])
            self.pending.append(result)
            if check["id"] in self.checks:
                self.next_run[check["id"]] = time.monotonic() + (check["interval"] if result["ok"] else check["retry_interval"])

    def run_due(self) -> None:
        now = time.monotonic()
        with self.lock:
            due = [check for check_id, check in self.checks.items() if self.next_run.get(check_id, 0) <= now and check_id not in self.running]
            self.running.update(check["id"] for check in due)
        for check in due:
            self.pool.submit(run_check, check).add_done_callback(lambda future, check=check: self._done(check, future))

    def report(self) -> bool:
        with self.lock:
            batch, tested = list(self.pending), list(self.test_results)
            self.pending.clear()
            self.test_results.clear()
        server_id, token = self.credentials

        def put_back() -> None:
            with self.lock:
                self.pending.extendleft(reversed(batch))
                self.test_results.extendleft(reversed(tested))

        try:
            response = send_check_results(self.api_url, server_id, token, batch, tested)
        except urllib.error.HTTPError as exc:
            put_back()
            if exc.code == 404:
                self.report_every = self.idle_every = 300   # a dashboard without availability checks
            return False
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
            put_back()
            return False
        if not isinstance(response, dict):
            response = {}
        self.set_checks(response.get("checks"))
        self.run_tests(response.get("tests"))
        self.report_every = _whole(response.get("report_every"), 2, 300) or 5
        self.idle_every = _whole(response.get("idle_every"), 5, 300) or IDLE_REPORT_SECONDS
        if batch:
            print(f"Reported {len(batch)} check results", flush=True)
        return True

    def run(self) -> None:
        last_report = -IDLE_REPORT_SECONDS
        while True:
            self.run_due()
            since = time.monotonic() - last_report
            # Test results go back at once (the dashboard allows a report every 2 s); check results every few seconds.
            if since >= self.idle_every or (self.test_results and since >= 2) or (self.pending and since >= self.report_every):
                self.report()
                last_report = time.monotonic()
            time.sleep(1)


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
    override = os.getenv("INFRADECK_MACHINE_ID")
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


LEGACY_STATE_DIR = Path("/var/lib/homelab-collector")


def _credentials_path() -> Path:
    """Where this collector keeps its credentials. A collector installed before the rename to Infradeck has its state
    volume mounted at the old path; it keeps using that, so an image update doesn't make it ask to join again."""
    configured = os.getenv("INFRADECK_STATE_DIR")
    if configured:
        return Path(configured) / "credentials.json"
    current = Path("/var/lib/infradeck-collector") / "credentials.json"
    legacy = LEGACY_STATE_DIR / "credentials.json"
    return legacy if not current.exists() and LEGACY_STATE_DIR.is_dir() else current


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
    # Created with mode 0600 from the start, so there is no moment when the token sits in a file others can read.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(json.dumps({"server_id": server_id, "collector_token": token}))
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
            registration = _post_json(f"{base}/monitoring/collectors/register", {"hostname": _host_hostname(), "machine_id": _machine_id(), "os_info": _host_os_info(), "join_token": os.getenv("INFRADECK_JOIN_TOKEN", "").strip() or None, "details": registration_details()})
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
    if os.getenv("INFRADECK_SERVER_ID") and os.getenv("INFRADECK_COLLECTOR_TOKEN"):
        return int(os.environ["INFRADECK_SERVER_ID"]), os.environ["INFRADECK_COLLECTOR_TOKEN"]
    saved = load_credentials()
    if saved:
        return saved
    credentials = None
    enroll_key = os.getenv("INFRADECK_ENROLL_KEY", "").strip()
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
    api_url = os.getenv("INFRADECK_API_URL", "http://localhost:8060")
    server_id, token = resolve_credentials(api_url)
    interval = max(15, int(os.getenv("INFRADECK_CHECK_INTERVAL_SECONDS", "60")))
    print(f"Collector started for server {server_id}; API={api_url}", flush=True)
    checks = CheckRunner(api_url, (server_id, token))
    checks.start()
    while True:
        started = time.monotonic()
        try:
            server, host = collect_host()
            containers, docker = collect_docker()
            host.update(docker)
            # How long gathering took, so the dashboard can warn when it no longer fits in the check interval.
            host["collect_duration_ms"] = int((time.monotonic() - started) * 1000)
            response = send_payload(api_url, server_id, token, {"server": server, "host": host, "containers": containers})
            interval = max(15, int(response.get("interval_seconds", interval)))
            print(f"Sent host metrics and {len(containers)} container records; next check in {interval}s", flush=True)
            delay = max(0, interval - (time.monotonic() - started))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 404) and not os.getenv("INFRADECK_SERVER_ID"):
                # Removed from the dashboard: ask to join again. This needs a fresh Add, so a
                # deleted server never comes back on its own.
                print(f"Backend no longer accepts server {server_id} (HTTP {exc.code}); requesting approval again", flush=True)
                _credentials_path().unlink(missing_ok=True)
                server_id, token = request_approval(api_url)
                checks.credentials = (server_id, token)
                try:
                    save_credentials(server_id, token)
                except OSError:
                    pass
                delay = 0
            elif exc.code in (401, 404):
                print(f"Backend rejected server {server_id} (HTTP {exc.code}); check INFRADECK_SERVER_ID / INFRADECK_COLLECTOR_TOKEN", flush=True)
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
