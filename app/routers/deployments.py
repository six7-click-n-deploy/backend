import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import deployments as crud_deployments
from app.crud import locks as crud_locks
from app.crud import openstack_credentials as crud_openstack_credentials
from app.crud import teams as crud_teams
from app.database import get_db
from app.models import TaskType, User
from app.schemas import (
    DeploymentCreate,
    DeploymentDetail,
    DeploymentOutputs,
    DeploymentResponse,
    DeploymentTeamMember,
    DeploymentTeamResponse,
    TaskSummary,
)
from app.services import task_service as task_service_module
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import ensure_deployment_access

logger = logging.getLogger(__name__)
router = APIRouter()


# ----------------------------------------------------------------
# GET ALL DEPLOYMENTS
# ----------------------------------------------------------------
@router.get("/", response_model=list[DeploymentResponse])
def list_deployments(
    skip: int = 0,
    limit: int = 100,
    app_id: UUID | None = None,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get all deployments owned by the current user.

    Listing is always scoped to the requester regardless of role — teachers
    and admins still only see their own deployments in the index. Cross-user
    access happens explicitly through `GET /deployments/{deployment_id}`,
    which is gated by `ensure_resource_access`.
    """
    deployments = crud_deployments.get_deployments(
        db,
        skip=skip,
        limit=limit,
        user_id=current_user.userId,
        app_id=app_id,
        status=status_filter
    )

    # Enrich with status and created_at from tasks
    result = []
    for deployment in deployments:
        status_value = crud_deployments.get_deployment_status(db, deployment.deploymentId)
        created_at = crud_deployments.get_deployment_created_at(db, deployment.deploymentId)
        # Parse userInputVar JSON string back to dict if it exists
        user_input_var_parsed = None
        if deployment.userInputVar:
            try:
                user_input_var_parsed = json.loads(deployment.userInputVar)
            except json.JSONDecodeError:
                user_input_var_parsed = None

        result.append(DeploymentResponse(
            deploymentId=deployment.deploymentId,
            name=deployment.name,
            appId=deployment.appId,
            userId=deployment.userId,
            releaseTag=deployment.releaseTag,
            userInputVar=user_input_var_parsed,
            status=status_value,
            created_at=created_at,
        ))

    return result


# ----------------------------------------------------------------
# GET DEPLOYMENT BY ID (Full Details)
# ----------------------------------------------------------------
@router.get("/{deployment_id}", response_model=DeploymentDetail)
def get_deployment(
    deployment_id: UUID,
    include_logs: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get deployment by ID with full details including:
    - User and App relations
    - Teams with members
    - Latest task status
    - Terraform outputs
    - Optionally: full logs (use include_logs=true)
    """
    deployment = crud_deployments.get_deployment_with_details(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )

    # Check access permission
    ensure_deployment_access(deployment, current_user, db)

    # Get latest task
    latest_task = crud_deployments.get_latest_task(db, deployment_id)
    task_summary = None
    logs = None

    if latest_task:
        task_summary = TaskSummary(
            taskId=latest_task.taskId,
            type=latest_task.type,
            status=latest_task.status,
            started_at=latest_task.started_at,
            finished_at=latest_task.finished_at,
            created_at=latest_task.created_at,
        )
        if include_logs:
            logs = latest_task.logs

    # Get teams with members
    teams_data = crud_deployments.get_deployment_teams_with_members(db, deployment_id)
    teams = [
        DeploymentTeamResponse(
            teamId=team["teamId"],
            name=team["name"],
            members=[
                DeploymentTeamMember(
                    userId=member["userId"],
                    email=member["email"],
                    username=member["username"]
                )
                for member in team["members"]
            ]
        )
        for team in teams_data
    ]

    # Get outputs
    outputs_data = crud_deployments.get_deployment_outputs(db, deployment_id)
    outputs = DeploymentOutputs(raw=outputs_data) if outputs_data else None

    # Get status and created_at from tasks
    status_value = crud_deployments.get_deployment_status(db, deployment_id)
    created_at = crud_deployments.get_deployment_created_at(db, deployment_id)

    # Parse userInputVar JSON string back to dict if it exists
    user_input_var_parsed = None
    if deployment.userInputVar:
        try:
            user_input_var_parsed = json.loads(deployment.userInputVar)
        except json.JSONDecodeError:
            user_input_var_parsed = None

    return DeploymentDetail(
        deploymentId=deployment.deploymentId,
        name=deployment.name,
        appId=deployment.appId,
        userId=deployment.userId,
        releaseTag=deployment.releaseTag,
        userInputVar=user_input_var_parsed,
        status=status_value,
        created_at=created_at,
        user=deployment.user,
        app=deployment.app,
        teams=teams,
        latest_task=task_summary,
        outputs=outputs,
        logs=logs,
    )


# ----------------------------------------------------------------
# CREATE DEPLOYMENT
# ----------------------------------------------------------------
@router.post("/", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
def create_deployment(
    deployment: DeploymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Create a new deployment

    Atomicity: a per-user advisory lock serializes credential mutation
    with deployment dispatch. The deployment row, teams, user mappings,
    and the initial PENDING task row are all inserted in a single
    transaction, so the user can never end up with a deployment row
    that has no matching task. Celery dispatch happens AFTER commit;
    if it fails, the task row is flipped to FAILED so the deployment
    surfaces an honest error instead of hanging in PENDING forever.
    """
    # Per-user lock — serializes against PUT /me/openstack-credentials
    # and any other concurrent POST /deployments from this user. Held
    # until the next COMMIT/ROLLBACK on this connection.
    crud_locks.acquire_user_xact_lock(db, current_user.userId)

    db_deployment = crud_deployments.create_deployment(
        db, deployment, current_user.userId
    )

    user_ids_in_deployment = set()
    if deployment.teams:
        teams_data = [
            {"name": team.name, "userIds": team.userIds}
            for team in deployment.teams
        ]
        crud_teams.create_teams_for_deployment(
            db=db,
            deployment_id=db_deployment.deploymentId,
            teams_data=teams_data,
        )
        for team in deployment.teams:
            user_ids_in_deployment.update(team.userIds)

    if user_ids_in_deployment:
        crud_deployments.create_user_to_deployments(
            db=db,
            deployment_id=db_deployment.deploymentId,
            user_ids=user_ids_in_deployment,
        )

    # Parse user input variables
    try:
        user_vars = (
            json.loads(db_deployment.userInputVar) if db_deployment.userInputVar else {}
        )
    except Exception:
        user_vars = {}

    # Format teams for Terraform (team_name: [user_emails])
    teams_dict = {}
    if deployment.teams:
        from app.crud import users as crud_users
        for team in deployment.teams:
            team_users = []
            for user_id in team.userIds:
                user = crud_users.get_user(db, user_id)
                if user:
                    team_users.append({"email": user.email})
            teams_dict[team.name] = team_users

    # Per-user OpenStack credentials are required to deploy. The envelope
    # carries ciphertext only — the worker decrypts in-process. Reading
    # this inside the locked TX guarantees the envelope matches whatever
    # credential row a concurrent PUT might have committed: PUT is
    # serialized behind us by the same advisory lock.
    try:
        openstack_envelope = crud_openstack_credentials.get_dispatch_envelope(
            db, current_user.userId
        )
    except crud_openstack_credentials.NoCredentialError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"reason": "openstack_credentials_missing"},
        )

    # Insert PENDING task row in the SAME transaction as the deployment.
    # If anything below fails before commit, the rollback drops both —
    # no orphan rows.
    try:
        task = task_service_module.prepare_task_in_tx(
            db,
            deployment_id=db_deployment.deploymentId,
            task_type=TaskType.DEPLOY,
        )
    except task_service_module.ActiveTaskExistsError:
        # Should be impossible on a freshly inserted deployment, but
        # the partial unique index would catch it too.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment already has an active task",
        )

    # Atomic commit: deployment + teams + user_to_deployments + task row.
    # The advisory lock is released here.
    db.commit()
    db.refresh(db_deployment)
    db.refresh(task)

    # Dispatch to Celery OUTSIDE the locked TX. On failure the task row
    # is flipped to FAILED in a fresh TX (handled in dispatch_to_celery)
    # and we surface 503 — the deployment row stays, but the user sees
    # an obvious failure instead of an eternal PENDING.
    try:
        task, _celery_id = task_service_module.dispatch_to_celery(
            db,
            task=task,
            celery_task_name="tasks.deploy_application",
            celery_args=[
                str(db_deployment.deploymentId),
                str(db_deployment.appId),
                db_deployment.app.git_link,
                db_deployment.releaseTag,
                user_vars,
                teams_dict,
                openstack_envelope,
            ],
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not dispatch deployment task — please retry",
        )

    status_value = crud_deployments.get_deployment_status(db, db_deployment.deploymentId)
    created_at = crud_deployments.get_deployment_created_at(db, db_deployment.deploymentId)

    user_input_var_parsed = None
    if db_deployment.userInputVar:
        try:
            user_input_var_parsed = json.loads(db_deployment.userInputVar)
        except json.JSONDecodeError:
            user_input_var_parsed = None

    return DeploymentResponse(
        deploymentId=db_deployment.deploymentId,
        name=db_deployment.name,
        appId=db_deployment.appId,
        userId=db_deployment.userId,
        releaseTag=db_deployment.releaseTag,
        userInputVar=user_input_var_parsed,
        status=status_value,
        created_at=created_at,
    )


# ----------------------------------------------------------------
# DELETE DEPLOYMENT
# ----------------------------------------------------------------
@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deployment(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Delete a deployment
    - **Owner or Teacher/Admin** can delete
    """
    deployment = crud_deployments.get_deployment(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )

    # Check access permission
    ensure_deployment_access(deployment, current_user, db)

    success = crud_deployments.delete_deployment(db, deployment_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )
    return None
