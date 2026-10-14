"""The agent worker: takes jobs from the queue in Redis (see src/agent/jobs.py) and runs them one at a time.

It runs in its own container, so a slow or stuck model never holds up the backend, and stopping it only means
incidents wait for their diagnosis; everything else keeps working. Run with ``python -m src.agent.worker``;
``--health`` exits 0 while a worker is alive, for the container's healthcheck.

Automatic diagnosis (#116) is not built yet, so a job only checks that its incident still needs one and notes
that. The plumbing (queue, retries, time limit, status in Admin) is what this file provides.
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time

from src.agent import jobs, usage
from src.backend.config import REDIS_URL
from src.backend.db import SessionLocal
from src.backend.models import IncidentAlert

JOB_TIMEOUT_SECONDS = 120
POLL_SECONDS = 5


def handle_incident(job: dict) -> tuple[str, str]:
    """``incident.opened`` and ``incident.worsened``. Returns (status, note) for the recent-jobs list."""
    db = SessionLocal()
    try:
        incident = db.get(IncidentAlert, job["incident_id"])
        if incident is None:
            return "skipped", "The incident no longer exists."
        if incident.status != "active":
            return "skipped", "The incident was already resolved."
        if usage.budget(db)["state"] == "paused":
            return "skipped", "This month's budget for the agent is used up."
        return "noted", "Automatic diagnosis is not built yet (#116); the incident keeps its rule-based diagnosis."
    finally:
        db.close()


HANDLERS = {jobs.OPENED: handle_incident, jobs.WORSENED: handle_incident}


def run_with_timeout(handler, job: dict, seconds: float) -> tuple[str, str]:
    """Run ``handler(job)`` in a thread and stop waiting after ``seconds``. Raises TimeoutError, or what the handler raised.

    A thread can't be killed, so a handler past its time is abandoned (it is a daemon and ends with the process);
    the model client's own timeout keeps that from piling up."""
    outcome: dict = {}

    def target():
        try:
            outcome["result"] = handler(job)
        except BaseException as exc:   # handed back to the caller below
            outcome["error"] = exc

    thread = threading.Thread(target=target, name=f"agent-job-{job.get('id', '')[:8]}", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"The job took longer than {seconds:g} s.")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def process(redis_client, raw: str, handlers=None, timeout: float = JOB_TIMEOUT_SECONDS) -> str:
    """One job from the processing list: run it, then mark it finished, retry it later or give up. Returns the outcome."""
    handlers = handlers or HANDLERS
    started = time.monotonic()
    try:
        job = json.loads(raw)
        handler = handlers[job["type"]]
    except (ValueError, KeyError, TypeError):
        jobs.finish(redis_client, raw, {"type": "unknown"}, "failed", "Not a job this worker knows.")
        return "failed"
    try:
        status_text, note_text = run_with_timeout(handler, job, timeout)
    except Exception as exc:
        return jobs.retry_or_fail(redis_client, raw, job, f"{type(exc).__name__}: {exc}", round((time.monotonic() - started) * 1000))
    jobs.finish(redis_client, raw, job, status_text, note_text, round((time.monotonic() - started) * 1000))
    return status_text


def main() -> int:
    import redis

    # Waiting for a job blocks for POLL_SECONDS, so reads must be allowed to take longer than that.
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=POLL_SECONDS + 10, socket_connect_timeout=5)
    if "--health" in sys.argv:
        return 0 if client.get(jobs.HEARTBEAT) else 1

    stopping = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopping.set())
    started_at = jobs.now_iso()
    recovered = jobs.recover(client)
    print(f"Agent worker started{f'; {recovered} unfinished jobs back in the queue' if recovered else ''}.", flush=True)
    while not stopping.is_set():
        try:
            jobs.beat(client, started_at)
            jobs.promote_due(client)
            raw = jobs.take(client, POLL_SECONDS)
            if raw is not None:
                outcome = process(client, raw)
                print(f"Agent job {outcome}: {raw[:200]}", flush=True)
        except redis.RedisError as exc:   # Redis restarting: wait and carry on
            print(f"Agent worker can't reach Redis: {exc}", flush=True)
            stopping.wait(POLL_SECONDS)
    print("Agent worker stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
