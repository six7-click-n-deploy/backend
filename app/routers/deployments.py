from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
import json

from app.database import get_db
from app.models import TaskType, User
from app.schemas import (
    DeploymentCreate, 
    DeploymentResponse, 
    DeploymentWithRelations,
    DeploymentDetail,
    DeploymentTeamResponse,
    DeploymentTeamMember,
    TaskSummary,
    DeploymentOutputs,
)
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import ensure_resource_access
from app.crud import deployments as crud_deployments, teams as crud_teams
from app.services.task_service import task_service

router = APIRouter()


# ----------------------------------------------------------------
# GET ALL DEPLOYMENTS
# ----------------------------------------------------------------
@router.get("/", response_model=List[DeploymentResponse])
def list_deployments(
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[UUID] = None,
    app_id: Optional[UUID] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get all deployments with optional filters
    - **Students**: Can only see their own deployments
    - **Teachers/Admins**: Can see all deployments
    """
    # Students can only see their own deployments
    if current_user.role.value == "student" and not user_id:
        user_id = current_user.userId
    elif current_user.role.value == "student" and user_id != current_user.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own deployments"
        )
    
    deployments = crud_deployments.get_deployments(
        db,
        skip=skip,
        limit=limit,
        user_id=user_id,
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
    ensure_resource_access(deployment.userId, current_user)
    
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
    - **All authenticated users** can create deployments
    - Deployment is initially set to PENDING status
    """
    db_deployment = crud_deployments.create_deployment(db, deployment, current_user.userId)

    # Create teams and collect all user IDs
    user_ids_in_deployment = set()
    
    if deployment.teams:
        # Teams data should already have correct userIds from frontend
        teams_data = [
            {"name": team.name, "userIds": team.userIds}
            for team in deployment.teams
        ]
        crud_teams.create_teams_for_deployment(
            db=db,
            deployment_id=db_deployment.deploymentId,
            teams_data=teams_data
        )
        
        # Collect all user IDs from teams
        for team in deployment.teams:
            user_ids_in_deployment.update(team.userIds)
    
    # Create UserToDeployment entries
    if user_ids_in_deployment:
        crud_deployments.create_user_to_deployments(
            db=db,
            deployment_id=db_deployment.deploymentId,
            user_ids=user_ids_in_deployment
        )
    
    db.commit()
    db.refresh(db_deployment)
    
    try:
        """
        Parse user input variables
        structure: {
            packer : { 
                "variable_name": "value",
                ...
            },
            terraform: { 
                "variable_name": "value",
                ...
            }
        }
        """
        # TODO: Verify and validate user input variables against structure definition
        user_vars = db_deployment.userInputVar if isinstance(db_deployment.userInputVar, dict) else {}
    except Exception:
        user_vars = {}
    
    # Format teams for Terraform (team_name: [user_emails])
    teams_dict = {}
    if deployment.teams:
        for team in deployment.teams:
            # Get user emails from user IDs
            from app.crud import users as crud_users
            team_users = []
            for user_id in team.userIds:
                user = crud_users.get_user(db, user_id)
                if user:
                    team_users.append({"email": user.email})
            
            teams_dict[team.name] = team_users

    # Start deployment task
    task, celery_task_id = task_service.register_new_task(
        db=db,
        deployment_id=db_deployment.deploymentId,
        task_type=TaskType.DEPLOY,
        celery_task_name="tasks.deploy_application",
        celery_args=[
            str(db_deployment.deploymentId),
            str(db_deployment.appId),
            db_deployment.app.git_link,
            db_deployment.releaseTag,
            user_vars,
            teams_dict  # Teams mit User-Emails
        ],
    )

    # Get status and created_at from the task we just created
    status_value = crud_deployments.get_deployment_status(db, db_deployment.deploymentId)
    created_at = crud_deployments.get_deployment_created_at(db, db_deployment.deploymentId)

    # Parse userInputVar for response
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
    ensure_resource_access(deployment.userId, current_user)
    
    success = crud_deployments.delete_deployment(db, deployment_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )
    return None
