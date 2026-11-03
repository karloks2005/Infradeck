"""Bringing the database to the latest schema at start (Alembic revisions in src/backend/migrations/versions).

Runs before the backend serves anything. On PostgreSQL an advisory lock makes a second process (another backend, or
the agent worker) wait instead of migrating at the same time. Each revision runs in its own transaction, so a
revision that fails leaves the database at the one before it, and the backend refuses to start rather than run
on a half-changed schema.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

# Any fixed number; it only has to be the same for every process that migrates this database.
ADVISORY_LOCK_ID = 7_120_351
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


def upgrade_database(engine) -> None:
    with engine.connect() as connection:
        postgres = connection.dialect.name == "postgresql"
        if postgres:
            connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": ADVISORY_LOCK_ID})
            connection.commit()
        try:
            command.upgrade(alembic_config(connection), "head")
            connection.commit()
        finally:
            if postgres:
                connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": ADVISORY_LOCK_ID})
                connection.commit()
