"""Availability checks: what a check may be, how results from the collectors are taken in and combined, when an
incident opens and resolves, and the uptime and heartbeat the page shows."""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.backend import availability, incidents, main
from src.backend.models import AppSetting, Base, Check, CheckResult, CheckRollup, IncidentAlert, Server, User
from tests.fakes import FakeRedis

TOKENS = {1: "token-one", 2: "token-two"}
NEW_CHECK = {"type": "http", "name": "Gitea", "target": "https://git.example.com/", "server_ids": [1, 2], "interval_seconds": 60, "retries": 2, "retry_interval_seconds": 20, "timeout_seconds": 10}


@pytest.fixture
def app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as seed:
        seed.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin", is_active=True))
        seed.add(User(id=2, name="Member", email="member@example.com", password_hash="x", role="member", is_active=True))
        for server_id, name in ((1, "alpha"), (2, "bravo")):
            seed.add(Server(id=server_id, owner_id=1, hostname=name, check_interval_seconds=60, collector_token_hash=main._hash_secret(TOKENS[server_id]),
                            machine_id=f"machine-{server_id}", system_info=json.dumps({"collector_version": "0.10.0" if server_id == 1 else "0.9.2"})))
        seed.commit()

    def get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[main.get_db] = get_db
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    monkeypatch.setattr(main, "_enforce_report_spacing", lambda server: None)
    user = {"id": 1, "role": "admin"}
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: user)
    client = TestClient(main.app, raise_server_exceptions=False)
    client.Session = Session
    client.user = user
    yield client
    main.app.dependency_overrides.clear()


def create(client, **changes):
    response = client.post("/availability/checks", json={**NEW_CHECK, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def age(client, check_id, minutes=30):
    """Make a check older than the quiet period for new checks."""
    with client.Session() as db:
        db.get(Check, check_id).created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        db.commit()


def report(client, server_id, results, token=None):
    return client.post("/monitoring/metrics/checks", json={"server_id": server_id, "results": results}, headers={"Authorization": f"Bearer {token or TOKENS[server_id]}"})


def run(check_id, ok, seconds_ago=0, **extra):
    return {"check_id": check_id, "time": (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(), "ok": ok, **extra}


def runs(check_id, oks, step=20, ago=0):
    """Several runs, oldest first, ``step`` seconds apart and ending ``ago`` seconds ago."""
    return [run(check_id, ok, ago + (len(oks) - 1 - index) * step) for index, ok in enumerate(oks)]


def incident(client, check_id):
    with client.Session() as db:
        return db.query(IncidentAlert).filter(IncidentAlert.rule_key == f"check:{check_id}:down").order_by(IncidentAlert.id.desc()).first()


def check_row(client, check_id):
    with client.Session() as db:
        return db.get(Check, check_id)


# --- what a check may be ----------------------------------------------------------------------------

@pytest.mark.parametrize("changes", [
    {"type": "ping"}, {"name": ""}, {"name": "x" * 121}, {"target": "ftp://example.com/"}, {"target": "file:///etc/passwd"}, {"target": "https://"},
    {"target": "https://exa mple.com/"}, {"server_ids": []}, {"server_ids": [99]}, {"server_ids": [True]}, {"interval_seconds": 5}, {"timeout_seconds": 60},
    {"retries": 11}, {"retries": "2"}, {"accepted_codes": ["2xx"]}, {"accepted_codes": ["200-299"] * 11},
    {"rule": "most"}, {"severity": "INFO"}, {"tags": ["a,b"]}, {"type": "keyword"}, {"type": "keyword", "keyword": "x" * 201},
    {"type": "tcp", "target": "db.example.com"}, {"type": "tcp", "target": "db.example.com:70000"}, {"type": "tcp", "target": "db;rm -rf /:22"},
])
def test_a_check_that_isnt_well_formed_is_refused_with_a_reason(app, changes):
    response = app.post("/availability/checks", json={**NEW_CHECK, **changes})

    assert response.status_code == 400
    assert response.json()["detail"]


def test_a_short_interval_brings_the_timeout_down_with_it(app):
    check = create(app, interval_seconds=30, timeout_seconds=48)

    assert (check["interval_seconds"], check["timeout_seconds"]) == (30, 24)


def test_only_admins_add_change_pause_or_delete_checks(app):
    check = create(app)
    app.user.update(id=2, role="member")

    assert app.post("/availability/checks", json=NEW_CHECK).status_code == 403
    assert app.patch(f"/availability/checks/{check['id']}", json=NEW_CHECK).status_code == 403
    assert app.post(f"/availability/checks/{check['id']}/pause", json={"paused": True}).status_code == 403
    assert app.delete(f"/availability/checks/{check['id']}").status_code == 403
    page = app.get("/availability").json()
    assert page["can_edit"] is False and [item["name"] for item in page["checks"]] == ["Gitea"]


def test_a_collector_gets_only_the_checks_of_its_own_server(app):
    create(app, name="Both")
    create(app, name="Only bravo", server_ids=[2], type="tcp", target="10.0.0.12:3306")
    paused = create(app, name="Paused", server_ids=[1])
    app.post(f"/availability/checks/{paused['id']}/pause", json={"paused": True})

    alpha = report(app, 1, []).json()
    bravo = report(app, 2, []).json()

    assert [check["target"] for check in alpha["checks"]] == ["https://git.example.com/"]
    assert [check["target"] for check in bravo["checks"]] == ["https://git.example.com/", "10.0.0.12:3306"]
    assert set(alpha["checks"][0]) == {"id", "type", "target", "interval", "retry_interval", "timeout", "accepted_codes", "follow_redirects", "ignore_tls", "method"}


def test_check_results_need_the_servers_own_collector_token(app):
    assert report(app, 1, [], token="token-two").status_code == 401
    assert report(app, 1, [], token="nope").status_code == 401
    assert app.post("/monitoring/metrics/checks", json={"server_id": 1, "results": []}).status_code == 401
    assert report(app, 1, [{}] * 501).status_code == 422


def test_results_a_collector_has_no_business_reporting_are_dropped(app):
    mine = create(app, server_ids=[1])
    theirs = create(app, name="Other", server_ids=[2])

    response = report(app, 1, [
        run(theirs["id"], False),                      # not this server's check
        run(mine["id"], "yes"),                        # ok isn't a boolean
        run(mine["id"], True, seconds_ago=2 * 3600),   # too old
        run(mine["id"], True, seconds_ago=-600),       # from the future
        {"check_id": mine["id"], "time": "yesterday", "ok": True},
        "junk",
        run(mine["id"], True, response_ms=10**9, code=99, detail="a\x00b" + "x" * 500, cert_days="soon"),
    ])

    assert response.status_code == 200
    with app.Session() as db:
        stored = db.query(CheckResult).all()
    assert len(stored) == 1
    assert (stored[0].check_id, stored[0].response_ms, stored[0].code, stored[0].cert_days, len(stored[0].detail)) == (mine["id"], None, None, None, 200)
    assert "\x00" not in stored[0].detail


# --- up and down --------------------------------------------------------------------------------------

def online(client, *server_ids):
    with client.Session() as db:
        for server_id in server_ids:
            server = db.get(Server, server_id)
            server.system_info, server.last_seen_at = json.dumps({"collector_version": "0.10.0"}), datetime.now(timezone.utc)
        db.commit()


def test_with_rule_all_one_server_failing_is_not_down_but_both_is(app):
    check = create(app)
    age(app, check["id"])
    online(app, 1, 2)

    report(app, 1, runs(check["id"], [False, False, False]))
    report(app, 2, runs(check["id"], [True, True, True], ago=60))
    assert check_row(app, check["id"]).state == "up"
    assert incident(app, check["id"]) is None

    report(app, 2, runs(check["id"], [False, False, False]))
    assert check_row(app, check["id"]).state == "down"
    alert = incident(app, check["id"])
    assert alert.status == "active" and alert.severity == "CRITICAL" and alert.title == "Gitea is down"
    assert "alpha" in alert.description and "bravo" in alert.description


def test_with_rule_all_a_server_yet_to_report_holds_off_down_but_an_offline_one_does_not(app):
    check = create(app)
    age(app, check["id"])
    online(app, 1, 2)

    report(app, 1, runs(check["id"], [False, False, False]))
    assert check_row(app, check["id"]).state != "down"

    with app.Session() as db:
        db.get(Server, 2).last_seen_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    report(app, 1, [run(check["id"], False)])
    assert check_row(app, check["id"]).state == "down"


def test_retries_must_all_fail_before_a_check_counts_as_down(app):
    check = create(app, server_ids=[1], retries=2)
    age(app, check["id"])

    report(app, 1, runs(check["id"], [True, False, False], ago=20))
    assert check_row(app, check["id"]).state == "pending"   # failing, but not yet as often as the retries allow

    report(app, 1, [run(check["id"], False, detail="HTTP 503")])
    assert check_row(app, check["id"]).state == "down"
    assert incident(app, check["id"]).description.startswith("HTTP 503 from alpha")


def test_with_rule_any_one_server_failing_is_enough(app):
    check = create(app, rule="any", severity="WARNING")
    age(app, check["id"])

    report(app, 1, runs(check["id"], [False, False, False]))
    report(app, 2, runs(check["id"], [True, True, True]))

    assert check_row(app, check["id"]).state == "down"
    assert incident(app, check["id"]).severity == "WARNING"


def test_coming_back_up_resolves_the_incident(app):
    check = create(app, server_ids=[1])
    age(app, check["id"])
    report(app, 1, runs(check["id"], [False, False, False]))

    report(app, 1, [run(check["id"], True)])

    assert check_row(app, check["id"]).state == "up"
    assert incident(app, check["id"]).status == "resolved"


def test_a_new_check_stays_quiet_for_ten_minutes(app):
    check = create(app, server_ids=[1])

    report(app, 1, runs(check["id"], [False, False, False]))

    assert check_row(app, check["id"]).state == "down"
    assert incident(app, check["id"]) is None
    assert app.get("/availability").json()["checks"][0]["quiet_until"]


def test_switching_the_rule_off_keeps_the_state_but_opens_no_incident(app):
    check = create(app, server_ids=[1])
    age(app, check["id"])
    with app.Session() as db:
        db.add(AppSetting(key=main.DISABLED_RULES_KEY, value=json.dumps(["check:down"])))
        db.commit()

    report(app, 1, runs(check["id"], [False, False, False]))

    assert check_row(app, check["id"]).state == "down"
    assert incident(app, check["id"]) is None


def test_an_upside_down_check_is_up_while_the_service_is_unreachable(app):
    check = create(app, server_ids=[1], type="tcp", target="10.0.0.5:23", upside_down=True)
    age(app, check["id"])

    report(app, 1, runs(check["id"], [False, False, False], ago=60))
    assert check_row(app, check["id"]).state == "up"

    report(app, 1, runs(check["id"], [True, True, True]))
    assert check_row(app, check["id"]).state == "down"


def test_pausing_resolves_the_incident_and_resuming_starts_over(app):
    check = create(app, server_ids=[1])
    age(app, check["id"])
    report(app, 1, runs(check["id"], [False, False, False]))

    paused = app.post(f"/availability/checks/{check['id']}/pause", json={"paused": True}).json()
    assert paused["state"] == "paused" and incident(app, check["id"]).status == "resolved"
    assert report(app, 1, []).json()["checks"] == []

    resumed = app.post(f"/availability/checks/{check['id']}/pause", json={"paused": False}).json()
    assert resumed["paused"] is False


def test_deleting_a_check_resolves_its_incident_and_removes_its_history(app):
    check = create(app, server_ids=[1])
    age(app, check["id"])
    report(app, 1, runs(check["id"], [False, False, False]))

    assert app.delete(f"/availability/checks/{check['id']}").status_code == 200

    assert incident(app, check["id"]).status == "resolved"
    with app.Session() as db:
        assert db.query(CheckResult).count() == 0 and db.query(Check).count() == 0


def test_removing_a_server_takes_it_out_of_its_checks(app):
    both = create(app)
    only = create(app, name="Only alpha", server_ids=[1])

    assert app.delete("/monitoring/servers/1").status_code == 200

    assert availability.servers_of(check_row(app, both["id"])) == [2]
    assert check_row(app, only["id"]).paused is True


def test_an_incident_from_a_check_has_its_own_rule_kind():
    assert incidents.rule_kind("check:12:down") == "check:down"
    assert any(row["kind"] == "check:down" for row in main.RULE_ROWS)


# --- what the page shows ---------------------------------------------------------------------------

def test_the_page_shows_the_heartbeat_uptime_and_counts(app):
    check = create(app, server_ids=[1], interval_seconds=60, retries=0)
    age(app, check["id"], minutes=120)
    results = [run(check["id"], index not in (3, 4), seconds_ago=(9 - index) * 60, response_ms=40 + index) for index in range(10)]
    report(app, 1, results)

    page = app.get("/availability").json()
    row = page["checks"][0]

    assert [slot["status"] for slot in row["strip"]][-10:] == ["up", "up", "up", "down", "down", "up", "up", "up", "up", "up"]
    assert row["uptime_24h"] == 80.0
    assert row["response_ms"] == 49
    assert page["counts"] == {"up": 1, "down": 0, "paused": 0, "pending": 0, "total": 1}
    assert {server["hostname"]: server["can_run_checks"] for server in page["servers"]} == {"alpha": True, "bravo": False}


def test_a_failure_within_the_retries_shows_as_retrying(app):
    check = create(app, server_ids=[1], interval_seconds=60, retries=2)
    report(app, 1, [run(check["id"], True, 120), run(check["id"], False, 60), run(check["id"], True, 0)])

    strip = app.get("/availability").json()["checks"][0]["strip"]

    assert [slot["status"] for slot in strip][-3:] == ["up", "retrying", "up"]


def test_a_certificate_close_to_expiring_is_listed(app):
    check = create(app, server_ids=[1])
    report(app, 1, [run(check["id"], True, cert_days=9)])

    page = app.get("/availability").json()

    assert page["expiring"] == [{"name": "Gitea", "days": 9}]


def test_completed_hours_are_rolled_up_and_old_raw_results_dropped(app):
    check = create(app, server_ids=[1], interval_seconds=60, retries=0)
    now = datetime.now(timezone.utc).replace(minute=30, second=0, microsecond=0)
    two_hours_ago = now.replace(minute=0) - timedelta(hours=2)
    with app.Session() as db:
        for minute in range(60):
            db.add(CheckResult(check_id=check["id"], server_id=1, time=two_hours_ago + timedelta(minutes=minute), ok=minute >= 6, response_ms=50))
        db.commit()

        assert availability.roll(db, now) == 1
        rollup = db.query(CheckRollup).one()
        assert (rollup.slots, rollup.down_slots, rollup.avg_ms) == (60, 6, 50.0)
        assert availability.roll(db, now) == 0      # nothing new to summarise

        db.add(CheckResult(check_id=check["id"], server_id=1, time=now - timedelta(days=8), ok=True))
        db.commit()
        assert availability.purge(db, now) == 1
        assert availability._uptime(db, db.get(Check, check["id"]), now - timedelta(days=30), now) == 90.0


def test_the_detail_has_the_response_per_server_ninety_days_and_the_times_it_went_down(app):
    check = create(app, interval_seconds=60, retries=0)
    age(app, check["id"], minutes=120)
    report(app, 1, [run(check["id"], True, 120, response_ms=40), run(check["id"], False, 60, detail="HTTP 503"), run(check["id"], True, 0, response_ms=60)])
    report(app, 2, [run(check["id"], True, 60, response_ms=90)])

    detail = app.get(f"/availability/checks/{check['id']}?range=1h").json()

    assert {series["hostname"]: [point["ms"] for point in series["points"]] for series in detail["series"]} == {"alpha": [40, 60], "bravo": [90]}
    assert len(detail["days"]) == 90 and detail["days"][-1]["uptime"] == 100.0 and detail["days"][0]["uptime"] is None
    assert detail["average_24h"] == 63
    assert app.get("/availability/checks/999").status_code == 404


def test_the_detail_lists_when_a_check_went_down_and_came_back(app):
    check = create(app, server_ids=[1])
    age(app, check["id"])
    report(app, 1, runs(check["id"], [False, False, False], ago=60))
    report(app, 1, [run(check["id"], True)])

    detail = app.get(f"/availability/checks/{check['id']}").json()

    assert [event["kind"] for event in detail["events"]] == ["up", "down"]
    assert detail["events"][1]["text"].startswith("Down · No answer from alpha")
    assert detail["last_down"]["ongoing"] is False



# --- testing a check before saving it -----------------------------------------------------------------

def test_a_test_goes_to_the_collectors_and_their_results_come_back(app):
    with app.Session() as db:
        db.get(Server, 2).system_info = json.dumps({"collector_version": "0.10.0"})
        db.commit()
    started = app.post("/availability/test", json={**NEW_CHECK, "upside_down": False}).json()
    assert started["done"] is False and [server["state"] for server in started["servers"]] == ["waiting", "waiting"]

    handed = report(app, 1, []).json()["tests"]
    assert [test["id"] for test in handed] == [started["id"]] and handed[0]["target"] == "https://git.example.com/"
    assert report(app, 1, []).json()["tests"] == []            # handed over once
    assert report(app, 1, []).json()["idle_every"] == 10

    app.post("/monitoring/metrics/checks", json={"server_id": 1, "results": [], "tests": [{"check_id": started["id"], "ok": True, "response_ms": 142, "code": 200}]},
             headers={"Authorization": f"Bearer {TOKENS[1]}"})
    app.post("/monitoring/metrics/checks", json={"server_id": 2, "results": [], "tests": [{"check_id": started["id"], "ok": False, "detail": "Connection refused"}]},
             headers={"Authorization": f"Bearer {TOKENS[2]}"})
    status = app.get(f"/availability/test/{started['id']}").json()

    assert status["done"] is True
    assert [(server["hostname"], server["ok"], server.get("code"), server.get("detail")) for server in status["servers"]] == [("alpha", True, 200, None), ("bravo", False, None, "Connection refused")]
    with app.Session() as db:
        assert db.query(Check).count() == 0 and db.query(CheckResult).count() == 0   # a test saves nothing


def test_a_collector_cant_answer_a_test_it_wasnt_given(app):
    started = app.post("/availability/test", json={**NEW_CHECK, "server_ids": [1]}).json()

    app.post("/monitoring/metrics/checks", json={"server_id": 2, "results": [], "tests": [{"check_id": started["id"], "ok": True}]}, headers={"Authorization": f"Bearer {TOKENS[2]}"})

    assert app.get(f"/availability/test/{started['id']}").json()["servers"][0]["state"] == "waiting"


def test_only_servers_that_can_run_checks_are_asked(app):
    assert app.post("/availability/test", json={**NEW_CHECK, "server_ids": [2]}).status_code == 400   # bravo's collector is 0.9.2
    started = app.post("/availability/test", json={**NEW_CHECK, "server_ids": [1, 2]}).json()
    assert [server["hostname"] for server in started["servers"]] == ["alpha"]
    assert report(app, 2, []).json()["tests"] == []


def test_a_test_is_checked_like_a_check_and_only_admins_run_it(app):
    assert app.post("/availability/test", json={**NEW_CHECK, "target": "file:///etc/passwd"}).status_code == 400
    app.user.update(id=2, role="member")
    assert app.post("/availability/test", json=NEW_CHECK).status_code == 403
    assert app.get("/availability/test/1").status_code == 403


def test_an_upside_down_test_passes_when_the_service_is_unreachable(app):
    started = app.post("/availability/test", json={**NEW_CHECK, "server_ids": [1], "upside_down": True}).json()
    app.post("/monitoring/metrics/checks", json={"server_id": 1, "results": [], "tests": [{"check_id": started["id"], "ok": False, "detail": "Connection refused"}]},
             headers={"Authorization": f"Bearer {TOKENS[1]}"})

    assert app.get(f"/availability/test/{started['id']}").json()["servers"][0]["ok"] is True


# --- the other check types: TLS, DNS, JSON, ping -----------------------------------------------------

@pytest.mark.parametrize("raw, stored", [
    ({"type": "tls", "target": "mail.example.com:993", "warn_days": 30}, {"warn_days": 30}),
    ({"type": "tls", "target": "example.com"}, {"warn_days": 14}),                       # port defaults to 443
    ({"type": "dns", "target": "deck.home.lan", "record_type": "A", "resolver": "10.0.0.12", "expected": "10.0.0.30"}, {"record_type": "A", "resolver": "10.0.0.12", "expected": "10.0.0.30"}),
    ({"type": "json", "target": "https://s.home.lan/api", "json_path": "data.status", "json_op": "equals", "json_expect": "ok"}, {"json_path": "data.status", "json_op": "equals", "json_expect": "ok"}),
    ({"type": "json", "target": "https://s.home.lan/api", "json_path": "items[0].ok", "json_op": "exists"}, {"json_op": "exists"}),
    ({"type": "ping", "target": "10.0.0.1"}, {}),
    ({"type": "ping", "target": "router.home.lan"}, {}),
])
def test_the_new_types_validate_and_store_their_options(app, raw, stored):
    created = create(app, **{key: value for key, value in raw.items() if key != "type"}, type=raw["type"])

    assert created["type"] == raw["type"]
    for key, value in stored.items():
        assert created["options"][key] == value


@pytest.mark.parametrize("changes", [
    {"type": "tls", "target": "no-port-ok-but-bad host"}, {"type": "tls", "target": "example.com", "warn_days": 0}, {"type": "tls", "target": "example.com", "warn_days": 9999},
    {"type": "dns", "target": "deck.home.lan", "record_type": "SRV"}, {"type": "dns", "target": "deck.home.lan", "record_type": "A", "resolver": "not-an-ip"},
    {"type": "dns", "target": "bad host name", "record_type": "A"},
    {"type": "json", "target": "https://s/api", "json_path": "", "json_op": "equals", "json_expect": "x"},
    {"type": "json", "target": "https://s/api", "json_path": "a", "json_op": "nope", "json_expect": "x"},
    {"type": "json", "target": "https://s/api", "json_path": "a", "json_op": "equals", "json_expect": ""},
    {"type": "ping", "target": "10.0.0.1:22"}, {"type": "ping", "target": "has a space"},
])
def test_a_malformed_check_of_the_new_types_is_refused(app, changes):
    assert app.post("/availability/checks", json={**NEW_CHECK, **changes}).status_code == 400


def test_a_tls_check_sends_its_warn_days_to_the_collector(app):
    create(app, server_ids=[1], type="tls", target="mail.example.com:993", warn_days=21)

    spec = next(c for c in report(app, 1, []).json()["checks"] if c["type"] == "tls")

    assert spec["target"] == "mail.example.com:993" and spec["warn_days"] == 21 and "upside_down" not in spec


# --- a check's address may hold a secret; non-admins and incidents don't see it --------------------

def test_credentials_in_a_check_url_are_refused(app):
    assert app.post("/availability/checks", json={**NEW_CHECK, "target": "https://user:pass@git.example.com/"}).status_code == 400


def test_a_non_admin_sees_the_target_without_its_query_or_credentials(app):
    create(app, name="Stock", target="https://stock.home.lan/api/?token=secret")

    full = app.get("/availability").json()["checks"]
    app.user.update(id=2, role="member")
    redacted = app.get("/availability").json()["checks"]

    assert next(c for c in full if c["name"] == "Stock")["target"] == "https://stock.home.lan/api/?token=secret"
    shown = next(c for c in redacted if c["name"] == "Stock")["target"]
    assert shown == "https://stock.home.lan/api/?…" and "secret" not in shown


def test_the_incident_description_does_not_leak_the_query_string(app):
    check = create(app, server_ids=[1], target="https://stock.home.lan/api/?token=secret")
    age(app, check["id"])

    report(app, 1, runs(check["id"], [False, False, False]))

    alert = incident(app, check["id"])
    assert alert is not None and "secret" not in alert.description and "stock.home.lan/api/" in alert.description
