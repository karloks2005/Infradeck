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


def confirmed(app, client, user_id=1):
    """Both steps of confirming a change (#184): the password, then a recovery code as the second factor (a TOTP code
    can't be used twice). Returns the fields the change itself sends."""
    codes = auth.issue_recovery_codes(app.db, app.db.get(User, user_id))
    app.db.commit()
    token = client.post("/auth/reauth", json={"password": PASSWORD}).json()["reauth_token"]
    return {"reauth_token": token, "code": codes[0]}


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
    monkeypatch.setattr(notify, "_post", lambda url, data, headers, address: (sent.append((url, data, headers)) or (True, "200")))
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


def test_a_new_incident_is_sent_however_many_came_before_it_that_day(app, outbox):
    viewer = signed_in(app, 3)
    viewer.post("/account/channels", json={"kind": "ntfy", "target": "https://ntfy.example/t"})
    now = datetime.now(timezone.utc)
    for index in range(250):   # a busy day, more than the 200 incidents a round used to look at (#212)
        app.db.add(IncidentAlert(id=1000 + index, server_id=1, severity="WARNING", title=f"Container c{index} exited", description="d", status="active",
                                 rule_key=f"container:c{index}:exited", created_at=now - timedelta(hours=20) + timedelta(minutes=index)))
    app.db.commit()
    assert main.dispatch_notifications(app.db) == 252   # all of them in one round, and the two from the fixture

    app.db.add(IncidentAlert(id=5000, server_id=1, severity="CRITICAL", title="Disk / on alpha is full", description="d", status="active", rule_key="host:disk:/",
                             created_at=now - timedelta(minutes=5)))
    app.db.commit()
    assert main.dispatch_notifications(app.db) == 1 and b"INC-5000" in outbox[-1][1]
    assert main.dispatch_notifications(app.db) == 0   # and still each incident once


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
    monkeypatch.setattr(main, "check_provider_connection", lambda db, user_id=None: {"provider": "Anthropic", "model": "claude-test"})

    saved = owner.put("/admin/agent/provider", json={"provider": "anthropic", "model": "claude-test", "api_key": "sk-ant-test-secret-value", **confirmed(app, owner)})
    system = owner.get("/admin/system")
    assert system.json()["agent"]["key_configured"] is True and system.json()["agent"]["provider"] == "anthropic"
    result = owner.post("/admin/agent/test")
    assert result.json() == {"ok": True, "provider": "Anthropic", "model": "claude-test"}
    assert "sk-ant-test-secret-value" not in saved.text + system.text + result.text
    assert signed_in(app, 3).post("/admin/agent/test").status_code == 403


def test_a_failed_agent_connection_test_explains_itself_without_the_key(app, monkeypatch):
    owner = signed_in(app, 1)

    def rejected(db, user_id=None):
        raise RuntimeError("Error code: 401 - Incorrect API key provided: sk-test-secret-value")
    monkeypatch.setattr(main, "check_provider_connection", rejected)

    response = owner.post("/admin/agent/test")
    assert response.status_code == 502 and "rejected the API key" in response.json()["detail"]
    assert "sk-test-secret-value" not in response.text


@pytest.mark.parametrize("body", [{"provider": "somewhere-else"}, {"provider": "openai", "model": "gpt test; drop"}, {"provider": "openai", "api_key": "has spaces in it"},
                                  {"provider": "openai", "api_key": "short"}, {"provider": "openai", "api_key": "x" * 513}, {}])
def test_the_agent_provider_refuses_odd_input(app, body):
    assert signed_in(app, 1).put("/admin/agent/provider", json=body).status_code == 422


def test_only_admins_choose_the_agent_provider_and_remove_its_key(app):
    viewer = signed_in(app, 3)
    assert viewer.put("/admin/agent/provider", json={"provider": "openai", "api_key": "sk-test-not-real"}).status_code == 403
    assert viewer.delete("/admin/agent/key").status_code == 403


def test_without_secrets_key_no_api_key_is_stored(app, monkeypatch):
    monkeypatch.delenv("SECRETS_KEY")
    response = signed_in(app, 1).put("/admin/agent/provider", json={"provider": "openai", "api_key": "sk-test-not-real"})

    assert response.status_code == 409 and "SECRETS_KEY" in response.json()["detail"]
    assert app.db.get(main.AppSetting, "agent_api_key_openai") is None


def test_saving_and_removing_the_agent_key_is_audited_without_the_key(app):
    owner = signed_in(app, 1)
    saved = owner.put("/admin/agent/provider", json={"provider": "gemini", "api_key": "AIza-test-secret-value", **confirmed(app, owner)}).json()
    assert saved["provider"] == "gemini" and saved["model"] == "gemini-3.1-flash-lite" and saved["key_configured"] is True

    assert owner.delete("/admin/agent/key").json()["key_configured"] is False
    logged = " ".join(entry.message for entry in app.db.query(main.AuditLog))
    assert "Google Gemini" in logged and "removed" in logged and "AIza-test-secret-value" not in logged


# --- the agent's spending (#112) ---------------------------------------------------------------------------------

from src.agent.chat import Answer
from src.agent.providers import Completion
from src.backend.models import AgentRun


def _fake_agent(monkeypatch, tokens=(1000, 200), fail=None):
    def fake(user_id, conversation_id, message, provider, usage_logger=None, **kwargs):
        if fail:
            raise fail
        # A dated model name, as the API reports it: it must still use the price set for "gpt-test".
        usage_logger(Completion(text="{}", model="gpt-test-2026-01-01", input_tokens=tokens[0], output_tokens=tokens[1]), 1500)
        return Answer("Here is the answer.")
    monkeypatch.setattr(main, "send_agent_message", fake)
    monkeypatch.setattr(main, "get_provider", lambda db: object())
    monkeypatch.setattr(main, "is_agent_chat_enabled", lambda db: True)


def _ask(client, conversation="c1-u1"):
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
    assert _ask(signed_in(app, 2), conversation="c2-u2").status_code == 200   # someone else still can


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

    assert owner.put("/admin/agent/provider", json={"provider": "openai", "api_key": "sk-test-not-real", **confirmed(app, owner)}).status_code == 200
    assert owner.post("/incidents/42/diagnose").json() == {"queued": True}
    assert owner.post("/incidents/42/diagnose").status_code == 200          # asking again is allowed
    queued = [json.loads(raw) for raw in app.redis.lrange(jobs.QUEUE, 0, -1)]
    assert [(job["type"], job["incident_id"]) for job in queued] == [("incident.requested", 42)] * 2
    assert signed_in(app, 3).post("/incidents/42/diagnose").status_code == 403


# --- the agent in notifications, who sees diagnoses, who may chat (#120, #121, #124) -----------------------------

def _report(app, incident_id=42, summary="gitlab is killed for memory after the update. Its limit is 4 GB. A third sentence."):
    from src.backend.models import AgentReport
    app.db.add(AgentReport(incident_id=incident_id, version=1, trigger="incident.opened", status="ok", model="gpt-test", confidence="high", summary=summary,
                           body=json.dumps({"signals": [], "fix_steps": [], "verify": {"type": "manual"}, "dropped_signals": 0}), evidence="{}"))
    app.db.commit()


def test_a_notification_waits_for_the_diagnosis_and_carries_it(app, outbox, monkeypatch):
    monkeypatch.setattr(main, "_diagnosis_coming", lambda db: True)
    signed_in(app, 1).post("/account/channels", json={"kind": "ntfy", "target": "https://ntfy.example/t"})
    for incident_id in (42, 43):
        app.db.get(IncidentAlert, incident_id).created_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    app.db.commit()

    assert main.dispatch_notifications(app.db) == 0          # both incidents are new: wait for the agent
    _report(app)
    assert main.dispatch_notifications(app.db) == 1          # INC-0042's diagnosis is in; INC-0043 still waits
    assert b"Agent: gitlab is killed for memory after the update. Its limit is 4 GB." in outbox[-1][1] and b"third" not in outbox[-1][1]


def test_after_three_minutes_it_goes_out_without_the_diagnosis(app, outbox, monkeypatch):
    monkeypatch.setattr(main, "_diagnosis_coming", lambda db: True)
    signed_in(app, 1).post("/account/channels", json={"kind": "ntfy", "target": "https://ntfy.example/t"})
    for incident_id in (42, 43):
        app.db.get(IncidentAlert, incident_id).created_at = NOW - timedelta(seconds=20)
    app.db.commit()

    assert main.dispatch_notifications(app.db, now=NOW + timedelta(seconds=30)) == 0
    assert main.dispatch_notifications(app.db, now=NOW + timedelta(minutes=4)) == 2
    assert all(b"Agent:" not in message[1] for message in outbox)


def test_without_a_diagnosis_on_the_way_nothing_waits(app, outbox):
    signed_in(app, 1).post("/account/channels", json={"kind": "ntfy", "target": "https://ntfy.example/t"})

    assert main.dispatch_notifications(app.db) == 2          # no key in tests, so no diagnosis is coming


def test_hidden_diagnoses_stay_hidden_from_users_in_the_page_the_overview_and_notifications(app, outbox):
    _report(app)
    owner, viewer = signed_in(app, 1), signed_in(app, 3)
    viewer.post("/account/channels", json={"kind": "ntfy", "target": "https://ntfy.example/v"})
    assert owner.put("/admin/settings", json={"agent_show_diagnoses_to_users": False}).json()["agent_show_diagnoses_to_users"] is False

    seen = viewer.get("/incidents/42").json()["agent"]
    assert seen["report"] is None and seen["hidden"] is True
    assert owner.get("/incidents/42").json()["agent"]["report"]["summary"].startswith("gitlab")   # admins always see it
    note = next(alert for alert in viewer.get("/monitoring/overview").json()["alerts"] if alert["id"] == 42)["agent_note"]
    assert note is None or not note.startswith("gitlab is killed")
    main.dispatch_notifications(app.db)
    assert outbox and all(b"Agent:" not in message[1] for message in outbox)


def test_nobody_can_take_another_persons_conversation_id(app, monkeypatch):
    monkeypatch.setattr(main, "send_agent_message", lambda *args, **kwargs: Answer("ok"))
    monkeypatch.setattr(main, "get_provider", lambda db: object())
    monkeypatch.setattr(main, "is_agent_chat_enabled", lambda db: True)
    owner, viewer = signed_in(app, 1), signed_in(app, 3)

    # The IDs the page builds for the owner (#201): the viewer can't create them first, nor one that only looks alike.
    for taken in ("general-u1", "incident-42-u1", "general-u11", "general"):
        assert viewer.post("/conversations", json={"conversation_id": taken}).status_code == 400, taken
    assert viewer.post("/conversations", json={"conversation_id": "general-u3"}).status_code == 200   # their own is fine

    assert owner.post("/conversations", json={"conversation_id": "general-u1"}).status_code == 200
    assert owner.post("/agent/chat", json={"conversation_id": "general-u1", "message": "hi"}).status_code == 200
    assert viewer.post("/agent/chat", json={"conversation_id": "general-u1", "message": "hi"}).status_code in (403, 404)
    assert viewer.delete("/conversations/general-u1").status_code == 404   # deleting still only touches your own
    assert [item["id"] for item in owner.get("/conversations").json()["conversations"]] == ["general-u1"]


def test_chat_is_for_admins_unless_allowed_per_user(app, monkeypatch):
    monkeypatch.setattr(main, "send_agent_message", lambda *args, **kwargs: Answer("ok"))
    monkeypatch.setattr(main, "get_provider", lambda db: object())
    monkeypatch.setattr(main, "is_agent_chat_enabled", lambda db: True)
    owner, viewer = signed_in(app, 1), signed_in(app, 3)
    viewer.post("/conversations", json={"conversation_id": "v1-u3"})
    ask = lambda: viewer.post("/agent/chat", json={"conversation_id": "v1-u3", "message": "hi"})  # noqa: E731

    assert ask().status_code == 403 and viewer.get("/agent/config").json()["may_chat"] is False          # admins only, the default
    assert owner.put("/admin/settings", json={"agent_chat_access": "per_user", **confirmed(app, owner)}).status_code == 200
    assert ask().status_code == 403                                                                       # per user, but not allowed yet
    users = owner.patch("/admin/users/3", json={"agent_chat_enabled": True, **confirmed(app, owner)}).json()
    assert next(user for user in users["users"] if user["id"] == 3)["agent_chat_enabled"] is True and users["agent_chat_access"] == "per_user"
    assert ask().status_code == 200 and viewer.get("/agent/config").json()["may_chat"] is True
    owner.put("/admin/settings", json={"agent_chat_access": "admins_only"})
    assert ask().status_code == 403                                                                       # admins only wins over the per-user switch
    assert owner.put("/admin/settings", json={"agent_chat_access": "everyone"}).status_code == 422


def test_live_refresh_is_a_display_choice_from_a_fixed_list(app):
    viewer = signed_in(app, 3)
    assert viewer.get("/account").json()["preferences"]["display"]["live_refresh"] is None     # the frontend's default

    assert viewer.put("/account/preferences", json={"display": {"live_refresh": 10}}).json()["display"]["live_refresh"] == 10
    for wrong in (7, 0, "10", True):
        assert viewer.put("/account/preferences", json={"display": {"live_refresh": wrong}}).json()["display"]["live_refresh"] == 10   # ignored


# --- confirming changes that send data to the AI provider (#184) ---------------------------------------------------

def test_turning_the_agent_on_needs_the_password_and_a_code(app):
    owner = signed_in(app, 1)
    owner.put("/admin/settings", json={"agent_auto_diagnose": False, "agent_chat_enabled": False})   # stopping needs nothing

    for body in ({"agent_auto_diagnose": True}, {"agent_chat_enabled": True}, {"agent_chat_access": "per_user"}):
        assert owner.put("/admin/settings", json=body).status_code == 403
        assert owner.put("/admin/settings", json={**body, **confirmed(app, owner)}).status_code == 200
    settings = owner.get("/admin/settings").json()
    assert settings["agent_auto_diagnose"] and settings["agent_chat_enabled"] and settings["agent_chat_access"] == "per_user"


def test_letting_one_user_chat_needs_the_password_and_a_code_but_stopping_does_not(app):
    owner = signed_in(app, 1)
    owner.put("/admin/settings", json={"agent_chat_access": "per_user", **confirmed(app, owner)})
    allowed = lambda: app.db.get(User, 3).agent_chat_enabled  # noqa: E731

    assert owner.patch("/admin/users/3", json={"agent_chat_enabled": True}).status_code == 403   # a stolen session alone can't (#202)
    app.db.expire_all()
    assert not allowed()
    assert owner.patch("/admin/users/3", json={"agent_chat_enabled": True, **confirmed(app, owner)}).status_code == 200
    app.db.expire_all()
    assert allowed()
    assert owner.patch("/admin/users/3", json={"agent_chat_enabled": False}).status_code == 200  # stopping needs nothing
    app.db.expire_all()
    assert not allowed()
    assert owner.patch("/admin/users/3", json={"role": "admin"}).status_code == 200            # other changes as before


def test_the_look_up_budgets_are_checked_audited_and_raising_one_from_0_is_confirmed(app):
    owner = signed_in(app, 1)

    assert owner.put("/admin/settings", json={"agent_tool_budget_diagnosis": 21}).status_code == 400
    assert owner.put("/admin/settings", json={"agent_tool_budget_chat": 0}).status_code == 200        # lowering needs nothing
    assert owner.put("/admin/settings", json={"agent_tool_budget_chat": 3}).status_code == 403        # up from 0: confirm
    assert owner.put("/admin/settings", json={"agent_tool_budget_chat": 3, **confirmed(app, owner)}).status_code == 200
    assert owner.put("/admin/settings", json={"agent_tool_budget_chat": 5}).status_code == 200        # already above 0

    settings = owner.get("/admin/settings").json()
    assert (settings["agent_tool_budget_diagnosis"], settings["agent_tool_budget_chat"]) == (8, 5)
    logged = [entry.message for entry in app.db.query(AuditLog)]
    assert any("tool budget to 0 look-ups per chat message" in message for message in logged) and any("to 5 look-ups" in message for message in logged)
    assert signed_in(app, 3).put("/admin/settings", json={"agent_tool_budget_chat": 1}).status_code == 403


def test_a_chat_message_looks_things_up_and_the_look_ups_outlive_the_conversation(app, monkeypatch):
    from tests.fakes import ScriptedProvider
    provider = ScriptedProvider([("server_info", {"server": "alpha", "reason": "Is alpha up?"})], '{"message":"alpha is online."}')
    monkeypatch.setattr(main, "get_provider", lambda db: provider)
    monkeypatch.setattr(main, "is_agent_chat_enabled", lambda db: True)
    owner = signed_in(app, 1)

    response = _ask(owner)

    assert response.status_code == 200 and response.json()["message"] == "alpha is online."
    step = response.json()["steps"][0]
    assert (step["tool"], step["reason"], step["asked"], step["status"]) == ("server_info", "Is alpha up?", "Machine facts for alpha", "ok") and "result" not in step
    saved = owner.get("/conversations").json()["conversations"][0]["messages"]
    assert [message["steps"] for message in saved] == [[], [step]]   # kept with the reply, so a reload shows it
    assert provider.started[0]["tools"] and json.loads(provider.results[0][0].content)["hostname"] == "alpha"
    assert app.db.query(AgentRun).count() == 2
    looked = app.db.query(AuditLog).filter(AuditLog.event_type == "agent_tool_call").one()
    assert (looked.user_id, looked.conversation_id, looked.task_title) == (1, "c1-u1", "Why did it restart?")
    assert owner.get("/dashboard/summary").json()["agent_lookups"] == 1

    assert owner.delete("/conversations/c1-u1").status_code == 200
    kept = app.db.query(AuditLog).filter(AuditLog.event_type == "agent_tool_call").one()
    assert kept.conversation_id is None


def test_choosing_the_provider_needs_confirmation_but_removing_the_key_does_not(app):
    owner = signed_in(app, 1)
    assert owner.put("/admin/agent/provider", json={"provider": "openai", "api_key": "sk-test-not-real"}).status_code == 403
    assert app.db.get(main.AppSetting, "agent_api_key_openai") is None

    owner.put("/admin/agent/provider", json={"provider": "openai", "api_key": "sk-test-not-real", **confirmed(app, owner)})
    assert owner.delete("/admin/agent/key").json()["key_configured"] is False


def test_a_wrong_password_gives_no_token(app):
    owner = signed_in(app, 1)
    response = owner.post("/auth/reauth", json={"password": "wrong"})

    assert response.status_code == 401 and "reauth_token" not in response.json()
    assert any("wrong password" in entry.message for entry in app.db.query(AuditLog))


def test_without_two_factor_the_agent_cant_be_turned_on(app):
    app.db.get(User, 1).totp_enabled = False
    app.db.commit()

    response = signed_in(app, 1).post("/auth/reauth", json={"password": PASSWORD})
    assert response.status_code == 403 and "two-factor" in response.json()["detail"]


def test_a_wrong_code_says_how_many_tries_are_left_and_the_fifth_ends_it(app):
    owner = signed_in(app, 1)
    fields = confirmed(app, owner)
    body = {"provider": "openai", "api_key": "sk-test-not-real", "reauth_token": fields["reauth_token"]}

    details = [owner.put("/admin/agent/provider", json={**body, "code": "000000"}).json()["detail"] for _ in range(5)]
    assert [detail.split(". ")[-1] for detail in details[:4]] == ["4 tries left.", "3 tries left.", "2 tries left.", "1 try left."]
    assert details[4].startswith("Too many wrong codes")

    late = owner.put("/admin/agent/provider", json={**body, "code": fields["code"]})   # the right code, too late
    assert late.status_code == 401 and app.db.get(main.AppSetting, "agent_api_key_openai") is None


def test_a_token_works_once_and_only_in_its_own_session(app):
    owner = signed_in(app, 1)
    fields = confirmed(app, owner)
    other_session = signed_in(app, 1)
    assert other_session.put("/admin/settings", json={"agent_chat_access": "per_user", **fields}).status_code == 401

    assert owner.put("/admin/settings", json={"agent_chat_access": "per_user", **fields}).status_code == 200
    owner.put("/admin/settings", json={"agent_chat_access": "admins_only"})
    assert owner.put("/admin/settings", json={"agent_chat_access": "per_user", **fields}).status_code == 401
