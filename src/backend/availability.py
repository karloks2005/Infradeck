"""Availability checks: services that the collectors on your servers try every so often (Milestone 11).

The backend keeps the checks and decides; the collectors only run them (src/collector/collector.py) and report
each run. A check is given to the collectors of the servers it should run from, they report results on their
own endpoint, and here the results are combined:

- Per server, the check is down once its last ``retries + 1`` runs all failed, and up again on the first good run.
- Over the servers: with the rule ``all`` the check is down only when every server sees it down (the service is
  down); with ``any`` one is enough (one network path is broken).
- A check going down opens an incident (``check:<id>:down``) with the check's severity, so it shows up in Needs
  attention, the Agent page and notifications; coming back up resolves it. New checks stay quiet for their first
  10 minutes, so a wrong path doesn't page anyone.

For the page, results are grouped into slots of one interval: a slot is up, slow, retrying (it failed, but not
often enough to count as down) or down. Uptime is the share of slots that weren't down. Completed hours are
summarised into check_rollups, which the 30-day, 1-year and 90-day views add up.
"""
from __future__ import annotations

import ipaddress
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from .alerts import raise_alert, resolve_alerts
from .models import Check, CheckResult, CheckRollup, IncidentAlert, Server

TYPES = ("http", "keyword", "tcp", "tls", "dns", "json", "ping")
URL_TYPES = ("http", "keyword", "json")       # check an http(s) URL
HOSTPORT_TYPES = ("tcp", "tls")               # connect to host:port
HOST_TYPES = ("ping", "dns")                  # a bare host or name, no port
DNS_RECORDS = ("A", "AAAA", "CNAME", "MX", "TXT", "NS")
JSON_OPS = ("equals", "contains", "gt", "lt", "exists")
DEFAULT_TLS_PORT = 443
MAX_WARN_DAYS = 365
RULES = ("all", "any")
SEVERITIES = ("CRITICAL", "WARNING")
MIN_INTERVAL, MAX_INTERVAL = 20, 86400
MAX_TIMEOUT = 48
MAX_RETRIES = 10
MAX_CHECKS_PER_SERVER = 50
MAX_RESULTS_PER_REPORT = 500
QUIET_FOR_NEW = timedelta(minutes=10)
RAW_KEPT = timedelta(days=7)
STRIP = 30
CHECK_RULE_PREFIX = "check:"
# Collectors that can run checks report at least this version.
MIN_COLLECTOR = (0, 10, 0)
_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,120}")
_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\]")
_CODES = re.compile(r"[1-5]\d\d(-[1-5]\d\d)?")


def _utc(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _version(text: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in (text or "").split(".")[:3])
    except ValueError:
        return ()


def can_run_checks(server: Server) -> bool:
    try:
        info = json.loads(server.system_info or "{}")
    except ValueError:
        return False
    return _version(info.get("collector_version")) >= MIN_COLLECTOR


# ---------- what a check is ----------

def _host_port(target: str, default_port: int | None) -> tuple[str, int] | None:
    """(host, port) from "host:port", "[ipv6]:port", or a bare host when ``default_port`` is given; None if unusable."""
    target = target.strip()
    if target.startswith("["):
        inner, _, rest = target[1:].partition("]")
        try:
            ipaddress.IPv6Address(inner)
        except ValueError:
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
        if not _HOST.fullmatch(host):
            return None
    if port is None or not 1 <= int(port) <= 65535:
        return None
    return host, int(port)


def _valid_host(target: str) -> bool:
    if target.startswith("[") and target.endswith("]"):
        try:
            ipaddress.IPv6Address(target[1:-1])
            return True
        except ValueError:
            return False
    return bool(_HOST.fullmatch(target))


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def validate(raw: dict, server_ids: set[int]) -> dict:
    """The fields of a check from the form, checked. Raises ValueError with a message for the person."""
    kind = raw.get("type")
    if kind not in TYPES:
        raise ValueError("Pick a check type: HTTP(s), Keyword or TCP port.")
    name = str(raw.get("name") or "").strip()
    if not _NAME.fullmatch(name):
        raise ValueError("Give the check a name of up to 120 characters.")
    target = str(raw.get("target") or "").strip()
    options: dict = {}
    if kind in URL_TYPES:
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not parts.hostname or len(target) > 2048 or any(char.isspace() for char in target):
            raise ValueError("The URL must start with http:// or https:// and name a host.")
        if parts.username or parts.password:
            raise ValueError("Put no user name or password in the URL; everyone who can sign in can read a check's address.")
        codes = raw.get("accepted_codes") or ["200-299"]
        if not isinstance(codes, list) or not 1 <= len(codes) <= 10 or not all(isinstance(code, str) and _CODES.fullmatch(code) for code in codes):
            raise ValueError("Accepted status codes are like 200-299 or 301, at most 10.")
        options.update(accepted_codes=codes, follow_redirects=raw.get("follow_redirects") is not False, ignore_tls=raw.get("ignore_tls") is True, method="HEAD" if raw.get("method") == "HEAD" and kind == "http" else "GET")
        if kind == "keyword":
            keyword = str(raw.get("keyword") or "")
            if not 1 <= len(keyword) <= 200:
                raise ValueError("Enter the text to look for, up to 200 characters.")
            options.update(keyword=keyword, case_sensitive=raw.get("case_sensitive") is True)
        if kind == "json":
            path = str(raw.get("json_path") or "").strip()
            if not 1 <= len(path) <= 200:
                raise ValueError("Enter the path to the value in the JSON, like data.status.")
            operator = raw.get("json_op", "equals")
            if operator not in JSON_OPS:
                raise ValueError("Unknown comparison for the JSON value.")
            expect = str(raw.get("json_expect") or "")
            if operator != "exists" and not expect:
                raise ValueError("Enter the value the JSON should have.")
            if len(expect) > 200:
                raise ValueError("The expected value is too long.")
            options.update(json_path=path, json_op=operator, json_expect=expect)
    elif kind in HOSTPORT_TYPES:
        parsed = _host_port(target, DEFAULT_TLS_PORT if kind == "tls" else None)
        if not parsed:
            raise ValueError("For a TCP port, enter host:port, like 10.0.0.12:3306." if kind == "tcp" else "For a TLS certificate, enter host or host:port, like mail.example.com:993.")
        if kind == "tls":
            warn_days = raw.get("warn_days", 14)
            if isinstance(warn_days, bool) or not isinstance(warn_days, int) or not 1 <= warn_days <= MAX_WARN_DAYS:
                raise ValueError(f"Warn before expiry must be a whole number of days from 1 to {MAX_WARN_DAYS}.")
            options.update(warn_days=warn_days)
    elif kind in HOST_TYPES:
        if not _valid_host(target) or len(target) > 253:
            raise ValueError("Enter a host name or IP address." if kind == "ping" else "Enter the name to look up, like deck.home.lan.")
        if kind == "dns":
            record = raw.get("record_type", "A")
            if record not in DNS_RECORDS:
                raise ValueError("Pick a DNS record type.")
            resolver = str(raw.get("resolver") or "").strip()
            if resolver and not _is_ip(resolver):
                raise ValueError("The DNS server must be an IP address, like 10.0.0.12.")
            expected = str(raw.get("expected") or "")[:200]
            options.update(record_type=record, resolver=resolver, expected=expected)
    options["upside_down"] = raw.get("upside_down") is True
    servers = raw.get("server_ids")
    if not isinstance(servers, list) or not servers or not all(isinstance(item, int) and not isinstance(item, bool) for item in servers) or not set(servers) <= server_ids:
        raise ValueError("Choose at least one server to check from.")
    numbers = {}
    for key, low, high, default in (("interval_seconds", MIN_INTERVAL, MAX_INTERVAL, 60), ("retries", 0, MAX_RETRIES, 2), ("retry_interval_seconds", MIN_INTERVAL, MAX_INTERVAL, 20), ("timeout_seconds", 1, MAX_TIMEOUT, 48)):
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key.replace('_seconds', '').replace('_', ' ').capitalize()} must be a whole number from {low} to {high}.")
        numbers[key] = value
    if numbers["timeout_seconds"] >= numbers["interval_seconds"]:
        # A short interval with the default timeout: the timeout follows the interval, like in Uptime Kuma.
        numbers["timeout_seconds"] = max(1, min(MAX_TIMEOUT, numbers["interval_seconds"] * 4 // 5))
    tags = [tag.strip() for tag in raw.get("tags") or [] if isinstance(tag, str) and tag.strip()]
    if len(tags) > 5 or any(len(tag) > 40 or "," in tag for tag in tags):
        raise ValueError("At most 5 tags of up to 40 characters, without commas.")
    rule = raw.get("rule", "all")
    severity = raw.get("severity", "CRITICAL")
    if rule not in RULES or severity not in SEVERITIES:
        raise ValueError("Unknown rule or severity.")
    return {"name": name, "type": kind, "target": target, "options": json.dumps(options), "tags": ",".join(tags), "server_ids": json.dumps(sorted(set(servers))), "rule": rule, "severity": severity, **numbers}


def servers_of(check: Check) -> list[int]:
    try:
        return [int(item) for item in json.loads(check.server_ids or "[]")]
    except (ValueError, TypeError):
        return []


def options_of(check: Check) -> dict:
    try:
        return json.loads(check.options or "{}")
    except ValueError:
        return {}


def for_collector(db, server: Server) -> list[dict]:
    """The checks this server's collector should run, in the exact shape the collector validates."""
    result = []
    for check in db.query(Check).filter(Check.paused.is_(False)).order_by(Check.id):
        if server.id in servers_of(check):
            result.append({"id": check.id, "type": check.type, "target": check.target, "interval": check.interval_seconds, "retry_interval": check.retry_interval_seconds,
                           "timeout": check.timeout_seconds, **{key: value for key, value in options_of(check).items() if key != "upside_down"}})
    return result[:MAX_CHECKS_PER_SERVER]



# ---------- testing a check before it is saved ----------
# The form's Test button: the check goes once to the collectors of the chosen servers, through Redis, with the
# answer to their next report (they ask every 10 s when idle), and their results come back the same way. Nothing
# is stored in the database; a test is forgotten after two minutes.

TEST_TTL = 120
MAX_TESTS_QUEUED = 5
IDLE_EVERY = 10


def _spec(fields: dict, check_id: int) -> dict:
    options = {key: value for key, value in json.loads(fields["options"]).items() if key != "upside_down"}
    return {"id": check_id, "type": fields["type"], "target": fields["target"], "interval": fields["interval_seconds"], "retry_interval": fields["retry_interval_seconds"],
            "timeout": fields["timeout_seconds"], **options}


def start_test(redis, fields: dict, servers: list[int]) -> int:
    """Queue one run of a check (validated ``fields``) for each server's collector. Returns the test's id."""
    test_id = int(redis.incr("infradeck:checktest:seq"))
    spec = json.dumps(_spec(fields, test_id))
    upside_down = json.loads(fields["options"]).get("upside_down") is True
    redis.set(f"infradeck:checktest:{test_id}", json.dumps({"servers": servers, "upside_down": upside_down}), ex=TEST_TTL)
    for server_id in servers:
        queue = f"infradeck:checktest:queue:{server_id}"
        redis.rpush(queue, spec)
        redis.ltrim(queue, -MAX_TESTS_QUEUED, -1)
        redis.expire(queue, 60)
    return test_id


def tests_for(redis, server_id: int) -> list[dict]:
    """The tests waiting for this server's collector; handing them over takes them off the queue."""
    queue = f"infradeck:checktest:queue:{server_id}"
    items = redis.lrange(queue, 0, -1)
    if items:
        redis.delete(queue)
    return [json.loads(item) for item in items]


def store_tests(redis, server_id: int, results: list) -> None:
    """Keep this server's results for tests it was given. Untrusted like every result: anything else is dropped."""
    for item in results[:MAX_TESTS_QUEUED * 2]:
        if not isinstance(item, dict) or not isinstance(item.get("check_id"), int) or isinstance(item.get("check_id"), bool) or not isinstance(item.get("ok"), bool):
            continue
        raw = redis.get(f"infradeck:checktest:{item['check_id']}")
        meta = json.loads(raw) if raw else None
        if not meta or server_id not in meta["servers"]:
            continue
        number = lambda key, low, high: item.get(key) if isinstance(item.get(key), int) and not isinstance(item.get(key), bool) and low <= item[key] <= high else None  # noqa: E731
        detail = item.get("detail")
        detail = "".join(char for char in detail if char.isprintable())[:200] if isinstance(detail, str) else None
        ok = item["ok"] != meta["upside_down"]
        key = f"infradeck:checktest:{item['check_id']}:results"
        redis.hset(key, str(server_id), json.dumps({"ok": ok, "response_ms": number("response_ms", 0, 600_000), "code": number("code", 100, 599),
                                                    "detail": detail or None, "cert_days": number("cert_days", -10_000, 10_000)}))
        redis.expire(key, TEST_TTL)


def test_status(redis, test_id: int, names: dict[int, str]) -> dict | None:
    """Where a test is: each server's result, or waiting. None when the test is unknown or forgotten."""
    raw = redis.get(f"infradeck:checktest:{test_id}")
    if not raw:
        return None
    meta = json.loads(raw)
    results = {int(server): json.loads(value) for server, value in (redis.hgetall(f"infradeck:checktest:{test_id}:results") or {}).items()}
    servers = [{"id": server, "hostname": names.get(server, f"server {server}"), **({"state": "done", **results[server]} if server in results else {"state": "waiting"})}
               for server in meta["servers"]]
    return {"id": test_id, "servers": servers, "done": all(server["state"] == "done" for server in servers)}

# ---------- results ----------

def store(db, server: Server, results: list, now: datetime | None = None) -> set[int]:
    """Keep the valid results this server's collector reported for its own checks. Returns the checks touched.
    Everything is untrusted: anything that isn't a well-formed result for a check of this server is dropped."""
    now = now or datetime.now(timezone.utc)
    mine = {check.id: check for check in db.query(Check).filter(Check.paused.is_(False)) if server.id in servers_of(check)}
    touched = set()
    for item in results[:MAX_RESULTS_PER_REPORT]:
        if not isinstance(item, dict) or item.get("check_id") not in mine or not isinstance(item.get("ok"), bool):
            continue
        try:
            moment = _utc(datetime.fromisoformat(str(item.get("time"))))
        except ValueError:
            continue
        if not now - timedelta(hours=1) <= moment <= now + timedelta(seconds=60):
            continue
        number = lambda key, low, high: item.get(key) if isinstance(item.get(key), int) and not isinstance(item.get(key), bool) and low <= item[key] <= high else None  # noqa: E731
        detail = item.get("detail")
        detail = "".join(char for char in detail if char.isprintable())[:200] if isinstance(detail, str) else None
        db.add(CheckResult(check_id=item["check_id"], server_id=server.id, time=moment, ok=item["ok"], response_ms=number("response_ms", 0, 600_000),
                           code=number("code", 100, 599), detail=detail or None, cert_days=number("cert_days", -10_000, 10_000)))
        touched.add(item["check_id"])
    db.flush()
    return touched


def _effective(check: Check, result: CheckResult) -> bool:
    """A result's ok, turned around for upside-down checks (up means the service is unreachable)."""
    return (not result.ok) if options_of(check).get("upside_down") else result.ok


def _results(db, check: Check, since: datetime, until: datetime | None = None) -> list[CheckResult]:
    query = db.query(CheckResult).filter(CheckResult.check_id == check.id, CheckResult.time >= since)
    if until is not None:
        query = query.filter(CheckResult.time < until)
    return query.order_by(CheckResult.time).limit(50_000).all()


def slots(check: Check, results: list[CheckResult]) -> list[dict]:
    """The results grouped into slots of one interval, oldest first: {start, status, response_ms, failed_on}.
    status is up, slow, retrying or down; failed_on lists the servers whose last run in the slot failed."""
    interval = check.interval_seconds
    streak: dict[int, int] = defaultdict(int)
    grouped: dict[int, dict[int, tuple[bool, bool, int | None, str | None]]] = defaultdict(dict)
    for result in results:
        ok = _effective(check, result)
        streak[result.server_id] = 0 if ok else streak[result.server_id] + 1
        counted = streak[result.server_id] >= check.retries + 1
        grouped[int(_utc(result.time).timestamp()) // interval][result.server_id] = (ok, counted, result.response_ms, result.detail)
    times = [value[2] for slot in grouped.values() for value in slot.values() if value[0] and value[2] is not None]
    slow_after = max(1000, 3 * statistics.median(times)) if times else None
    output = []
    for key in sorted(grouped):
        per_server = grouped[key]
        failed = [server for server, value in per_server.items() if not value[0]]
        down_servers = [server for server, value in per_server.items() if not value[0] and value[1]]
        combined_failed = len(failed) == len(per_server) if check.rule == "all" else bool(failed)
        combined_down = (len(down_servers) == len(per_server)) if check.rule == "all" else bool(down_servers)
        response = [value[2] for value in per_server.values() if value[0] and value[2] is not None]
        average = round(sum(response) / len(response)) if response else None
        if combined_down:
            status = "down"
        elif combined_failed or failed:
            status = "retrying" if combined_failed else "up"
        else:
            status = "slow" if slow_after and average and average > slow_after else "up"
        details = [value[3] for value in per_server.values() if not value[0] and value[3]]
        output.append({"start": datetime.fromtimestamp(key * interval, timezone.utc).isoformat(), "status": status, "response_ms": average, "failed_on": sorted(failed), "detail": details[0] if details else None})
    return output


def server_states(db, check: Check, now: datetime) -> dict[int, str]:
    """Each server's verdict on the check now: up, down, or unknown (no fresh result)."""
    fresh = now - timedelta(seconds=check.interval_seconds * 3 + check.timeout_seconds + 60)
    states = {}
    for server_id in servers_of(check):
        latest = db.query(CheckResult).filter(CheckResult.check_id == check.id, CheckResult.server_id == server_id).order_by(CheckResult.time.desc()).limit(check.retries + 1).all()
        if not latest or _utc(latest[0].time) < fresh:
            states[server_id] = "unknown"
        elif _effective(check, latest[0]):
            states[server_id] = "up"
        elif len(latest) == check.retries + 1 and not any(_effective(check, result) for result in latest):
            states[server_id] = "down"
        else:
            states[server_id] = "retrying"
    return states


def _reporting(db, now: datetime) -> set[int]:
    """Servers whose collector is online and new enough to run checks."""
    return {server.id for server in db.query(Server).filter(Server.last_seen_at.isnot(None)) if can_run_checks(server)
            and now - _utc(server.last_seen_at) <= timedelta(seconds=max(server.check_interval_seconds or 60, 60) * 3)}


def rule_key(check: Check) -> str:
    return f"{CHECK_RULE_PREFIX}{check.id}:down"


def evaluate(db, check: Check, now: datetime | None = None, alerting: bool = True) -> str:
    """Bring the check's state up to date and open or resolve its incident. Returns the state.
    With ``alerting`` False (the rule is switched off in Admin › Rules) the state still changes, but no incident opens."""
    now = now or datetime.now(timezone.utc)
    if check.paused:
        new = "paused"
    else:
        states = server_states(db, check, now)
        known = {server: state for server, state in states.items() if state != "unknown"}
        if check.rule == "all":
            # A server that should be reporting but hasn't yet keeps the check from counting as down everywhere;
            # one that can't (offline, or a collector too old to run checks) doesn't hold it up.
            waiting = [server for server, state in states.items() if state == "unknown" and server in _reporting(db, now)]
            known.update({server: "retrying" for server in waiting})
        if not known:
            new = "pending" if check.state in ("pending", "paused") else check.state
        elif check.rule == "all":
            new = "down" if all(state == "down" for state in known.values()) else "up" if any(state == "up" for state in known.values()) else check.state if check.state in ("up", "down") else "pending"
        else:
            new = "down" if any(state == "down" for state in known.values()) else "up" if all(state == "up" for state in known.values()) else check.state if check.state in ("up", "down") else "pending"
    if new != check.state:
        check.state, check.state_since = new, now
    servers = servers_of(check)
    if new == "down" and alerting and servers and now - _utc(check.created_at or now) >= QUIET_FOR_NEW:
        latest = db.query(CheckResult).filter(CheckResult.check_id == check.id).order_by(CheckResult.time.desc()).first()
        names = dict(db.query(Server.id, Server.hostname).filter(Server.id.in_(servers)))
        failing = [names.get(server, f"server {server}") for server, state in server_states(db, check, now).items() if state == "down"]
        why = (latest.detail if latest and latest.detail else "No answer") + f" from {', '.join(failing) or 'its servers'}"
        raise_alert(db, servers[0], rule_key(check), check.severity, f"{check.name} is down"[:255],
                    f"{why}. Failed {check.retries + 1} checks in a row. {redacted_target(check.target)}"[:1000])
    elif servers:
        resolve_alerts(db, servers[0], [rule_key(check)], now)
    return new


def forget_check(db, check: Check) -> None:
    """Delete a check with its history; its open incident is resolved, so the record of it stays."""
    servers = servers_of(check)
    if servers:
        resolve_alerts(db, servers[0], [rule_key(check)])
    db.query(CheckResult).filter(CheckResult.check_id == check.id).delete(synchronize_session=False)
    db.query(CheckRollup).filter(CheckRollup.check_id == check.id).delete(synchronize_session=False)
    db.delete(check)


def forget_server(db, server_id: int) -> None:
    """A server is removed: its results go, and checks stop running from it (a check left with no server pauses)."""
    db.query(CheckResult).filter(CheckResult.server_id == server_id).delete(synchronize_session=False)
    for check in db.query(Check):
        servers = servers_of(check)
        if server_id in servers:
            remaining = [item for item in servers if item != server_id]
            check.server_ids = json.dumps(remaining)
            if not remaining:
                check.paused, check.state = True, "paused"


# ---------- summaries for the page ----------

def _uptime(db, check: Check, since: datetime, now: datetime) -> float | None:
    """Share of slots since ``since`` that weren't down, in percent: completed hours from the rollups, the rest raw."""
    total = down = 0
    rolled = _utc(check.rolled_until) if check.rolled_until else None
    if rolled and rolled > since:
        for slots_count, down_count in db.query(CheckRollup.slots, CheckRollup.down_slots).filter(CheckRollup.check_id == check.id, CheckRollup.hour >= since, CheckRollup.hour < rolled):
            total += slots_count
            down += down_count
    raw_from = max(since, rolled or since)
    for slot in slots(check, _results(db, check, raw_from)):
        total += 1
        down += slot["status"] == "down"
    return round(100 * (total - down) / total, 2) if total else None


def redacted_target(target: str) -> str:
    """A check's address with the secret-bearing parts removed, for anyone who can't edit it: credentials in the
    URL and the query string (which can carry tokens) go; scheme, host, port and path stay. The collector still
    gets the full target. Non-URL targets (host:port, a name) have no secret part and pass through unchanged."""
    parts = urlsplit(target)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return target
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    cleaned = urlunsplit((parts.scheme, host, parts.path, "", ""))
    return f"{cleaned}?\u2026" if parts.query else cleaned


def summary(db, check: Check, names: dict[int, str], now: datetime | None = None, redact: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    recent = slots(check, _results(db, check, now - timedelta(seconds=check.interval_seconds * (STRIP + 2) + 3600)))[-STRIP:]
    latest = db.query(CheckResult).filter(CheckResult.check_id == check.id).order_by(CheckResult.time.desc()).first()
    cert = db.query(CheckResult.cert_days).filter(CheckResult.check_id == check.id, CheckResult.cert_days.isnot(None)).order_by(CheckResult.time.desc()).limit(1).scalar()
    options = options_of(check)
    return {
        "id": check.id, "name": check.name, "type": check.type, "target": redacted_target(check.target) if redact else check.target, "tags": [tag for tag in check.tags.split(",") if tag],
        "keyword": options.get("keyword"), "options": options, "rule": check.rule, "severity": check.severity, "paused": check.paused, "state": check.state,
        "state_since": _utc(check.state_since).isoformat() if check.state_since else None, "interval_seconds": check.interval_seconds, "retries": check.retries,
        "retry_interval_seconds": check.retry_interval_seconds, "timeout_seconds": check.timeout_seconds,
        "servers": [{"id": server, "hostname": names.get(server, f"server {server}")} for server in servers_of(check)],
        "strip": [{"status": "paused" if check.paused else slot["status"], "start": slot["start"], "response_ms": slot["response_ms"]} for slot in recent],
        "response_ms": next((slot["response_ms"] for slot in reversed(recent) if slot["response_ms"] is not None), None),
        "uptime_24h": _uptime(db, check, now - timedelta(hours=24), now), "uptime_30d": _uptime(db, check, now - timedelta(days=30), now),
        "cert_days": cert, "last_detail": latest.detail if latest else None, "last_result_at": _utc(latest.time).isoformat() if latest else None,
        "quiet_until": (_utc(check.created_at) + QUIET_FOR_NEW).isoformat() if now - _utc(check.created_at) < QUIET_FOR_NEW else None,
        "created_at": _utc(check.created_at).isoformat(),
    }


def overview(db, now: datetime | None = None, redact: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    names = dict(db.query(Server.id, Server.hostname).all())
    checks = [summary(db, check, names, now, redact=redact) for check in db.query(Check).order_by(Check.name)]
    month = [check["uptime_30d"] for check in checks if check["uptime_30d"] is not None and not check["paused"]]
    return {
        "checks": checks,
        "counts": {"up": sum(check["state"] == "up" for check in checks), "down": sum(check["state"] == "down" for check in checks), "paused": sum(check["paused"] for check in checks),
                   "pending": sum(check["state"] == "pending" for check in checks), "total": len(checks)},
        "uptime_30d": round(sum(month) / len(month), 2) if month else None,
        "expiring": [{"name": check["name"], "days": check["cert_days"]} for check in checks if check["cert_days"] is not None and check["cert_days"] <= 21],
        "servers": [{"id": server.id, "hostname": server.hostname, "can_run_checks": can_run_checks(server)} for server in db.query(Server).order_by(Server.hostname)],
    }


RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
CHART_POINTS = 120
DAYS = 90


def _response_series(db, check: Check, span: timedelta, now: datetime, names: dict[int, str]) -> list[dict]:
    """Average response time per server in up to CHART_POINTS buckets. Raw results cover a week; 30 days come from
    the hourly rollups, which are for all servers together."""
    since = now - span
    if span > RAW_KEPT:
        points = [{"time": _utc(hour).isoformat(), "ms": avg} for hour, avg in db.query(CheckRollup.hour, CheckRollup.avg_ms)
                  .filter(CheckRollup.check_id == check.id, CheckRollup.hour >= since).order_by(CheckRollup.hour)]
        return [{"server_id": None, "hostname": "all servers", "points": points}] if points else []
    bucket = max(check.interval_seconds, int(span.total_seconds()) // CHART_POINTS)
    sums: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for result in _results(db, check, since):
        if result.ok and result.response_ms is not None:
            sums[result.server_id][int(_utc(result.time).timestamp()) // bucket].append(result.response_ms)
    return [{"server_id": server, "hostname": names.get(server, f"server {server}"),
             "points": [{"time": datetime.fromtimestamp(key * bucket, timezone.utc).isoformat(), "ms": round(sum(values) / len(values))} for key, values in sorted(buckets.items())]}
            for server, buckets in sorted(sums.items())]


def _days(db, check: Check, now: datetime) -> list[dict]:
    """Uptime per day for the last DAYS days, oldest first; None for a day without results."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=DAYS - 1)
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for hour, slots_count, down_count in db.query(CheckRollup.hour, CheckRollup.slots, CheckRollup.down_slots).filter(CheckRollup.check_id == check.id, CheckRollup.hour >= start):
        totals[_utc(hour).date().isoformat()][0] += slots_count
        totals[_utc(hour).date().isoformat()][1] += down_count
    rolled = _utc(check.rolled_until) if check.rolled_until else None
    for slot in slots(check, _results(db, check, max(start, rolled or start))):
        day = datetime.fromisoformat(slot["start"]).date().isoformat()
        totals[day][0] += 1
        totals[day][1] += slot["status"] == "down"
    output = []
    for index in range(DAYS):
        day = (start + timedelta(days=index)).date().isoformat()
        total, down = totals.get(day, (0, 0))
        output.append({"day": day, "uptime": round(100 * (total - down) / total, 2) if total else None})
    return output


def detail(db, check: Check, range_id: str, now: datetime | None = None) -> dict:
    """What the check's detail shows besides its row: the response chart, 90 days, uptime over a year, and the
    times it went down (from its incidents)."""
    now = now or datetime.now(timezone.utc)
    names = dict(db.query(Server.id, Server.hostname).all())
    span = RANGES.get(range_id, RANGES["24h"])
    downs = db.query(IncidentAlert).filter(IncidentAlert.rule_key == rule_key(check)).order_by(IncidentAlert.created_at.desc()).limit(10).all()
    events = []
    for incident in downs:
        if incident.resolved_at:
            events.append({"time": _utc(incident.resolved_at).isoformat(), "kind": "up", "text": f"Up again after {_duration(_utc(incident.resolved_at) - _utc(incident.created_at))}"})
        events.append({"time": _utc(incident.created_at).isoformat(), "kind": "down", "text": f"Down · {incident.description.split('. Failed')[0]}"})
    last = downs[0] if downs else None
    window = [result for result in _results(db, check, now - timedelta(hours=24)) if result.ok and result.response_ms is not None]
    return {
        "range": range_id if range_id in RANGES else "24h",
        "series": _response_series(db, check, span, now, names),
        "days": _days(db, check, now),
        "uptime_1y": _uptime(db, check, now - timedelta(days=365), now),
        "average_24h": round(sum(result.response_ms for result in window) / len(window)) if window else None,
        "last_down": {"at": _utc(last.created_at).isoformat(), "for": _duration((_utc(last.resolved_at) if last.resolved_at else now) - _utc(last.created_at)), "ongoing": last.resolved_at is None} if last else None,
        "events": events[:10],
    }


def _duration(span: timedelta) -> str:
    seconds = max(0, int(span.total_seconds()))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    if seconds < 172800:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} d"

# ---------- keeping the history small ----------

def roll(db, now: datetime | None = None) -> int:
    """Summarise every completed hour of every check into check_rollups. Returns how many hours were added."""
    now = now or datetime.now(timezone.utc)
    hour_now = now.replace(minute=0, second=0, microsecond=0)
    added = 0
    for check in db.query(Check):
        start = _utc(check.rolled_until) if check.rolled_until else None
        if start is None:
            first = db.query(CheckResult.time).filter(CheckResult.check_id == check.id).order_by(CheckResult.time).limit(1).scalar()
            if first is None:
                continue
            start = _utc(first).replace(minute=0, second=0, microsecond=0)
        hour = start
        while hour < hour_now:
            results = _results(db, check, hour, hour + timedelta(hours=1))
            if results:
                hour_slots = slots(check, results)
                times = [slot["response_ms"] for slot in hour_slots if slot["response_ms"] is not None]
                db.add(CheckRollup(check_id=check.id, hour=hour, slots=len(hour_slots), down_slots=sum(slot["status"] == "down" for slot in hour_slots),
                                   avg_ms=round(sum(times) / len(times), 1) if times else None, max_ms=max(times) if times else None))
                added += 1
            hour += timedelta(hours=1)
        check.rolled_until = hour_now
    db.commit()
    return added


def purge(db, now: datetime | None = None) -> int:
    """Drop raw results older than a week; the hourly rollups keep the history. Returns how many went."""
    now = now or datetime.now(timezone.utc)
    removed = db.query(CheckResult).filter(CheckResult.time < now - RAW_KEPT).delete(synchronize_session=False)
    db.commit()
    return removed
