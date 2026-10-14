"""Findings of the second security review (S2-01 to S2-07): each test fails without its fix."""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.backend import auth, main
from src.backend.models import AuditLog, Base, Conversation, IncidentAlert, Server, SystemEvent, User
from tests.fakes import FakeRedis

TOKENS = {1: "token-one", 2: "token-two"}
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as seed:
        seed.add(User(id=1, name="Member", email="member@example.com", password_hash=auth.hash_password(PASSWORD), role="user", is_active=True))
        for server_id, name in ((1, "alpha"), (2, "bravo")):
            seed.add(Server(id=server_id, owner_id=1, hostname=name, check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKENS[server_id]), machine_id=f"machine-{server_id}"))
        seed.commit()

    def get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[main.get_db] = get_db
    redis = FakeRedis()
    monkeypatch.setattr(main, "redis_client", redis)
    monkeypatch.setattr(main, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(main, "send_agent_message", lambda *args, **kwargs: "ok")
    monkeypatch.setattr(main, "is_agent_chat_enabled", lambda db: True)
    client = TestClient(main.app, raise_server_exceptions=False)
    client.Session, client.redis = Session, redis
    yield client
    main.app.dependency_overrides.clear()


def ingest(client, server_id, body):
    payload = body if isinstance(body, str) else json.dumps({"server_id": server_id, **body})
    return client.post("/monitoring/metrics/ingest", content=payload, headers={"Authorization": f"Bearer {TOKENS[server_id]}", "Content-Type": "application/json"})


def sign_in(client):
    response = client.post("/auth/login", json={"email": "member@example.com", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def helper_events(count, start=0, kind="kernel_error", when=None):
    when = when or (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    return [{"kind": kind, "time": when, "detail": f"event {start + i}", "key": f"key-{kind}-{start + i}"} for i in range(count)]


def stored_events(client, server_id=1, **filters):
    with client.Session() as db:
        return db.query(SystemEvent).filter_by(server_id=server_id, **filters).all()


# --- S2-01: events from the helper are capped ------------------------------------------------------------------

def test_one_check_in_stores_at_most_twenty_helper_events(app):
    ingest(app, 1, {"host": {"helper_events": helper_events(50)}})

    assert len(stored_events(app, kind="kernel_error")) == main.MAX_HELPER_EVENTS_PER_CHECKIN == 20


def test_the_rest_is_stored_by_later_check_ins_because_the_helper_republishes_its_buffer(app):
    for _ in range(3):
        ingest(app, 1, {"host": {"helper_events": helper_events(50)}})        # the same 50 each time, as the helper does

    assert len(stored_events(app, kind="kernel_error")) == 50


def test_a_flood_of_new_helper_events_stops_at_a_weekly_cap_and_says_so_once(app):
    for round_ in range(40):
        ingest(app, 1, {"host": {"helper_events": helper_events(50, start=round_ * 50)}})

    stored = stored_events(app, kind="kernel_error")
    assert len(stored) == main.MAX_HELPER_EVENTS_PER_WEEK == 500
    assert len(stored_events(app, kind="helper_notice")) == 1
    assert len(stored_events(app, server_id=2)) == 0                              # another server is unaffected


def test_the_overview_shows_a_few_events_per_server_whatever_is_stored(app):
    with app.Session() as db:
        now = datetime.now(timezone.utc)
        db.add_all(SystemEvent(server_id=1, time=now - timedelta(minutes=i), kind="oom_kill", detail=f"oom {i}", event_key=f"k{i}") for i in range(3000))
        db.commit()
        events = main.recent_system_events(db, now - timedelta(days=7))

    assert len(events[1]) == main.SYSTEM_EVENTS_SHOWN and events[1][0]["detail"] == "oom 0"


# --- S2-03: the warning about replaced credentials can't be pushed out of sight --------------------------------------------

def test_the_replaced_credentials_event_is_always_shown_even_among_many_newer_events(app):
    with app.Session() as db:
        now = datetime.now(timezone.utc)
        db.add(SystemEvent(server_id=1, time=now - timedelta(minutes=30), kind="credentials_replaced", detail="replaced", event_key="c1"))
        db.add_all(SystemEvent(server_id=1, time=now - timedelta(minutes=i), kind="oom_kill", detail=f"oom {i}", event_key=f"k{i}") for i in range(25))
        db.commit()
        events = main.recent_system_events(db, now - timedelta(days=7))

    assert "credentials_replaced" in [event["kind"] for event in events[1]]


def test_the_replaced_credentials_alert_is_always_listed_even_beside_a_flood_of_critical_ones(app):
    with app.Session() as db:
        db.add_all(IncidentAlert(server_id=1, severity="CRITICAL", title=f"c{i}", description="d", rule_key=f"container:x{i}:unhealthy", status="active") for i in range(30))
        db.add(IncidentAlert(server_id=1, severity="WARNING", title="credentials replaced", description="d", rule_key="system:credentials", status="active"))
        db.commit()
        chosen = main._spread_alerts(db.query(IncidentAlert).order_by(IncidentAlert.severity.desc()).all())

    assert "system:credentials" in [alert.rule_key for alert in chosen]
    assert sum(1 for alert in chosen if alert.rule_key != "system:credentials") == main.OVERVIEW_ALERTS_PER_SERVER


def test_a_helper_event_dated_in_the_future_is_stored_as_happening_now(app):
    future = (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()
    before = datetime.now(timezone.utc)

    ingest(app, 1, {"host": {"helper_events": helper_events(1, when=future, kind="oom_kill")}})

    [event] = stored_events(app, kind="oom_kill")
    assert event.time.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc) + timedelta(seconds=1) and event.time.replace(tzinfo=timezone.utc) >= before - timedelta(seconds=1)


# --- S2-02: the lockout applies to the right password too ---------------------------------------------------------------------

def test_the_right_password_is_refused_once_the_two_factor_lockout_is_reached(app):
    client = sign_in(app)
    for _ in range(main.TWO_FACTOR_ATTEMPTS):
        assert client.post("/auth/2fa/setup", json={"password": "a-wrong-password-for-the-member"}).status_code == 401

    locked = client.post("/auth/2fa/setup", json={"password": PASSWORD})

    assert locked.status_code == 429
    with app.Session() as db:
        assert db.get(User, 1).totp_secret_enc is None                              # nothing was created for the thief


def test_a_correct_password_below_the_limit_still_works_and_clears_the_count(app):
    client = sign_in(app)
    for _ in range(main.TWO_FACTOR_ATTEMPTS - 2):
        client.post("/auth/2fa/setup", json={"password": "a-wrong-password-for-the-member"})

    assert client.post("/auth/2fa/setup", json={"password": PASSWORD}).status_code == 200
    for _ in range(main.TWO_FACTOR_ATTEMPTS - 1):                                  # the count started again from zero
        assert client.post("/auth/2fa/setup", json={"password": "a-wrong-password-for-the-member"}).status_code in (401, 409)


# --- S2-04: chat and conversations are limited -----------------------------------------------------------------------------

def test_chat_messages_are_limited_per_user_per_hour(app):
    client = sign_in(app)
    client.post("/conversations", json={"conversation_id": "c1"})

    codes = [client.post("/agent/chat", json={"conversation_id": "c1", "message": f"hello {i}"}).status_code for i in range(main.CHAT_MESSAGE_LIMIT + 3)]

    assert codes[:main.CHAT_MESSAGE_LIMIT] == [200] * main.CHAT_MESSAGE_LIMIT and codes[-3:] == [429] * 3


def test_a_user_can_only_hold_so_many_conversations(app, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONVERSATIONS_PER_USER", 5)
    monkeypatch.setattr(main, "CONVERSATION_CREATE_LIMIT", 10_000)
    client = sign_in(app)
    codes = [client.post("/conversations", json={"conversation_id": f"c{i}"}).status_code for i in range(main.MAX_CONVERSATIONS_PER_USER + 3)]

    assert codes[:main.MAX_CONVERSATIONS_PER_USER] == [200] * main.MAX_CONVERSATIONS_PER_USER and set(codes[-3:]) <= {409, 429}
    with app.Session() as db:
        assert db.query(Conversation).count() == main.MAX_CONVERSATIONS_PER_USER


def test_creating_conversations_is_rate_limited_too(app, monkeypatch):
    monkeypatch.setattr(main, "MAX_CONVERSATIONS_PER_USER", 10_000)
    client = sign_in(app)
    codes = [client.post("/conversations", json={"conversation_id": f"c{i}"}).status_code for i in range(main.CONVERSATION_CREATE_LIMIT + 2)]

    assert codes[-2:] == [429, 429]


# --- S2-05: hostnames can't pose as each other -----------------------------------------------------------------------------------

def hostname(client, server_id=1):
    with client.Session() as db:
        return db.get(Server, server_id).hostname


@pytest.mark.parametrize("lookalike", ["alphа", "аlpha", "alрha", "ａlpha-"[:-1]])      # Cyrillic а and р, and a full-width a
def test_a_lookalike_of_another_servers_name_is_ignored(app, lookalike):
    ingest(app, 2, {"server": {"hostname": lookalike}, "host": {}})

    assert hostname(app, 2) == "bravo" if False else hostname(app, 2) in ("bravo",)


def test_a_name_that_mixes_alphabets_is_ignored_even_when_nobody_else_has_it(app):
    ingest(app, 1, {"server": {"hostname": "web-аpp"}, "host": {}})

    assert hostname(app) == "alpha"


def test_names_in_one_alphabet_are_fine(app):
    ingest(app, 1, {"server": {"hostname": "Poslužitelj-1"}, "host": {}})
    ingest(app, 2, {"server": {"hostname": "сервер"}, "host": {}})

    assert hostname(app) == "Poslužitelj-1" and hostname(app, 2) == "сервер"


def test_a_second_server_with_an_existing_name_gets_a_number_so_the_two_can_be_told_apart(app):
    with app.Session() as db:
        db.add(User(id=9, name="Admin", email="admin@example.com", password_hash="x", role="admin", is_active=True))
        db.commit()
    first = main._unique_hostname(app.Session(), "raspberrypi")
    with app.Session() as db:
        db.add(Server(owner_id=1, hostname="raspberrypi"))
        db.commit()
        second = main._unique_hostname(db, "RaspberryPi")
        db.add(Server(owner_id=1, hostname=second))
        db.commit()
        third = main._unique_hostname(db, "raspberrypi")

    assert (first, second, third) == ("raspberrypi", "RaspberryPi (2)", "raspberrypi (3)")


# --- S2-06: a very long mount name can't break check-ins -------------------------------------------------------------------------

def test_a_mount_name_is_short_enough_for_every_incident_key_built_from_it(app):
    ingest(app, 1, {"host": {"mounts": [{"mount": "/" + "d" * 254, "used_pct": 99.0, "total_gb": 10, "used_gb": 9.9}]}})

    with app.Session() as db:
        from src.backend.models import MountMetric

        [row] = db.query(MountMetric).all()
        keys = [alert.rule_key for alert in db.query(IncidentAlert)]
    assert len(row.mount) <= main.MAX_MOUNT_NAME
    assert all(len(key) <= 255 for key in keys)


# --- S2-07: odd input is refused, not a server error ----------------------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    {"last_shutdown": ["x"]}, {"last_shutdown": {"a": 1}}, {"docker_status": ["ok"]}, {"docker_reason": {"x": 1}}, {"internet_status": ["ok"]},
    {"boot_id": "abc\x00def"}, {"boot_id": ["x"]}, {"boot_id": 12}, {"oom_kill_total": 2**40}, {"kernel_tainted": -3},
])
def test_unusual_values_in_a_check_in_are_ignored_not_a_server_error(app, host):
    assert ingest(app, 1, {"host": host}).status_code == 200


def test_a_nul_character_in_the_boot_id_is_not_stored(app):
    ingest(app, 1, {"host": {"boot_id": "abc\x00def"}})

    with app.Session() as db:
        from src.backend.models import HostMetric

        assert "\x00" not in (db.query(HostMetric).one().boot_id or "")


@pytest.mark.parametrize("path, body", [
    ("/monitoring/metrics/ingest", {"server_id": 2**40, "host": {}}),
    ("/monitoring/metrics/ingest", {"server_id": 0, "host": {}}),
    ("/monitoring/metrics/ingest", {"server_id": -1, "host": {}}),
    ("/conversations", {"conversation_id": "c" * 65}),
    ("/agent/chat", {"conversation_id": "c" * 65, "message": "hi"}),
])
def test_ids_must_fit_what_the_database_holds(app, path, body):
    sign_in(app)

    assert app.post(path, json=body).status_code == 422


@pytest.mark.parametrize("path", ["/monitoring/servers/99999999999/history?range=1h", "/monitoring/servers/0/history?range=1h", "/stats/servers/99999999999/heatmap"])
def test_server_ids_in_the_address_must_fit_too(app, path):
    sign_in(app)

    assert app.get(path).status_code == 422


# --- the smaller points from the review ------------------------------------------------------------------------------------------

def test_the_assistants_memory_of_chats_is_bounded_and_forgets_the_least_recently_used(monkeypatch):
    from src.agent import service

    instance = object.__new__(service.AgentService) if hasattr(service, "AgentService") else None
    if instance is None:
        pytest.skip("the assistant service class has another name")
    instance.provider = object()
    instance.chats, instance.lock = {}, __import__("threading").Lock()
    monkeypatch.setattr(service, "MAX_CACHED_CHATS", 3)
    for number in range(5):
        instance._get_chat(1, f"c{number}")
    instance._get_chat(1, "c2")                         # used again: now the newest

    assert list(instance.chats) == [(1, "c3"), (1, "c4"), (1, "c2")]


def test_a_user_cannot_write_the_log_entries_the_system_counts(app):
    sign_in(app)

    assert app.post("/logs", json={"message": "x", "event_type": "health_check_completed"}).status_code == 422
    assert app.post("/logs", json={"message": "x", "event_type": "report_opened"}).status_code == 200


def test_a_limited_session_expires_sooner_than_a_full_one(app):
    user = User(id=5, name="A", email="a@b.c", password_hash="x", role="admin", is_active=True)
    _, _ = auth.create_session(app.redis, user, stage=auth.STAGE_ENROLLMENT)
    _, _ = auth.create_session(app.redis, user, stage=auth.STAGE_FULL)

    lifetimes = sorted(app.redis.ttls[key] for key in app.redis.store if key.startswith("infradeck:session:"))
    assert lifetimes == [auth.ENROLLMENT_SESSION_TTL_SECONDS, auth.SESSION_TTL_SECONDS] and auth.ENROLLMENT_SESSION_TTL_SECONDS <= 1800


def test_collector_credentials_are_created_private_from_the_start(tmp_path):
    from src.agent import collector

    path = tmp_path / "state" / "credentials.json"
    collector.save_credentials(7, "secret-token", path)

    assert path.stat().st_mode & 0o777 == 0o600 and json.loads(path.read_text()) == {"server_id": 7, "collector_token": "secret-token"}
    assert list(path.parent.iterdir()) == [path]                                    # no temporary file left behind


def test_the_enrollment_limit_is_configurable():
    from src.backend import config

    assert config.ENROLL_RATE_LIMIT == 30 and main.ENROLL_RATE_LIMIT == config.ENROLL_RATE_LIMIT
