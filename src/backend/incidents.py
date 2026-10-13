"""Incidents as the Agent page shows them: an inbox, and for each incident a diagnosis built from the data.

Nothing here is guessed or written by a language model. The summary is the rule's own description, the evidence is
the metric the rule looks at, the signals are checks against stored samples, and the fixes come from a fixed
playbook per kind of rule, with the container or mount name filled in.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from .alerts import CRITICAL, INFO, SERVER_OFFLINE_RULE, WARNING
from .dashboard import _event_source
from .models import ContainerMetric, HostMetric, IncidentAlert, MountMetric, Server, SystemEvent, User

MAX_LISTED = 200
EVIDENCE_POINTS = 120
EVIDENCE_LEAD = timedelta(minutes=60)
MAX_LINES = 14
MAX_TIMELINE = 12
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _utc(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def rule_kind(rule_key: str | None) -> str:
    """The kind of rule behind an alert, without the container, mount or port it is about:
    ``container:gitlab:restart_loop`` -> ``container:restart_loop``, ``host:disk:/mnt/x`` -> ``host:disk``."""
    if not rule_key:
        return "other"
    parts = rule_key.split(":")
    if parts[0] == "container" and len(parts) >= 3:
        return f"container:{parts[-1]}"
    if parts[0] == "trend":
        return "trend:memory"
    if parts[0] in ("host", "system", "network") and len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return rule_key


def subject(rule_key: str | None) -> str | None:
    """The container, mount or port a rule is about, when it names one."""
    parts = (rule_key or "").split(":")
    if parts[0] in ("container", "trend") and len(parts) >= 3:
        return ":".join(parts[1:-1])
    if parts[0] in ("host", "network") and len(parts) >= 3:
        return ":".join(parts[2:])
    return None


def reference(alert_id: int) -> str:
    return f"INC-{alert_id:04d}"


def is_muted(alert: IncidentAlert, now: datetime) -> bool:
    return alert.muted_until is not None and _utc(alert.muted_until) > now


def state_of(alert: IncidentAlert) -> str:
    """What the inbox says under the title: diagnosed (there are steps to take) or watching (nothing to do yet)."""
    return "watching" if alert.severity == INFO or rule_kind(alert.rule_key) in ("trend:memory", "host:swap") else "diagnosed"


def _command_name(name: str | None) -> str:
    """A container name that is safe to show inside a command to copy; anything unusual becomes a placeholder."""
    return name if name and _SAFE_NAME.fullmatch(name) else "<container>"


def _gb(megabytes: float) -> str:
    return f"{megabytes / 1024:.1f} GB" if megabytes >= 1024 else f"{megabytes:.0f} MB"


def _step(title: str, text: str, command: str | None = None, label: str | None = None, recommended: bool = False) -> dict:
    return {"title": title, "text": text, "command": command, "label": label, "recommended": recommended}


def _downsample(rows: list[tuple[datetime, float | None]], limit: int = EVIDENCE_POINTS) -> list[dict]:
    if len(rows) > limit:
        size = math.ceil(len(rows) / limit)
        grouped = []
        for start in range(0, len(rows), size):
            chunk = [value for _, value in rows[start:start + size] if value is not None]
            # The highest value in each group: an incident is about peaks, and an average would hide them.
            grouped.append((rows[start][0], max(chunk) if chunk else None))
        rows = grouped
    return [{"time": _utc(moment).isoformat(), "value": None if value is None else round(value, 2)} for moment, value in rows]


def _container_samples(db, server_id: int, name: str, since: datetime, until: datetime) -> list[ContainerMetric]:
    return db.query(ContainerMetric).filter(ContainerMetric.server_id == server_id, ContainerMetric.name == name, ContainerMetric.time >= since, ContainerMetric.time <= until).order_by(ContainerMetric.time).limit(5000).all()


def _container_diagnosis(db, alert: IncidentAlert, server: Server, kind: str, since: datetime, until: datetime) -> dict:
    name = subject(alert.rule_key) or ""
    samples = _container_samples(db, server.id, name, since, until)
    shown = _command_name(name)
    latest = samples[-1] if samples else None
    host_mb = (server.total_ram_gb or 0) * 1024
    limit = latest.memory_limit_mb if latest and latest.memory_limit_mb and (not host_mb or latest.memory_limit_mb < host_mb * 0.98) else None
    restarts = [(sample.time, sample.restart_count) for sample in samples]
    marks = [{"time": _utc(moment).isoformat(), "label": "restart"} for (_, before), (moment, after) in zip(restarts, restarts[1:]) if after > before]
    peak = max((sample.memory_used_mb for sample in samples if sample.memory_used_mb is not None), default=None)
    oom = any(sample.oom_killed for sample in samples)
    codes = sorted({sample.exit_code for sample in samples if sample.exit_code is not None})
    signals = []
    if restarts:
        rose = restarts[-1][1] - restarts[0][1]
        signals.append({"text": f"The restart count rose by {rose} in this window.", "source": f"{len(marks)} of {len(samples)} check-ins", "ok": rose > 0})
    if limit and peak is not None:
        signals.append({"text": f"Memory reached {_gb(peak)} of its {_gb(limit)} limit.", "source": "container memory", "ok": peak >= limit * 0.9})
    if oom:
        signals.append({"text": "Docker reports the last exit as an out-of-memory kill.", "source": "container state", "ok": True})
    elif codes:
        signals.append({"text": f"Exit code{'s' if len(codes) > 1 else ''} seen: {', '.join(map(str, codes))}{' (137 means it was killed)' if 137 in codes else ''}.", "source": "container state", "ok": True})
    if latest and latest.healthcheck_status:
        signals.append({"text": f"Its healthcheck reports {latest.healthcheck_status}.", "source": "docker healthcheck", "ok": latest.healthcheck_status == "unhealthy"})
    memory_bound = oom or (limit is not None and peak is not None and peak >= limit * 0.9)
    steps = []
    if memory_bound and limit:
        bigger = max(1, math.ceil(limit * 1.5 / 1024))
        steps.append(_step(f"Raise the memory limit to {bigger} GB", f"{name} runs into its {_gb(limit)} limit. In its compose file, raise the limit and recreate the container:", f"mem_limit: {bigger}g", "compose", True))
    elif memory_bound:
        steps.append(_step("Find what is using the memory", f"{name} has no memory limit of its own, so the host ran out. See which containers hold the most:", "docker stats --no-stream --format 'table {{.Name}}\\t{{.MemUsage}}' | sort -k2 -h", "shell", True))
    steps.append(_step("Read why it exits" if kind != "container:unhealthy" else "Read what the healthcheck says", "The container's own log usually names the reason in its last lines before each exit." if kind != "container:unhealthy" else "The last healthcheck output is kept by Docker.",
                       f"docker logs --since 30m --tail 200 {shown}" if kind != "container:unhealthy" else f"docker inspect -f '{{{{json .State.Health}}}}' {shown}", "shell", not steps))
    steps.append(_step("Confirm it's fixed", "Infradeck resolves this incident on its own once the container stays up with no new restarts.", f"docker inspect -f '{{{{.RestartCount}}}} {{{{.State.Status}}}}' {shown}", "shell"))
    series = _downsample([(sample.time, sample.memory_used_mb) for sample in samples])
    return {
        "evidence": {"title": "container memory", "unit": "MB", "series": series, "limit": limit, "limit_label": "Container limit", "marks": marks[-40:], "mark_label": "Restart"} if series else None,
        "signals": signals, "steps": steps, "container": name,
        "note": (f"limit is {_gb(limit)}, peak {_gb(peak)}" if limit and peak is not None and memory_bound else f"{len(marks)} restarts in this window" if marks else None),
    }


def _host_series(db, server: Server, column, since: datetime, until: datetime) -> list[tuple[datetime, float | None]]:
    return [(moment, value) for moment, value in db.query(HostMetric.time, column).filter(HostMetric.server_id == server.id, HostMetric.time >= since, HostMetric.time <= until).order_by(HostMetric.time).limit(20000)]


def _top_containers(db, server: Server, column, count: int = 3) -> list[tuple[str, float]]:
    latest = db.query(HostMetric.time).filter(HostMetric.server_id == server.id).order_by(HostMetric.time.desc()).limit(1).scalar()
    if latest is None:
        return []
    rows = db.query(ContainerMetric.name, column).filter(ContainerMetric.server_id == server.id, ContainerMetric.time == latest, column.isnot(None)).order_by(column.desc()).limit(count).all()
    return [(name, value) for name, value in rows if value]


def _host_diagnosis(db, alert: IncidentAlert, server: Server, kind: str, since: datetime, until: datetime) -> dict:
    confirm = _step("Confirm it's fixed", "Infradeck resolves this incident on its own once the value stays under the threshold for a few check-ins.")
    if kind in ("host:memory", "host:swap"):
        swap = kind == "host:swap"
        rows = _host_series(db, server, HostMetric.swap_pct if swap else HostMetric.ram_pct, since, until)
        top = _top_containers(db, server, ContainerMetric.swap_mb if swap else ContainerMetric.memory_used_mb)
        values = [value for _, value in rows if value is not None]
        signals = [{"text": f"{'Swap' if swap else 'Memory'} use peaked at {max(values):.0f}% in this window.", "source": "host metrics", "ok": True}] if values else []
        if top:
            signals.append({"text": f"{top[0][0]} holds the most {'swap' if swap else 'memory'}: {_gb(top[0][1])}." + (f" Then {', '.join(f'{name} ({_gb(value)})' for name, value in top[1:])}." if len(top) > 1 else ""), "source": "container metrics", "ok": True})
        steps = [_step("See what holds the memory", "The biggest containers right now are listed above. To see it on the machine:", "docker stats --no-stream --format 'table {{.Name}}\\t{{.MemUsage}}\\t{{.MemPerc}}'", "shell", True)]
        if top:
            steps.append(_step(f"Give {top[0][0]} a memory limit", "A limit stops one container from pushing the rest of the machine into swap. In its compose file:", f"mem_limit: {max(1, math.ceil(top[0][1] * 1.3 / 1024))}g", "compose"))
        return {"evidence": {"title": "swap in use" if swap else "memory in use", "unit": "%", "series": _downsample(rows), "limit": None, "marks": []}, "signals": signals, "steps": [*steps, confirm],
                "note": f"top {'swap' if swap else 'memory'} user is {top[0][0]} ({_gb(top[0][1])})" if top else None}
    if kind == "host:load":
        rows = _host_series(db, server, HostMetric.load_1m, since, until)
        top = _top_containers(db, server, ContainerMetric.cpu_pct)
        signals = [{"text": f"The machine has {server.cpu_cores} cores; load above that means work is waiting.", "source": "host metrics", "ok": True}] if server.cpu_cores else []
        if top:
            signals.append({"text": f"{top[0][0]} uses the most CPU right now: {top[0][1]:.0f}%.", "source": "container metrics", "ok": True})
        return {"evidence": {"title": "load (1 min)", "unit": "", "series": _downsample(rows), "limit": server.cpu_cores, "limit_label": f"{server.cpu_cores} cores", "marks": []}, "signals": signals,
                "steps": [_step("See what is using the CPU", "Load counts processes that are running or waiting for the CPU or the disk.", "docker stats --no-stream --format 'table {{.Name}}\\t{{.CPUPerc}}' | sort -k2 -rh | head", "shell", True), confirm],
                "note": f"busiest container is {top[0][0]} ({top[0][1]:.0f}% CPU)" if top else None}
    if kind in ("host:disk", "host:inodes"):
        mount = subject(alert.rule_key) or "/"
        column = MountMetric.used_pct if kind == "host:disk" else MountMetric.inodes_pct
        rows = [(moment, value) for moment, value in db.query(MountMetric.time, column).filter(MountMetric.server_id == server.id, MountMetric.mount == mount, MountMetric.time >= since, MountMetric.time <= until).order_by(MountMetric.time).limit(20000)]
        values = [value for _, value in rows if value is not None]
        signals = [{"text": f"{mount} went from {values[0]:.1f}% to {values[-1]:.1f}% in this window.", "source": "mount metrics", "ok": values[-1] >= values[0]}] if len(values) > 1 else []
        docker = mount in ("/", "/var", "/var/lib", "/var/lib/docker")
        steps = [_step("See what Docker can give back", "Unused images and build cache are the usual cause on a Docker host.", "docker system df", "shell", True),
                 _step("Remove what nothing uses", "This deletes every image no container uses and the unused build cache. Images you keep for later will be pulled again.", "docker image prune -a && docker builder prune", "shell")] if docker else []
        steps.append(_step("Find the biggest folders", "Shows the largest directories on that filesystem.", f"sudo du -xh --max-depth=2 {mount if mount.startswith('/') else '/'} | sort -rh | head -20", "shell", not docker))
        return {"evidence": {"title": f"{mount} used" if kind == "host:disk" else f"{mount} inodes used", "unit": "%", "series": _downsample(rows), "limit": None, "marks": []}, "signals": signals, "steps": [*steps, confirm], "note": None}
    return {}


PLAYBOOKS: dict[str, list[dict]] = {
    SERVER_OFFLINE_RULE: [
        _step("Check that the machine is up", "If it answers a ping, the machine is fine and the collector or the network path is the problem.", None, None, True),
        _step("Look at the collector on that machine", "It logs why it could not send.", "docker compose -p homelab-collector logs --tail 50 collector", "shell"),
        _step("Confirm it's back", "This incident resolves on the collector's next check-in."),
    ],
    "network:port": [
        _step("Decide whether it should be reachable", "If the service is only used on this machine or behind a reverse proxy, bind it to localhost in its compose file:", 'ports: ["127.0.0.1:8080:8080"]', "compose", True),
        _step("Or allow it on purpose", "If other machines need it, add a firewall rule for it so the choice is written down. Note that ports published by Docker bypass ufw.", "sudo ufw allow from 10.0.0.0/24 to any port <port>", "shell"),
    ],
    "network:firewall": [
        _step("Turn the firewall back on", "If it was switched off by accident:", "sudo ufw enable && sudo ufw status verbose", "shell", True),
        _step("Check what changed", "The Network tab lists which ports are reachable from other machines now."),
    ],
    "network:internet": [_step("Check the uplink", "The collector could not open a connection to its targets.", "ping -c 3 1.1.1.1", "shell", True)],
    "network:dns": [_step("Check name resolution", "Connections by address work but names don't resolve.", "resolvectl status | head -20", "shell", True)],
    "system:reboot_required": [_step("Reboot when it suits you", "Installed updates, usually a new kernel, only take effect after a restart.", "sudo systemctl reboot", "shell", True)],
    "system:ntp": [_step("Check the time service", "One of chrony, systemd-timesyncd or ntpd should be running.", "timedatectl status", "shell", True)],
    "system:oom": [_step("See what the kernel killed", "The kernel log names the process and how much memory it held.", "sudo dmesg -T | grep -i 'out of memory' | tail", "shell", True)],
    "system:kernel": [_step("Read the kernel log", "The fault flag clears after a reboot; the log says what raised it.", "sudo dmesg -T --level=err,crit,alert,emerg | tail -30", "shell", True)],
    "system:docker_unreachable": [_step("Check the Docker daemon and the proxy", "Container data stops while either is down.", "sudo systemctl status docker --no-pager && docker ps --filter name=docker-proxy", "shell", True)],
    "system:docker_slow": [_step("See what keeps Docker busy", "A daemon that takes seconds to list containers is usually starved of CPU or disk.", "docker stats --no-stream | head -15", "shell", True)],
    "system:collect_slow": [_step("Give the collector more time", "Raise this server's check interval in Admin, or reduce the number of containers on the machine.", None, None, True)],
    "system:reboot": [_step("Find out why it restarted", "The previous boot's last log lines usually say.", "journalctl -b -1 -n 50 --no-pager", "shell", True)],
    "system:credentials": [_step("Make sure it was you", "The enrollment key gave this server new credentials. If you did not re-install its collector, rotate the enrollment key and remove the server.", None, None, True)],
    "container:removed": [_step("Bring it back if it should exist", "The container is no longer on the machine.", "docker compose up -d", "shell", True)],
    "container:image": [_step("Check what changed", "The container now runs a different image build than before.", "docker image history --no-trunc <image> | head", "shell", True)],
    "trend:memory": [_step("Watch it, then restart it", "Memory has climbed steadily. Restarting the container frees it; a limit keeps a leak from taking the host down.", "mem_limit: 2g", "compose", True)],
}


def diagnose(db, alert: IncidentAlert, server: Server, now: datetime | None = None) -> dict:
    """Everything the incident page shows under the title, worked out from stored data."""
    now = _utc(now or datetime.now(timezone.utc))
    created, resolved = _utc(alert.created_at), _utc(alert.resolved_at)
    since, until = created - EVIDENCE_LEAD, resolved or now
    kind = rule_kind(alert.rule_key)
    found: dict = {}
    if kind.startswith("container:") and kind not in ("container:removed", "container:image"):
        found = _container_diagnosis(db, alert, server, kind, since, until)
    elif kind.startswith("host:"):
        found = _host_diagnosis(db, alert, server, kind, since, until)
    steps = found.get("steps") or PLAYBOOKS.get(kind) or []
    events = db.query(SystemEvent).filter(SystemEvent.server_id == server.id, SystemEvent.time >= since - timedelta(hours=1), SystemEvent.time <= until).order_by(SystemEvent.time.desc()).limit(200).all()
    lines = [{"time": _utc(event.time).isoformat(), "source": _event_source(event.kind), "text": event.detail, "strong": event.kind in ("oom_kill", "kernel_error", "kernel_fault", "firewall_weakened")} for event in reversed(events[:MAX_LINES])]
    timeline = [{"time": _utc(event.time).isoformat(), "text": event.detail, "tone": "plain"} for event in events[:MAX_TIMELINE - 2]]
    timeline.append({"time": created.isoformat(), "text": f"Incident opened · {alert.title}", "tone": "signal" if alert.severity == CRITICAL else "warn" if alert.severity == WARNING else "plain"})
    if alert.acknowledged_at:
        timeline.append({"time": _utc(alert.acknowledged_at).isoformat(), "text": "Acknowledged", "tone": "plain"})
    if resolved:
        timeline.append({"time": resolved.isoformat(), "text": f"Resolved · {alert.resolution or 'the condition cleared'}", "tone": "ok"})
    timeline.sort(key=lambda item: item["time"])
    signals = found.get("signals") or []
    return {
        "summary": alert.description, "state": state_of(alert), "kind": kind, "container": found.get("container"), "note": found.get("note"),
        "evidence": found.get("evidence"), "lines": lines, "signals": signals, "agreeing": sum(1 for signal in signals if signal["ok"]),
        "steps": steps, "timeline": timeline[-MAX_TIMELINE:], "window": {"start": since.isoformat(), "end": until.isoformat()},
    }


def summary(alert: IncidentAlert, hostname: str | None, now: datetime, users: dict[int, str] | None = None) -> dict:
    return {
        "id": alert.id, "reference": reference(alert.id), "server_id": alert.server_id, "hostname": hostname, "severity": alert.severity, "title": alert.title, "description": alert.description,
        "rule_key": alert.rule_key, "kind": rule_kind(alert.rule_key), "status": alert.status, "state": state_of(alert), "created_at": _utc(alert.created_at).isoformat(),
        "resolved_at": _utc(alert.resolved_at).isoformat() if alert.resolved_at else None, "resolution": alert.resolution,
        "acknowledged_at": _utc(alert.acknowledged_at).isoformat() if alert.acknowledged_at else None, "acknowledged_by": (users or {}).get(alert.acknowledged_by),
        "muted_until": _utc(alert.muted_until).isoformat() if is_muted(alert, now) else None, "notified": bool(alert.notified_at),
    }


def inbox(db, now: datetime | None = None) -> dict:
    """Open incidents (most severe first), the ones resolved lately, and the muted ones, each as a summary."""
    now = _utc(now or datetime.now(timezone.utc))
    hostnames = dict(db.query(Server.id, Server.hostname).all())
    users = dict(db.query(User.id, User.name).all())
    rank = {CRITICAL: 0, WARNING: 1, INFO: 2}
    active = db.query(IncidentAlert).filter(IncidentAlert.status == "active").order_by(IncidentAlert.created_at.desc()).limit(MAX_LISTED).all()
    active.sort(key=lambda alert: (rank.get(alert.severity, 3), -_utc(alert.created_at).timestamp()))
    recent = db.query(IncidentAlert).filter(IncidentAlert.status == "resolved", IncidentAlert.resolved_at.isnot(None)).order_by(IncidentAlert.resolved_at.desc()).limit(MAX_LISTED).all()
    row = lambda alert: summary(alert, hostnames.get(alert.server_id), now, users)  # noqa: E731
    return {
        "open": [row(alert) for alert in active if not is_muted(alert, now)],
        "muted": [row(alert) for alert in active if is_muted(alert, now)],
        "resolved": [row(alert) for alert in recent],
        "resolved_count": db.query(IncidentAlert).filter(IncidentAlert.status == "resolved").count(),
    }
