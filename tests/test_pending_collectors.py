import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agent import collector
from src.backend import main
from src.backend.models import Base, PendingCollector, Server, User


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.commit()
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    monkeypatch.setattr(main, "enforce_ip_limit", lambda *args, **kwargs: None)
    return session


def _register(db, machine_id="machine-a", ip="10.0.0.5", hostname="nas"):
    request = SimpleNamespace(client=SimpleNamespace(host=ip))
    return main.register_collector(main.CollectorRegisterRequest(hostname=hostname, machine_id=machine_id, os_info="Debian 12"), request, db=db)


def _poll(db, secret):
    return main.poll_collector_registration(authorization=f"Bearer {secret}", db=db)


def test_registration_waits_until_approved_then_hands_out_token(db):
    registration = _register(db)
    assert _poll(db, registration["request_secret"]) == {"status": "pending"}
    assert db.query(Server).count() == 0

    pending_id = db.query(PendingCollector).one().id
    approved = main.approve_pending_collector(pending_id, session_id="s", db=db)
    result = _poll(db, registration["request_secret"])

    server = db.query(Server).one()
    assert result["status"] == "approved"
    assert result["server_id"] == approved["server_id"] == server.id
    assert server.owner_id == 1 and server.hostname == "nas" and server.machine_id == "machine-a"
    assert server.collector_token_hash == main._hash_secret(result["collector_token"])
    # The request is consumed: the token can't be fetched twice.
    assert db.query(PendingCollector).count() == 0


def test_approved_server_appears_in_fleet_before_first_checkin(db):
    _register(db)
    main.approve_pending_collector(db.query(PendingCollector).one().id, session_id="s", db=db)

    overview = main.monitoring_overview(session_id="s", db=db)
    assert [server["hostname"] for server in overview["servers"]] == ["nas"]
    assert overview["pending_collectors"] == []


def test_pending_list_shows_live_requests_only(db):
    _register(db)
    stale = db.query(PendingCollector).one()
    assert [item["hostname"] for item in main.visible_pending_collectors(db)] == ["nas"]

    stale.last_poll_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.commit()
    assert main.visible_pending_collectors(db) == []


def test_dismissed_request_stays_listed_and_can_still_be_added(db):
    registration = _register(db)
    pending_id = db.query(PendingCollector).one().id
    main.dismiss_pending_collector(pending_id, session_id="s", db=db)

    assert _poll(db, registration["request_secret"]) == {"status": "dismissed"}
    assert main.visible_pending_collectors(db)[0]["status"] == "dismissed"

    main.approve_pending_collector(pending_id, session_id="s", db=db)
    assert _poll(db, registration["request_secret"])["status"] == "approved"


def test_restarted_collector_replaces_its_earlier_request(db):
    _register(db)
    _register(db)
    assert db.query(PendingCollector).count() == 1

    _register(db, ip="10.0.0.9")  # same machine-id from another address is kept separate
    assert db.query(PendingCollector).count() == 2


def test_approving_known_machine_reuses_existing_server(db):
    db.add(Server(id=3, owner_id=1, hostname="nas", machine_id="machine-a"))
    db.commit()
    registration = _register(db)
    main.approve_pending_collector(db.query(PendingCollector).one().id, session_id="s", db=db)

    assert _poll(db, registration["request_secret"])["server_id"] == 3
    assert db.query(Server).count() == 1


def test_unknown_poll_secret_is_rejected(db):
    with pytest.raises(HTTPException) as exc:
        _poll(db, "not-a-real-secret")
    assert exc.value.status_code == 404


def test_purge_removes_abandoned_requests(db):
    _register(db)
    db.query(PendingCollector).one().last_poll_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()

    main.purge_stale_pending(db)
    assert db.query(PendingCollector).count() == 0


def test_collector_source_is_served():
    assert "def run_collector" in main.collector_source().body.decode()


def test_collector_waits_for_approval_without_restart(monkeypatch):
    responses = iter([
        {"request_secret": "secret", "pairing_code": "ABC-234", "poll_interval_seconds": 5},
        {"status": "pending"},
        {"status": "approved", "server_id": 4, "collector_token": "tok"},
    ])
    calls = []

    def fake_post(url, payload, bearer=None):
        calls.append((url.rsplit("/", 1)[-1], bearer))
        return next(responses)

    monkeypatch.setattr(collector, "_post_json", fake_post)
    monkeypatch.setattr(collector.time, "sleep", lambda seconds: None)

    assert collector.request_approval("http://dash/api") == (4, "tok")
    assert calls == [("register", None), ("poll", "secret"), ("poll", "secret")]


def test_collector_without_key_or_credentials_requests_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMELAB_STATE_DIR", str(tmp_path))
    for name in ("HOMELAB_SERVER_ID", "HOMELAB_COLLECTOR_TOKEN", "HOMELAB_ENROLL_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(collector, "request_approval", lambda api_url: (11, "approved-token"))

    assert collector.resolve_credentials("http://dash/api") == (11, "approved-token")
    assert collector.load_credentials() == (11, "approved-token")
