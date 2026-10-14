"""What the Agent, Account and Admin pages rest on: incidents you can act on, API tokens with a narrow reach,
notification channels that can't be pointed inwards, invitations, rule switches and two-factor for everyone."""
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import bcrypt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from src.backend import auth, incidents, main, notify, secrets_box
from src.backend.models import ApiToken, AuditLog, Base, ContainerMetric, HostMetric, IncidentAlert, Invite, NotificationChannel, Server, User
from tests.fakes import FakeRedis

PASSWORD = "correct-horse-battery-staple"
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch):
    real = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=12, prefix=b"2b": real(rounds=4, prefix=prefix))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())
    redis = FakeRedis()
    monkeypatch.setattr(main, "redis_client", redis)
    monkeypatch.setattr(main, "SESSION_COOKIE_SECURE", False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    password_hash = auth.hash_password(PASSWORD)
    db.add_all([User(id=1, name="Karlo Owner", email="owner@example.com", password_hash=password_hash, role="admin", totp_enabled=True),
                User(id=2, name="Ana Admin", email="ana@example.com", password_hash=password_hash, role="admin", totp_enabled=True),
                User(id=3, name="Vid Viewer", email="vid@example.com", password_hash=password_hash, role="user")])
    db.add(Server(id=1, owner_id=1, hostname="alpha", cpu_cores=4, total_ram_gb=16.0, check_interval_seconds=60, last_seen_at=NOW))
    db.add(IncidentAlert(id=42, server_id=1, severity="CRITICAL", title="Container gitlab is in a restart loop", description="gitlab restarted 6 times.", status="active", rule_key="container:gitlab:restart_loop", created_at=NOW - timedelta(minutes=12)))
    db.add(IncidentAlert(id=43, server_id=1, severity="WARNING", title="Swap usage on alpha is 60%", description="swap", status="active", rule_key="host:swap", created_at=NOW - timedelta(hours=2)))
    for index in range(30):
        moment = NOW - timedelta(minutes=30 - index)
        db.add(HostMetric(server_id=1, time=moment, cpu_utilization_pct=10, ram_pct=50, swap_pct=index * 2.0, load_1m=0.4))
        db.add(ContainerMetric(server_id=1, container_id="g" * 64, name="gitlab", status="running", restart_count=index // 5, memory_used_mb=3900 if index % 5 == 4 else 2000, memory_limit_mb=4096, exit_code=137, oom_killed=index > 20, time=moment))
    db.commit()
    main.app.dependency_overrides[main.get_db] = lambda: db
    yield SimpleNamespace(db=db, redis=redis)
    main.app.dependency_overrides.clear()


def signed_in(app, user_id):
    """A browser session for one of the seeded accounts (skipping the two-factor step, which has its own tests)."""
    client = TestClient(main.app)
    session_id, _public = auth.create_session(app.redis, app.db.get(User, user_id))
    client.cookies.set("homie_session", session_id)
    return client


# --- incidents ---------------------------------------------------------------------------------------

def test_the_inbox_lists_open_incidents_most_severe_first_and_says_who_may_act(app):
    inbox = signed_in(app, 3).get("/incidents").json()

    assert [item["reference"] for item in inbox["open"]] == ["INC-0042", "INC-0043"] and inbox["can_act"] is False
    assert [item["state"] for item in inbox["open"]] == ["diagnosed", "watching"] and inbox["muted"] == [] and inbox["resolved"] == []
    assert signed_in(app, 1).get("/incidents").json()["can_act"] is True


def test_a_diagnosis_is_built_from_the_stored_samples(app):
    incident = signed_in(app, 3).get("/incidents/42").json()
    found = incident["diagnosis"]

    assert found["container"] == "gitlab" and found["evidence"]["limit"] == 4096 and len(found["evidence"]["marks"]) == 5
    assert [signal["ok"] for signal in found["signals"]] == [True, True, True] and found["agreeing"] == 3
    assert found["signals"][0]["text"] == "The restart count rose by 5 in this window."
    assert found["steps"][0]["recommended"] and found["steps"][0]["command"] == "mem_limit: 6g" and found["steps"][-1]["title"] == "Confirm it's fixed"
    assert found["note"] == "limit is 4.0 GB, peak 3.8 GB"
    assert found["timeline"][-1]["text"].startswith("Incident opened")
    swap = signed_in(app, 3).get("/incidents/43").json()["diagnosis"]
    assert swap["evidence"]["title"] == "swap in use" and swap["evidence"]["series"][-1]["value"] == 58.0
    assert signed_in(app, 3).get("/incidents/999").status_code == 404


def test_a_container_name_never_becomes_part_of_a_command_unless_it_is_plain(app):
    assert incidents._command_name("gitlab-ce_1") == "gitlab-ce_1"
    assert incidents._command_name("x; rm -rf /") == "<container>" and incidents._command_name("$(id)") == "<container>" and incidents._command_name(None) == "<container>"


def test_only_admins_acknowledge_mute_and_resolve_and_each_is_logged(app):
    viewer, owner = signed_in(app, 3), signed_in(app, 1)
    for action in ("acknowledge", "resolve", "mute", "unmute"):
        assert viewer.post(f"/incidents/42/{action}").status_code == 403

    assert owner.post("/incidents/42/acknowledge").json()["acknowledged_by"] == "Karlo Owner"
    assert owner.post("/incidents/42/mute", json={"minutes": 60}).json()["muted_until"] is not None
    inbox = owner.get("/incidents").json()
    assert [item["id"] for item in inbox["open"]] == [43] and [item["id"] for item in inbox["muted"]] == [42]
    overview = owner.get("/monitoring/overview").json()
    assert overview["alerts_by_severity"] == {"WARNING": 1} and [alert["id"] for alert in overview["alerts"]] == [43]   # muted: out of the count and the list
    owner.post("/incidents/42/unmute")
    resolved = owner.post("/incidents/42/resolve").json()
    assert resolved["status"] == "resolved" and resolved["resolution"] == "Marked resolved by Karlo Owner"
    assert owner.post("/incidents/42/resolve").status_code == 409
    assert [row.message for row in app.db.query(AuditLog).filter(AuditLog.source == "incidents")][0].startswith("Acknowledged INC-0042")
    assert owner.post("/incidents/42/mute", json={"minutes": 999999}).status_code == 422


def test_the_overview_carries_the_agents_note_for_each_incident(app):
    alerts = {alert["id"]: alert for alert in signed_in(app, 3).get("/monitoring/overview").json()["alerts"]}
    assert alerts[42]["agent_note"] == "limit is 4.0 GB, peak 3.8 GB" and alerts[42]["reference"] == "INC-0042"


# --- API tokens --------------------------------------------------------------------------------------

def make_token(app, user_id, scope="read", **extra):
    return signed_in(app, user_id).post("/account/tokens", json={"name": "script", "scope": scope, **extra})


def with_token(token):
    client = TestClient(main.app)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def test_a_token_is_shown_once_stored_as_a_hash_and_reads_but_does_not_write(app):
    created = make_token(app, 3).json()
    token = created["token"]

    assert token.startswith("idk_r_") and created["hint"] == token[-4:]
    assert app.db.query(ApiToken).one().token_hash == main._hash_secret(token) and token not in str(signed_in(app, 3).get("/account").json())
    client = with_token(token)
    assert client.get("/monitoring/overview").status_code == 200 and client.get("/incidents").status_code == 200
    assert client.post("/incidents/42/acknowledge").status_code == 403
    assert app.db.query(ApiToken).one().last_used_at is not None


@pytest.mark.parametrize("method, path", [("get", "/account"), ("post", "/account/tokens"), ("get", "/admin/users"), ("get", "/admin/settings"), ("get", "/auth/sessions"), ("get", "/conversations"), ("delete", "/monitoring/servers/1"), ("post", "/monitoring/join-tokens"), ("patch", "/monitoring/servers/1")])
def test_no_token_reaches_the_account_admin_or_anything_that_changes_servers(app, method, path):
    token = make_token(app, 1, "write").json()["token"]   # the strongest token there is: an owner's write token
    response = getattr(with_token(token), method)(path, **({"json": {"name": "another", "hostname": "x"}} if method in ("post", "patch") else {}))
    assert response.status_code in (401, 403), (path, response.status_code)


def test_a_write_token_acknowledges_but_only_for_an_admin_and_never_sees_pending_servers(app):
    assert make_token(app, 3, "write").status_code == 403   # a viewer can't mint one
    token = make_token(app, 1, "write").json()["token"]
    client = with_token(token)

    assert client.post("/incidents/42/acknowledge").status_code == 200
    assert client.get("/monitoring/overview").json()["pending_collectors"] == []
    app.db.get(User, 1).role = "user"
    app.db.commit()
    assert client.post("/incidents/42/mute").status_code == 403   # the owner's role changed: the token follows it


def test_expired_revoked_and_unknown_tokens_are_refused(app):
    created = make_token(app, 3, expires_in_days=1).json()
    client = with_token(created["token"])
    assert client.get("/incidents").status_code == 200
    app.db.query(ApiToken).one().expires_at = NOW - timedelta(minutes=1)
    app.db.commit()
    assert client.get("/incidents").status_code == 401

    fresh = make_token(app, 3).json()
    assert signed_in(app, 2).delete(f"/account/tokens/{fresh['id']}").status_code == 404   # not someone else's to revoke
    assert signed_in(app, 3).delete(f"/account/tokens/{fresh['id']}").status_code == 200
    assert with_token(fresh["token"]).get("/incidents").status_code == 401
    assert with_token("idk_r_" + "x" * 43).get("/incidents").status_code == 401
    app.db.get(User, 3).is_active = False
    app.db.commit()
    assert with_token(make_token(app, 1).json()["token"]).get("/incidents").status_code == 200   # other accounts are unaffected


# --- profile and preferences -------------------------------------------------------------------------

def test_profile_and_preferences_keep_only_what_is_valid(app):
    client = signed_in(app, 3)
    account = client.get("/account").json()
    assert account["role_name"] == "Viewer" and account["preferences"]["display"]["theme"] == "dark" and signed_in(app, 1).get("/account").json()["role_name"] == "Owner"

    assert client.patch("/account/profile", json={"name": "Vid V.", "username": "Vid", "avatar_color": "#FF5A1F"}).json()["username"] == "vid"
    assert client.patch("/account/profile", json={"username": "no spaces"}).status_code == 400 and client.patch("/account/profile", json={"avatar_color": "red"}).status_code == 400
    assert signed_in(app, 2).patch("/account/profile", json={"username": "vid"}).status_code == 409
    saved = client.put("/account/preferences", json={"display": {"theme": "light", "density": "tiny", "timezone": "Europe/Zagreb", "evil": "<script>"}, "quiet": {"enabled": True, "start": "23:00", "end": "07:00"}}).json()
    assert saved["display"]["theme"] == "light" and saved["display"]["density"] == "comfortable" and saved["display"]["timezone"] == "Europe/Zagreb" and "evil" not in saved["display"]
    assert client.put("/account/preferences", json={"display": {"timezone": "Mars/Olympus"}}).status_code == 400
    assert client.put("/account/preferences", json={"quiet": {"start": "25:00", "end": "07:00"}}).status_code == 400


# --- notification channels ---------------------------------------------------------------------------

@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "resolve", lambda host, port: ["8.8.8.8"] if host == "ntfy.example" else ["10.0.0.5"] if host == "ntfy.lan" else ["127.0.0.1"])
    monkeypatch.setattr(notify, "_post", lambda url, data, headers: (sent.append((url, data, headers)) or (True, "200")))
    return sent


def test_channels_cannot_point_inwards_and_only_admins_reach_the_local_network(app, outbox):
    viewer, owner = signed_in(app, 3), signed_in(app, 1)
    for bad in ("http://localhost:6379/", "http://ntfy.lan/topic", "file:///etc/passwd", "http://user:pw@ntfy.example/t", ""):
        assert viewer.post("/account/channels", json={"kind": "ntfy", "target": bad}).status_code == 400, bad
    assert owner.post("/account/channels", json={"kind": "ntfy", "target": "http://localhost:6379/"}).status_code == 400
    assert owner.post("/account/channels", json={"kind": "ntfy", "target": "http://ntfy.lan/topic"}).status_code == 200

    created = viewer.post("/account/channels", json={"kind": "ntfy", "target": "https://ntfy.example/infradeck-secret-topic"}).json()
    assert created["display"] == "ntfy.example/…-topic" and "secret" not in str(viewer.get("/account").json()["channels"])
    assert "infradeck-secret-topic" not in app.db.query(NotificationChannel).filter_by(user_id=3).one().target_enc   # stored encrypted
    tested = viewer.post(f"/account/channels/{created['id']}/test").json()
    assert tested["last_ok"] is True and outbox[-1][0] == "https://ntfy.example/infradeck-secret-topic"
    assert owner.post(f"/account/channels/{created['id']}/test").status_code == 404 and owner.delete(f"/account/channels/{created['id']}").status_code == 404
    assert viewer.post("/account/channels", json={"kind": "email"}).status_code == 400   # no SMTP configured


def test_an_address_that_later_resolves_inwards_is_refused_at_send_time(app, outbox, monkeypatch):
    viewer = signed_in(app, 3)
    created = viewer.post("/account/channels", json={"kind": "webhook", "target": "https://ntfy.example/hook"}).json()
    monkeypatch.setattr(notify, "resolve", lambda host, port: ["169.254.169.254"])

    tested = viewer.post(f"/account/channels/{created['id']}/test").json()
    assert tested["last_ok"] is False and tested["last_status"] == "address refused" and outbox == []


def test_incidents_are_sent_once_following_routing_quiet_hours_and_muting(app, outbox):
    viewer = signed_in(app, 3)
    channel = viewer.post("/account/channels", json={"kind": "ntfy", "target": "https://ntfy.example/t"}).json()

    assert main.dispatch_notifications(app.db) == 2 and main.dispatch_notifications(app.db) == 0   # each incident once
    critical = next(message for message in outbox if b"INC-0042" in message[1])
    assert critical[2]["Priority"] == "5" and app.db.get(IncidentAlert, 42).notified_at is not None
    alert = app.db.get(IncidentAlert, 43)
    alert.status, alert.resolved_at = "resolved", datetime.now(timezone.utc)
    app.db.commit()
    assert main.dispatch_notifications(app.db) == 0   # "resolved" is off unless asked for
    viewer.put("/account/preferences", json={"routing": {"resolved": {str(channel["id"]): True}, "critical": {str(channel["id"]): False}, "warning": {"999": True}}})
    assert main.dispatch_notifications(app.db) == 1 and outbox[-1][2]["Title"].startswith("Resolved: ")

    app.db.add(IncidentAlert(id=50, server_id=1, severity="WARNING", title="late warning", description="d", status="active", rule_key="host:memory"))
    app.db.add(IncidentAlert(id=51, server_id=1, severity="CRITICAL", title="muted critical", description="d", status="active", rule_key="host:disk:/", muted_until=datetime.now(timezone.utc) + timedelta(hours=1)))
    app.db.commit()
    viewer.put("/account/preferences", json={"routing": {}, "quiet": {"enabled": True, "start": "00:00", "end": "23:59"}})
    assert main.dispatch_notifications(app.db) == 0                      # the warning waits, the muted one stays silent
    viewer.put("/account/preferences", json={"quiet": {"enabled": False, "start": "00:00", "end": "23:59"}})
    assert main.dispatch_notifications(app.db) == 1 and b"INC-0050" in outbox[-1][1]


# --- admin -------------------------------------------------------------------------------------------

def test_users_are_listed_and_changed_but_not_the_owner_or_yourself(app):
    ana = signed_in(app, 2)
    assert signed_in(app, 3).get("/admin/users").status_code == 403
    assert [(user["name"], user["role_name"]) for user in ana.get("/admin/users").json()["users"]] == [("Karlo Owner", "Owner"), ("Ana Admin", "Admin"), ("Vid Viewer", "Viewer")]
    assert ana.patch("/admin/users/1", json={"role": "user"}).status_code == 403 and ana.patch("/admin/users/2", json={"is_active": False}).status_code == 403

    vid = signed_in(app, 3)
    assert ana.patch("/admin/users/3", json={"is_active": False}).status_code == 200
    assert vid.get("/incidents").status_code == 401   # signed out at once
    assert ana.patch("/admin/users/3", json={"is_active": True, "role": "admin"}).json()["users"][2]["role_name"] == "Admin"


def test_an_invitation_lets_one_person_in_while_registration_is_closed(app):
    owner = signed_in(app, 1)
    assert owner.post("/admin/invites", json={"email": "owner@example.com"}).status_code == 409
    invite = owner.post("/admin/invites", json={"email": "Luka@Home.lan", "role": "user"}).json()
    assert app.db.query(Invite).one().token_hash == main._hash_secret(invite["token"]) and owner.get("/admin/users").json()["invites"][0]["email"] == "luka@home.lan"

    guest = TestClient(main.app)
    assert guest.get(f"/auth/invite?token={invite['token']}").json() == {"email": "luka@home.lan", "role": "user"}
    body = {"name": "Luka", "password": PASSWORD}
    assert guest.post("/auth/register", json={**body, "email": "luka@home.lan"}).status_code == 403                                   # closed without the invitation
    assert guest.post("/auth/register", json={**body, "email": "else@home.lan", "invite_token": invite["token"]}).status_code == 403   # not for this address
    assert guest.post("/auth/register", json={**body, "email": "luka@home.lan", "invite_token": "x" * 40}).status_code == 403
    assert guest.post("/auth/register", json={**body, "email": "luka@home.lan", "invite_token": invite["token"]}).json()["user"]["role"] == "user"
    assert TestClient(main.app).post("/auth/register", json={**body, "email": "luka2@home.lan", "invite_token": invite["token"]}).status_code == 403   # used
    assert TestClient(main.app).get(f"/auth/invite?token={invite['token']}").status_code == 404


def test_two_factor_can_be_required_of_everyone(app):
    owner = signed_in(app, 1)
    client = TestClient(main.app)
    assert client.post("/auth/login", json={"email": "vid@example.com", "password": PASSWORD}).json()["setup_required"] is False

    assert owner.put("/admin/settings", json={"require_2fa_for_everyone": True}).json()["require_2fa_for_everyone"] is True
    limited = TestClient(main.app)
    assert limited.post("/auth/login", json={"email": "vid@example.com", "password": PASSWORD}).json()["setup_required"] is True
    assert limited.get("/monitoring/overview").status_code == 403 and limited.get("/auth/2fa/status").json()["required"] is True


def test_a_rule_can_be_switched_off_and_the_table_shows_how_often_each_fired(app):
    owner = signed_in(app, 1)
    rules = {rule["kind"]: rule for rule in owner.get("/admin/rules").json()["rules"]}
    assert rules["container:restart_loop"]["fired"] == 1 and rules["container:restart_loop"]["when"] == "Container restarts ≥ 3" and rules["container:restart_loop"]["window"] == "30 min"
    assert rules["host:swap"]["enabled"] is True

    saved = owner.put("/admin/settings", json={"disabled_rules": ["host:swap", "made:up"]}).json()
    assert saved["disabled_rules"] == ["host:swap"]
    firing = {"host:swap": ("WARNING", "t", "d"), "host:memory": ("WARNING", "t", "d")}
    assert list(main._enabled_rules(app.db, firing)) == ["host:memory"]
    assert signed_in(app, 3).get("/admin/rules").status_code == 403


def test_every_threshold_the_old_admin_page_could_change_can_still_be_changed(app):
    rules = signed_in(app, 1).get("/admin/rules").json()["rules"]
    editable = {key for rule in rules for key in rule["edit"]}
    assert editable == set(main.DEFAULT_THRESHOLDS)
    by_kind = {rule["kind"]: rule for rule in rules}
    assert by_kind["system:docker_slow"]["when"] == "Docker takes > 2000 ms to list containers"
    assert by_kind["trend:memory"]["window"] == "12 h"
    # Rules that exist in rules.py but were missing from the table could not be switched off.
    firing = {"system:docker_slow": ("WARNING", "t", "d"), "system:collect_slow": ("WARNING", "t", "d")}
    signed_in(app, 1).put("/admin/settings", json={"disabled_rules": ["system:docker_slow", "system:collect_slow"]})
    assert main._enabled_rules(app.db, firing) == {}


def test_image_change_and_removal_incidents_can_be_switched_off(app):
    # Every rebuild of a stack opens one of these per container, so they must be switchable like any other rule.
    owner = signed_in(app, 1)
    kinds = {rule["kind"] for rule in owner.get("/admin/rules").json()["rules"]}
    assert {"container:image", "container:removed"} <= kinds
    owner.put("/admin/settings", json={"disabled_rules": ["container:image", "container:removed"]})
    firing = {"container:web:image": ("INFO", "t", "d"), "container:db:removed": ("INFO", "t", "d"), "container:web:exit": ("WARNING", "t", "d")}
    assert list(main._enabled_rules(app.db, firing)) == ["container:web:exit"]


def test_a_server_is_renamed_and_keeps_the_name_and_the_install_describes_itself(app):
    owner = signed_in(app, 1)
    assert owner.patch("/monitoring/servers/1", json={"hostname": "media-box", "check_interval_seconds": 15}).json() == {"id": 1, "hostname": "media-box", "check_interval_seconds": 15}
    assert app.db.get(Server, 1).name_locked is True and owner.patch("/monitoring/servers/1", json={"check_interval_seconds": 7}).status_code == 400
    assert signed_in(app, 3).patch("/monitoring/servers/1", json={"hostname": "x"}).status_code == 403

    system = owner.get("/admin/system").json()
    assert system["version"] == main.APP_VERSION and system["database"]["host_rows"] == 30 and system["agent"]["incidents_this_month"] >= 1


def test_admin_tests_the_agent_connection_and_the_key_never_comes_back(app, monkeypatch):
    owner = signed_in(app, 1)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    monkeypatch.setattr(main, "check_provider_connection", lambda: {"provider": "OpenAI", "model": "gpt-test"})

    system = owner.get("/admin/system")
    assert system.json()["agent"]["key_configured"] is True and system.json()["agent"]["provider"] == "OpenAI"
    result = owner.post("/admin/agent/test")
    assert result.json() == {"ok": True, "provider": "OpenAI", "model": "gpt-test"}
    assert "sk-test-secret-value" not in system.text + result.text
    assert signed_in(app, 3).post("/admin/agent/test").status_code == 403


def test_a_failed_agent_connection_test_explains_itself_without_the_key(app, monkeypatch):
    owner = signed_in(app, 1)

    def rejected():
        raise RuntimeError("Error code: 401 - Incorrect API key provided: sk-test-secret-value")
    monkeypatch.setattr(main, "check_provider_connection", rejected)

    response = owner.post("/admin/agent/test")
    assert response.status_code == 502 and "rejected the API key" in response.json()["detail"]
    assert "sk-test-secret-value" not in response.text


# --- the agent's spending (#112) ---------------------------------------------------------------------------------

from src.agent.providers import Completion
from src.backend.models import AgentRun


def _fake_agent(monkeypatch, tokens=(1000, 200), fail=None):
    def fake(user_id, conversation_id, message, command_logger=None, usage_logger=None):
        if fail:
            raise fail
        # A dated model name, as the API reports it: it must still use the price set for "gpt-test".
        usage_logger(Completion(text="{}", model="gpt-test-2026-01-01", input_tokens=tokens[0], output_tokens=tokens[1]), 1500)
        return "Here is the answer."
    monkeypatch.setattr(main, "send_agent_message", fake)
    monkeypatch.setattr(main, "is_agent_chat_enabled", lambda db: True)


def _ask(client, conversation="c1"):
    client.post("/conversations", json={"conversation_id": conversation, "title": "t"})
    return client.post("/agent/chat", json={"conversation_id": conversation, "message": "Why did it restart?", "incident_id": 42})


def test_every_chat_call_is_counted_with_its_tokens_and_cost(app, monkeypatch):
    _fake_agent(monkeypatch)
    owner = signed_in(app, 1)
    assert owner.put("/admin/settings", json={"agent_prices": {"gpt-test": {"input": 1, "output": 10}}}).status_code == 200

    assert _ask(owner).status_code == 200

    run = app.db.query(AgentRun).one()
    assert (run.kind, run.status, run.input_tokens, run.output_tokens, run.duration_ms, run.user_id, run.incident_id) == ("chat", "ok", 1000, 200, 1500, 1, 42)
    assert run.cost_usd == pytest.approx(0.003)   # 1000 × $1 + 200 × $10 per million tokens
    usage = owner.get("/admin/agent/usage").json()
    assert usage["calls"] == 1 and usage["cost"] == pytest.approx(0.003) and usage["by_model"][0]["model"] == "gpt-test-2026-01-01"
    assert usage["daily"][-1]["cost"] == pytest.approx(0.003)
    assert signed_in(app, 3).get("/admin/agent/usage").status_code == 403


def test_calls_made_before_a_price_was_entered_are_priced_later(app, monkeypatch):
    _fake_agent(monkeypatch)
    owner = signed_in(app, 1)
    _ask(owner)
    assert owner.get("/admin/agent/usage").json()["unpriced_runs"] == 1

    owner.put("/admin/settings", json={"agent_prices": {"gpt-test": {"input": 1, "output": 10}}})

    usage = owner.get("/admin/agent/usage").json()
    assert usage["cost"] == pytest.approx(0.003) and usage["unpriced_runs"] == 0


def test_the_monthly_budget_pauses_the_chat_and_tells_the_admins_once(app, monkeypatch):
    _fake_agent(monkeypatch)
    sent = []
    monkeypatch.setattr(main, "_deliver", lambda db, channel, owner, title, body, severity: sent.append((owner.id, title)) or True)
    app.db.add(NotificationChannel(user_id=1, kind="ntfy", target_enc=secrets_box.encrypt("https://ntfy.sh/x"), display="ntfy.sh/x"))
    app.db.add(NotificationChannel(user_id=3, kind="ntfy", target_enc=secrets_box.encrypt("https://ntfy.sh/y"), display="ntfy.sh/y"))
    app.db.commit()
    owner = signed_in(app, 1)
    owner.put("/admin/settings", json={"agent_prices": {"gpt-test": {"input": 1, "output": 10}}, "agent_monthly_cap_usd": 0.005})

    assert _ask(owner).status_code == 200           # $0.003 of $0.005
    assert _ask(owner).status_code == 200           # $0.006: over the budget
    paused = _ask(owner)

    assert paused.status_code == 429 and "budget" in paused.json()["detail"]
    assert app.db.query(AgentRun).count() == 2      # the paused request never reached the model
    assert sent == [(1, "The incident agent paused: its monthly budget is used up")]   # admins only, once
    assert owner.get("/agent/config").json()["budget_paused"] is True

    owner.put("/admin/settings", json={"agent_monthly_cap_usd": 0})   # removing the budget lets it run again
    assert _ask(owner).status_code == 200


def test_the_daily_limit_counts_messages_per_person(app, monkeypatch):
    _fake_agent(monkeypatch)
    owner = signed_in(app, 1)
    owner.put("/admin/settings", json={"agent_daily_chat_limit": 2})

    assert [_ask(owner).status_code for _ in range(3)] == [200, 200, 429]
    assert _ask(signed_in(app, 2), conversation="c2").status_code == 200   # someone else still can


def test_a_failed_call_is_recorded_without_tokens(app, monkeypatch):
    _fake_agent(monkeypatch, fail=TimeoutError("timed out"))
    owner = signed_in(app, 1)

    assert _ask(owner).status_code == 503

    run = app.db.query(AgentRun).one()
    assert (run.status, run.input_tokens, run.cost_usd) == ("error", 0, None) and "timed out" in run.error


@pytest.mark.parametrize("body", [
    {"agent_prices": {"gpt test": {"input": 1, "output": 2}}},
    {"agent_prices": {"gpt-test": {"input": -1, "output": 2}}},
    {"agent_prices": {"gpt-test": {"input": 1}}},
    {"agent_monthly_cap_usd": -5},
    {"agent_daily_chat_limit": 0},
])
def test_spending_settings_are_checked(app, body):
    assert signed_in(app, 1).put("/admin/settings", json=body).status_code == 400


def test_admins_see_the_agent_workers_status(app):
    status = signed_in(app, 1).get("/admin/agent/worker")
    assert status.status_code == 200 and status.json()["online"] is False and status.json()["queued"] >= 0
    assert signed_in(app, 3).get("/admin/agent/worker").status_code == 403


def test_an_incident_carries_the_agents_latest_report(app):
    from src.backend.models import AgentReport
    owner = signed_in(app, 1)
    assert owner.get("/incidents/42").json()["agent"]["report"] is None

    app.db.add(AgentReport(incident_id=42, version=1, trigger="incident.opened", status="ok", model="gpt-test", confidence="high", summary="gitlab is killed for memory. Raise its limit.",
                           body=json.dumps({"signals": [{"text": "Memory hits the limit.", "holds": True, "evidence": [{"id": "metric:main", "label": "container memory"}]}],
                                            "fix_steps": [], "verify": {"type": "manual"}, "dropped_signals": 0}), evidence="{}"))
    app.db.commit()
    main.redis_client.delete("infradeck:incident-note:42")

    report = owner.get("/incidents/42").json()["agent"]["report"]
    assert (report["version"], report["confidence"], report["signals"][0]["evidence"][0]["id"]) == (1, "high", "metric:main")
    assert "evidence" not in report                       # the pack stays on the server
    note = next(alert for alert in owner.get("/monitoring/overview").json()["alerts"] if alert["id"] == 42)["agent_note"]
    assert note == "gitlab is killed for memory."         # the Overview line is the report's first sentence


def test_asking_for_a_new_diagnosis_queues_a_job(app, monkeypatch):
    from src.agent import jobs
    owner = signed_in(app, 1)
    assert owner.post("/incidents/42/diagnose").status_code == 409          # no key configured

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    assert owner.post("/incidents/42/diagnose").json() == {"queued": True}
    assert owner.post("/incidents/42/diagnose").status_code == 200          # asking again is allowed
    queued = [json.loads(raw) for raw in app.redis.lrange(jobs.QUEUE, 0, -1)]
    assert [(job["type"], job["incident_id"]) for job in queued] == [("incident.requested", 42)] * 2
    assert signed_in(app, 3).post("/incidents/42/diagnose").status_code == 403
