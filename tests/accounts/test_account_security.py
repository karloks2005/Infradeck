import base64
import json
import re
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import bcrypt
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from src.backend import auth, main, secrets_box, totp
from src.backend.models import AppSetting, AuditLog, Base, RecoveryCode, User
from tests.fakes import FakeRedis

PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "another-long-passphrase-here"
CLOCK = 1_800_000_000
COOKIE = "homie_session"


# --- TOTP (RFC 6238) --------------------------------------------------------------------------------

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize("timestamp, expected", [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")])
def test_codes_match_the_rfc_6238_reference_vectors(timestamp, expected):
    assert totp.code_at(RFC_SECRET, timestamp) == expected


def test_a_code_is_accepted_in_its_own_period_and_one_either_side_but_not_further():
    now = 1_800_000_015
    for offset, accepted in ((-90, False), (-60, False), (-30, True), (0, True), (30, True), (60, False), (90, False)):
        assert (totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, now + offset), now=now) is not None) is accepted, offset


def test_a_code_cannot_be_used_twice_or_after_a_newer_one():
    now = 1_800_000_015
    step = totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, now), now=now)

    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, now), last_step=step, now=now) is None
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, now - 30), last_step=step, now=now) is None
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, now + 30), last_step=step, now=now) == step + 1


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34 5", "١٢٣٤٥٦", "123456\x00", None, 123456, ["123456"], {"c": 1}, "１２３４５６"])
def test_malformed_codes_are_rejected(bad):
    assert totp.verify(RFC_SECRET, bad, now=1_800_000_015) is None


def test_spaces_and_a_hyphen_in_a_typed_code_are_ignored():
    code = totp.code_at(RFC_SECRET, 1_800_000_015)
    assert totp.verify(RFC_SECRET, f"{code[:3]} {code[3:]}", now=1_800_000_015) is not None
    assert totp.verify(RFC_SECRET, f"{code[:3]}-{code[3:]}", now=1_800_000_015) is not None


def test_new_secrets_are_random_and_authenticator_friendly():
    first, second = totp.new_secret(), totp.new_secret()
    assert first != second and len(first) == 32 and set(first) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert totp.code_at(first, 1_800_000_000).isdigit()


def test_the_otpauth_link_names_the_account_and_issuer_safely():
    uri = totp.otpauth_uri("ABCDEFGH", "a+b@example.com&x=1")
    assert uri.startswith("otpauth://totp/Vexion%3Aa%2Bb%40example.com%26x%3D1?secret=ABCDEFGH&issuer=Vexion") and "&x=1&" not in uri


def test_recovery_codes_are_random_formatted_and_normalised():
    codes = totp.generate_recovery_codes()
    assert len(codes) == len(set(codes)) == 10 and all(len(code) == 19 and code.count("-") == 3 for code in codes)
    assert totp.normalize_recovery_code(codes[0].lower().replace("-", " ")) == codes[0].replace("-", "")
    assert totp.hash_recovery_code(codes[0]) == totp.hash_recovery_code(codes[0].lower()) != totp.hash_recovery_code(codes[1])


@pytest.mark.parametrize("bad", ["", "short", "A" * 17, "0O1I-0O1I-0O1I-0O1I", None, 5, "ABCD-EFGH-JKLM-NPQ!"])
def test_things_that_are_not_recovery_codes_are_refused(bad):
    assert totp.normalize_recovery_code(bad) is None


# --- encrypted secrets ------------------------------------------------------------------------------

def test_secrets_are_encrypted_and_round_trip(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())
    token = secrets_box.encrypt("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in token and secrets_box.decrypt(token) == "JBSWY3DPEHPK3PXP"
    assert secrets_box.encrypt("JBSWY3DPEHPK3PXP") != token  # fresh randomness each time


def test_without_a_key_nothing_can_be_stored(monkeypatch):
    monkeypatch.delenv("SECRETS_KEY", raising=False)
    assert secrets_box.available() is False
    with pytest.raises(secrets_box.SecretsUnavailable):
        secrets_box.encrypt("x")


@pytest.mark.parametrize("bad_key", ["", "   ", "not-a-key", "short", "x" * 44, ","])
def test_an_invalid_key_is_refused_not_used(monkeypatch, bad_key):
    monkeypatch.setenv("SECRETS_KEY", bad_key)
    assert secrets_box.available() is False


def test_a_wrong_key_or_tampered_value_cannot_be_decrypted(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())
    token = secrets_box.encrypt("secret")
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())
    with pytest.raises(secrets_box.SecretsUnavailable):
        secrets_box.decrypt(token)
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())
    tampered = secrets_box.encrypt("secret")[:-4] + "AAAA"
    for bad in (tampered, "garbage", "", "é"):
        with pytest.raises(secrets_box.SecretsUnavailable):
            secrets_box.decrypt(bad)


def test_keys_can_be_rotated_by_listing_the_new_one_first(monkeypatch):
    old, new = secrets_box.generate_key(), secrets_box.generate_key()
    monkeypatch.setenv("SECRETS_KEY", old)
    token = secrets_box.encrypt("secret")
    monkeypatch.setenv("SECRETS_KEY", f"{new},{old}")

    assert secrets_box.decrypt(token) == "secret"
    fresh = secrets_box.encrypt("secret")
    assert Fernet(new).decrypt(fresh.encode()) == b"secret"  # new values use the first key
    monkeypatch.setenv("SECRETS_KEY", new)
    assert secrets_box.decrypt(fresh) == "secret"


# --- sessions ---------------------------------------------------------------------------------------

def _user(user_id=1, role="admin"):
    return User(id=user_id, name="N", email=f"u{user_id}@example.com", password_hash="x", role=role)


def test_a_session_is_indexed_under_its_user_and_shows_only_public_ids():
    redis = FakeRedis()
    session_id, public_id = auth.create_session(redis, _user(), ip="203.0.113.7", user_agent="Mozilla/5.0 \x1b[31mEvil" + "x" * 500)

    listed = auth.list_user_sessions(redis, 1, public_id)

    assert len(listed) == 1 and listed[0]["id"] == public_id and listed[0]["current"] is True and listed[0]["ip"] == "203.0.113.7"
    assert session_id not in repr(listed) and "\x1b" not in listed[0]["user_agent"] and len(listed[0]["user_agent"]) <= 120


def test_session_ids_are_long_random_and_distinct():
    redis = FakeRedis()
    ids = {auth.create_session(redis, _user())[0] for _ in range(20)}
    assert len(ids) == 20 and all(len(session_id) >= 43 for session_id in ids)


def test_one_session_can_be_revoked_without_touching_the_others():
    redis = FakeRedis()
    first, first_public = auth.create_session(redis, _user())
    second, second_public = auth.create_session(redis, _user())

    assert auth.revoke_user_session(redis, 1, second_public) is True
    assert auth.get_user_from_session(redis, second) is None and auth.get_user_from_session(redis, first) is not None
    assert [item["id"] for item in auth.list_user_sessions(redis, 1, first_public)] == [first_public]


def test_all_other_sessions_can_be_revoked_keeping_the_current_one():
    redis = FakeRedis()
    keep, keep_public = auth.create_session(redis, _user())
    others = [auth.create_session(redis, _user())[0] for _ in range(3)]

    assert auth.revoke_other_sessions(redis, 1, keep_public) == 3
    assert auth.get_user_from_session(redis, keep) is not None and all(auth.get_user_from_session(redis, other) is None for other in others)


def test_a_user_cannot_revoke_or_see_someone_elses_session():
    redis = FakeRedis()
    victim, victim_public = auth.create_session(redis, _user(2, "user"))
    _mine, _ = auth.create_session(redis, _user(1))

    assert auth.revoke_user_session(redis, 1, victim_public) is False
    assert auth.get_user_from_session(redis, victim) is not None and auth.list_user_sessions(redis, 1, None)[0]["id"] != victim_public
    # even a corrupted index entry pointing at another user's session is ignored and cleaned up
    redis.hset(auth._user_sessions_key(1), "0123456789abcdef", victim)
    assert all(item["id"] != victim_public for item in auth.list_user_sessions(redis, 1, None))
    assert auth.revoke_user_session(redis, 1, "0123456789abcdef") is False and auth.get_user_from_session(redis, victim) is not None


@pytest.mark.parametrize("bad", ["", "short", "../etc/passwd", "g" * 16, "0123456789abcdeF", "0" * 17, None, 5])
def test_malformed_public_ids_are_never_looked_up(bad):
    assert auth.revoke_user_session(FakeRedis(), 1, bad) is False


def test_expired_sessions_drop_out_of_the_list():
    redis = FakeRedis()
    session_id, public_id = auth.create_session(redis, _user())
    redis.delete(auth._session_key(session_id))  # what Redis does when the lifetime ends

    assert auth.list_user_sessions(redis, 1, None) == [] and redis.hgetall(auth._user_sessions_key(1)) == {}


def test_logging_out_removes_the_session_from_the_index():
    redis = FakeRedis()
    session_id, _ = auth.create_session(redis, _user())

    assert auth.delete_session(redis, session_id) is True and auth.list_user_sessions(redis, 1, None) == []
    assert auth.delete_session(redis, session_id) is False and auth.delete_session(redis, None) is False


def test_limited_sessions_are_refused_unless_a_caller_opts_in():
    redis = FakeRedis()
    session_id, _ = auth.create_session(redis, _user(), stage=auth.STAGE_ENROLLMENT)

    assert auth.get_user_from_session(redis, session_id) is None
    assert auth.get_user_from_session(redis, session_id, allow_limited=True)["stage"] == auth.STAGE_ENROLLMENT


def test_sessions_created_before_stages_existed_count_as_full():
    import json
    redis = FakeRedis()
    redis.set(auth._session_key("legacy"), json.dumps({"user_id": 1, "email": "a@b.c", "name": "A", "role": "admin"}))

    assert auth.get_user_from_session(redis, "legacy")["stage"] == auth.STAGE_FULL


# --- the app over HTTP ------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch):
    real = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=12, prefix=b"2b": real(rounds=4, prefix=prefix))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())
    redis, clock = FakeRedis(), {"now": CLOCK}
    monkeypatch.setattr(main, "redis_client", redis)
    monkeypatch.setattr(main, "SESSION_COOKIE_SECURE", False)  # the test client speaks plain HTTP, whatever .env says
    monkeypatch.setattr(totp, "_now", lambda: clock["now"])
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    password_hash = auth.hash_password(PASSWORD)
    db.add_all([User(id=1, name="Admin", email="admin@example.com", password_hash=password_hash, role="admin"),
                User(id=2, name="Member", email="member@example.com", password_hash=password_hash, role="user")])
    db.commit()
    main.app.dependency_overrides[main.get_db] = lambda: db
    yield SimpleNamespace(db=db, redis=redis, clock=clock)
    main.app.dependency_overrides.clear()


def _login(client, email="admin@example.com", password=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def _signed_in(email="admin@example.com"):
    client = TestClient(main.app)
    response = _login(client, email)
    assert response.status_code == 200, response.text
    return client


def _enroll(app, client):
    secret = client.post("/auth/2fa/setup", json={"password": PASSWORD}).json()["secret"]
    response = client.post("/auth/2fa/enable", json={"code": totp.code_at(secret, app.clock["now"])})
    assert response.status_code == 200, response.text
    return secret, response.json()["recovery_codes"]


def _code(app, secret, advance=0):
    app.clock["now"] += advance
    return totp.code_at(secret, app.clock["now"])


def _second_step(client, app, secret, code=None, advance=30, email="admin@example.com"):
    """Sign in with a password and return the response of the second step."""
    first = _login(client, email).json()
    assert first["status"] == "two_factor_required"
    return client.post("/auth/login/2fa", json={"pending_token": first["pending_token"], "code": code or _code(app, secret, advance)})


def _audit(app):
    return [row.message for row in app.db.query(AuditLog).filter(AuditLog.source == "auth").order_by(AuditLog.id)]


def test_a_member_signs_in_with_just_a_password(app):
    client = TestClient(main.app)
    response = _login(client, "member@example.com")

    assert response.status_code == 200 and response.json()["setup_required"] is False and COOKIE in client.cookies
    assert client.get("/monitoring/overview").status_code == 200


def test_an_admin_without_two_factor_gets_a_limited_session_when_it_is_required(app):
    client = TestClient(main.app)
    response = _login(client)

    assert response.json()["setup_required"] is True
    assert client.get("/auth/me").json()["user"]["setup_required"] is True


def test_a_limited_session_is_refused_everywhere_except_the_two_factor_setup(app):
    client = _signed_in()
    password_body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    for method, path, body in (("get", "/monitoring/overview", None), ("get", "/admin/settings", None), ("get", "/auth/sessions", None), ("get", "/agent/config", None),
                               ("get", "/dashboard/summary", None), ("post", "/auth/password", password_body), ("delete", "/monitoring/servers/1", None),
                               ("post", "/monitoring/join-tokens", None), ("get", "/monitoring/enrollment-key", None), ("get", "/conversations", None),
                               ("get", "/logs", None), ("post", "/auth/sessions/revoke-others", None)):
        response = client.request(method.upper(), path, json=body) if body else getattr(client, method)(path)
        assert response.status_code in (401, 403), (path, response.status_code)
    for path in ("/auth/me", "/auth/2fa/status"):
        assert client.get(path).status_code == 200, path


def test_every_route_that_needs_a_session_refuses_a_limited_one(app):
    allowed = {"/auth/me", "/auth/2fa/status", "/auth/2fa/setup", "/auth/2fa/enable", "/auth/logout", "/health", "/auth/config", "/auth/login", "/auth/login/2fa", "/auth/register",
               "/monitoring/collectors/register", "/monitoring/collectors/register/poll", "/monitoring/collectors/enroll", "/monitoring/metrics/ingest", "/"}
    client = _signed_in()
    leaked = []
    for route in main.app.routes:
        path = getattr(route, "path", None)
        if not path or path in allowed or path.startswith(("/docs", "/redoc", "/openapi")):
            continue
        for method in sorted(set(route.methods or []) & {"GET", "DELETE", "PATCH", "PUT", "POST"}):
            response = client.request(method, path.replace("{", "").replace("}", "").replace("server_id", "1").replace("pending_id", "1").replace("container_id", "x").replace("conversation_id", "x").replace("public_id", "0123456789abcdef"), json={})
            if response.status_code < 300:
                leaked.append((method, path, response.status_code))
    assert leaked == []


def test_when_no_key_is_configured_two_factor_is_not_enforced_and_cannot_be_set_up(app, monkeypatch):
    monkeypatch.delenv("SECRETS_KEY", raising=False)
    client = TestClient(main.app)

    assert _login(client).json()["setup_required"] is False
    assert client.get("/monitoring/overview").status_code == 200
    assert client.get("/auth/2fa/status").json()["available"] is False
    assert client.post("/auth/2fa/setup", json={"password": PASSWORD}).status_code == 503


def test_the_policy_can_be_turned_off_and_defaults_to_on(app):
    assert main.require_admin_two_factor(app.db) is True and main.two_factor_enforced(app.db) is True
    app.db.add(AppSetting(key=main.REQUIRE_ADMIN_2FA_KEY, value="false"))
    app.db.commit()

    assert main.require_admin_two_factor(app.db) is False and main.two_factor_enforced(app.db) is False
    assert _login(TestClient(main.app)).json()["setup_required"] is False


def test_the_policy_only_applies_to_administrators(app):
    app.db.add(AppSetting(key=main.REQUIRE_ADMIN_2FA_KEY, value="true"))
    app.db.commit()

    assert _login(TestClient(main.app), "member@example.com").json()["setup_required"] is False


def test_admins_can_change_the_policy_and_it_is_audited(app, monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda session_id, db: {"id": 1})
    assert main.get_admin_settings(session_id="s", db=app.db)["require_2fa_for_admins"] is True

    result = main.update_admin_settings(main.AdminSettingsRequest(require_2fa_for_admins=False), session_id="s", db=app.db)

    assert result["require_2fa_for_admins"] is False and result["secrets_configured"] is True
    assert any("Stopped requiring two-factor" in row.message for row in app.db.query(AuditLog))


def test_enrolling_upgrades_the_session_and_rotates_its_id(app):
    client = _signed_in()
    limited_cookie = client.cookies.get(COOKIE)

    secret, codes = _enroll(app, client)

    assert client.cookies.get(COOKIE) != limited_cookie and len(codes) == 10
    assert client.get("/monitoring/overview").status_code == 200
    old = TestClient(main.app)
    old.cookies.set(COOKIE, limited_cookie)
    assert old.get("/auth/me").status_code == 401  # the limited session no longer exists


def test_a_wrong_code_does_not_enable_two_factor(app):
    client = _signed_in()
    client.post("/auth/2fa/setup", json={"password": PASSWORD})
    user_before = app.db.get(User, 1).totp_enabled

    response = client.post("/auth/2fa/enable", json={"code": "000000"})

    assert response.status_code == 400 and user_before is False and app.db.get(User, 1).totp_enabled is False
    assert client.get("/monitoring/overview").status_code == 403


def test_enabling_without_starting_setup_is_refused(app):
    assert _signed_in().post("/auth/2fa/enable", json={"code": "123456"}).status_code == 400


def test_the_secret_is_stored_encrypted_and_recovery_codes_only_as_hashes(app):
    client = _signed_in()
    secret, codes = _enroll(app, client)

    user = app.db.get(User, 1)
    assert secret not in user.totp_secret_enc and secrets_box.decrypt(user.totp_secret_enc) == secret
    stored = {row.code_hash for row in app.db.query(RecoveryCode)}
    assert len(stored) == 10 and not any(code.replace("-", "") in " ".join(stored) for code in codes)
    assert stored == {totp.hash_recovery_code(code) for code in codes}


def test_secrets_never_appear_in_api_responses(app):
    client = _signed_in()
    secret, _ = _enroll(app, client)
    stored = app.db.get(User, 1).totp_secret_enc

    for path in ("/auth/me", "/auth/2fa/status", "/auth/sessions", "/admin/settings"):
        body = client.get(path).text
        assert secret not in body and stored not in body and "totp_secret" not in body, path


def test_signing_in_with_two_factor_needs_both_steps_and_creates_no_session_before_the_second(app):
    secret, _ = _enroll(app, _signed_in())
    client = TestClient(main.app)
    first = _login(client)

    assert first.json()["status"] == "two_factor_required" and COOKIE not in client.cookies and "user" not in first.json()
    assert client.get("/auth/me").status_code == 401 and client.get("/monitoring/overview").status_code == 401
    app.clock["now"] += 30
    done = client.post("/auth/login/2fa", json={"pending_token": first.json()["pending_token"], "code": totp.code_at(secret, app.clock["now"])})
    assert done.status_code == 200 and client.get("/monitoring/overview").status_code == 200


def test_a_wrong_or_malformed_code_is_refused_with_one_generic_message(app):
    secret, _ = _enroll(app, _signed_in())
    client = TestClient(main.app)
    messages = set()
    for code in ("000000", "12345", "abcdef", "١٢٣٤٥٦", "ABCD-EFGH-JKLM-NPQR"):
        token = _login(client).json()["pending_token"]
        response = client.post("/auth/login/2fa", json={"pending_token": token, "code": code})
        assert response.status_code == 401
        messages.add(response.json()["detail"])

    assert messages == {"Invalid code."} and COOKIE not in client.cookies


def test_a_code_cannot_be_replayed(app):
    secret, _ = _enroll(app, _signed_in())
    app.clock["now"] += 30
    code = totp.code_at(secret, app.clock["now"])

    assert _second_step(TestClient(main.app), app, secret, code=code, advance=0).status_code == 200
    assert _second_step(TestClient(main.app), app, secret, code=code, advance=0).status_code == 401
    assert _second_step(TestClient(main.app), app, secret, advance=30).status_code == 200  # the next period works


def test_the_code_from_the_enrollment_step_cannot_be_reused_to_sign_in(app):
    client = _signed_in()
    secret = client.post("/auth/2fa/setup", json={"password": PASSWORD}).json()["secret"]
    code = totp.code_at(secret, app.clock["now"])
    client.post("/auth/2fa/enable", json={"code": code})

    assert _second_step(TestClient(main.app), app, secret, code=code, advance=0).status_code == 401


def test_recovery_codes_work_once_and_the_count_drops(app):
    _secret, codes = _enroll(app, _signed_in())
    client = TestClient(main.app)

    first = client.post("/auth/login/2fa", json={"pending_token": _login(client).json()["pending_token"], "code": codes[0].lower()})
    assert first.status_code == 200 and first.json()["recovery_codes_left"] == 9
    again = client.post("/auth/login/2fa", json={"pending_token": _login(client).json()["pending_token"], "code": codes[0]})
    assert again.status_code == 401
    assert any("recovery code was used" in message for message in _audit(app))


def test_a_pending_login_token_is_single_use_and_unknown_tokens_are_refused(app):
    secret, _ = _enroll(app, _signed_in())
    client = TestClient(main.app)
    token = _login(client).json()["pending_token"]
    app.clock["now"] += 30
    code = totp.code_at(secret, app.clock["now"])

    assert client.post("/auth/login/2fa", json={"pending_token": token, "code": code}).status_code == 200
    app.clock["now"] += 30
    assert client.post("/auth/login/2fa", json={"pending_token": token, "code": totp.code_at(secret, app.clock["now"])}).status_code == 401
    for bad in ("", "x", "a" * 43, "../../x", "a" * 500):
        assert client.post("/auth/login/2fa", json={"pending_token": bad, "code": code}).status_code in (401, 422), bad


def test_an_expired_pending_login_cannot_be_completed(app):
    secret, _ = _enroll(app, _signed_in())
    client = TestClient(main.app)
    token = _login(client).json()["pending_token"]
    app.redis.delete(auth._pending_key(token))  # what Redis does when the five minutes are over

    assert client.post("/auth/login/2fa", json={"pending_token": token, "code": _code(app, secret, 30)}).status_code == 401


def test_a_pending_login_is_destroyed_after_five_wrong_codes(app):
    secret, _ = _enroll(app, _signed_in())
    client = TestClient(main.app)
    token = _login(client).json()["pending_token"]

    statuses = [client.post("/auth/login/2fa", json={"pending_token": token, "code": "000000"}).status_code for _ in range(6)]
    right = client.post("/auth/login/2fa", json={"pending_token": token, "code": _code(app, secret, 30)})

    assert statuses == [401, 401, 401, 401, 401, 401] and right.status_code == 401 and COOKIE not in client.cookies


def test_guessing_across_many_pending_logins_locks_the_account_for_a_while(app):
    secret, _ = _enroll(app, _signed_in())
    client = TestClient(main.app)
    statuses = []
    for _ in range(7):
        token = _login(client).json()["pending_token"]
        statuses.append(client.post("/auth/login/2fa", json={"pending_token": token, "code": "000000"}).status_code)

    assert statuses[:5] == [401] * 5 and statuses[5:] == [429, 429]
    token = _login(client).json()["pending_token"]
    assert client.post("/auth/login/2fa", json={"pending_token": token, "code": _code(app, secret, 30)}).status_code == 429  # even the right code, until the window ends


def test_a_successful_second_step_resets_the_failure_count(app):
    secret, _ = _enroll(app, _signed_in())
    client = TestClient(main.app)
    for _ in range(3):
        client.post("/auth/login/2fa", json={"pending_token": _login(client).json()["pending_token"], "code": "000000"})
    assert _second_step(client, app, secret).status_code == 200
    for _ in range(4):
        assert client.post("/auth/login/2fa", json={"pending_token": _login(client).json()["pending_token"], "code": "000000"}).status_code == 401


def test_the_per_address_limit_on_code_attempts_applies_too(app):
    client = TestClient(main.app)
    statuses = {client.post("/auth/login/2fa", json={"pending_token": "a" * 43, "code": "000000"}).status_code for _ in range(25)}

    assert 429 in statuses


def test_unknown_emails_cost_the_same_work_as_wrong_passwords(app, monkeypatch):
    calls = []
    real = bcrypt.checkpw
    monkeypatch.setattr(bcrypt, "checkpw", lambda *args: calls.append(1) or real(*args))
    client = TestClient(main.app)

    unknown = _login(client, "nobody@example.com", PASSWORD)
    wrong = _login(client, "member@example.com", "wrong-password-entirely")

    assert unknown.status_code == wrong.status_code == 401 and unknown.json() == wrong.json() and len(calls) == 2


@pytest.mark.parametrize("body", [{"pending_token": "a" * 43, "code": 123456}, {"pending_token": "a" * 43, "code": None}, {"pending_token": "a" * 43, "code": ["1"]},
                                  {"pending_token": "a" * 43, "code": "1" * 65}, {"pending_token": "a" * 101, "code": "123456"}, {"pending_token": 5, "code": "123456"}, {}])
def test_hostile_second_step_bodies_are_rejected_before_any_work(app, body):
    assert TestClient(main.app).post("/auth/login/2fa", json=body).status_code == 422


def test_a_members_two_factor_is_optional_but_available(app):
    client = _signed_in("member@example.com")
    status = client.get("/auth/2fa/status").json()
    assert status == {"enabled": False, "available": True, "required": False, "recovery_codes_left": 0, "setup_required": False}

    _enroll(app, client)
    assert client.get("/auth/2fa/status").json()["enabled"] is True and client.get("/auth/2fa/status").json()["recovery_codes_left"] == 10


def test_setup_is_refused_while_two_factor_is_already_on(app):
    client = _signed_in()
    _enroll(app, client)

    assert client.post("/auth/2fa/setup", json={"password": PASSWORD}).status_code == 409 and client.post("/auth/2fa/enable", json={"code": "123456"}).status_code == 409


def test_setting_up_again_before_confirming_replaces_the_pending_secret(app):
    client = _signed_in()
    first = client.post("/auth/2fa/setup", json={"password": PASSWORD}).json()["secret"]
    second = client.post("/auth/2fa/setup", json={"password": PASSWORD}).json()["secret"]

    assert first != second
    assert client.post("/auth/2fa/enable", json={"code": totp.code_at(first, app.clock["now"])}).status_code == 400


def test_turning_two_factor_off_needs_the_password_and_a_code_and_ends_other_sessions(app):
    member = _signed_in("member@example.com")
    secret, _ = _enroll(app, member)
    other = TestClient(main.app)
    assert _second_step(other, app, secret, email="member@example.com").status_code == 200

    wrong_password = member.post("/auth/2fa/disable", json={"password": "wrong", "code": _code(app, secret, 30)})
    wrong_code = member.post("/auth/2fa/disable", json={"password": PASSWORD, "code": "000000"})
    assert wrong_password.status_code == wrong_code.status_code == 401 and wrong_password.json() == wrong_code.json()
    assert app.db.get(User, 2).totp_enabled is True

    done = member.post("/auth/2fa/disable", json={"password": PASSWORD, "code": _code(app, secret, 30)})
    assert done.status_code == 200 and app.db.get(User, 2).totp_enabled is False and app.db.get(User, 2).totp_secret_enc is None
    assert app.db.query(RecoveryCode).filter(RecoveryCode.user_id == 2).count() == 0
    assert other.get("/auth/me").status_code == 401 and member.get("/auth/me").status_code == 200
    assert _login(TestClient(main.app), "member@example.com").json()["user"]["email"] == "member@example.com"  # single step again


def test_an_admin_cannot_turn_two_factor_off_while_the_policy_requires_it(app):
    client = _signed_in()
    secret, _ = _enroll(app, client)

    response = client.post("/auth/2fa/disable", json={"password": PASSWORD, "code": _code(app, secret, 30)})

    assert response.status_code == 403 and app.db.get(User, 1).totp_enabled is True


def test_a_limited_session_cannot_disable_regenerate_or_change_the_password(app):
    client = _signed_in()
    for path, body in (("/auth/2fa/disable", {"password": PASSWORD, "code": "123456"}), ("/auth/2fa/recovery-codes", {"password": PASSWORD, "code": "123456"}),
                       ("/auth/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD})):
        assert client.post(path, json=body).status_code == 403, path


def test_regenerating_recovery_codes_replaces_the_old_ones(app):
    client = _signed_in("member@example.com")
    secret, old_codes = _enroll(app, client)

    assert client.post("/auth/2fa/recovery-codes", json={"password": "wrong", "code": _code(app, secret, 30)}).status_code == 401
    response = client.post("/auth/2fa/recovery-codes", json={"password": PASSWORD, "code": _code(app, secret, 30)})
    new_codes = response.json()["recovery_codes"]

    assert response.status_code == 200 and len(new_codes) == 10 and not set(new_codes) & set(old_codes)
    other = TestClient(main.app)
    assert other.post("/auth/login/2fa", json={"pending_token": _login(other, "member@example.com").json()["pending_token"], "code": old_codes[0]}).status_code == 401
    assert other.post("/auth/login/2fa", json={"pending_token": _login(other, "member@example.com").json()["pending_token"], "code": new_codes[0]}).status_code == 200


def test_if_the_key_is_lost_totp_codes_stop_working_but_recovery_codes_still_do(app, monkeypatch):
    _secret, codes = _enroll(app, _signed_in("member@example.com"))
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())  # a different key from now on
    client = TestClient(main.app)

    token = _login(client, "member@example.com").json()["pending_token"]
    assert client.post("/auth/login/2fa", json={"pending_token": token, "code": "123456"}).status_code == 503
    token = _login(client, "member@example.com").json()["pending_token"]
    assert client.post("/auth/login/2fa", json={"pending_token": token, "code": codes[0]}).status_code == 200


def test_a_wrong_code_during_sign_in_is_audited_without_the_code(app):
    _enroll(app, _signed_in())
    client = TestClient(main.app)
    client.post("/auth/login/2fa", json={"pending_token": _login(client).json()["pending_token"], "code": "135790"})

    messages = _audit(app)
    assert "A wrong two-factor code was entered" in messages and not any("135790" in message for message in messages)


def test_changing_the_password_signs_out_other_sessions_and_rotates_this_one(app):
    client = _signed_in("member@example.com")
    other = _signed_in("member@example.com")
    old_cookie = client.cookies.get(COOKIE)

    response = client.post("/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})

    assert response.status_code == 200 and response.json()["other_sessions_signed_out"] == 1
    assert client.cookies.get(COOKIE) != old_cookie and client.get("/auth/me").status_code == 200
    assert other.get("/auth/me").status_code == 401
    stale = TestClient(main.app)
    stale.cookies.set(COOKIE, old_cookie)
    assert stale.get("/auth/me").status_code == 401
    assert _login(TestClient(main.app), "member@example.com", PASSWORD).status_code == 401
    assert _login(TestClient(main.app), "member@example.com", NEW_PASSWORD).status_code == 200
    assert app.db.get(User, 2).password_changed_at is not None and any("Password was changed" in message for message in _audit(app))


@pytest.mark.parametrize("current, new, status", [
    ("wrong-current-password", NEW_PASSWORD, 400),
    (PASSWORD, "short", 400),
    (PASSWORD, "p" * 73, 400),
    (PASSWORD, PASSWORD, 400),
    (PASSWORD, "", 400),
])
def test_bad_password_changes_are_refused_and_change_nothing(app, current, new, status):
    client = _signed_in("member@example.com")
    before = app.db.get(User, 2).password_hash

    response = client.post("/auth/password", json={"current_password": current, "new_password": new})

    assert response.status_code == status and app.db.get(User, 2).password_hash == before
    assert _login(TestClient(main.app), "member@example.com", PASSWORD).status_code == 200


def test_password_change_attempts_are_rate_limited(app):
    client = _signed_in("member@example.com")
    statuses = [client.post("/auth/password", json={"current_password": "wrong-current-password", "new_password": NEW_PASSWORD}).status_code for _ in range(7)]

    assert statuses[:5] == [400] * 5 and statuses[5:] == [429, 429]
    assert client.post("/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 429


def test_the_account_page_lists_sessions_and_can_end_them(app):
    mine, other = _signed_in("member@example.com"), _signed_in("member@example.com")
    listed = mine.get("/auth/sessions").json()["sessions"]
    assert len(listed) == 2 and sum(item["current"] for item in listed) == 1
    assert COOKIE not in repr(listed) and mine.cookies.get(COOKIE) not in repr(listed) and other.cookies.get(COOKIE) not in repr(listed)

    theirs = next(item["id"] for item in listed if not item["current"])
    assert mine.delete(f"/auth/sessions/{theirs}").status_code == 200 and other.get("/auth/me").status_code == 401
    assert mine.delete(f"/auth/sessions/{theirs}").status_code == 404


def test_ending_the_current_session_through_the_list_clears_the_cookie(app):
    client = _signed_in("member@example.com")
    current = next(item["id"] for item in client.get("/auth/sessions").json()["sessions"] if item["current"])

    assert client.delete(f"/auth/sessions/{current}").status_code == 200 and client.get("/auth/me").status_code == 401


def test_sign_out_everywhere_else_keeps_only_this_session(app):
    mine = _signed_in("member@example.com")
    others = [_signed_in("member@example.com") for _ in range(3)]

    assert mine.post("/auth/sessions/revoke-others").json() == {"signed_out": 3}
    assert mine.get("/auth/me").status_code == 200 and all(other.get("/auth/me").status_code == 401 for other in others)


def test_one_user_cannot_end_another_users_session_by_guessing_its_id(app):
    admin_client = _signed_in("admin@example.com")
    member = _signed_in("member@example.com")
    admin_public = next(item["id"] for item in admin_client.get("/auth/sessions").json()["sessions"] if False) if False else None
    member_public = next(item["id"] for item in member.get("/auth/sessions").json()["sessions"])

    # an admin in a full session (after enrolling) still can't use a member's id
    secret, _ = _enroll(app, admin_client)
    assert admin_client.delete(f"/auth/sessions/{member_public}").status_code == 404 and member.get("/auth/me").status_code == 200


@pytest.mark.parametrize("path", ["/auth/sessions/short", "/auth/sessions/..%2F..%2Fetc", "/auth/sessions/" + "g" * 16, "/auth/sessions/" + "0" * 40])
def test_malformed_session_ids_in_the_url_are_not_found(app, path):
    assert _signed_in("member@example.com").delete(path).status_code in (404, 405, 422)


def test_logout_ends_the_session_everywhere_and_the_cookie_is_cleared(app):
    client = _signed_in("member@example.com")
    cookie = client.cookies.get(COOKIE)

    assert client.post("/auth/logout").status_code == 200 and client.get("/auth/me").status_code == 401
    assert app.redis.hgetall(auth._user_sessions_key(2)) == {} and app.redis.get(auth._session_key(cookie)) is None


def test_logging_out_a_session_that_already_expired_is_still_fine(app):
    client = TestClient(main.app)
    client.cookies.set(COOKIE, "an-expired-session-id")
    assert client.post("/auth/logout").status_code == 200


def test_the_first_account_on_a_fresh_install_must_enrol_when_the_policy_is_on(app):
    app.db.query(User).delete()
    app.db.commit()
    token = main.ensure_bootstrap_token(app.db)
    client = TestClient(main.app)

    response = client.post("/auth/register", json={"name": "Owner", "email": "owner@example.com", "password": PASSWORD, "bootstrap_token": token})

    assert response.status_code == 200 and response.json()["setup_required"] is True
    assert client.get("/monitoring/overview").status_code == 403 and client.get("/auth/me").json()["user"]["role"] == "admin"


def test_cross_site_requests_to_the_new_endpoints_are_refused(app):
    client = _signed_in("member@example.com")
    for path, body in (("/auth/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}), ("/auth/2fa/setup", {}), ("/auth/sessions/revoke-others", {})):
        assert client.post(path, json=body, headers={"Origin": "https://evil.example"}).status_code == 403, path
    assert app.db.get(User, 2).password_changed_at is None


def test_resetting_two_factor_from_the_command_line_helper(app):
    member = _signed_in("member@example.com")
    secret, _ = _enroll(app, member)
    other = TestClient(main.app)
    assert _second_step(other, app, secret, email="member@example.com").status_code == 200

    auth.reset_two_factor(app.db, app.redis, app.db.get(User, 2))

    user = app.db.get(User, 2)
    assert (user.totp_enabled, user.totp_secret_enc, user.totp_last_step) == (False, None, None)
    assert app.db.query(RecoveryCode).filter(RecoveryCode.user_id == 2).count() == 0
    assert member.get("/auth/me").status_code == 401 and other.get("/auth/me").status_code == 401
    assert "Two-factor was reset from the command line" in _audit(app)
    assert "user" in _login(TestClient(main.app), "member@example.com").json()


def test_a_reset_admin_is_asked_to_enrol_again_when_it_is_required(app):
    client = _signed_in()
    _enroll(app, client)
    auth.reset_two_factor(app.db, app.redis, app.db.get(User, 1))

    assert _login(TestClient(main.app)).json()["setup_required"] is True


def test_the_reset_script_asks_for_exactly_one_email():
    script = os.path.join(os.path.dirname(__file__), "..", "..", "src", "backend", "reset_2fa.py")
    source = open(script).read()
    assert "reset_two_factor" in source and "len(argv) != 2" in source


# --- turning two-factor on from a normal session needs the password again ---------------------------------

def test_a_normal_session_must_give_the_password_to_set_up_two_factor(app):
    client = _signed_in("member@example.com")

    for body in (None, {}, {"password": None}, {"password": ""}, {"password": "wrong-password-entirely"}):
        response = client.post("/auth/2fa/setup", json=body) if body is not None else client.post("/auth/2fa/setup")
        assert response.status_code == 401, body
    user = app.db.get(User, 2)
    app.db.refresh(user)
    assert user.totp_secret_enc is None and user.totp_enabled is False            # nothing was created
    assert any("wrong or missing password" in message for message in _audit(app))


def test_the_right_password_starts_the_setup_from_a_normal_session(app):
    client = _signed_in("member@example.com")

    response = client.post("/auth/2fa/setup", json={"password": PASSWORD})

    assert response.status_code == 200 and len(response.json()["secret"]) == 32


def test_a_stolen_session_cannot_lock_the_owner_out_by_enrolling_its_own_authenticator(app):
    thief = _signed_in("member@example.com")           # has the session cookie but not the password

    assert thief.post("/auth/2fa/setup").status_code == 401
    assert thief.post("/auth/2fa/enable", json={"code": "123456"}).status_code == 400
    owner = TestClient(main.app)
    assert _login(owner, "member@example.com").json().get("status") != "two_factor_required"     # the owner can still sign in with the password alone


def test_wrong_passwords_while_setting_up_count_toward_the_two_factor_lockout(app):
    client = _signed_in("member@example.com")
    codes = [client.post("/auth/2fa/setup", json={"password": "nope-nope-nope-nope"}).status_code for _ in range(main.TWO_FACTOR_ATTEMPTS + 2)]

    assert codes[:main.TWO_FACTOR_ATTEMPTS] == [401] * main.TWO_FACTOR_ATTEMPTS and codes[-1] == 429


def test_the_forced_first_enrolment_does_not_ask_for_the_password_again(app):
    client = _signed_in("admin@example.com")           # an administrator who must enrol: the session just passed the password

    assert client.get("/auth/me").json()["user"]["setup_required"] is True
    assert client.post("/auth/2fa/setup").status_code == 200


# --- smaller hardening ----------------------------------------------------------------------------------

def test_turning_two_factor_off_gives_this_session_a_new_id(app):
    member = _signed_in("member@example.com")
    secret, _ = _enroll(app, member)
    before = member.cookies.get(COOKIE)

    assert member.post("/auth/2fa/disable", json={"password": PASSWORD, "code": _code(app, secret, 30)}).status_code == 200

    after = member.cookies.get(COOKIE)
    assert after and after != before
    assert main.redis_client.get(auth._session_key(before)) is None and member.get("/auth/me").status_code == 200


def test_a_successful_sign_in_does_not_reset_the_address_counter(app):
    """Someone with an account of their own must not be able to reset the limit between guesses at another account."""
    attacker = TestClient(main.app)
    for _ in range(main.LOGIN_RATE_LIMIT - 1):
        assert _login(attacker, "admin@example.com", "a-wrong-password-for-the-admin").status_code == 401
    assert _login(attacker, "member@example.com").status_code == 200                # their own account, from the same address

    # signing in did not buy more guesses: only the one allowed guess is left, then the address is locked out
    assert _login(attacker, "admin@example.com", "the-last-allowed-guess-attempt").status_code == 401
    assert _login(attacker, "admin@example.com", "another-wrong-password-attempt").status_code == 429


def test_signing_in_successfully_does_not_use_up_the_addresss_allowance(app):
    """People behind one shared address can all sign in without locking each other out."""
    for _ in range(main.LOGIN_RATE_LIMIT * 3):
        assert _login(TestClient(main.app), "member@example.com").status_code == 200


def test_a_successful_sign_in_still_resets_that_accounts_own_counter(app):
    client = TestClient(main.app)
    for _ in range(main.LOGIN_RATE_LIMIT - 2):
        _login(client, "member@example.com", "wrong-password-for-the-member")
    assert _login(client, "member@example.com").status_code == 200

    keys = [key for key in main.redis_client.keys("*") if "login-email" in key and "member" in key]
    assert keys == []


# --- Redis never holds a working session ID -----------------------------------------------------------------

def _everything_in_redis(redis):
    """Every key, hash field and value Redis holds, as text."""
    seen = list(redis.store) + [str(value) for value in redis.store.values()]
    for name, mapping in redis.hashes.items():
        seen += [name] + list(mapping) + [str(value) for value in mapping.values()]
    return seen


def test_redis_never_holds_the_session_cookie_value_or_the_pending_login_token(app):
    client = _signed_in("member@example.com")
    cookie = client.cookies.get(COOKIE)
    assert client.get("/auth/sessions").status_code == 200                     # the list and index are in use too

    assert cookie and not any(cookie in item for item in _everything_in_redis(app.redis))

    secret, _ = _enroll(app, client)
    pending = _login(TestClient(main.app), "member@example.com").json()["pending_token"]
    assert not any(pending in item for item in _everything_in_redis(app.redis))


def test_the_sessions_index_keeps_digests_not_session_ids(app):
    _signed_in("member@example.com")

    values = app.redis.hvals(auth._user_sessions_key(2))

    assert len(values) == 1 and re.fullmatch(r"[0-9a-f]{64}", values[0])


def test_a_session_stored_under_the_raw_cookie_value_is_not_accepted(app):
    """What an old-format key (or a guess at one) looks like: the cookie value itself in the key."""
    app.redis.set("homelab:session:planted-cookie-value", json.dumps({"user_id": 1, "email": "a@b.c", "name": "A", "role": "admin", "stage": "full", "public_id": "0" * 16}))
    client = TestClient(main.app, cookies={COOKIE: "planted-cookie-value"})

    assert client.get("/monitoring/overview").status_code == 401


def test_listing_and_ending_sessions_still_work_with_digests(app):
    first = _signed_in("member@example.com")
    second = _signed_in("member@example.com")

    listed = first.get("/auth/sessions").json()["sessions"]
    other = next(item for item in listed if not item["current"])
    assert len(listed) == 2 and first.delete(f"/auth/sessions/{other['id']}").status_code == 200

    assert second.get("/auth/me").status_code == 401 and first.get("/auth/me").status_code == 200


def test_old_format_session_data_is_removed_at_start_and_new_data_is_kept(app):
    client = _signed_in("member@example.com")
    old_session, old_pending, old_attempts = "homelab:session:" + "a" * 43, "homelab:2fa-pending:" + "b" * 43, "homelab:2fa-attempts:" + "c" * 43
    for key in (old_session, old_pending, old_attempts):
        app.redis.set(key, "x")
    app.redis.hset("homelab:user-sessions:9", "0123456789abcdef", "raw-session-id-not-a-digest")
    before = len(app.redis.keys("homelab:session:*"))

    removed = auth.purge_legacy_session_keys(app.redis)

    assert removed == 4
    for key in (old_session, old_pending, old_attempts):
        assert app.redis.get(key) is None
    assert app.redis.hgetall("homelab:user-sessions:9") == {}
    assert len(app.redis.keys("homelab:session:*")) == before - 1 and client.get("/auth/me").status_code == 200   # the live session survived


def test_the_cleanup_of_old_session_data_never_stops_the_backend_from_starting(app):
    class Broken:
        def scan_iter(self, *args, **kwargs):
            raise ConnectionError("redis is down")

    assert auth.purge_legacy_session_keys(Broken()) == 0
