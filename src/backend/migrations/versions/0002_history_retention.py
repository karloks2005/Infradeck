"""History retention: check-ins for 7 days, hourly summaries and events for the admin's setting (retention.py).

The old setting, ``metrics_retention_days``, kept check-ins and events for 1 to 3650 days (30 by default). Its
meaning is gone, so it is removed and the new setting starts at its default, one year. The two indexes serve the
purge of availability results and their hourly summaries.

Revision 0002, after 0001.
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # if_not_exists: a database the baseline brought up to date may already have them from create_all.
    op.create_index('ix_check_results_time', 'check_results', ['time'], unique=False, if_not_exists=True)
    op.create_index('ix_check_rollups_hour', 'check_rollups', ['hour'], unique=False, if_not_exists=True)
    op.execute(sa.text("DELETE FROM app_settings WHERE key = 'metrics_retention_days'"))


def downgrade() -> None:
    op.drop_index('ix_check_rollups_hour', table_name='check_rollups')
    op.drop_index('ix_check_results_time', table_name='check_results')
