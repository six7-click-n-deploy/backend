from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
import json

from app.database import get_db
from app.models import TaskType, User, DeploymentStatus
from app.schemas import DeploymentCreate, DeploymentUpdate, DeploymentResponse, DeploymentWithRelations
from app.utils.auth import get_current_user
from app.utils.permissions import ensure_resource_access
from app.crud import deployments as crud_deployments
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
    status_filter: Optional[DeploymentStatus] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
    return deployments


# ----------------------------------------------------------------
# GET DEPLOYMENT BY ID
# ----------------------------------------------------------------
@router.get("/{deployment_id}", response_model=DeploymentWithRelations)
def get_deployment(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get deployment by ID with all relations"""
    deployment = crud_deployments.get_deployment(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )
    
    # Check access permission
    ensure_resource_access(deployment.userId, current_user)
    
    return deployment


# ----------------------------------------------------------------
# CREATE DEPLOYMENT
# ----------------------------------------------------------------
@router.post("/", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
def create_deployment(
    deployment: DeploymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new deployment
    - **All authenticated users** can create deployments
    - Deployment is initially set to PENDING status
    """
    db_deployment = crud_deployments.create_deployment(db, deployment, current_user.userId)

    try:
        user_vars = json.loads(db_deployment.userInputVar) if db_deployment.userInputVar else {}
    except Exception:
        user_vars = {}

    # Start deployment task
    task, celery_task_id = task_service.start_and_register_task(
        db=db,
        deployment_id=db_deployment.deploymentId,
        task_type=TaskType.DEPLOY,
        celery_task_name="tasks.deploy_application",
        celery_args=[
            str(db_deployment.deploymentId),
            db_deployment.app.git_link,
            db_deployment.releaseTag,
            user_vars
        ],
    )

    return db_deployment

# ----------------------------------------------------------------
# UPDATE DEPLOYMENT
# ----------------------------------------------------------------
@router.put("/{deployment_id}", response_model=DeploymentResponse)
def update_deployment(
    deployment_id: UUID,
    deployment_update: DeploymentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a deployment
    - **Owner or Teacher/Admin** can update
    """
    deployment = crud_deployments.get_deployment(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )
    
    # Check access permission
    ensure_resource_access(deployment.userId, current_user)
    
    updated_deployment = crud_deployments.update_deployment(db, deployment_id, deployment_update)
    return updated_deployment


# ----------------------------------------------------------------
# UPDATE DEPLOYMENT STATUS
# ----------------------------------------------------------------
@router.patch("/{deployment_id}/status", response_model=DeploymentResponse)
def update_deployment_status(
    deployment_id: UUID,
    new_status: DeploymentStatus,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update deployment status
    - **Owner or Teacher/Admin** can update status
    """
    deployment = crud_deployments.get_deployment(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )
    
    # Check access permission
    ensure_resource_access(deployment.userId, current_user)
    
    updated_deployment = crud_deployments.update_deployment_status(db, deployment_id, new_status)
    return updated_deployment


# ----------------------------------------------------------------
# DELETE DEPLOYMENT
# ----------------------------------------------------------------
@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deployment(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
