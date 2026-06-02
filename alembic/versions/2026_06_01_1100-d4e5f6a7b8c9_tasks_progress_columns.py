"""tasks: add current_phase + progress_pct for live progress

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-01 11:00:00.000000

The deploy worker emits ``task-progress`` events as it walks through
phases (Git → Packer → Terraform). The backend's celery event
listener forwards each event to live SSE subscribers and also
persists the most recent phase/percent on the active task here so a
page reload shows the last known state instead of an empty bar.

Both columns are nullable because:
* historical task rows have no progress data
* completed tasks don't need progress (final status carries that)
* short-running tasks (output-only) may finish before the listener
  manages a single update
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("current_phase", sa.String(length=50), nullable=True))
    op.add_column("tasks", sa.Column("progress_pct", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "progress_pct")
    op.drop_column("tasks", "current_phase")
