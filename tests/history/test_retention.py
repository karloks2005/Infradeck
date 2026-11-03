"""How long history is kept (retention.py): check-ins and availability results 7 days; hourly summaries, hourly
availability summaries and events for the admin's setting; daily and coarser summaries forever."""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend import retention, rollups
from src.backend.models import AppSetting, Base, Check, CheckResult, CheckRollup, ContainerRollup, HostMetric, HostRollup, MountRollup, Server, SystemEvent, User

NOW = datetime(2027, 6, 15, 12, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add(Server(id=1, owner_id=1, hostname="nas", check_interval_seconds=60))
    session.add(Check(id=1, name="web", type="http", target="http://web.lan/"))
    # Every tier is summarised up to now.
    session.add_all(AppSetting(key=rollups.WATERMARK_PREFIX + tier, value=NOW.isoformat()) for tier in rollups.TIERS)
    session.commit()
    return session


def _setting(db, days):
    db.add(AppSetting(key=retention.HISTORY_KEY, value=str(days)))
    db.commit()


def _summaries(db, tier, *days_back):
    for days in days_back:
        start = NOW - timedelta(days=days)
        db.add(HostRollup(server_id=1, tier=tier, bucket_start=start, samples=60))
        db.add(MountRollup(server_id=1, mount="/", tier=tier, bucket_start=start, samples=60))
        db.add(ContainerRollup(server_id=1, container_id="c", name="web", tier=tier, bucket_start=start, samples=60))
    db.commit()


def _ages(db, model=HostRollup, tier=rollups.HOUR):
    return sorted((NOW - rollups._utc(row.bucket_start)).days for row in db.query(model).filter_by(tier=tier))


def test_history_is_kept_a_year_unless_the_admin_chose_otherwise(db):
    assert retention.history_days(db) == 365
    _setting(db, 730)
    assert retention.history_days(db) == 730


@pytest.mark.parametrize("stored", ["30", "soon", "-1"])
def test_a_stored_value_that_is_not_a_choice_means_the_default(db, stored):
    _setting(db, stored)
    assert retention.history_days(db) == 365


def test_hourly_summaries_older_than_the_setting_go_and_coarser_ones_stay(db):
    _summaries(db, rollups.HOUR, 10, 300, 400)
    _summaries(db, rollups.DAY, 10, 400, 900)
    _summaries(db, rollups.MONTH, 900)

    retention.purge(db, NOW)

    for model in (HostRollup, MountRollup, ContainerRollup):
        assert _ages(db, model) == [10, 300]
        assert _ages(db, model, rollups.DAY) == [10, 400, 900]
        assert _ages(db, model, rollups.MONTH) == [900]


def test_two_years_keeps_what_one_year_would_delete(db):
    _setting(db, 730)
    _summaries(db, rollups.HOUR, 400, 800)

    retention.purge(db, NOW)

    assert _ages(db) == [400]


def test_forever_deletes_no_summary_and_no_event(db):
    _setting(db, retention.FOREVER)
    _summaries(db, rollups.HOUR, 2000)
    db.add(SystemEvent(server_id=1, time=NOW - timedelta(days=2000), kind="reboot", detail="x"))
    db.add(CheckRollup(check_id=1, hour=NOW - timedelta(days=2000), slots=60, down_slots=0))
    db.commit()

    retention.purge(db, NOW)

    assert _ages(db) == [2000] and db.query(SystemEvent).count() == 1 and db.query(CheckRollup).count() == 1


def test_hourly_summaries_stay_until_their_day_is_summarised(db):
    db.get(AppSetting, rollups.WATERMARK_PREFIX + rollups.DAY).value = (NOW - timedelta(days=500)).isoformat()
    db.commit()
    _summaries(db, rollups.HOUR, 400, 600)

    retention.purge(db, NOW)

    assert _ages(db) == [400]   # past the year, but its day hasn't been summarised yet


def test_events_older_than_the_setting_go(db):
    db.add_all(SystemEvent(server_id=1, time=NOW - timedelta(days=days), kind="reboot", detail="x") for days in (40, 364, 366))
    db.commit()

    retention.purge(db, NOW)

    assert sorted((NOW - rollups._utc(row.time)).days for row in db.query(SystemEvent)) == [40, 364]


def test_availability_results_go_after_a_week_and_their_hours_after_the_setting(db):
    db.add_all(CheckResult(check_id=1, server_id=1, time=NOW - timedelta(days=days), ok=True) for days in (6, 8))
    db.add_all(CheckRollup(check_id=1, hour=NOW - timedelta(days=days), slots=60, down_slots=0) for days in (8, 300, 400))
    db.commit()

    retention.purge(db, NOW)

    assert [(NOW - rollups._utc(row.time)).days for row in db.query(CheckResult)] == [6]
    assert sorted((NOW - rollups._utc(row.hour)).days for row in db.query(CheckRollup)) == [8, 300]


def test_check_ins_go_after_a_week(db):
    db.add_all(HostMetric(server_id=1, time=NOW - timedelta(days=days)) for days in (1, 6, 8, 30))
    db.commit()

    retention.purge(db, NOW)

    assert sorted((NOW - rollups._utc(row.time)).days for row in db.query(HostMetric)) == [1, 6]


def test_a_big_purge_goes_in_chunks_and_still_deletes_everything_due(db, monkeypatch):
    monkeypatch.setattr(retention, "CHUNK", 2)
    db.add_all(HostMetric(server_id=1, time=NOW - timedelta(days=10, minutes=minute)) for minute in range(5))
    db.commit()

    assert retention.purge(db, NOW) == 5
    assert db.query(HostMetric).count() == 0
