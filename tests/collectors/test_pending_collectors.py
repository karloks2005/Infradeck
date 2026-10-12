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
from src.backend.models import Base, JoinToken, PendingCollector, Server, User


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
    pending_id = db.query(PendingCollector).one().id
    assert main.visible_pending_collectors(db)[0]["replaces"] == {"id": 3, "hostname": "nas"}

    # Replacing a server's credentials locks out its current collector, so it needs explicit confirmation.
    with pytest.raises(HTTPException) as exc:
        main.approve_pending_collector(pending_id, session_id="s", db=db)
    assert exc.value.status_code == 409 and "nas" in exc.value.detail
    assert db.query(PendingCollector).one().status == "pending"

    main.approve_pending_collector(pending_id, main.ApproveRequest(replace=True), session_id="s", db=db)

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


def test_collector_sends_its_join_token_when_registering(monkeypatch):
    sent = []

    def fake_post(url, payload, bearer=None):
        sent.append((url, payload))
        if url.endswith("/register"):
            return {"request_secret": "s", "pairing_code": "ABC-123", "poll_interval_seconds": 2}
        return {"status": "approved", "server_id": 4, "collector_token": "tok"}

    monkeypatch.setenv("HOMELAB_JOIN_TOKEN", "hlj_abc ")
    monkeypatch.setattr(collector, "_post_json", fake_post)
    monkeypatch.setattr(collector.time, "sleep", lambda seconds: None)

    assert collector.request_approval("http://dash/api") == (4, "tok")
    assert sent[0][1]["join_token"] == "hlj_abc"


def test_collector_without_a_join_token_sends_none(monkeypatch):
    sent = []
    monkeypatch.delenv("HOMELAB_JOIN_TOKEN", raising=False)
    monkeypatch.setattr(collector, "_post_json", lambda url, payload, bearer=None: sent.append(payload) or {"request_secret": "s", "pairing_code": "A", "poll_interval_seconds": 2} if url.endswith("/register") else {"status": "approved", "server_id": 1, "collector_token": "t"})
    monkeypatch.setattr(collector.time, "sleep", lambda seconds: None)
    collector.request_approval("http://dash/api")
    assert sent[0]["join_token"] is None


def test_collector_explains_a_refused_registration(monkeypatch, capsys):
    import io, json, urllib.error
    calls = []

    def fake_post(url, payload, bearer=None):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(json.dumps({"detail": "The join token is invalid, expired or already used."}).encode()))
        if url.endswith("/register"):
            return {"request_secret": "s", "pairing_code": "A", "poll_interval_seconds": 2}
        return {"status": "approved", "server_id": 1, "collector_token": "t"}

    monkeypatch.setattr(collector, "_post_json", fake_post)
    monkeypatch.setattr(collector.time, "sleep", lambda seconds: None)
    collector.request_approval("http://dash/api")

    assert "join token is invalid, expired or already used" in capsys.readouterr().out


# --- join tokens and the collector listener -------------------------------------------------

def _mint(db):
    return main.create_join_token(session_id="s", db=db)["join_token"]


def _request(via="", ip="10.0.0.5"):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers={"x-collector-via": via} if via else {})


def _register_via(db, via, token=None, machine_id="machine-a", hostname="nas", ip="10.0.0.5"):
    payload = main.CollectorRegisterRequest(hostname=hostname, machine_id=machine_id, join_token=token)
    return main.register_collector(payload, _request(via, ip), db=db)


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(main, "COLLECTOR_ACCESS", "strict")


def test_join_token_is_minted_hashed_and_expires(db):
    token = _mint(db)
    row = db.query(JoinToken).one()

    assert token.startswith("hlj_") and row.token_hash == main._hash_secret(token) and token not in row.token_hash
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)  # SQLite drops the zone
    assert 0 < (expires - datetime.now(timezone.utc)).total_seconds() <= main.JOIN_TOKEN_TTL_SECONDS


def test_join_token_minting_requires_admin_and_is_capped(db, monkeypatch):
    for _ in range(main.MAX_OPEN_JOIN_TOKENS):
        _mint(db)
    with pytest.raises(HTTPException) as exc:
        _mint(db)
    assert exc.value.status_code == 429

    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 2})
    db.add(User(id=2, name="Member", email="m@example.com", password_hash="x", role="user"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        main.create_join_token(session_id="s", db=db)
    assert exc.value.status_code == 403


def test_registration_with_a_valid_token_is_marked_verified(db, strict):
    _register_via(db, "vpn", _mint(db))

    [item] = main.visible_pending_collectors(db)
    assert item["verified"] is True


def test_vpn_listener_refuses_registration_without_a_token(db, strict):
    with pytest.raises(HTTPException) as exc:
        _register_via(db, "vpn")
    assert exc.value.status_code == 403 and "join token" in exc.value.detail
    assert db.query(PendingCollector).count() == 0


@pytest.mark.parametrize("bad", ["", "hlj_nonsense", "x" * 200])      # 200 is the longest the request model accepts
def test_wrong_tokens_are_refused(db, strict, bad):
    _mint(db)
    with pytest.raises(HTTPException) as exc:
        _register_via(db, "vpn", bad or "   ")
    assert exc.value.status_code == 403
    assert db.query(PendingCollector).count() == 0


def test_expired_token_is_refused(db, strict):
    token = _mint(db)
    db.query(JoinToken).update({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    db.commit()
    with pytest.raises(HTTPException) as exc:
        _register_via(db, "vpn", token)
    assert exc.value.status_code == 403


def test_token_is_consumed_when_the_server_is_approved(db, strict):
    token = _mint(db)
    _register_via(db, "vpn", token)
    main.approve_pending_collector(db.query(PendingCollector).one().id, session_id="s", db=db)

    assert db.query(JoinToken).one().used_at is not None
    with pytest.raises(HTTPException) as exc:
        _register_via(db, "vpn", token, machine_id="machine-b", hostname="other")
    assert exc.value.status_code == 403


def test_same_host_can_retry_with_its_token_before_approval(db, strict):
    token = _mint(db)
    _register_via(db, "vpn", token)
    _register_via(db, "vpn", token)

    assert db.query(PendingCollector).count() == 1


def test_strict_mode_hides_collector_endpoints_from_the_public_listener(db, strict):
    for via in ("", "public", "something-else"):
        with pytest.raises(HTTPException) as exc:
            main.require_collector_listener(_request(via))
        assert exc.value.status_code == 404
    main.require_collector_listener(_request("vpn"))
    main.require_collector_listener(_request("internal"))


def test_internal_listener_needs_no_token_so_the_dashboards_own_collector_can_join(db, strict):
    _register_via(db, "internal")
    assert main.visible_pending_collectors(db)[0]["verified"] is False


def test_open_mode_keeps_working_without_tokens(db):
    _register_via(db, "")
    assert db.query(PendingCollector).count() == 1


def test_a_bad_token_is_refused_even_in_open_mode(db):
    with pytest.raises(HTTPException) as exc:
        _register_via(db, "", "hlj_wrong")
    assert exc.value.status_code == 403


def test_install_info_reports_the_configured_addresses(db, monkeypatch):
    monkeypatch.setattr(main, "COLLECTOR_PUBLIC_URL", "http://10.8.0.1:8081/api")
    monkeypatch.setattr(main, "COLLECTOR_IMAGE", "ghcr.io/o/c@sha256:abc")
    info = main.install_info(session_id="s", db=db)
    assert info == {"strict": False, "collector_url": "http://10.8.0.1:8081/api", "collector_image": "ghcr.io/o/c@sha256:abc", "proxy_image": None}
