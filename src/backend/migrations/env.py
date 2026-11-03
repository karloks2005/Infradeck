"""Alembic's entry point. The backend passes its own connection in (src/backend/migrate.py); the `alembic` command
line connects to DATABASE_URL instead."""
from alembic import context
from sqlalchemy import create_engine

from src.backend.config import DATABASE_URL
from src.backend.models import Base


def _applied(ctx, step, heads, run_args) -> None:
    print(f"Database migration {'applied' if step.is_upgrade else 'reverted'}: {step.up_revision_id} ({step.up_revision.doc.splitlines()[0]})", flush=True)


def _run(connection) -> None:
    # SQLite can't alter most of a table in place; batch mode copies the table instead. One transaction per
    # revision, so a failing one leaves the database at the revision before it.
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=connection.dialect.name == "sqlite", compare_type=True,
                      transaction_per_migration=True, on_version_apply=_applied)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    with create_engine(DATABASE_URL).connect() as own:
        _run(own)
