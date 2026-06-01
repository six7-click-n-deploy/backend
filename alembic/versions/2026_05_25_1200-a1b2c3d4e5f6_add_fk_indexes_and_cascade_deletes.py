"""add fk indexes and cascade deletes

Revision ID: a1b2c3d4e5f6
Revises: fc856a18c82d
Create Date: 2026-05-25 12:00:00.000000

Adds B-tree indexes on every foreign-key column (Postgres does NOT auto-index
FKs, which made joins from the parent side and ON DELETE checks hit sequential
scans) and switches the FK constraints whose parent owns the child rows to
ON DELETE CASCADE so that deleting a Deployment / Team / User actually cleans
up the dependent rows instead of failing with an integrity error.

CASCADE edges (child rows deleted with the parent):
  - tasks.deploymentId            -> deployments.deploymentId
  - user_to_deployments.userId    -> users.userId
  - user_to_deployments.deploymentId -> deployments.deploymentId
  - teams.deploymentId            -> deployments.deploymentId
  - user_to_teams.userId          -> users.userId
  - user_to_teams.teamId          -> teams.teamId

Constraints kept on default (RESTRICT/NO ACTION):
  - users.courseId, apps.userId, deployments.userId, deployments.appId
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'fc856a18c82d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, fk_name, referenced_table, referenced_column)
CASCADE_FKS = [
    ('tasks', 'deploymentId', 'tasks_deploymentId_fkey', 'deployments', 'deploymentId'),
    ('user_to_deployments', 'userId', 'user_to_deployments_userId_fkey', 'users', 'userId'),
    ('user_to_deployments', 'deploymentId', 'user_to_deployments_deploymentId_fkey', 'deployments', 'deploymentId'),
    ('teams', 'deploymentId', 'teams_deploymentId_fkey', 'deployments', 'deploymentId'),
    ('user_to_teams', 'userId', 'user_to_teams_userId_fkey', 'users', 'userId'),
    ('user_to_teams', 'teamId', 'user_to_teams_teamId_fkey', 'teams', 'teamId'),
]

# (index_name, table, column)
FK_INDEXES = [
    ('ix_users_courseId', 'users', 'courseId'),
    ('ix_apps_userId', 'apps', 'userId'),
    ('ix_deployments_userId', 'deployments', 'userId'),
    ('ix_deployments_appId', 'deployments', 'appId'),
    ('ix_tasks_deploymentId', 'tasks', 'deploymentId'),
    ('ix_user_to_deployments_userId', 'user_to_deployments', 'userId'),
    ('ix_user_to_deployments_deploymentId', 'user_to_deployments', 'deploymentId'),
    ('ix_teams_deploymentId', 'teams', 'deploymentId'),
    ('ix_user_to_teams_userId', 'user_to_teams', 'userId'),
    ('ix_user_to_teams_teamId', 'user_to_teams', 'teamId'),
]


def upgrade() -> None:
    # 1) Indexes on every FK column.
    for index_name, table, column in FK_INDEXES:
        op.create_index(index_name, table, [column])

    # 2) Recreate FK constraints with ON DELETE CASCADE where the parent owns the child rows.
    for table, column, fk_name, ref_table, ref_column in CASCADE_FKS:
        op.drop_constraint(fk_name, table, type_='foreignkey')
        op.create_foreign_key(
            fk_name,
            table,
            ref_table,
            [column],
            [ref_column],
            ondelete='CASCADE',
        )


def downgrade() -> None:
    for table, column, fk_name, ref_table, ref_column in CASCADE_FKS:
        op.drop_constraint(fk_name, table, type_='foreignkey')
        op.create_foreign_key(
            fk_name,
            table,
            ref_table,
            [column],
            [ref_column],
        )

    for index_name, table, _column in FK_INDEXES:
        op.drop_index(index_name, table_name=table)
