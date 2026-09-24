import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend.auth import authenticate_user, record_audit_log, register_user
from src.backend.models import AuditLog, Base, User


class FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)
        return True


def test_register_user_creates_record_and_hashes_password():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    redis = FakeRedis()

    result = register_user(
        db=session,
        redis_client=redis,
        name="Ada Lovelace",
        email="ada@example.com",
        password="secret123",
    )

    assert result["user"]["email"] == "ada@example.com"
    assert result["session_id"]
    assert session.query(User).count() == 1
    assert session.query(User).first().password_hash != "secret123"


def test_authenticate_user_returns_session_for_valid_credentials():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    redis = FakeRedis()

    register_user(
        db=session,
        redis_client=redis,
        name="Grace Hopper",
        email="grace@example.com",
        password="hunter2",
    )

    result = authenticate_user(
        db=session,
        redis_client=redis,
        email="grace@example.com",
        password="hunter2",
    )

    assert result["user"]["email"] == "grace@example.com"
    assert result["session_id"]


def test_record_audit_log_persists_database_entries():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    redis = FakeRedis()

    user = register_user(
        db=session,
        redis_client=redis,
        name="Linus Torvalds",
        email="linus@example.com",
        password="linux123",
    )

    record_audit_log(
        db=session,
        user_id=user["user"]["id"],
        level="INFO",
        message="Dashboard opened",
        source="dashboard",
    )

    record_audit_log(
        db=session,
        user_id=user["user"]["id"],
        level="INFO",
        message="Server health check completed",
        source="health_monitor",
        conversation_id="conversation-1",
        event_type="health_check_completed",
        steps=["Completed predefined check 'server_health' with status ok."],
    )

    logs = session.query(AuditLog).order_by(AuditLog.created_at.desc()).all()
    assert len(logs) >= 3
    health_log = logs[0]
    assert health_log.message == "Server health check completed"
    assert health_log.conversation_id == "conversation-1"
    assert health_log.event_type == "health_check_completed"


def test_registration_closed_still_allows_first_account(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from src.backend import main

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(main, "ALLOW_REGISTRATION", False)

    assert main.auth_config(db=session) == {"registration_enabled": True}
    session.add(User(name="Owner", email="owner@example.com", password_hash="x"))
    session.commit()
    assert main.auth_config(db=session) == {"registration_enabled": False}

    with pytest.raises(HTTPException) as exc:
        main.auth_register(main.RegisterRequest(name="Stranger", email="s@example.com", password="secret123"), request=None, response=None, db=session)
    assert exc.value.status_code == 403


def test_registration_open_by_default(monkeypatch):
    from src.backend import main

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(name="Owner", email="owner@example.com", password_hash="x"))
    session.commit()
    monkeypatch.setattr(main, "ALLOW_REGISTRATION", True)

    assert main.registration_open(session) is True
