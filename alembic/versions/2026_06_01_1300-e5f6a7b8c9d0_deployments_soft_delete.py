"""deployments: add deleted_at for soft-delete

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-01 13:00:00.000000

The lifecycle module's DELETE action used to map onto a hard
``db.delete(...)`` which removed the deployment row + cascade-deleted
its tasks/teams. That destroyed the audit trail (which user destroyed
which deployment when) and made it impossible to restore an
accidentally-deleted record.

The new behaviour: DELETE only sets ``deleted_at = utcnow()`` and the
default query filter hides those rows. The OpenStack resources are
already gone by the time DELETE is allowed (status must be ``failed``,
``destroyed`` or ``cancelled``), so there's no infrastructure cleanup
to coordinate — DELETE is purely a DB hide.

The partial index on ``deleted_at IS NULL`` keeps the default
deployment-list query as fast as before; without it we'd be doing a
sequential scan with a ``WHERE deleted_at IS NULL`` filter on every
listing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    # Partial index covers the only common query (the deployment list,
    # which always wants live rows). Audit lookups that include deleted
    # rows are infrequent and can do the seq scan.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_deployments_live
        ON deployments ("deploymentId")
        WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_deployments_live")
    op.drop_column("deployments", "deleted_at")
