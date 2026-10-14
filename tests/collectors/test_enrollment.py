import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agent import collector
from src.backend import main
from src.backend.models import Base, EnrollmentKey, IncidentAlert, Server, User


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.commit()
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})
    return session


def _enroll(db, key, machine_id="machine-a", hostname="nas"):
    return main.enroll_collector(main.EnrollRequest(enroll_key=key, hostname=hostname, machine_id=machine_id), db=db)


def test_enrollment_key_is_shown_once_and_stored_hashed(db):
    created = main.rotate_enrollment_key(session_id="s", db=db)

    stored = db.query(EnrollmentKey).one()
    assert created["enroll_key"].startswith("hlk_")
    assert stored.key_hash != created["enroll_key"]
    assert main.get_enrollment_key(session_id="s", db=db)["exists"] is True


def test_enroll_creates_server_owned_by_key_owner(db):
    key = main.rotate_enrollment_key(session_id="s", db=db)["enroll_key"]

    result = _enroll(db, key)

    server = db.query(Server).one()
    assert result["server_id"] == server.id
    assert server.owner_id == 1
    assert server.machine_id == "machine-a"
    assert server.collector_token_hash == main._hash_secret(result["collector_token"])


def test_reenroll_same_machine_reuses_server_and_replaces_token(db):
    key = main.rotate_enrollment_key(session_id="s", db=db)["enroll_key"]
    first = _enroll(db, key)
    second = _enroll(db, key)

    assert db.query(Server).count() == 1
    assert first["server_id"] == second["server_id"]
    assert db.query(Server).one().collector_token_hash == main._hash_secret(second["collector_token"])


def test_different_machines_get_separate_servers(db):
    key = main.rotate_enrollment_key(session_id="s", db=db)["enroll_key"]
    _enroll(db, key, machine_id="machine-a")
    _enroll(db, key, machine_id="machine-b", hostname="pi")

    assert db.query(Server).count() == 2


def test_rotated_or_revoked_key_is_rejected(db):
    old_key = main.rotate_enrollment_key(session_id="s", db=db)["enroll_key"]
    main.rotate_enrollment_key(session_id="s", db=db)
    with pytest.raises(HTTPException) as exc:
        _enroll(db, old_key)
    assert exc.value.status_code == 401

    main.revoke_enrollment_key(session_id="s", db=db)
    assert main.get_enrollment_key(session_id="s", db=db)["exists"] is False


def test_offline_alert_raised_once_and_resolved_on_ingest(db):
    now = datetime.now(timezone.utc)
    token = "collector-token"
    db.add(Server(id=5, owner_id=1, hostname="nas", check_interval_seconds=60, last_seen_at=now - timedelta(minutes=10), collector_token_hash=main._hash_secret(token)))
    db.commit()

    assert main.mark_offline_servers(db, now) == 1
    assert main.mark_offline_servers(db, now) == 0
    assert db.query(IncidentAlert).filter_by(status="active").count() == 1

    main.ingest_metrics(main.MetricsIngestRequest(server_id=5), authorization=f"Bearer {token}", session_id=None, db=db)

    assert db.query(IncidentAlert).filter_by(status="active").count() == 0


def test_recently_seen_server_is_not_offline(db):
    now = datetime.now(timezone.utc)
    db.add(Server(id=6, owner_id=1, hostname="pi", check_interval_seconds=60, last_seen_at=now - timedelta(seconds=90)))
    db.commit()

    assert main.mark_offline_servers(db, now) == 0


def test_collector_prefers_saved_credentials_over_enrolling(tmp_path, monkeypatch):
    monkeypatch.setenv("INFRADECK_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("INFRADECK_SERVER_ID", raising=False)
    monkeypatch.delenv("INFRADECK_COLLECTOR_TOKEN", raising=False)
    monkeypatch.setenv("INFRADECK_ENROLL_KEY", "hlk_key")
    collector.save_credentials(7, "saved-token")
    monkeypatch.setattr(collector, "enroll", lambda *args: pytest.fail("should not enroll"))

    assert collector.resolve_credentials("http://backend") == (7, "saved-token")


def test_collector_enrolls_and_saves_when_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("INFRADECK_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("INFRADECK_SERVER_ID", raising=False)
    monkeypatch.delenv("INFRADECK_COLLECTOR_TOKEN", raising=False)
    monkeypatch.setenv("INFRADECK_ENROLL_KEY", "hlk_key")
    monkeypatch.setattr(collector, "enroll", lambda api_url, key: (9, "new-token"))

    assert collector.resolve_credentials("http://backend") == (9, "new-token")
    assert collector.load_credentials() == (9, "new-token")
    assert oct((tmp_path / "credentials.json").stat().st_mode & 0o777) == "0o600"


def test_machine_id_read_from_host_root(tmp_path, monkeypatch):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/machine-id").write_text("abc123\n")
    monkeypatch.delenv("INFRADECK_MACHINE_ID", raising=False)
    monkeypatch.setattr(collector, "HOST_ROOT", tmp_path)

    assert collector._machine_id() == "abc123"


def test_a_collector_from_before_the_rename_keeps_its_credentials_and_settings(tmp_path, monkeypatch):
    from src.agent import collector
    legacy = tmp_path / "homelab-collector"
    legacy.mkdir()
    (legacy / "credentials.json").write_text('{"server_id": 4, "token": "t"}')
    monkeypatch.delenv("INFRADECK_STATE_DIR", raising=False)
    monkeypatch.setattr(collector, "LEGACY_STATE_DIR", legacy)
    assert collector._credentials_path() == legacy / "credentials.json"
    monkeypatch.setenv("INFRADECK_STATE_DIR", str(tmp_path / "new"))
    assert collector._credentials_path() == tmp_path / "new" / "credentials.json"
