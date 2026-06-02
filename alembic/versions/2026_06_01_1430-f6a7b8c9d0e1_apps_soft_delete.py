"""apps: add deleted_at for soft-delete

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-01 14:30:00.000000

Mirrors the soft-delete pattern we introduced for deployments
(``e5f6a7b8c9d0``). The DELETE on ``/apps/{id}`` was a hard delete
which cascade-removed the app row plus any historical breadcrumbs
the user might have wanted later (deployment counts, audit). New
behaviour: set ``deleted_at = utcnow()`` and let the CRUD layer
hide soft-deleted apps from default queries.

The partial index on ``deleted_at IS NULL`` keeps the default
listing query as fast as before; without it we'd be scanning the
whole apps table on every list request.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("apps", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_apps_live
        ON apps ("appId")
        WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_apps_live")
    op.drop_column("apps", "deleted_at")
