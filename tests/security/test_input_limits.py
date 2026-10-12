"""Request bodies have sizes and shapes: nothing is stored or processed just because someone sent it."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.backend import main
from src.backend.models import AuditLog, Base, User
from tests.fakes import FakeRedis


@pytest.fixture
def app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as seed:
        seed.add(User(id=1, name="Member", email="member@example.com", password_hash="x", role="user", is_active=True))
        seed.commit()

    def get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[main.get_db] = get_db
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    monkeypatch.setattr(main, "get_user_from_session", lambda redis_client, session_id: {"id": 1, "role": "user"} if session_id else None)
    client = TestClient(main.app, raise_server_exceptions=False, cookies={main.SESSION_COOKIE_NAME: "a-session"})
    client.Session = Session
    yield client
    main.app.dependency_overrides.clear()


def entries(client):
    with client.Session() as db:
        return db.query(AuditLog).filter(AuditLog.source == "dashboard").all()


# --- POST /logs ---------------------------------------------------------------------------------------

def test_a_sensible_log_entry_is_stored_under_the_dashboard_source(app):
    response = app.post("/logs", json={"level": "warning", "message": "Opened the report", "steps": ["one", "  ", "two"], "event_type": "report_opened", "source": "auth"})

    assert response.status_code == 200
    [entry] = entries(app)
    assert (entry.level, entry.message, entry.source, entry.event_type) == ("WARNING", "Opened the report", "dashboard", "report_opened")


def test_nobody_can_write_entries_that_look_like_they_came_from_another_part_of_the_system(app):
    app.post("/logs", json={"message": "Admin changed the thresholds", "source": "admin", "event_type": "admin"})
    app.post("/logs", json={"message": "x", "source": "auth"})

    assert {entry.source for entry in entries(app)} == {"dashboard"}


@pytest.mark.parametrize("body", [
    {"message": "x" * 2001}, {"task_title": "t" * 256}, {"steps": ["s"] * 51}, {"steps": ["s" * 1001]}, {"steps": "not a list"}, {"steps": [1, 2]},
    {"level": "SURPRISE"}, {"level": 5}, {"event_type": "has spaces"}, {"event_type": "Upper"}, {"event_type": "e" * 51}, {"conversation_id": "c" * 256},
    {"message": ["a", "list"]}, {"message": None},
])
def test_log_entries_outside_the_limits_are_refused_and_nothing_is_stored(app, body):
    assert app.post("/logs", json=body).status_code == 422
    assert entries(app) == []


def test_a_megabyte_message_is_refused(app):
    assert app.post("/logs", json={"message": "m" * 1_000_000}).status_code == 422


def test_writing_log_entries_is_rate_limited_per_user(app):
    codes = [app.post("/logs", json={"message": f"entry {i}"}).status_code for i in range(main.LOG_WRITE_LIMIT + 2)]

    assert codes[:main.LOG_WRITE_LIMIT] == [200] * main.LOG_WRITE_LIMIT and codes[-2:] == [429, 429]


def test_log_entries_need_a_session(app):
    app.cookies.clear()

    assert app.post("/logs", json={"message": "x"}).status_code == 401


# --- other bodies ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, body", [
    ("/agent/chat", {"conversation_id": "c" * 256, "message": "hi"}),
    ("/agent/chat", {"conversation_id": "c", "message": "m" * 8001}),
    ("/conversations", {"conversation_id": "c" * 256}),
    ("/conversations", {"conversation_id": "c", "title": "t" * 256}),
    ("/auth/login", {"email": "a@b.co", "password": "p" * 1025}),
    ("/auth/register", {"name": "n" * 256, "email": "a@b.co", "password": "p" * 20}),
    ("/auth/register", {"name": "n", "email": "a@b.co", "password": "p" * 1025}),
    ("/auth/register", {"name": "n", "email": "a@b.co", "password": "p" * 20, "bootstrap_token": "t" * 201}),
    ("/monitoring/collectors/enroll", {"enroll_key": "k" * 201, "hostname": "h"}),
    ("/monitoring/collectors/enroll", {"enroll_key": "k", "hostname": "h" * 256}),
    ("/monitoring/collectors/enroll", {"enroll_key": "k", "hostname": "h", "machine_id": "m" * 65}),
    ("/monitoring/collectors/register", {"hostname": "h" * 256}),
    ("/monitoring/collectors/register", {"hostname": "h", "os_info": "o" * 256}),
    ("/monitoring/collectors/register", {"hostname": "h", "join_token": "j" * 201}),
    ("/monitoring/servers", {"hostname": "h" * 256}),
    ("/monitoring/servers", {"hostname": "h", "ip_address": "1" * 65}),
])
def test_oversized_fields_in_other_bodies_are_refused_before_any_work_is_done(app, path, body):
    assert app.post(path, json=body).status_code == 422


# --- safer defaults and smaller leaks ---------------------------------------------------------------------

def test_registration_is_closed_unless_someone_opens_it(monkeypatch):
    import importlib

    from src.backend import config

    monkeypatch.delenv("ALLOW_REGISTRATION", raising=False)
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    reloaded = importlib.reload(config)
    try:
        assert reloaded.ALLOW_REGISTRATION is False and reloaded.ENABLE_API_DOCS is False and reloaded.CORS_ORIGINS == []
        monkeypatch.setenv("ALLOW_REGISTRATION", "true")
        assert importlib.reload(config).ALLOW_REGISTRATION is True
    finally:
        monkeypatch.delenv("ALLOW_REGISTRATION", raising=False)
        importlib.reload(config)


def test_the_api_documentation_is_not_served_to_anonymous_visitors(app):
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert app.get(path).status_code == 404, path


def test_an_open_registration_is_called_out_in_the_log(app, monkeypatch):
    with app.Session() as db:
        monkeypatch.setattr(main, "ALLOW_REGISTRATION", False)
        assert main.registration_warning(db) is None
        monkeypatch.setattr(main, "ALLOW_REGISTRATION", True)
        assert "SECURITY WARNING" in main.registration_warning(db) and "ALLOW_REGISTRATION=false" in main.registration_warning(db)
        db.query(User).delete()
        db.commit()
        assert main.registration_warning(db) is None          # a fresh install has nobody to protect yet
