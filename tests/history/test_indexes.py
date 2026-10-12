import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine, inspect

from src.backend.models import Base

METRIC_TABLES = ("host_metrics", "mount_metrics", "container_metrics", "system_events")


@pytest.fixture(scope="module")
def indexes():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return {table: {index["name"]: index["column_names"] for index in inspect(engine).get_indexes(table)} for table in METRIC_TABLES}


def test_the_metrics_tables_carry_exactly_the_indexes_they_need(indexes):
    assert indexes["host_metrics"] == {"ix_host_metrics_time": ["time"], "ix_host_metrics_server_time": ["server_id", "time"]}
    assert indexes["mount_metrics"] == {"ix_mount_metrics_time": ["time"], "ix_mount_metrics_server_time": ["server_id", "time"]}
    assert indexes["container_metrics"] == {"ix_container_metrics_time": ["time"], "ix_container_metrics_server_container_time": ["server_id", "container_id", "time"]}
    assert indexes["system_events"] == {"ix_system_events_time": ["time"], "ix_system_events_server_time": ["server_id", "time"], "ix_system_events_event_key": ["event_key"]}


@pytest.mark.parametrize("table", METRIC_TABLES)
def test_deleting_a_server_and_purging_old_rows_can_both_use_an_index(indexes, table):
    columns = list(indexes[table].values())
    assert any(index[0] == "server_id" for index in columns), "deleting a server cascades through server_id"
    assert ["time"] in columns, "the hourly purge deletes by time alone"


@pytest.mark.parametrize("table", METRIC_TABLES)
def test_a_primary_key_is_never_indexed_a_second_time(indexes, table):
    assert not any(index == ["id"] for index in indexes[table].values())
