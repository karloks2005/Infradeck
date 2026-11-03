import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from src.backend.routes import monitoring as monitoring_routes
from src.backend import main, rollups
from src.backend.models import (
    AppSetting, Base, ContainerMetric, ContainerRollup, HostMetric, HostRollup, MountMetric, MountRollup, Server, User,
)


def T(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, name="Owner", email="owner@example.com", password_hash="x", role="admin"))
    session.add_all(Server(id=n, owner_id=1, hostname=f"srv{n}", check_interval_seconds=60) for n in (1, 2))
    session.commit()
    return session


def ago(days, hour=10):
    """A moment a few days back, for the tests that use the real clock (maintenance and server deletion)."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def host(db, when, server=1, **fields):
    db.add(HostMetric(server_id=server, time=when, **fields))
    db.commit()


def catch_up(db, now):
    """Run the job until it has nothing left to do (a long history takes several chunks)."""
    for _ in range(100):
        if not any(rollups.run_rollups(db, now).values()):
            return
    raise AssertionError("the job never finished")


def hosts(db, tier, server=1):
    rows = db.query(HostRollup).filter_by(tier=tier, server_id=server).order_by(HostRollup.bucket_start).all()
    return [(rollups._utc(row.bucket_start), row) for row in rows]


# --- bucket arithmetic ---------------------------------------------------------------------

def test_buckets_start_on_utc_boundaries_and_weeks_on_monday():
    assert rollups.floor(rollups.HOUR, T(2026, 3, 4, 17, 59)) == T(2026, 3, 4, 17)
    assert rollups.floor(rollups.DAY, T(2026, 3, 4, 17, 59)) == T(2026, 3, 4)
    sunday = T(2026, 3, 8, 23, 59)
    assert sunday.weekday() == 6
    assert rollups.floor(rollups.WEEK, sunday) == T(2026, 3, 2) and T(2026, 3, 2).weekday() == 0
    assert rollups.floor(rollups.WEEK, T(2026, 3, 9)) == T(2026, 3, 9)  # the next Monday starts a new week
    assert rollups.floor(rollups.MONTH, T(2026, 3, 31, 23)) == T(2026, 3, 1)


def test_a_time_with_another_offset_is_converted_to_utc_first():
    plus_two = datetime(2026, 3, 5, 1, 30, tzinfo=timezone(timedelta(hours=2)))  # 23:30 UTC the day before
    assert rollups.floor(rollups.DAY, plus_two) == T(2026, 3, 4)
    assert rollups.floor(rollups.DAY, datetime(2026, 3, 4, 12, 0)) == T(2026, 3, 4)  # naive counts as UTC


def test_the_following_bucket_handles_month_ends_and_leap_years():
    assert rollups.following(rollups.MONTH, T(2026, 12, 1)) == T(2027, 1, 1)
    assert rollups.following(rollups.MONTH, T(2028, 2, 1)) == T(2028, 3, 1)
    assert rollups.following(rollups.WEEK, T(2026, 12, 28)) == T(2027, 1, 4)
    assert rollups.following(rollups.HOUR, T(2026, 3, 8, 23)) == T(2026, 3, 9)


# --- hourly summaries ----------------------------------------------------------------------

def test_an_hour_is_summarised_with_average_maximum_and_count(db):
    for minute, cpu in ((0, 10.0), (20, 20.0), (40, 60.0)):
        host(db, T(2026, 3, 4, 10, minute), cpu_utilization_pct=cpu, ram_pct=50.0)

    assert rollups.run_rollups(db, T(2026, 3, 4, 11, 10))[rollups.HOUR] == 1

    [(start, row)] = hosts(db, rollups.HOUR)
    assert start == T(2026, 3, 4, 10)
    assert (row.samples, row.avg_cpu, row.max_cpu, row.avg_ram, row.max_ram) == (3, 30.0, 60.0, 50.0, 50.0)
    assert row.avg_swap is None and row.max_swap is None  # never reported stays unknown, not zero


def test_a_metric_missing_from_some_samples_is_averaged_over_the_ones_that_have_it(db):
    host(db, T(2026, 3, 4, 10, 0), cpu_utilization_pct=10.0)
    host(db, T(2026, 3, 4, 10, 1))
    host(db, T(2026, 3, 4, 10, 2), cpu_utilization_pct=30.0)

    rollups.run_rollups(db, T(2026, 3, 4, 11, 10))

    [(_, row)] = hosts(db, rollups.HOUR)
    assert (row.samples, row.avg_cpu, row.max_cpu) == (3, 20.0, 30.0)


def test_a_history_of_nothing_but_unknowns_still_counts_check_ins_and_does_not_break(db):
    for minute in range(5):
        host(db, T(2026, 3, 4, 10, minute))
    db.add(MountMetric(server_id=1, time=T(2026, 3, 4, 10, 0), mount="/"))
    db.add(ContainerMetric(server_id=1, time=T(2026, 3, 4, 10, 0), container_id="c", name="web", status="running"))
    db.commit()

    rollups.run_rollups(db, T(2026, 3, 4, 11, 10))

    [(_, row)] = hosts(db, rollups.HOUR)
    assert row.samples == 5 and row.avg_cpu is None and row.max_cpu is None
    assert db.query(MountRollup).one().avg_used_pct is None
    container = db.query(ContainerRollup).one()
    assert (container.samples, container.down_samples, container.avg_cpu, container.max_restarts) == (1, 0, None, 0)   # restarts default to 0


def test_only_completed_buckets_are_written_after_a_short_grace_period(db):
    host(db, T(2026, 3, 4, 10, 30), cpu_utilization_pct=10.0)

    assert rollups.run_rollups(db, T(2026, 3, 4, 10, 59))[rollups.HOUR] == 0
    assert rollups.run_rollups(db, T(2026, 3, 4, 11, 4))[rollups.HOUR] == 0   # ended, but check-ins may still be arriving
    assert hosts(db, rollups.HOUR) == []
    assert rollups.run_rollups(db, T(2026, 3, 4, 11, 6))[rollups.HOUR] == 1
    assert len(hosts(db, rollups.HOUR)) == 1


def test_running_twice_changes_nothing(db):
    for minute in range(0, 60, 10):
        host(db, T(2026, 3, 4, 10, minute), cpu_utilization_pct=float(minute))
    db.add(ContainerMetric(server_id=1, time=T(2026, 3, 4, 10, 0), container_id="c", name="web", status="running", cpu_pct=5.0))
    db.commit()
    now = T(2026, 3, 6, 0, 30)

    rollups.run_rollups(db, now)
    first = [(r.tier, r.bucket_start, r.samples, r.avg_cpu) for r in db.query(HostRollup).order_by(HostRollup.id)]
    again = rollups.run_rollups(db, now)

    assert again == {"hour": 0, "day": 0, "week": 0, "month": 0}
    assert [(r.tier, r.bucket_start, r.samples, r.avg_cpu) for r in db.query(HostRollup).order_by(HostRollup.id)] == first


def test_rebuilding_a_bucket_replaces_it_instead_of_duplicating_it(db):
    host(db, T(2026, 3, 4, 10, 0), cpu_utilization_pct=10.0)
    rollups.run_rollups(db, T(2026, 3, 4, 11, 10))
    db.query(AppSetting).filter(AppSetting.key == "rollup_done_hour").delete()   # forget how far it got
    host(db, T(2026, 3, 4, 10, 30), cpu_utilization_pct=30.0)

    rollups.run_rollups(db, T(2026, 3, 4, 11, 10))

    [(_, row)] = hosts(db, rollups.HOUR)
    assert (row.samples, row.avg_cpu) == (2, 20.0)


def test_servers_mounts_and_containers_are_summarised_separately(db):
    host(db, T(2026, 3, 4, 10, 0), server=1, cpu_utilization_pct=10.0)
    host(db, T(2026, 3, 4, 10, 0), server=2, cpu_utilization_pct=90.0)
    for mount, used in (("/", 40.0), ("/data", 80.0)):
        db.add(MountMetric(server_id=1, time=T(2026, 3, 4, 10, 0), mount=mount, used_pct=used, used_gb=used, total_gb=100.0))
    for name, cpu in (("web", 5.0), ("db", 50.0)):
        db.add(ContainerMetric(server_id=1, time=T(2026, 3, 4, 10, 0), container_id=f"id-{name}", name=name, status="running", cpu_pct=cpu, memory_used_mb=100.0, memory_limit_mb=512.0, restart_count=2))
    db.commit()

    rollups.run_rollups(db, T(2026, 3, 4, 11, 10))

    assert [row.avg_cpu for _, row in hosts(db, rollups.HOUR, 1)] == [10.0] and [row.avg_cpu for _, row in hosts(db, rollups.HOUR, 2)] == [90.0]
    mounts = {row.mount: (row.avg_used_pct, row.max_total_gb) for row in db.query(MountRollup)}
    assert mounts == {"/": (40.0, 100.0), "/data": (80.0, 100.0)}
    containers = {row.name: (row.container_id, row.avg_cpu, row.max_memory_limit_mb, row.max_restarts) for row in db.query(ContainerRollup)}
    assert containers == {"web": ("id-web", 5.0, 512.0, 2), "db": ("id-db", 50.0, 512.0, 2)}


def test_down_samples_count_the_check_ins_where_a_container_was_not_running(db):
    for minute, status in ((0, "running"), (1, "exited"), (2, "exited"), (3, "restarting")):
        db.add(ContainerMetric(server_id=1, time=T(2026, 3, 4, 10, minute), container_id="c", name="web", status=status))
    db.commit()

    rollups.run_rollups(db, T(2026, 3, 4, 11, 10))

    row = db.query(ContainerRollup).one()
    assert (row.samples, row.down_samples) == (4, 3)


# --- daily, weekly and monthly summaries -----------------------------------------------------

def test_a_day_is_the_samples_weighted_average_of_its_hours_and_keeps_the_maximum(db):
    host(db, T(2026, 3, 4, 10, 0), cpu_utilization_pct=100.0)
    for minute in range(3):
        host(db, T(2026, 3, 4, 11, minute), cpu_utilization_pct=0.0)

    rollups.run_rollups(db, T(2026, 3, 5, 0, 10))

    [(start, day)] = hosts(db, rollups.DAY)
    assert start == T(2026, 3, 4)
    assert (day.samples, day.avg_cpu, day.max_cpu) == (4, 25.0, 100.0)   # 1 sample of 100 and 3 of 0, not (100 + 0) / 2


def test_each_average_remembers_how_many_check_ins_reported_it(db):
    host(db, T(2026, 3, 4, 10, 0), cpu_utilization_pct=10.0, ram_pct=40.0)
    host(db, T(2026, 3, 4, 10, 1), cpu_utilization_pct=20.0)
    host(db, T(2026, 3, 4, 10, 2))

    rollups.run_rollups(db, T(2026, 3, 5, 0, 10))

    for tier in (rollups.HOUR, rollups.DAY):
        [(_, row)] = hosts(db, tier)
        assert (row.samples, row.n_cpu, row.n_ram, row.n_swap) == (3, 2, 1, 0)


def test_a_container_that_was_stopped_part_of_the_time_is_averaged_over_the_time_it_ran(db):
    for hour, status, cpu in ((10, "running", 90.0), (11, "exited", None), (12, "exited", None), (13, "exited", None)):
        db.add(ContainerMetric(server_id=1, time=T(2026, 3, 4, hour), container_id="c", name="web", status=status, cpu_pct=cpu))
    db.add(ContainerMetric(server_id=1, time=T(2026, 3, 5, 10), container_id="c", name="web", status="running", cpu_pct=10.0))
    db.commit()

    catch_up(db, T(2026, 3, 9, 0, 10))

    day = db.query(ContainerRollup).filter_by(tier="day", bucket_start=T(2026, 3, 4)).one()
    week = db.query(ContainerRollup).filter_by(tier="week").one()
    assert (day.samples, day.down_samples, day.avg_cpu, day.n_cpu) == (4, 3, 90.0, 1)
    assert (week.samples, week.down_samples, week.avg_cpu, week.n_cpu) == (5, 3, 50.0, 2)   # (90 + 10) / 2, not (90*4 + 10) / 5


def test_hours_that_never_reported_a_metric_do_not_pull_a_day_toward_zero(db):
    host(db, T(2026, 3, 4, 10, 0), cpu_utilization_pct=80.0)
    host(db, T(2026, 3, 4, 11, 0), docker_latency_ms=50)
    host(db, T(2026, 3, 4, 11, 1), docker_latency_ms=70)

    rollups.run_rollups(db, T(2026, 3, 5, 0, 10))

    [(_, day)] = hosts(db, rollups.DAY)
    assert (day.avg_cpu, day.max_cpu, day.avg_docker_ms, day.max_docker_ms, day.samples) == (80.0, 80.0, 60.0, 70, 3)


def test_a_week_runs_monday_to_sunday_and_is_written_only_when_sunday_has_ended(db):
    assert T(2026, 3, 2).weekday() == 0
    for day in range(2, 9):                                    # Monday 2 March to Sunday 8 March
        host(db, T(2026, 3, day, 12, 0), cpu_utilization_pct=float(day))
    host(db, T(2026, 3, 9, 12, 0), cpu_utilization_pct=1000.0)  # the following Monday belongs to the next week

    rollups.run_rollups(db, T(2026, 3, 8, 23, 59))
    assert hosts(db, rollups.WEEK) == []                        # still Sunday

    rollups.run_rollups(db, T(2026, 3, 9, 0, 10))
    [(start, week)] = hosts(db, rollups.WEEK)
    assert start == T(2026, 3, 2)
    assert (week.samples, week.avg_cpu, week.max_cpu) == (7, 5.0, 8.0)   # days 2..8 average 5, the Monday after is excluded


def test_a_month_is_built_from_its_days_including_a_leap_february(db):
    for day in range(1, 30):
        host(db, T(2028, 2, day, 12, 0), cpu_utilization_pct=10.0)

    catch_up(db, T(2028, 3, 1, 0, 10))

    [(start, month)] = hosts(db, rollups.MONTH)
    assert start == T(2028, 2, 1) and (month.samples, month.avg_cpu) == (29, 10.0)


def test_data_either_side_of_new_year_lands_in_different_months_and_weeks(db):
    host(db, T(2026, 12, 31, 12, 0), cpu_utilization_pct=10.0)   # a Thursday
    host(db, T(2027, 1, 1, 12, 0), cpu_utilization_pct=20.0)

    catch_up(db, T(2027, 2, 1, 1, 0))

    assert [(start, row.avg_cpu) for start, row in hosts(db, rollups.MONTH)] == [(T(2026, 12, 1), 10.0), (T(2027, 1, 1), 20.0)]
    assert [(start, row.avg_cpu) for start, row in hosts(db, rollups.WEEK)] == [(T(2026, 12, 28), 15.0)]   # one ISO week spans both


def test_every_tier_is_built_for_mounts_and_containers_too(db):
    for day in (2, 3):
        db.add(MountMetric(server_id=1, time=T(2026, 3, day, 12), mount="/", used_pct=40.0 + day, used_gb=10.0, total_gb=100.0))
        db.add(ContainerMetric(server_id=1, time=T(2026, 3, day, 12), container_id="c", name="web", status="running", cpu_pct=float(day), memory_used_mb=100.0))
    db.add(ContainerMetric(server_id=1, time=T(2026, 3, 3, 13), container_id="c", name="web", status="exited"))
    db.commit()

    catch_up(db, T(2026, 4, 2))

    for model in (MountRollup, ContainerRollup):
        assert {row.tier for row in db.query(model)} == {"hour", "day", "week", "month"}
    week = db.query(ContainerRollup).filter_by(tier="week").one()
    assert (week.samples, week.down_samples, week.avg_cpu, week.max_cpu, week.avg_memory_mb) == (3, 1, 2.5, 3.0, 100.0)
    assert db.query(MountRollup).filter_by(tier="week").one().max_used_pct == 43.0


# --- gaps, catching up and the watermark -----------------------------------------------------

def test_a_gap_stays_a_gap(db):
    host(db, T(2026, 3, 4, 10, 0), server=1, cpu_utilization_pct=10.0)
    host(db, T(2026, 3, 4, 10, 0), server=2, cpu_utilization_pct=20.0)
    host(db, T(2026, 3, 4, 14, 0), server=1, cpu_utilization_pct=30.0)   # server 2 was silent at 14:00 and 11:00-13:00
    host(db, T(2026, 3, 6, 9, 0), server=1, cpu_utilization_pct=40.0)    # nothing at all on the 5th

    rollups.run_rollups(db, T(2026, 3, 7, 0, 10))

    assert [start for start, _ in hosts(db, rollups.HOUR, 1)] == [T(2026, 3, 4, 10), T(2026, 3, 4, 14), T(2026, 3, 6, 9)]
    assert [start for start, _ in hosts(db, rollups.HOUR, 2)] == [T(2026, 3, 4, 10)]
    assert [start for start, _ in hosts(db, rollups.DAY, 1)] == [T(2026, 3, 4), T(2026, 3, 6)]


def test_the_first_run_summarises_the_history_that_already_exists(db):
    for day in range(2, 11):                                   # Monday 2 March to Tuesday 10 March
        for hour in range(0, 24, 6):
            host(db, T(2026, 3, day, hour, 0), cpu_utilization_pct=float(day))

    result = rollups.run_rollups(db, T(2026, 3, 11, 0, 10))

    assert result[rollups.HOUR] == 216 and result[rollups.DAY] == 9 and result[rollups.WEEK] == 1   # only 2-8 March is a complete week
    assert len(hosts(db, rollups.HOUR)) == 36 and len(hosts(db, rollups.DAY)) == 9


def test_new_samples_are_picked_up_by_the_next_run(db):
    host(db, T(2026, 3, 4, 10, 0), cpu_utilization_pct=10.0)
    rollups.run_rollups(db, T(2026, 3, 4, 11, 10))
    host(db, T(2026, 3, 4, 12, 0), cpu_utilization_pct=20.0)

    assert rollups.run_rollups(db, T(2026, 3, 4, 13, 10))[rollups.HOUR] == 2   # the empty 11:00 hour and the 12:00 one
    assert [row.avg_cpu for _, row in hosts(db, rollups.HOUR)] == [10.0, 20.0]


def test_after_downtime_the_job_catches_up_a_chunk_at_a_time(db, monkeypatch):
    monkeypatch.setitem(rollups.MAX_BUCKETS_PER_RUN, rollups.HOUR, 5)
    for hour in range(12):
        host(db, T(2026, 3, 4, hour, 0), cpu_utilization_pct=float(hour))
    now = T(2026, 3, 4, 12, 10)

    built = [rollups.run_rollups(db, now)[rollups.HOUR] for _ in range(4)]

    assert built == [5, 5, 2, 0]
    assert [row.avg_cpu for _, row in hosts(db, rollups.HOUR)] == [float(hour) for hour in range(12)]


def test_a_long_empty_stretch_cannot_stall_the_job(db, monkeypatch):
    monkeypatch.setitem(rollups.MAX_BUCKETS_PER_RUN, rollups.HOUR, 5)
    host(db, T(2026, 3, 4, 0, 0), cpu_utilization_pct=1.0)
    host(db, T(2026, 3, 5, 6, 0), cpu_utilization_pct=2.0)     # 30 hours later, with nothing between

    for _ in range(10):
        rollups.run_rollups(db, T(2026, 3, 5, 8, 0))

    assert [row.avg_cpu for _, row in hosts(db, rollups.HOUR)] == [1.0, 2.0]


def test_the_watermark_is_stored_per_tier_and_a_damaged_one_is_ignored(db):
    host(db, T(2026, 3, 4, 10, 0), cpu_utilization_pct=10.0)
    rollups.run_rollups(db, T(2026, 3, 6, 0, 10))

    assert db.get(AppSetting, "rollup_done_hour").value.startswith("2026-03-06T00:00:00")
    assert db.get(AppSetting, "rollup_done_day").value.startswith("2026-03-06T00:00:00")

    db.get(AppSetting, "rollup_done_hour").value = "not a date"
    db.commit()
    rollups.run_rollups(db, T(2026, 3, 6, 0, 10))                # starts over from the first sample, harmlessly
    assert len(hosts(db, rollups.HOUR)) == 1 and len(hosts(db, rollups.DAY)) == 1


# --- purging only what has been summarised ---------------------------------------------------

def test_without_any_summaries_no_check_in_is_purged(db):
    host(db, T(2026, 3, 1, 10, 0), cpu_utilization_pct=1.0)
    host(db, T(2026, 3, 20, 10, 0), cpu_utilization_pct=2.0)

    main.retention.purge(db, now=T(2026, 3, 20, 12, 0))

    assert db.query(HostMetric).count() == 2


def test_samples_that_were_not_summarised_yet_are_never_purged(db):
    for day in range(1, 10):
        host(db, T(2026, 3, day, 10, 0), cpu_utilization_pct=float(day))
    db.add(AppSetting(key="rollup_done_hour", value=T(2026, 3, 2, 12).isoformat()))   # only the first day and a half are summarised
    db.commit()

    main.retention.purge(db, now=T(2026, 3, 12, 12, 0))      # the week alone would delete everything before 5 March

    # 1 and 2 March are summarised and old enough; 3 and 4 March are old enough but not summarised yet, so they stay
    assert [rollups._utc(row.time).day for row in db.query(HostMetric).order_by(HostMetric.time)] == [3, 4, 5, 6, 7, 8, 9]


def test_maintenance_summarises_before_it_purges(db):
    for days_back, cpu in ((9, 1.0), (8, 2.0)):
        host(db, ago(days_back), cpu_utilization_pct=cpu)
    db.add(ContainerMetric(server_id=1, time=ago(9), container_id="c", name="web", status="running"))
    host(db, datetime.now(timezone.utc) - timedelta(minutes=1), cpu_utilization_pct=99.0)   # recent: kept
    db.commit()

    assert main.metrics_maintenance(db, purge_due=True) is True

    assert [row.cpu_utilization_pct for row in db.query(HostMetric)] == [99.0] and db.query(ContainerMetric).count() == 0
    assert [row.avg_cpu for _, row in hosts(db, rollups.HOUR)] == [1.0, 2.0] and db.query(ContainerRollup).filter_by(tier="hour").count() == 1


def test_a_failing_rollup_job_stops_the_purge(db, monkeypatch):
    host(db, ago(9), cpu_utilization_pct=1.0)

    def broken(*args, **kwargs):
        raise RuntimeError("database trouble")

    monkeypatch.setattr(rollups, "run_rollups", broken)

    assert main.metrics_maintenance(db, purge_due=True) is False
    assert db.query(HostMetric).count() == 1


def test_between_purges_maintenance_only_summarises(db):
    host(db, ago(2), cpu_utilization_pct=1.0)

    assert main.metrics_maintenance(db, purge_due=False) is False
    assert len(hosts(db, rollups.HOUR)) == 1 and db.query(HostMetric).count() == 1


def test_deleting_a_server_deletes_its_summaries_but_not_another_servers(db, monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda session_id, db: {"id": 1})
    for server in (1, 2):
        host(db, ago(2), server=server, cpu_utilization_pct=10.0)
        db.add(MountMetric(server_id=server, time=ago(2), mount="/", used_pct=1.0))
        db.add(ContainerMetric(server_id=server, time=ago(2), container_id="c", name="web", status="running"))
    db.commit()
    rollups.run_rollups(db)

    monitoring_routes.delete_monitored_server(1, session_id="s", db=db)

    for model in (HostRollup, MountRollup, ContainerRollup):
        assert db.query(model).filter_by(server_id=1).count() == 0
        assert db.query(model).filter_by(server_id=2).count() > 0
