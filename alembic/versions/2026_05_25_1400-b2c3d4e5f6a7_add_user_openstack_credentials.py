"""add user openstack credentials

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-25 14:00:00.000000

Adds the per-user OpenStack credential store. Secret material
(`encrypted_identifier`, `encrypted_secret`) is Fernet-ciphertext stored as
BYTEA. Non-secret connection metadata (auth_url, project_id, etc.) is plaintext.
The 1:1 relationship to `users` is enforced by a UNIQUE constraint on userId.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OPENSTACK_AUTH_TYPE = postgresql.ENUM(
    "v3applicationcredential",
    "password",
    name="openstackauthtype",
    create_type=False,
)


def upgrade() -> None:
    OPENSTACK_AUTH_TYPE.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "user_openstack_credentials",
        sa.Column("credentialId", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("userId", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auth_type", OPENSTACK_AUTH_TYPE, nullable=False),
        sa.Column("auth_url", sa.String(), nullable=False),
        sa.Column("region_name", sa.String(), nullable=True),
        sa.Column("interface", sa.String(), nullable=True),
        sa.Column("identity_api_version", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("project_name", sa.String(), nullable=True),
        sa.Column("user_domain_name", sa.String(), nullable=True),
        sa.Column("project_domain_name", sa.String(), nullable=True),
        sa.Column("encrypted_identifier", sa.LargeBinary(), nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(), nullable=True),
        sa.Column("last_validation_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["userId"],
            ["users.userId"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("userId", name="uq_user_openstack_credentials_userId"),
    )
    op.create_index(
        "ix_user_openstack_credentials_userId",
        "user_openstack_credentials",
        ["userId"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_openstack_credentials_userId", table_name="user_openstack_credentials")
    op.drop_table("user_openstack_credentials")
    OPENSTACK_AUTH_TYPE.drop(op.get_bind(), checkfirst=True)
