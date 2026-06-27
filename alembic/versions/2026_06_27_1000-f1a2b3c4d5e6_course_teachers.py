"""course_teachers many-to-many table — schema only, no backfill yet

Revision ID: f1a2b3c4d5e6
Revises: 0e43dd2bc856
Create Date: 2026-06-27 10:00:00.000000

Creates the ``course_teachers`` join table that backs the per-course
"course-teacher" capability check (see :mod:`app.utils.capabilities`).

Phase 1 contract:
    Schema only — no rows are written here. A separate migration in
    Phase 3 will backfill the existing 1:1 ``users.courseId`` teacher
    relationship into this many-to-many shape, after which the
    ``is_course_teacher`` capability flips from its current
    "return False" placeholder to a real query.

Columns are named with underscores (``course_id`` / ``user_id``) to
follow the existing convention for join tables (compare
``user_to_teams``). The application model exposes them as
``courseId`` / ``userId`` via column aliasing, which keeps the SQL
side clean and the Python side consistent with the rest of the ORM.

The composite primary key ``(course_id, user_id)`` gives us natural
idempotency on insert and dedupes accidental double-adds; the
secondary index on ``user_id`` keeps the "which courses does this
teacher own?" lookup cheap (the PK index already covers the other
direction).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '0e43dd2bc856'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "course_teachers",
        sa.Column(
            "course_id",
            sa.Uuid(),
            sa.ForeignKey("courses.courseId", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.userId", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_course_teachers_user",
        "course_teachers",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_course_teachers_user", table_name="course_teachers")
    op.drop_table("course_teachers")
