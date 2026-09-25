import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend import main
from src.backend.auth import register_user
from src.backend.models import AppSetting, Base, ContainerMetric, HostMetric, PendingCollector, Server, User


class FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, ex=None):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _login_as(monkeypatch, user_id):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": user_id})


def test_first_registered_user_is_admin_and_later_ones_are_not(db):
    first = register_user(db, FakeRedis(), name="First", email="first@example.com", password="secret123")
    second = register_user(db, FakeRedis(), name="Second", email="second@example.com", password="secret123")

    assert first["user"]["role"] == "admin"
    assert second["user"]["role"] == "user"


def test_ensure_admin_exists_promotes_oldest_user_only_when_no_admin(db):
    db.add_all([User(id=1, name="Old", email="old@example.com", password_hash="x"), User(id=2, name="New", email="new@example.com", password_hash="x")])
    db.commit()

    main.ensure_admin_exists(db)
    main.ensure_admin_exists(db)

    assert [user.role for user in db.query(User).order_by(User.id)] == ["admin", "user"]


def test_require_admin_rejects_regular_and_disabled_users(db, monkeypatch):
    db.add_all([
        User(id=1, name="Admin", email="a@example.com", password_hash="x", role="admin"),
        User(id=2, name="User", email="u@example.com", password_hash="x", role="user"),
        User(id=3, name="Disabled", email="d@example.com", password_hash="x", role="admin", is_active=False),
    ])
    db.commit()

    _login_as(monkeypatch, 1)
    assert main._require_admin("s", db) == {"id": 1}
    for user_id in (2, 3):
        _login_as(monkeypatch, user_id)
        with pytest.raises(HTTPException) as error:
            main._require_admin("s", db)
        assert error.value.status_code == 403


def test_admin_can_change_retention_within_limits(db, monkeypatch):
    db.add(User(id=1, name="Admin", email="a@example.com", password_hash="x", role="admin"))
    db.commit()
    _login_as(monkeypatch, 1)

    assert main.get_admin_settings(session_id="s", db=db)["metrics_retention_days"] == main.DEFAULT_METRICS_RETENTION_DAYS
    main.update_admin_settings(main.AdminSettingsRequest(metrics_retention_days=7), session_id="s", db=db)
    assert main.get_admin_settings(session_id="s", db=db)["metrics_retention_days"] == 7

    with pytest.raises(HTTPException) as error:
        main.update_admin_settings(main.AdminSettingsRequest(metrics_retention_days=0), session_id="s", db=db)
    assert error.value.status_code == 400


def test_purge_old_metrics_deletes_only_samples_past_retention(db):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    db.add(User(id=1, name="Owner", email="o@example.com", password_hash="x"))
    db.add(Server(id=1, owner_id=1, hostname="nas"))
    db.add(AppSetting(key=main.METRICS_RETENTION_KEY, value="7"))
    db.add_all([
        HostMetric(server_id=1, time=now - timedelta(days=8)),
        HostMetric(server_id=1, time=now - timedelta(days=6)),
        ContainerMetric(server_id=1, container_id="c1", name="web", status="running", time=now - timedelta(days=30)),
        ContainerMetric(server_id=1, container_id="c1", name="web", status="running", time=now - timedelta(hours=1)),
    ])
    db.commit()

    assert main.purge_old_metrics(db, now=now) == 2
    assert db.query(HostMetric).count() == 1
    assert db.query(ContainerMetric).count() == 1


@pytest.fixture
def fleet(db, monkeypatch):
    monkeypatch.setattr(main, "enforce_ip_limit", lambda *args, **kwargs: None)
    db.add_all([
        User(id=1, name="Admin", email="a@example.com", password_hash="x", role="admin"),
        User(id=2, name="Viewer", email="v@example.com", password_hash="x", role="user"),
    ])
    db.add(Server(id=10, owner_id=1, hostname="nas", check_interval_seconds=60))
    db.commit()
    return db


def test_regular_user_sees_servers_but_not_pending_collectors(fleet, monkeypatch):
    fleet.add(PendingCollector(poll_secret_hash="h", pairing_code="ABCD", hostname="pi", last_poll_at=datetime.now(timezone.utc)))
    fleet.commit()

    _login_as(monkeypatch, 2)
    viewer = main.monitoring_overview(session_id="s", db=fleet)
    _login_as(monkeypatch, 1)
    admin = main.monitoring_overview(session_id="s", db=fleet)

    assert [server["hostname"] for server in viewer["servers"]] == ["nas"]
    assert viewer["pending_collectors"] == []
    assert [item["hostname"] for item in admin["pending_collectors"]] == ["pi"]


@pytest.mark.parametrize("call", [
    lambda db: main.get_enrollment_key(session_id="s", db=db),
    lambda db: main.rotate_enrollment_key(session_id="s", db=db),
    lambda db: main.revoke_enrollment_key(session_id="s", db=db),
    lambda db: main.approve_pending_collector(1, session_id="s", db=db),
    lambda db: main.dismiss_pending_collector(1, session_id="s", db=db),
    lambda db: main.add_monitored_server(main.ServerRequest(hostname="new"), session_id="s", db=db),
    lambda db: main.delete_monitored_server(10, session_id="s", db=db),
    lambda db: main.update_check_interval(10, {"check_interval_seconds": 30}, session_id="s", db=db),
    lambda db: main.get_admin_settings(session_id="s", db=db),
])
def test_regular_user_cannot_manage_servers_or_collectors(fleet, monkeypatch, call):
    _login_as(monkeypatch, 2)
    with pytest.raises(HTTPException) as error:
        call(fleet)
    assert error.value.status_code == 403


def test_enrollment_key_stops_working_when_owner_is_no_longer_admin(fleet, monkeypatch):
    _login_as(monkeypatch, 1)
    key = main.rotate_enrollment_key(session_id="s", db=fleet)["enroll_key"]
    fleet.get(User, 1).role = "user"
    fleet.commit()

    with pytest.raises(HTTPException) as error:
        main.enroll_collector(main.EnrollRequest(enroll_key=key, hostname="pi"), db=fleet)
    assert error.value.status_code == 403


def test_admin_can_turn_agent_chat_off_and_on(fleet, monkeypatch):
    _login_as(monkeypatch, 1)
    assert main.get_admin_settings(session_id="s", db=fleet)["agent_chat_enabled"] is True

    result = main.update_admin_settings(main.AdminSettingsRequest(agent_chat_enabled=False), session_id="s", db=fleet)

    assert result == {"metrics_retention_days": main.DEFAULT_METRICS_RETENTION_DAYS, "agent_chat_enabled": False}
    _login_as(monkeypatch, 2)
    assert main.agent_config(session_id="s", db=fleet) == {"chat_enabled": False}


def test_agent_chat_is_rejected_while_disabled(fleet, monkeypatch):
    fleet.add(AppSetting(key=main.AGENT_CHAT_ENABLED_KEY, value="false"))
    fleet.commit()
    monkeypatch.setattr(main, "get_user_from_session", lambda redis_client, session_id: {"id": 2})
    monkeypatch.setattr(main, "send_agent_message", lambda *args, **kwargs: pytest.fail("agent must not be called"))

    with pytest.raises(HTTPException) as error:
        main.agent_chat(main.AgentChatRequest(conversation_id="c1", message="hi"), session_id="s", db=fleet)
    assert error.value.status_code == 403
