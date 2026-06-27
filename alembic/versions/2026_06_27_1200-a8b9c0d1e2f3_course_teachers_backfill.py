"""Backfill course_teachers from existing users.courseId teacher rows.

Revision ID: a8b9c0d1e2f3
Revises: f1a2b3c4d5e6
Create Date: 2026-06-27 12:00:00.000000

Phase 3 follow-up to the schema-only migration ``f1a2b3c4d5e6``.

Before Phase 3, the platform's notion of "teacher of a course" was
implicit: any user with ``role = 'teacher'`` who was enrolled in a
course (``users.courseId = <course>``) was effectively treated as a
teacher of that course by the staff-blanket gate in the router layer.
With Bug #6 + Bug #15 removed, that blanket access is gone — each
course now has an explicit teacher roster in the ``course_teachers``
join table created by the previous migration.

To avoid revoking access from anyone today, we backfill the join
table from the legacy single-FK relationship. For every user with
``role = 'teacher'`` who currently sits in a course, we insert one
``(course_id, user_id)`` row. The composite PK + ``ON CONFLICT DO
NOTHING`` make this re-runnable without duplicates, and the source
filter ``courseId IS NOT NULL`` skips orphan-teacher rows that have
no course context (a teacher who hasn't enrolled in their own course
yet sees no behavior change — they were already locked out of
foreign courses by the per-resource gate).

Note on schema-naming: SQLAlchemy attribute names use camelCase
(``User.courseId``) while the underlying SQL column for users is
also ``courseId`` (no aliasing on the User table — only the join
table ``course_teachers`` uses snake_case column names). The
literal SQL below reflects that mix.

Downgrade is a no-op: we don't track which rows were inserted by
this backfill versus by the application later, so blanket-deleting
would erase real data. Operators who need to roll back should
delete the join table via the schema migration ``f1a2b3c4d5e6``'s
downgrade instead.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8b9c0d1e2f3'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The User table is not aliased — ``users.courseId`` and
    # ``users.userId`` are the literal column names. The join table
    # uses snake_case (``course_teachers.course_id`` /
    # ``course_teachers.user_id``) per the existing convention.
    # ``ON CONFLICT DO NOTHING`` makes the backfill idempotent so
    # re-running the upgrade after a manual seed never errors out.
    op.execute(
        sa.text(
            """
            INSERT INTO course_teachers (course_id, user_id)
            SELECT DISTINCT u."courseId", u."userId"
            FROM users u
            WHERE u.role = 'teacher'
              AND u."courseId" IS NOT NULL
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Intentionally a no-op — see module docstring. The schema-only
    # migration ``f1a2b3c4d5e6`` is the right place to drop the table.
    pass
