"""add_destroyed_task_status

Revision ID: e00ea943757b
Revises: fc856a18c82d
Create Date: 2026-05-17 00:22:08.702048

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e00ea943757b'
down_revision: Union[str, None] = 'fc856a18c82d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'DESTROYED'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values — downgrade is a no-op
    pass
