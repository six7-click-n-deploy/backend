"""
Tasks Router

Read-only access to task information for a deployment. Tasks are created by the
deployment flow itself; this router exposes status and details so the frontend
can render progress.

Every endpoint enforces deployment-level access via `ensure_deployment_access`
to prevent IDOR — without it, any authenticated user could read foreign task
logs (which include Terraform outputs, IPs, etc.).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.database import get_db
from app.models import User
from app.schemas import TaskResponse
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import ensure_deployment_access
from app.crud import tasks as crud_tasks, deployments as crud_deployments

router = APIRouter()


@router.get("/deployment/{deployment_id}", response_model=List[TaskResponse])
def get_deployment_tasks(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """List all tasks for a deployment the caller has access to."""
    deployment = crud_deployments.get_deployment(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found",
        )
    ensure_deployment_access(deployment, current_user, db)
    return crud_tasks.get_tasks(db, deployment_id=deployment_id)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """Fetch a single task; access is checked against its parent deployment."""
    task = crud_tasks.get_task(db, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    deployment = crud_deployments.get_deployment(db, task.deploymentId)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment for task not found",
        )
    ensure_deployment_access(deployment, current_user, db)
    return task
