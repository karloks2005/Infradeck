"""Optional privileged helper: reads the kernel log and wtmp, writes a small sanitized JSON summary.

Runs in its own container (the ``system-events`` compose profile) with a single capability,
CAP_SYSLOG, no network, a read-only filesystem and wtmp mounted read-only. The collector
reads the summary from a shared volume; it never touches the kernel log itself.

Everything parsed here is attacker-influenced (a process can name itself anything, usernames
and hostnames land in wtmp), so nothing is ever executed or interpolated: lines are matched
with fixed patterns, strings are reduced to printable text and truncated, and the output has a
fixed schema. The helper is stateless; each run re-reads the kernel ring buffer and publishes
a snapshot, and the backend de-duplicates events by their key.

Standard library only. Run with ``python -m src.agent.system_helper`` or as a standalone script.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import re
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOST_PROC = Path(os.getenv("HOST_PROC") or "/proc")
WTMP_PATH = Path(os.getenv("HOMELAB_WTMP_PATH", "/host/wtmp"))
OUTPUT_PATH = Path(os.getenv("HOMELAB_HELPER_OUTPUT", "/out/system.json"))

SYSLOG_ACTION_READ_ALL = 3  # reads the ring buffer without clearing it
SYSLOG_ACTION_SIZE_BUFFER = 10
MAX_KLOG_BYTES = 8 * 1024 * 1024
MAX_OOM_EVENTS = 20
MAX_ERROR_EVENTS = 10
MAX_TEXT = 200
MAX_PROCESS_NAME = 64

# "<6>[ 3800.756804] message": priority, seconds since boot, text. Lines without a timestamp are ignored.
_KLOG_LINE = re.compile(r"^<(\d{1,3})>\[\s*(\d{1,10}\.\d{6})\] (.*)$")
_OOM_KILL = re.compile(r"[Kk]illed process (\d{1,10}) \(([^)]{0,200})\)")
ERROR_PRIORITY = 3  # kern.err and worse

# struct utmp on 64-bit Linux (glibc): 384 bytes, fixed layout.
_UTMP = struct.Struct("<h2xi32s4s32s256shhiii4i20x")
_RUN_LVL, _BOOT_TIME = 1, 2
MAX_WTMP_RECORDS = 5000
BOOT_TIME_TOLERANCE_SECONDS = 300


def sanitize(text: str, limit: int = MAX_TEXT) -> str:
    """Printable characters only, so log content can't carry control sequences or hide in the output."""
    return "".join(char if char.isprintable() else "?" for char in text).strip()[:limit]


def _decode(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def read_kernel_log() -> str:
    """The kernel ring buffer; needs CAP_SYSLOG when kernel.dmesg_restrict is set."""
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    size = libc.klogctl(SYSLOG_ACTION_SIZE_BUFFER, None, 0)
    if size <= 0:
        raise OSError(ctypes.get_errno(), "cannot size the kernel log")
    size = min(size, MAX_KLOG_BYTES)
    buffer = ctypes.create_string_buffer(size)
    read = libc.klogctl(SYSLOG_ACTION_READ_ALL, buffer, size)
    if read < 0:
        raise OSError(ctypes.get_errno(), "cannot read the kernel log")
    return buffer.raw[:read].decode("utf-8", errors="replace")


def parse_kernel_log(text: str, boot_id: str, monotonic_now: float, wall_now: float) -> list[dict[str, str]]:
    """OOM kills and kernel errors as events with wall-clock times, oldest first."""
    oom: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for line in text.splitlines():
        match = _KLOG_LINE.match(line)
        if not match:
            continue
        priority, stamp, message = int(match.group(1)) & 7, match.group(2), match.group(3)
        # printk timestamps are CLOCK_MONOTONIC, the same clock as time.monotonic().
        when = datetime.fromtimestamp(wall_now - (monotonic_now - float(stamp)), timezone.utc).isoformat(timespec="seconds")
        killed = _OOM_KILL.search(message)
        if killed and "ut of memory" in message:
            oom.append({"kind": "oom_kill", "time": when, "key": f"{boot_id}:oom:{stamp}",
                        "detail": f"The kernel OOM killer stopped {sanitize(killed.group(2), MAX_PROCESS_NAME) or 'a process'} (pid {killed.group(1)})"})
        elif priority <= ERROR_PRIORITY:
            errors.append({"kind": "kernel_error", "time": when, "key": f"{boot_id}:err:{stamp}", "detail": sanitize(message)})
    return oom[-MAX_OOM_EVENTS:] + errors[-MAX_ERROR_EVENTS:]


def read_wtmp_records(path: Path = WTMP_PATH) -> list[tuple[int, str, int]]:
    """(type, user, unix time) for the most recent records; [] when unreadable."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return []
    try:
        size = os.fstat(fd).st_size
        records = min(size // _UTMP.size, MAX_WTMP_RECORDS)
        os.lseek(fd, (size // _UTMP.size - records) * _UTMP.size, os.SEEK_SET)
        data = b""
        while len(data) < records * _UTMP.size:
            chunk = os.read(fd, records * _UTMP.size - len(data))
            if not chunk:
                break
            data += chunk
    except OSError:
        return []
    finally:
        os.close(fd)
    result = []
    for offset in range(0, len(data) - _UTMP.size + 1, _UTMP.size):
        fields = _UTMP.unpack_from(data, offset)
        result.append((fields[0], _decode(fields[4]), fields[9]))
    return result


def classify_last_shutdown(records: list[tuple[int, str, int]], boot_time: float) -> str:
    """How the boot before this one ended: "clean", "unclean" or "unknown".

    A clean shutdown writes a "shutdown" record before the next boot record; a crash or power
    loss leaves none. It only counts as unclean when wtmp has recorded shutdowns before, so a
    system that never writes them isn't reported as crashing on every reboot.
    """
    boots = [index for index, (kind, _user, _time) in enumerate(records) if kind == _BOOT_TIME]
    if len(boots) < 2 or abs(records[boots[-1]][2] - boot_time) > BOOT_TIME_TOLERANCE_SECONDS:
        return "unknown"
    previous, latest = boots[-2], boots[-1]
    is_shutdown = lambda record: record[0] == _RUN_LVL and record[1] == "shutdown"  # noqa: E731
    if any(is_shutdown(record) for record in records[previous + 1:latest]):
        return "clean"
    return "unclean" if any(is_shutdown(record) for record in records[:latest]) else "unknown"


def build_snapshot() -> dict[str, Any]:
    boot_id = (HOST_PROC / "sys/kernel/random/boot_id").read_text().strip()[:64]
    wall_now, monotonic_now = time.time(), time.monotonic()
    snapshot: dict[str, Any] = {
        "version": 1,
        "generated_at": datetime.fromtimestamp(wall_now, timezone.utc).isoformat(timespec="seconds"),
        "boot_id": boot_id,
        "last_shutdown": "unknown",
        "events": [],
    }
    try:
        snapshot["events"] = parse_kernel_log(read_kernel_log(), boot_id, monotonic_now, wall_now)
    except OSError as error:
        print(f"Kernel log unavailable ({error}); is CAP_SYSLOG granted?", flush=True)
    uptime = float((HOST_PROC / "uptime").read_text().split()[0])
    snapshot["last_shutdown"] = classify_last_shutdown(read_wtmp_records(), wall_now - uptime)
    return snapshot


def write_snapshot(snapshot: dict[str, Any], path: Path = OUTPUT_PATH) -> None:
    """Atomic replace, so the collector never reads a half-written file."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(snapshot, separators=(",", ":")))
    temporary.chmod(0o644)
    temporary.replace(path)


def run() -> None:
    interval = max(10, int(os.getenv("HOMELAB_HELPER_INTERVAL_SECONDS", "30")))
    print(f"System helper started; writing {OUTPUT_PATH} every {interval}s", flush=True)
    while True:
        started = time.monotonic()
        try:
            write_snapshot(build_snapshot())
        except (OSError, ValueError) as error:
            print(f"Snapshot failed ({type(error).__name__}): {error}", flush=True)
        time.sleep(max(1, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    run()
