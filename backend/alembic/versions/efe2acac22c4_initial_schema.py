"""initial schema — baseline stamp (no-op)

Marks the pre-existing schema as the starting point for Alembic.
All live tables already exist; this migration makes no changes.
Subsequent migrations add new tables/columns only.

Revision ID: efe2acac22c4
Revises:
Create Date: 2026-06-17 17:33:37.089021

"""
from typing import Sequence, Union
from alembic import op

revision: str = 'efe2acac22c4'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # baseline — schema already exists


def downgrade() -> None:
    pass  # baseline — nothing to reverse
