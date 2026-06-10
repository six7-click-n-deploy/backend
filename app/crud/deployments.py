import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, exists
from sqlalchemy.orm import Session, joinedload

from app.models import Deployment, Task, TaskStatus, TaskType, Team, User, UserToDeployment
from app.schemas import DeploymentCreate


def _is_destroyed_subq():
    """Correlated EXISTS: deployment has a successful DESTROY task."""
    return exists().where(
        (Task.deploymentId == Deployment.deploymentId)
        & (Task.type == TaskType.DESTROY)
        & (Task.status == TaskStatus.SUCCESS)
    )


def count_active_user_deployments(db: Session, user_id: UUID) -> int:
    """Number of non-destroyed deployments owned by user."""
    return (
        db.query(Deployment)
        .filter(Deployment.userId == user_id)
        .filter(~_is_destroyed_subq())
        .count()
    )


def has_active_user_deployment(db: Session, user_id: UUID) -> bool:
    return (
        db.query(Deployment.deploymentId)
        .filter(Deployment.userId == user_id)
        .filter(~_is_destroyed_subq())
        .first() is not None
    )


def get_deployment(
    db: Session,
    deployment_id: UUID,
    include_deleted: bool = False,
) -> Deployment | None:
    """Get deployment by ID. Hides soft-deleted rows by default.

    ``include_deleted=True`` is for the rare audit/restore lookup; the
    HTTP API never sets it.
    """
    q = db.query(Deployment).filter(Deployment.deploymentId == deployment_id)
    if not include_deleted:
        q = q.filter(Deployment.deleted_at.is_(None))
    return q.first()


def get_deployment_with_details(
    db: Session,
    deployment_id: UUID,
    include_deleted: bool = False,
) -> Deployment | None:
    """Get deployment by ID with all relations loaded. Hides soft-deleted by default."""
    q = (
        db.query(Deployment)
        .options(
            joinedload(Deployment.user),
            joinedload(Deployment.app),
            joinedload(Deployment.teams),
        )
        .filter(Deployment.deploymentId == deployment_id)
    )
    if not include_deleted:
        q = q.filter(Deployment.deleted_at.is_(None))
    return q.first()


def get_latest_task(db: Session, deployment_id: UUID) -> Task | None:
    """Get the most recent task for a deployment"""
    return (
        db.query(Task)
        .filter(Task.deploymentId == deployment_id)
        .order_by(desc(Task.created_at))
        .first()
    )


def get_first_task(db: Session, deployment_id: UUID) -> Task | None:
    """Get the first task for a deployment (when deployment was created)"""
    from sqlalchemy import asc
    return (
        db.query(Task)
        .filter(Task.deploymentId == deployment_id)
        .order_by(asc(Task.created_at))
        .first()
    )


def get_deployment_status(db: Session, deployment_id: UUID) -> str | None:
    """Effective deployment status, derived from the latest task.

    The bare ``task.status`` (pending/running/success/failed/cancelled) is
    not enough by itself: a destroy task in flight should surface as
    ``destroying`` and a finished destroy as ``destroyed``, neither of
    which exists as a stored enum value. We synthesize them on the fly
    from ``(task.type, task.status)``.

    Pause/resume follow the same pattern:

    * ``(PAUSE,  pending|running)`` → ``pausing``
    * ``(PAUSE,  success)``         → ``paused``
    * ``(RESUME, pending|running)`` → ``resuming``
    * ``(RESUME, success)``         → falls through to ``success``,
      so a resumed deployment behaves identically to a fresh deploy
      from the lifecycle matrix's point of view (PAUSE is offered
      again, RESUME isn't).

    PAUSE/RESUME ``failed`` and ``cancelled`` are intentionally NOT
    rewritten — they bleed through unchanged so the user sees the
    pause/resume itself broke (vs. the original deploy succeeded).
    Destroy is still available from ``failed`` to recover.

    Returns ``None`` if the deployment has no tasks yet.
    """
    task = get_latest_task(db, deployment_id)
    if task is None or task.status is None:
        return None

    raw_status = task.status.value
    raw_type = task.type.value if task.type else None

    if raw_type == "destroy":
        if raw_status in ("pending", "running"):
            return "destroying"
        if raw_status == "success":
            return "destroyed"
        # failed/cancelled bleed through unchanged so the user sees that
        # the destroy itself broke (vs. the original deploy succeeded).
    elif raw_type == "pause":
        if raw_status in ("pending", "running"):
            return "pausing"
        if raw_status == "success":
            return "paused"
        if raw_status == "failed":
            # Distinguish a *pause* failure from a *deploy* failure —
            # the OpenStack resources are still running, only the
            # stop-instances pass broke. The user shouldn't see this
            # as a generic "failed" because the deployment itself is
            # unaffected; the lifecycle matrix below still allows
            # destroy / pause-retry / resume from this synthetic
            # state.
            return "pause_failed"
        # cancelled bleeds through unchanged.
    elif raw_type == "resume":
        if raw_status in ("pending", "running"):
            return "resuming"
        if raw_status == "failed":
            # Same logic as pause_failed: the SHUTOFF instances are
            # still there, only the start pass tripped. Surface that
            # so the user can retry resume or destroy without first
            # being scared by a generic failed badge.
            return "resume_failed"
        # On resume success the deployment is "running again" — we
        # let raw_status = "success" pass through so the lifecycle
        # matrix treats it like a fresh successful deploy.
    return raw_status


def get_deployment_created_at(db: Session, deployment_id: UUID):
    """Get deployment creation time from first task"""
    task = get_first_task(db, deployment_id)
    return task.created_at if task else None


def get_team_members(db: Session, team_id: UUID) -> list[User]:
    """Get all users in a team"""
    from app.models import UserToTeam
    user_ids = (
        db.query(UserToTeam.userId)
        .filter(UserToTeam.teamId == team_id)
        .all()
    )
    user_ids = [uid[0] for uid in user_ids]

    if not user_ids:
        return []

    return db.query(User).filter(User.userId.in_(user_ids)).all()


def get_deployment_teams_with_members(db: Session, deployment_id: UUID) -> list[dict[str, Any]]:
    """Get all teams for a deployment with their members"""
    teams = db.query(Team).filter(Team.deploymentId == deployment_id).all()

    result = []
    for team in teams:
        members = get_team_members(db, team.teamId)
        result.append({
            "teamId": team.teamId,
            "name": team.name,
            "members": [
                {
                    "userId": member.userId,
                    "email": member.email,
                    "username": member.username
                }
                for member in members
            ]
        })

    return result


def get_deployment_outputs(db: Session, deployment_id: UUID) -> dict[str, Any] | None:
    """Get parsed Terraform outputs from the latest successful task"""
    task = (
        db.query(Task)
        .filter(Task.deploymentId == deployment_id)
        .filter(Task.outputs.isnot(None))
        .order_by(desc(Task.created_at))
        .first()
    )

    if task and task.outputs:
        try:
            return json.loads(task.outputs)
        except json.JSONDecodeError:
            return None
    return None


def get_deployments(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    user_id: UUID | None = None,
    member_user_id: UUID | None = None,
    app_id: UUID | None = None,
    status: str | None = None,
    include_deleted: bool = False,
) -> list[Deployment]:
    """Get deployments with optional filters. Hides soft-deleted by default.

    ``user_id`` filters by deployment owner (``Deployment.userId``).

    ``member_user_id`` filters by **either** owner or membership: a
    deployment matches when the user is the creator OR appears in any
    team's ``UserToTeam`` row OR has a direct ``UserToDeployment``
    mapping. Used by the listing endpoint so a student sees deployments
    they were added to without seeing every deployment in the system.
    Mutually exclusive with ``user_id``; if both are set ``user_id``
    wins (caller bug, but the stricter filter is the safer default).
    """
    query = db.query(Deployment)

    if not include_deleted:
        # Backed by the partial index ix_deployments_live so this stays
        # cheap even with many rows.
        query = query.filter(Deployment.deleted_at.is_(None))
    if user_id:
        query = query.filter(Deployment.userId == user_id)
    elif member_user_id:
        # Owner OR team member OR direct mapping. Use a UNION-ish
        # approach via a subquery on teamIds the user belongs to so
        # the OR doesn't explode into a cartesian.
        from app.models import UserToTeam
        member_team_ids = (
            db.query(UserToTeam.teamId).filter(UserToTeam.userId == member_user_id)
        )
        member_deployment_ids_via_teams = (
            db.query(Team.deploymentId).filter(Team.teamId.in_(member_team_ids))
        )
        member_deployment_ids_direct = (
            db.query(UserToDeployment.deploymentId)
            .filter(UserToDeployment.userId == member_user_id)
        )
        query = query.filter(
            (Deployment.userId == member_user_id)
            | (Deployment.deploymentId.in_(member_deployment_ids_via_teams))
            | (Deployment.deploymentId.in_(member_deployment_ids_direct))
        )
    if app_id:
        query = query.filter(Deployment.appId == app_id)

    # Order by deploymentId (UUID) - could also join with Task for created_at ordering
    query = query.order_by(desc(Deployment.deploymentId))

    deployments = query.offset(skip).limit(limit).all()

    # Filter by status if specified (requires checking latest task)
    if status:
        filtered = []
        for deployment in deployments:
            latest_task = get_latest_task(db, deployment.deploymentId)
            if latest_task and latest_task.status and latest_task.status.value == status:
                filtered.append(deployment)
        return filtered

    return deployments


def get_deployments_with_status(db: Session, deployments: list[Deployment]) -> list[dict[str, Any]]:
    """Enrich deployments with their current status from latest task"""
    result = []
    for deployment in deployments:
        status = get_deployment_status(db, deployment.deploymentId)
        result.append({
            "deployment": deployment,
            "status": status
        })
    return result


def create_deployment(db: Session, deployment: DeploymentCreate, user_id: UUID) -> Deployment:
    """Insert a deployment row in the current transaction.

    Does NOT commit — the caller is expected to also insert teams/tasks
    in the same TX and commit once at the end. This is necessary so the
    advisory lock acquired at the start of the request stays held across
    all related inserts.
    """
    # Convert userInputVar dict to JSON string for database storage
    user_input_var_json = None
    if deployment.userInputVar is not None:
        user_input_var_json = json.dumps(deployment.userInputVar)

    db_deployment = Deployment(
        name=deployment.name,
        appId=deployment.appId,
        userId=user_id,
        releaseTag=deployment.releaseTag,
        userInputVar=user_input_var_json,
    )
    db.add(db_deployment)
    db.flush()
    db.refresh(db_deployment)
    return db_deployment

def soft_delete_deployment(db: Session, deployment_id: UUID) -> bool:
    """Mark a deployment as deleted without removing the row.

    Sets ``deleted_at = utcnow()`` so default queries skip it. The
    related tasks/teams/user-mappings are intentionally untouched —
    they're useful for audit and the partial-unique index on active
    tasks already prevents the deployment from accepting new work.

    Returns ``False`` if the deployment doesn't exist (or was already
    deleted), ``True`` on a successful soft-delete.
    """
    db_deployment = get_deployment(db, deployment_id)
    if not db_deployment:
        return False
    db_deployment.deleted_at = datetime.utcnow()
    db.commit()
    return True


# Back-compat alias. The old hard-delete contract no longer exists; any
# remaining caller now soft-deletes. New code should call
# ``soft_delete_deployment`` directly.
delete_deployment = soft_delete_deployment


def create_user_to_deployments(
    db: Session,
    deployment_id: UUID,
    user_ids: set[UUID],
) -> list[UserToDeployment]:
    """
    Create UserToDeployment entries for multiple users
    """
    user_to_deployments = []

    for user_id in user_ids:
        user_to_deployment = UserToDeployment(
            userId=user_id,
            deploymentId=deployment_id
        )
        db.add(user_to_deployment)
        user_to_deployments.append(user_to_deployment)

    return user_to_deployments
