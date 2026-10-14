"""The agent's job queue in Redis: the backend publishes, the agent worker (src/agent/worker.py) consumes.

When an incident opens, or gets more severe, the backend queues one job for the worker. Jobs are published only
after the database commit that created or changed the incident, so the worker never looks for an incident that
was rolled back, and each event is queued once (``incident.opened`` once per incident, ``incident.worsened`` once
per incident and severity).

The queue is reliable for one worker: a job the worker takes moves to a processing list and leaves it only when
it is finished, so a job interrupted by a crash or a restart goes back to the queue when the worker starts again.
A job that fails is tried again later, up to MAX_ATTEMPTS times. If Redis is down, the incident is still saved;
only the job is lost, and that is logged.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

QUEUE = "infradeck:agent:queue"
PROCESSING = "infradeck:agent:processing"
DELAYED = "infradeck:agent:delayed"
RECENT = "infradeck:agent:recent"
HEARTBEAT = "infradeck:agent:worker"
ONCE = "infradeck:agent:once:"

OPENED, WORSENED, REQUESTED = "incident.opened", "incident.worsened", "incident.requested"
JOB_TYPES = (OPENED, WORSENED, REQUESTED)
# INFO incidents (an image changed, a container went away) are notes, not problems to diagnose.
DIAGNOSED_SEVERITIES = ("WARNING", "CRITICAL")
SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 30
ONCE_SECONDS = 30 * 86400
RECENT_KEPT = 50
HEARTBEAT_SECONDS = 30

# Set by the backend (and the worker) to how they reach Redis; read when a job is published.
redis_getter: Callable[[], object] | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job(job_type: str, incident_id: int, severity: str | None = None) -> dict:
    return {"id": uuid.uuid4().hex, "type": job_type, "incident_id": int(incident_id), "severity": severity, "attempts": 0, "enqueued_at": now_iso()}


def publish(redis_client, job_type: str, incident_id: int, severity: str | None = None) -> bool:
    """Queue one job, unless the same event was already queued. Returns True if it was queued.

    ``incident.requested`` (someone clicked "Diagnose again") is always queued."""
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unknown agent job: {job_type}")
    once = f"{ONCE}{job_type}:{incident_id}" + (f":{severity}" if job_type == WORSENED else "")
    if job_type != REQUESTED and not redis_client.set(once, "1", nx=True, ex=ONCE_SECONDS):
        return False
    redis_client.lpush(QUEUE, json.dumps(new_job(job_type, incident_id, severity)))
    return True


# ---------- the backend side: noting events in a database session and publishing them after commit ----------

def note(db, job_type: str, incident, severity: str) -> None:
    """Called where incidents are opened or escalated; the job is published once the session commits."""
    if severity in DIAGNOSED_SEVERITIES:
        db.info.setdefault("agent_pending", []).append((job_type, incident, severity))


@event.listens_for(Session, "after_flush")
def _collect_ids(session, _context):
    pending = session.info.pop("agent_pending", None)
    if pending:
        session.info.setdefault("agent_ready", []).extend((job_type, incident.id, severity) for job_type, incident, severity in pending if incident.id is not None)


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session):
    ready = session.info.pop("agent_ready", None)
    if not ready or redis_getter is None:
        return
    try:
        client = redis_getter()
        for job_type, incident_id, severity in ready:
            publish(client, job_type, incident_id, severity)
    except Exception as exc:   # the incident is saved either way; only its diagnosis job is lost
        print(f"Could not queue agent jobs: {type(exc).__name__}: {exc}", flush=True)


@event.listens_for(Session, "after_rollback")
def _forget_after_rollback(session):
    session.info.pop("agent_pending", None)
    session.info.pop("agent_ready", None)


# ---------- the worker side ----------

def recover(redis_client) -> int:
    """Put jobs a previous worker took but never finished back in the queue. Returns how many."""
    moved = 0
    while redis_client.lmove(PROCESSING, QUEUE, "LEFT", "RIGHT") is not None:
        moved += 1
    return moved


def promote_due(redis_client, now: float | None = None) -> int:
    """Move retries whose wait is over from the delayed set back to the queue."""
    moved = 0
    for raw in redis_client.zrangebyscore(DELAYED, "-inf", now if now is not None else time.time()):
        if redis_client.zrem(DELAYED, raw):
            redis_client.rpush(QUEUE, raw)
            moved += 1
    return moved


def take(redis_client, timeout: int = 5) -> str | None:
    """The oldest job, moved to the processing list; None when nothing arrived within ``timeout`` seconds."""
    return redis_client.blmove(QUEUE, PROCESSING, timeout, "RIGHT", "LEFT")


def finish(redis_client, raw: str, job: dict, status: str, note_text: str = "", duration_ms: int = 0) -> None:
    redis_client.lrem(PROCESSING, 1, raw)
    redis_client.lpush(RECENT, json.dumps({"id": job.get("id"), "type": job.get("type"), "incident_id": job.get("incident_id"), "status": status,
                                           "note": note_text[:300], "attempts": job.get("attempts", 0) + 1, "duration_ms": duration_ms, "finished_at": now_iso()}))
    redis_client.ltrim(RECENT, 0, RECENT_KEPT - 1)


def retry_or_fail(redis_client, raw: str, job: dict, error: str, duration_ms: int = 0, now: float | None = None) -> str:
    """After a failure: wait and try again (30 s, then 60 s), or give up after MAX_ATTEMPTS. Returns what happened."""
    attempts = job.get("attempts", 0) + 1
    if attempts >= MAX_ATTEMPTS:
        finish(redis_client, raw, job, "failed", error, duration_ms)
        return "failed"
    redis_client.lrem(PROCESSING, 1, raw)
    later = {**job, "attempts": attempts, "last_error": error[:300]}
    redis_client.zadd(DELAYED, {json.dumps(later): (now if now is not None else time.time()) + RETRY_BASE_SECONDS * 2 ** (attempts - 1)})
    return "retry"


def beat(redis_client, started_at: str) -> None:
    redis_client.set(HEARTBEAT, json.dumps({"started_at": started_at, "last_seen": now_iso()}), ex=HEARTBEAT_SECONDS)


def status(redis_client) -> dict:
    """For Admin: is a worker running, how much is waiting, and the latest results."""
    heartbeat = redis_client.get(HEARTBEAT)
    recent = [json.loads(raw) for raw in redis_client.lrange(RECENT, 0, 9)]
    return {
        "online": heartbeat is not None, "worker": json.loads(heartbeat) if heartbeat else None,
        "queued": redis_client.llen(QUEUE), "processing": redis_client.llen(PROCESSING), "retrying": redis_client.zcard(DELAYED),
        "recent": recent,
    }
