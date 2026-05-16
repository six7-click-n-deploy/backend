"""
Tasks Router

API endpoints for querying task status and managing tasks.
Tasks are created by deployments, this router provides read-only access to task information.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import tasks as crud_tasks
from app.database import get_db
from app.models import User
from app.schemas import TaskResponse
from app.utils.keycloak_auth import get_current_user_keycloak

router = APIRouter()


# ----------------------------------------------------------------
# GET TASKS FOR DEPLOYMENT
# ----------------------------------------------------------------
@router.get("/deployment/{deployment_id}", response_model=list[TaskResponse])
def get_deployment_tasks(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get all tasks for a specific deployment
    """
    tasks = crud_tasks.get_tasks(db, deployment_id=deployment_id)
    return tasks


# ----------------------------------------------------------------
# GET TASK BY ID
# ----------------------------------------------------------------
@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get task by ID
    """
    task = crud_tasks.get_task(db, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )
    return task
