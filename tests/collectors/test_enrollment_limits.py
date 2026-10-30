"""The enrollment key is powerful (it can re-issue a server's credentials), so its use is rate limited, the number
of servers is capped, and replacing credentials is never silent."""
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

from src.backend import main
from src.backend.models import AuditLog, Base, EnrollmentKey, IncidentAlert, PendingCollector, Server, SystemEvent, User
from tests.fakes import FakeRedis

KEY = "enroll-key-secret"


@pytest.fixture
def app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as seed:
        seed.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin", is_active=True))
        seed.add(EnrollmentKey(owner_id=1, key_hash=main._hash_secret(KEY)))
        seed.add(Server(id=2, owner_id=1, hostname="existing", check_interval_seconds=60, collector_token_hash=main._hash_secret("old-token"), machine_id="machine-existing"))
        seed.commit()

    def get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[main.get_db] = get_db
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    client = TestClient(main.app, raise_server_exceptions=False)
    client.Session = Session
    yield client
    main.app.dependency_overrides.clear()


def enroll(client, machine_id="machine-new", hostname="new-box", key=KEY):
    return client.post("/monitoring/collectors/enroll", json={"enroll_key": key, "hostname": hostname, "machine_id": machine_id})


def ingest(client, server_id, token):
    return client.post("/monitoring/metrics/ingest", content=json.dumps({"server_id": server_id, "host": {"cpu_utilization_pct": 3}}), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})


# --- replacing credentials is never silent --------------------------------------------------------------

def test_enrolling_with_a_known_machine_id_still_works_but_signs_the_old_collector_out(app):
    response = enroll(app, "machine-existing", "whatever")

    body = response.json()
    assert response.status_code == 200 and body["server_id"] == 2 and body["collector_token"] != "old-token"
    assert ingest(app, 2, "old-token").status_code == 401
    assert ingest(app, 2, body["collector_token"]).status_code == 200


def test_replacing_credentials_leaves_an_event_and_an_audit_entry(app):
    enroll(app, "machine-existing")

    with app.Session() as db:
        [event] = db.query(SystemEvent).filter_by(kind="credentials_replaced").all()
        assert event.server_id == 2 and "replaced" in event.detail and "enrollment key" in event.detail
        audit = db.query(AuditLog).filter_by(source="enrollment").one()
        assert audit.level == "WARNING" and "server 2" in audit.message


def test_the_replacement_opens_a_warning_that_clears_by_itself(app):
    token = enroll(app, "machine-existing").json()["collector_token"]
    ingest(app, 2, token)

    with app.Session() as db:
        alert = db.query(IncidentAlert).filter_by(server_id=2, rule_key="system:credentials", status="active").one()
        assert alert.severity == "WARNING" and "enrollment key" in alert.description
        db.query(SystemEvent).update({SystemEvent.time: datetime.now(timezone.utc) - timedelta(hours=3)})
        db.commit()
    ingest(app, 2, token)
    with app.Session() as db:
        assert db.query(IncidentAlert).filter_by(rule_key="system:credentials", status="active").count() == 0


def test_a_brand_new_machine_is_not_a_replacement(app):
    body = enroll(app).json()

    with app.Session() as db:
        assert db.query(SystemEvent).filter_by(kind="credentials_replaced").count() == 0
        assert db.query(AuditLog).filter_by(source="enrollment").count() == 0
        assert db.get(Server, body["server_id"]).hostname == "new-box"


def test_the_hostname_is_made_printable_and_bounded(app):
    body = enroll(app, hostname="x" * 250 + "\x00\x07").json()

    with app.Session() as db:
        assert db.get(Server, body["server_id"]).hostname == "x" * 250 + "??"        # control characters become "?"
    assert enroll(app, "machine-long", hostname="h" * 256).status_code == 422       # longer than a hostname can be: refused outright


# --- rate limits ----------------------------------------------------------------------------------------

def test_one_address_can_only_enroll_so_often(app):
    codes = [enroll(app, f"machine-{i}", f"box{i}").status_code for i in range(main.ENROLL_RATE_LIMIT + 3)]

    assert codes[:main.ENROLL_RATE_LIMIT] == [200] * main.ENROLL_RATE_LIMIT and codes[main.ENROLL_RATE_LIMIT:] == [429] * 3


def test_wrong_keys_count_against_the_limit_too(app):
    for i in range(main.ENROLL_RATE_LIMIT):
        assert enroll(app, f"m{i}", key="not-the-key").status_code == 401

    assert enroll(app, "later").status_code == 429


def test_another_address_has_its_own_allowance(app):
    for i in range(main.ENROLL_RATE_LIMIT + 1):
        enroll(app, f"machine-{i}")
    other = TestClient(main.app, client=("203.0.113.9", 50000), raise_server_exceptions=False)

    assert enroll(other, "from-elsewhere").status_code == 200


def test_the_key_itself_has_a_limit_across_addresses(app):
    limit = main.ENROLL_RATE_LIMIT * 2
    for i in range(limit):
        client = TestClient(main.app, client=(f"198.51.100.{i % 250 + 1}", 40000 + i), raise_server_exceptions=False)
        assert enroll(client, f"machine-{i}", f"box{i}").status_code == 200

    last = TestClient(main.app, client=("192.0.2.77", 1), raise_server_exceptions=False)
    assert enroll(last, "one-too-many").status_code == 429


def test_a_redis_outage_does_not_block_enrolling(app, monkeypatch):
    class Down:
        def __getattr__(self, name):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(main, "redis_client", Down())

    assert enroll(app).status_code == 200


# --- how many servers ------------------------------------------------------------------------------------

def test_a_full_install_refuses_new_servers_but_still_serves_the_ones_it_has(app, monkeypatch):
    monkeypatch.setattr(main, "MAX_SERVERS", 2)
    assert enroll(app, "machine-a").status_code == 200       # the second server

    refused = enroll(app, "machine-b")

    assert refused.status_code == 409 and "most servers" in refused.json()["detail"]
    assert enroll(app, "machine-existing").status_code == 200   # an existing server can still be re-enrolled
    with app.Session() as db:
        assert db.query(Server).count() == 2


def test_the_cap_applies_to_adding_by_hand_and_to_approving_requests(app, monkeypatch):
    monkeypatch.setattr(main, "MAX_SERVERS", 1)
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1, "role": "admin"})
    monkeypatch.setattr(main, "_require_admin", lambda session_id, db: {"id": 1, "role": "admin"})

    assert app.post("/monitoring/servers", json={"hostname": "manual", "check_interval_seconds": 60}).status_code == 409
    with app.Session() as db:
        pending = PendingCollector(hostname="waiting", machine_id="machine-waiting", status="pending", poll_secret_hash="x", pairing_code="ABCD2345")
        db.add(pending)
        db.commit()
        pending_id = pending.id
    assert app.post(f"/monitoring/pending-collectors/{pending_id}/approve", json={}).status_code == 409
