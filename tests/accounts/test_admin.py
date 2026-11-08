import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend.routes import chat as chat_routes
from src.backend.routes import monitoring as monitoring_routes
from src.backend import main, rules
from src.backend.auth import register_user
from tests.fakes import FakeRedis
from src.backend.models import AppSetting, Base, ContainerMetric, HostMetric, PendingCollector, Server, User


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _login_as(monkeypatch, user_id):
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": user_id})


def test_first_registered_user_is_admin_and_later_ones_are_not(db):
    first = register_user(db, FakeRedis(), name="First", email="first@example.com", password="correct-horse-battery-staple")
    second = register_user(db, FakeRedis(), name="Second", email="second@example.com", password="correct-horse-battery-staple")

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


def test_admin_can_keep_history_for_one_year_two_years_or_forever_and_nothing_shorter(db, monkeypatch):
    db.add(User(id=1, name="Admin", email="a@example.com", password_hash="x", role="admin"))
    db.commit()
    _login_as(monkeypatch, 1)

    assert main.get_admin_settings(session_id="s", db=db)["history_retention_days"] == 365
    for days in (730, 0, 365):
        main.update_admin_settings(main.AdminSettingsRequest(history_retention_days=days), session_id="s", db=db)
        assert main.get_admin_settings(session_id="s", db=db)["history_retention_days"] == days

    for days in (7, 30, 364, 3650):
        with pytest.raises(HTTPException) as error:
            main.update_admin_settings(main.AdminSettingsRequest(history_retention_days=days), session_id="s", db=db)
        assert error.value.status_code == 400


def test_the_purge_deletes_check_ins_older_than_a_week(db):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    db.add(User(id=1, name="Owner", email="o@example.com", password_hash="x"))
    db.add(Server(id=1, owner_id=1, hostname="nas"))
    db.add(AppSetting(key="rollup_done_hour", value=now.isoformat()))   # everything is summarised
    db.add_all([
        HostMetric(server_id=1, time=now - timedelta(days=8)),
        HostMetric(server_id=1, time=now - timedelta(days=6)),
        ContainerMetric(server_id=1, container_id="c1", name="web", status="running", time=now - timedelta(days=30)),
        ContainerMetric(server_id=1, container_id="c1", name="web", status="running", time=now - timedelta(hours=1)),
    ])
    db.commit()

    assert main.retention.purge(db, now=now) == 2
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
    viewer = monitoring_routes.monitoring_overview(session_id="s", db=fleet)
    _login_as(monkeypatch, 1)
    admin = monitoring_routes.monitoring_overview(session_id="s", db=fleet)

    assert [server["hostname"] for server in viewer["servers"]] == ["nas"]
    assert viewer["pending_collectors"] == []
    assert [item["hostname"] for item in admin["pending_collectors"]] == ["pi"]


@pytest.mark.parametrize("call", [
    lambda db: monitoring_routes.get_enrollment_key(session_id="s", db=db),
    lambda db: monitoring_routes.rotate_enrollment_key(session_id="s", db=db),
    lambda db: monitoring_routes.revoke_enrollment_key(session_id="s", db=db),
    lambda db: monitoring_routes.approve_pending_collector(1, session_id="s", db=db),
    lambda db: monitoring_routes.dismiss_pending_collector(1, session_id="s", db=db),
    lambda db: monitoring_routes.add_monitored_server(main.ServerRequest(hostname="new"), session_id="s", db=db),
    lambda db: monitoring_routes.delete_monitored_server(10, session_id="s", db=db),
    lambda db: monitoring_routes.update_check_interval(10, {"check_interval_seconds": 30}, session_id="s", db=db),
    lambda db: main.get_admin_settings(session_id="s", db=db),
])
def test_regular_user_cannot_manage_servers_or_collectors(fleet, monkeypatch, call):
    _login_as(monkeypatch, 2)
    with pytest.raises(HTTPException) as error:
        call(fleet)
    assert error.value.status_code == 403


def test_enrollment_key_stops_working_when_owner_is_no_longer_admin(fleet, monkeypatch):
    _login_as(monkeypatch, 1)
    key = monitoring_routes.rotate_enrollment_key(session_id="s", db=fleet)["enroll_key"]
    fleet.get(User, 1).role = "user"
    fleet.commit()

    with pytest.raises(HTTPException) as error:
        monitoring_routes.enroll_collector(main.EnrollRequest(enroll_key=key, hostname="pi"), db=fleet)
    assert error.value.status_code == 403


def test_admin_can_turn_agent_chat_off_and_on(fleet, monkeypatch):
    _login_as(monkeypatch, 1)
    assert main.get_admin_settings(session_id="s", db=fleet)["agent_chat_enabled"] is True

    result = main.update_admin_settings(main.AdminSettingsRequest(agent_chat_enabled=False), session_id="s", db=fleet)

    assert result == {"instance_name": main.get_instance_name(fleet), "history_retention_days": 365, "agent_chat_enabled": False, "alert_thresholds": rules.DEFAULT_THRESHOLDS, "mount_thresholds": {}, "require_2fa_for_admins": True, "secrets_configured": main.secrets_box.available(), "require_2fa_for_everyone": False, "disabled_rules": [],
                      "agent_prices": {}, "agent_monthly_cap_usd": None, "agent_daily_chat_limit": main.agent_usage.DEFAULT_DAILY_CHAT_LIMIT, "agent_auto_diagnose": True,
                      "agent_show_diagnoses_to_users": True, "agent_chat_access": "admins_only", "agent_tool_budget_diagnosis": 8, "agent_tool_budget_chat": 4}
    _login_as(monkeypatch, 2)
    config = main.agent_config(session_id="s", db=fleet)
    assert config["chat_enabled"] is False and config["budget_paused"] is False


def test_agent_chat_is_rejected_while_disabled(fleet, monkeypatch):
    fleet.add(AppSetting(key=main.AGENT_CHAT_ENABLED_KEY, value="false"))
    fleet.commit()
    monkeypatch.setattr(main, "get_user_from_session", lambda redis_client, session_id: {"id": 2})
    monkeypatch.setattr(main, "send_agent_message", lambda *args, **kwargs: pytest.fail("agent must not be called"))

    with pytest.raises(HTTPException) as error:
        chat_routes.agent_chat(main.AgentChatRequest(conversation_id="c1", message="hi"), session_id="s", db=fleet)
    assert error.value.status_code == 403
