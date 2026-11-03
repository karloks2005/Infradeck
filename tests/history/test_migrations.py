"""Migrations (src/backend/migrations): a new database and one from before Alembic both end at the latest revision
with exactly the schema the models describe, and running the upgrade again changes nothing.

Set MIGRATION_TEST_POSTGRES_URL to an empty PostgreSQL database to run the same checks there too."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from src.backend.migrate import MIGRATIONS_DIR, alembic_config, upgrade_database
from src.backend.models import Base

POSTGRES_URL = os.getenv("MIGRATION_TEST_POSTGRES_URL")


@pytest.fixture(params=["sqlite"] + (["postgresql"] if POSTGRES_URL else []))
def engine(request, tmp_path):
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    else:
        engine = create_engine(POSTGRES_URL)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def _head() -> str:
    with create_engine("sqlite://").connect() as connection:
        return ScriptDirectory.from_config(alembic_config(connection)).get_current_head()


def _version(engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _differences(engine) -> list:
    with engine.connect() as connection:
        return compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata)


def test_the_migrations_live_next_to_the_backend():
    assert (MIGRATIONS_DIR / "versions").is_dir()


def test_a_new_database_ends_at_the_latest_revision_with_the_models_schema(engine):
    upgrade_database(engine)
    assert _version(engine) == _head()
    assert _differences(engine) == []


def test_upgrading_twice_changes_nothing(engine):
    upgrade_database(engine)
    before = {table: inspect(engine).get_columns(table) for table in inspect(engine).get_table_names()}
    upgrade_database(engine)
    after = {table: inspect(engine).get_columns(table) for table in inspect(engine).get_table_names()}
    assert _version(engine) == _head()
    assert [(table, [column["name"] for column in columns]) for table, columns in before.items()] == [(table, [column["name"] for column in columns]) for table, columns in after.items()]


def test_a_database_from_before_migrations_keeps_its_data_and_ends_at_the_latest_revision(engine):
    Base.metadata.create_all(engine)   # how the schema was made before Alembic
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO app_settings (key, value, updated_at) VALUES ('instance_name', 'lab', CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO app_settings (key, value, updated_at) VALUES ('metrics_retention_days', '30', CURRENT_TIMESTAMP)"))
    upgrade_database(engine)
    assert _version(engine) == _head()
    assert _differences(engine) == []
    with engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM app_settings WHERE key = 'instance_name'")).scalar() == "lab"
        # 0002: the old retention setting has no meaning any more
        assert connection.execute(text("SELECT count(*) FROM app_settings WHERE key = 'metrics_retention_days'")).scalar() == 0


def test_an_old_database_missing_tables_and_columns_is_brought_up_to_date(engine):
    """An install that last ran an old version: no availability tables yet, and servers without the newer columns."""
    tables = [table for name, table in Base.metadata.tables.items() if not name.startswith("check")]
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE servers DROP COLUMN name_locked"))
        connection.execute(text("ALTER TABLE servers DROP COLUMN system_info"))
    upgrade_database(engine)
    assert {"checks", "check_results", "check_rollups"} <= set(inspect(engine).get_table_names())
    assert {"name_locked", "system_info"} <= {column["name"] for column in inspect(engine).get_columns("servers")}
    assert _differences(engine) == []
