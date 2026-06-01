"""tasks: nullable celeryTaskId + partial unique active task per deployment

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-25 18:00:00.000000

Two changes — both make the deployment dispatch flow race-free:

1. `tasks.celeryTaskId` becomes nullable. The new dispatch flow inserts
   the Task row inside the same transaction as the Deployment row and
   only stamps the celery_task_id AFTER the Celery `send_task` returns.
   Until then we don't have an ID to record, hence NULL.

2. A partial unique index on `(deploymentId)` filtered to PENDING/RUNNING
   tasks. Defense in depth against the application-level "only one
   active task per deployment" policy in `task_service.prepare_task_in_tx`.
   Two concurrent dispatchers that somehow bypass the policy check
   (e.g. cross-backend without advisory lock coordination) would fail
   the second insert at the DB layer instead of silently producing
   duplicate active tasks.

Note on enum case: the Postgres `taskstatus` enum stores values in
UPPERCASE (Python enum NAMES, not VALUES — the SQLAlchemy column does
not use `values_callable`). The partial index predicate must therefore
use 'PENDING' and 'RUNNING' literally.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "tasks",
        "celeryTaskId",
        existing_type=sa.String(),
        nullable=True,
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_active_per_deployment
        ON tasks ("deploymentId")
        WHERE status IN ('PENDING', 'RUNNING')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_tasks_active_per_deployment")

    # Best-effort: existing NULL celery_task_ids would block the alter.
    # Set them to a sentinel so downgrade doesn't fail in dev — production
    # rollback should be coordinated with a manual cleanup pass.
    op.execute(
        "UPDATE tasks SET \"celeryTaskId\" = '__missing__' "
        "WHERE \"celeryTaskId\" IS NULL"
    )
    op.alter_column(
        "tasks",
        "celeryTaskId",
        existing_type=sa.String(),
        nullable=False,
    )
