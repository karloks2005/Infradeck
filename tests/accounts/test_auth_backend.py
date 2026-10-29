import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend.auth import create_session, record_audit_log, register_user, verify_credentials
from tests.fakes import FakeRedis
from src.backend.models import AuditLog, Base, User


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
        password="correct-horse-battery-staple",
    )

    assert result["user"]["email"] == "ada@example.com"
    assert result["session_id"]
    assert session.query(User).count() == 1
    assert session.query(User).first().password_hash != "correct-horse-battery-staple"


def test_valid_credentials_identify_the_user_and_open_a_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    redis = FakeRedis()

    register_user(db=session, redis_client=redis, name="Grace Hopper", email="grace@example.com", password="correct-horse-battery-staple")
    user = verify_credentials(session, email="Grace@Example.com", password="correct-horse-battery-staple")
    session_id, public_id = create_session(redis, user)

    assert user.email == "grace@example.com" and session_id and public_id


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
        password="correct-horse-battery-staple",
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

    public = {"server_count": 0, "version": main.APP_VERSION}
    assert auth_routes.auth_config(db=session) == {"registration_enabled": True, "bootstrap_required": True, "instance_name": None, **public}
    session.add(User(name="Owner", email="owner@example.com", password_hash="x"))
    session.commit()
    # The first account is not an admin here, so there is nobody to name the install after.
    assert auth_routes.auth_config(db=session) == {"registration_enabled": False, "bootstrap_required": False, "instance_name": None, **public}

    with pytest.raises(HTTPException) as exc:
        auth_routes.auth_register(main.RegisterRequest(name="Stranger", email="s@example.com", password="correct-horse-battery-staple"), request=None, response=None, db=session)
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


import pytest
from fastapi import HTTPException


# --- first-account setup token --------------------------------------------------------------

def _fresh(monkeypatch, token=""):
    import os
    from src.backend import main
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(main, "ALLOW_REGISTRATION", False)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_TOKEN", token)
    monkeypatch.setattr(main, "enforce_register_limit", lambda *a, **k: None)
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    return main, session


def _register(main, session, token, email="owner@example.com"):
    from types import SimpleNamespace
    from fastapi import Response
    request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"), headers={})
    return auth_routes.auth_register(main.RegisterRequest(name="Owner", email=email, password="correct-horse-battery-staple", bootstrap_token=token), request=request, response=Response(), db=session)


def test_fresh_install_generates_a_token_and_stores_only_its_hash(monkeypatch):
    main, session = _fresh(monkeypatch)
    token = main.ensure_bootstrap_token(session)

    assert token.startswith("hlb_") and len(token) > 20
    assert session.get(main.AppSetting, main.BOOTSTRAP_TOKEN_KEY).value == main._hash_secret(token) != token


def test_the_token_is_regenerated_at_each_start_until_an_account_exists(monkeypatch):
    main, session = _fresh(monkeypatch)
    first, second = main.ensure_bootstrap_token(session), main.ensure_bootstrap_token(session)
    assert first != second
    assert main._bootstrap_token_valid(session, second) and not main._bootstrap_token_valid(session, first)


def test_configured_token_is_used_and_not_printed(monkeypatch):
    main, session = _fresh(monkeypatch, token="my-own-setup-token")
    assert main.ensure_bootstrap_token(session) is None
    assert main._bootstrap_token_valid(session, "my-own-setup-token")


@pytest.mark.parametrize("bad", [None, "", "   ", "hlb_wrong", "x" * 200])      # 200 is the longest the request model accepts (longer is refused before it gets here)
def test_first_account_is_refused_without_the_right_token(monkeypatch, bad):
    main, session = _fresh(monkeypatch)
    main.ensure_bootstrap_token(session)
    with pytest.raises(HTTPException) as exc:
        _register(main, session, bad)
    assert exc.value.status_code == 403 and "setup token" in exc.value.detail
    assert session.query(User).count() == 0


def test_first_account_with_the_token_becomes_admin_and_the_token_is_removed(monkeypatch):
    main, session = _fresh(monkeypatch)
    token = main.ensure_bootstrap_token(session)

    result = _register(main, session, token)

    assert result["user"]["role"] == "admin"
    assert session.get(main.AppSetting, main.BOOTSTRAP_TOKEN_KEY) is None
    # The token can't create a second account, and registration is closed anyway.
    with pytest.raises(HTTPException) as exc:
        _register(main, session, token, email="second@example.com")
    assert exc.value.status_code == 403


def test_without_a_stored_token_the_first_account_is_refused(monkeypatch):
    main, session = _fresh(monkeypatch)  # startup never ran
    with pytest.raises(HTTPException) as exc:
        _register(main, session, "anything")
    assert exc.value.status_code == 403


def test_existing_installs_need_no_token_and_leftovers_are_cleaned_up(monkeypatch):
    main, session = _fresh(monkeypatch)
    main.ensure_bootstrap_token(session)
    session.add(User(name="Owner", email="o@example.com", password_hash="x"))
    session.commit()

    assert main.ensure_bootstrap_token(session) is None
    assert session.get(main.AppSetting, main.BOOTSTRAP_TOKEN_KEY) is None


# --- password policy ------------------------------------------------------------------------

from src.backend.routes import auth as auth_routes
from src.backend import auth as auth_module


@pytest.mark.parametrize("password", ["", "short", "a" * 15, "fifteen-chars-ok"[:15], "12345678901234 "[:15]])
def test_passwords_under_16_characters_are_refused(password):
    with pytest.raises(ValueError, match="at least 16"):
        auth_module.validate_password(password)


@pytest.mark.parametrize("password", ["a" * 16, "correct horse battery staple", "p" * 72, "ü" * 36, "пароль-пароль-пароль"])
def test_acceptable_passwords_pass(password):
    auth_module.validate_password(password)


@pytest.mark.parametrize("password", ["p" * 73, "ü" * 37, "x" * 5000])
def test_passwords_over_bcrypts_72_byte_limit_are_refused_not_truncated(password):
    with pytest.raises(ValueError, match="at most 72 bytes"):
        auth_module.validate_password(password)


def test_registration_enforces_the_policy_and_creates_no_account():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    with pytest.raises(ValueError, match="at least 16"):
        register_user(session, FakeRedis(), name="A", email="a@example.com", password="hunter2hunter2")  # 14 characters
    assert session.query(User).count() == 0


def test_existing_accounts_with_short_passwords_can_still_sign_in():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(name="Old", email="old@example.com", password_hash=bcrypt.hashpw(b"old6pw", bcrypt.gensalt()).decode(), role="admin"))
    session.commit()

    assert verify_credentials(session, email="old@example.com", password="old6pw").email == "old@example.com"


def test_an_overlong_password_at_login_is_just_invalid_not_an_error_with_internals():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(name="U", email="u@example.com", password_hash=bcrypt.hashpw(b"x" * 20, bcrypt.gensalt()).decode()))
    session.commit()

    with pytest.raises(ValueError) as exc:
        verify_credentials(session, email="u@example.com", password="x" * 200)
    assert str(exc.value) == "Invalid email or password."


# --- cross-site request check ---------------------------------------------------------------

from src.backend.main import cross_site_request

ALLOWED = ["http://localhost:5173"]


def _h(**headers):
    return {key.replace("_", "-"): value for key, value in headers.items()}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_request_from_another_site_is_refused(method):
    assert cross_site_request(method, _h(origin="https://evil.example", host="dashboard.example.com"), ALLOWED) is True


def test_same_host_origin_is_allowed_whatever_the_scheme():
    for origin in ("https://dashboard.example.com", "http://dashboard.example.com"):
        assert cross_site_request("POST", _h(origin=origin, host="dashboard.example.com"), ALLOWED) is False


def test_the_port_must_match_too():
    assert cross_site_request("POST", _h(origin="http://dashboard.example.com:8080", host="dashboard.example.com"), ALLOWED) is True
    assert cross_site_request("POST", _h(origin="http://localhost:5173", host="localhost:5173"), ALLOWED) is False


def test_a_sibling_subdomain_is_a_different_site_for_this_purpose():
    assert cross_site_request("POST", _h(origin="https://other-app.example.com", host="dashboard.example.com"), ALLOWED) is True
    assert cross_site_request("POST", _h(origin="https://dashboard.example.com.evil.io", host="dashboard.example.com"), ALLOWED) is True


def test_origins_from_the_configured_list_are_allowed():
    assert cross_site_request("POST", _h(origin="http://localhost:5173", host="localhost:8000"), ALLOWED) is False


@pytest.mark.parametrize("origin", ["null", "", "file://", "javascript:alert(1)", "ftp://dashboard.example.com", "https://", "dashboard.example.com"])
def test_odd_origins_are_refused(origin):
    assert cross_site_request("POST", _h(origin=origin, host="dashboard.example.com"), ALLOWED) is True


def test_without_an_origin_the_fetch_metadata_header_decides():
    assert cross_site_request("POST", _h(sec_fetch_site="cross-site", host="d"), ALLOWED) is True
    assert cross_site_request("POST", _h(sec_fetch_site="same-site", host="d"), ALLOWED) is True
    assert cross_site_request("POST", _h(sec_fetch_site="same-origin", host="d"), ALLOWED) is False
    assert cross_site_request("POST", _h(sec_fetch_site="none", host="d"), ALLOWED) is False


def test_non_browser_clients_and_safe_methods_are_not_affected():
    # Collectors and curl send neither header and authenticate with tokens.
    assert cross_site_request("POST", _h(host="d"), ALLOWED) is False
    # Reads are never state-changing here, and CORS covers cross-origin reads.
    for method in ("GET", "HEAD", "OPTIONS"):
        assert cross_site_request(method, _h(origin="https://evil.example", host="d"), ALLOWED) is False


def test_the_real_app_refuses_a_cross_site_post_before_any_handler_runs():
    from starlette.testclient import TestClient
    from src.backend import main as app_main
    client = TestClient(app_main.app)

    response = client.post("/monitoring/pending-collectors/1/approve", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and response.json() == {"detail": "Cross-site request refused."}
    # Not a CSRF block: it fails later for lack of a session or a database, but never with the cross-site message.
    allowed = client.post("/auth/logout", headers={"Origin": "http://testserver"})
    assert allowed.json().get("detail") != "Cross-site request refused."
