"""The agent's job queue and worker (#110): one job per opened or worsened incident, published only after the
commit, and a worker that retries, gives up, survives restarts and never waits forever on one job."""
import json
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agent import worker
from src.agent import jobs as agent_jobs
from src.backend import alerts
from src.backend.models import Base, IncidentAlert, Server
from tests.fakes import FakeRedis


@pytest.fixture
def redis(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(agent_jobs, "redis_getter", lambda: client)
    return client


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Server(id=1, owner_id=1, hostname="alpha", check_interval_seconds=60))
    session.commit()
    session.bind_engine = engine
    yield session
    session.close()


def queued(redis):
    return [json.loads(raw) for raw in reversed(redis.lrange(agent_jobs.QUEUE, 0, -1))]


def test_opening_an_incident_queues_exactly_one_job_after_the_commit(db, redis):
    alerts.raise_alert(db, 1, "host:memory", "WARNING", "Memory", "high")
    assert queued(redis) == []                       # nothing before the commit
    db.commit()
    alerts.raise_alert(db, 1, "host:memory", "WARNING", "Memory", "still high")   # the same condition, refreshed
    db.commit()

    jobs = queued(redis)
    incident = db.query(IncidentAlert).one()
    assert [(job["type"], job["incident_id"], job["severity"]) for job in jobs] == [("incident.opened", incident.id, "WARNING")]


def test_getting_worse_queues_one_more_job_and_getting_better_does_not(db, redis):
    alerts.raise_alert(db, 1, "host:memory", "WARNING", "Memory", "high")
    db.commit()
    for severity in ("CRITICAL", "CRITICAL", "WARNING", "CRITICAL"):
        alerts.raise_alert(db, 1, "host:memory", severity, "Memory", "x")
        db.commit()

    assert [job["type"] for job in queued(redis)] == ["incident.opened", "incident.worsened"]


def test_info_incidents_and_rolled_back_ones_queue_nothing(db, redis):
    alerts.raise_alert(db, 1, "container:web:image", "INFO", "Image", "changed")
    db.commit()
    alerts.raise_alert(db, 1, "host:disk:/", "CRITICAL", "Disk", "full")
    db.flush()
    db.rollback()
    db.commit()

    assert queued(redis) == []


def test_the_incident_is_saved_even_when_redis_is_down(db, monkeypatch):
    def broken():
        raise ConnectionError("redis is down")
    monkeypatch.setattr(agent_jobs, "redis_getter", broken)

    alerts.raise_alert(db, 1, "host:disk:/", "CRITICAL", "Disk", "full")
    db.commit()

    assert db.query(IncidentAlert).count() == 1


def _take(redis):
    return agent_jobs.take(redis)


def test_a_finished_job_leaves_the_processing_list_and_shows_in_recent(redis):
    agent_jobs.publish(redis, agent_jobs.OPENED, 7, "CRITICAL")
    raw = _take(redis)

    assert worker.process(redis, raw, {agent_jobs.OPENED: lambda job: ("noted", f"incident {job['incident_id']}")}) == "noted"

    assert redis.llen(agent_jobs.PROCESSING) == 0
    recent = agent_jobs.status(redis)["recent"]
    assert (recent[0]["status"], recent[0]["note"], recent[0]["incident_id"]) == ("noted", "incident 7", 7)


def test_a_failing_job_is_retried_later_and_given_up_after_three_attempts(redis):
    def broken(job):
        raise RuntimeError("the model did not answer")
    agent_jobs.publish(redis, agent_jobs.OPENED, 7, "CRITICAL")

    outcomes = []
    for _ in range(agent_jobs.MAX_ATTEMPTS):
        agent_jobs.promote_due(redis, now=time.time() + 3600)   # pretend the wait is over
        outcomes.append(worker.process(redis, _take(redis), {agent_jobs.OPENED: broken}))

    assert outcomes == ["retry", "retry", "failed"]
    assert redis.llen(agent_jobs.QUEUE) == 0 and redis.zcard(agent_jobs.DELAYED) == 0 and redis.llen(agent_jobs.PROCESSING) == 0
    assert "did not answer" in agent_jobs.status(redis)["recent"][0]["note"]


def test_a_retry_waits_before_it_runs_again(redis):
    agent_jobs.publish(redis, agent_jobs.OPENED, 7, "CRITICAL")
    worker.process(redis, _take(redis), {agent_jobs.OPENED: lambda job: 1 / 0})

    assert agent_jobs.promote_due(redis, now=time.time()) == 0          # 30 s haven't passed
    assert agent_jobs.promote_due(redis, now=time.time() + 31) == 1


def test_a_job_that_hangs_is_abandoned_after_its_time_limit(redis):
    agent_jobs.publish(redis, agent_jobs.OPENED, 7, "CRITICAL")

    assert worker.process(redis, _take(redis), {agent_jobs.OPENED: lambda job: time.sleep(5)}, timeout=0.05) == "retry"


def test_jobs_interrupted_by_a_restart_go_back_to_the_queue(redis):
    agent_jobs.publish(redis, agent_jobs.OPENED, 7, "CRITICAL")
    _take(redis)                                   # taken, then the worker died

    assert agent_jobs.recover(redis) == 1
    assert redis.llen(agent_jobs.QUEUE) == 1 and redis.llen(agent_jobs.PROCESSING) == 0


def test_garbage_in_the_queue_is_dropped_not_retried(redis):
    redis.lpush(agent_jobs.QUEUE, "not json")

    assert worker.process(redis, _take(redis)) == "failed"
    assert redis.zcard(agent_jobs.DELAYED) == 0


def test_a_job_checks_that_its_incident_still_needs_a_diagnosis(db, redis, monkeypatch):
    monkeypatch.setattr(agent_jobs, "redis_getter", lambda: redis)
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.bind_engine))
    alerts.raise_alert(db, 1, "host:disk:/", "CRITICAL", "Disk", "full")
    db.commit()
    incident = db.query(IncidentAlert).one()

    assert worker.handle_incident({"incident_id": incident.id})[0] == "skipped"   # no API key in tests: rule-based only
    incident.status = "resolved"
    db.commit()
    assert worker.handle_incident({"incident_id": incident.id}) == ("skipped", "The incident was already resolved.")
    assert worker.handle_incident({"incident_id": 999})[0] == "skipped"


def test_status_says_whether_a_worker_is_alive(redis):
    assert agent_jobs.status(redis)["online"] is False

    agent_jobs.beat(redis, agent_jobs.now_iso())

    assert agent_jobs.status(redis)["online"] is True
