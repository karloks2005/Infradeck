"""The agent's look-ups next to each chat reply (#158): ``chat_messages.steps``, JSON of the steps in plain words
(src/agent/steps.py). Empty for questions and for replies from before this revision.

Revision 0003, after 0002.
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A database the baseline brought up to date with create_all may already have the column.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("chat_messages")}
    if "steps" not in columns:
        op.add_column('chat_messages', sa.Column('steps', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_messages', 'steps')
