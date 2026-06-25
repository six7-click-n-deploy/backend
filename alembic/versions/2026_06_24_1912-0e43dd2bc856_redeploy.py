"""redeploy

Revision ID: 0e43dd2bc856
Revises: 73fd123a60aa
Create Date: 2026-06-24 19:12:32.218152

Adds the ``REDEPLOY`` value to the Postgres ``tasktype`` enum so the
single-VM redeploy Celery task can persist its Task row.

Casing note:
  The ``tasktype`` enum was originally defined with UPPERCASE labels
  (see ``2025_12_30_1601-...add_task_table_and_celerytaskid.py``).
  SQLAlchemy's default ``Column(Enum(TaskType))`` (no
  ``values_callable``) serialises an enum member by its ``.name``,
  i.e. ``REDEPLOY`` (uppercase). So we add the uppercase label.

Why this is not auto-generated:
  Alembic's autogenerate diffs the table-level schema but does NOT
  introspect existing Postgres enum values against the Python enum.
  An ``ALTER TYPE ... ADD VALUE`` always needs to be written by hand.

Postgres ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction
block. We commit Alembic's implicit transaction first, then issue the
DDL on a fresh connection. ``IF NOT EXISTS`` makes the migration
idempotent — re-running it (e.g. after a hand-fix) is a no-op.

Downgrade: Postgres has no ``ALTER TYPE ... DROP VALUE``, so we
rebuild the enum without ``REDEPLOY``. Any task rows of type
``REDEPLOY`` are removed first — destructive, dev/local resets only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e43dd2bc856'
down_revision: Union[str, None] = '73fd123a60aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction
    # block. Commit Alembic's implicit transaction first, then run
    # the DDL on a fresh autocommit connection.
    connection = op.get_bind()
    connection.execute(sa.text("COMMIT"))
    connection.execute(sa.text("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'REDEPLOY'"))


def downgrade() -> None:
    # Destructive: removes ``REDEPLOY`` rows before rebuilding the
    # enum. Intended for dev/local resets only.
    #
    # Cast both sides to ``text`` so the comparison works even when
    # the enum hasn't been extended yet (e.g. when downgrading from
    # the previously-empty version of this migration). A plain
    # ``WHERE type = 'REDEPLOY'`` would try to coerce the literal to
    # the enum type and blow up with ``invalid input value for enum
    # tasktype: "REDEPLOY"`` — the very value we're trying to drop.
    op.execute("DELETE FROM tasks WHERE type::text = 'REDEPLOY'")
    op.execute("ALTER TYPE tasktype RENAME TO tasktype_old")
    op.execute(
        "CREATE TYPE tasktype AS ENUM ('DEPLOY', 'UPDATE', 'DESTROY', 'PAUSE', 'RESUME')"
    )
    op.execute(
        "ALTER TABLE tasks ALTER COLUMN type TYPE tasktype "
        "USING type::text::tasktype"
    )
    op.execute("DROP TYPE tasktype_old")
