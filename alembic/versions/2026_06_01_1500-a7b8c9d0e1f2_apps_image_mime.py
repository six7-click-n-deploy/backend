"""apps: add image_mime so the API can serve a proper data-URL

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-01 15:00:00.000000

The ``apps.image`` column has stored the raw bytes of an uploaded
logo since the schema's first migration, but there's nowhere to
record what *kind* of image it is. The API can't render the bytes
back into a ``<img>`` without a mime-type prefix in the data-URL,
so we add ``image_mime VARCHAR(64)`` alongside.

Existing rows have ``image=NULL`` (no logo was ever uploaded
through the broken old flow), so backfilling isn't needed — the
column is nullable and the new upload path always sets both.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("apps", sa.Column("image_mime", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("apps", "image_mime")
