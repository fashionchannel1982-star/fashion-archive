"""add events table

Revision ID: 307b7c29fe88
Revises: efe2acac22c4
Create Date: 2026-06-17 17:34:23.969838

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '307b7c29fe88'
down_revision: Union[str, Sequence[str], None] = 'efe2acac22c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'events',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('query_text', sa.Text(), nullable=True),
        sa.Column('moment_id', sa.String(), nullable=True),
        sa.Column('session_id', sa.String(), nullable=True),
        sa.Column('event_meta', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('events_event_type_idx', 'events', ['event_type'])
    op.create_index('events_created_at_idx', 'events', ['created_at'])


def downgrade() -> None:
    op.drop_index('events_created_at_idx', table_name='events')
    op.drop_index('events_event_type_idx', table_name='events')
    op.drop_table('events')
