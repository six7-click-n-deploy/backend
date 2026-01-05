"""
Tasks Router

API endpoints for querying task status and managing tasks.
Tasks are created by deployments, this router provides read-only access to task information.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.database import get_db
from app.models import User
from app.schemas import TaskResponse
from app.utils.auth import get_current_user
from app.crud import tasks as crud_tasks
from app.services.task_service import get_task_status

router = APIRouter()


# ----------------------------------------------------------------
# GET TASKS FOR DEPLOYMENT
# ----------------------------------------------------------------
@router.get("/deployment/{deployment_id}", response_model=List[TaskResponse])
def get_deployment_tasks(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
    current_user: User = Depends(get_current_user)
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


# ----------------------------------------------------------------
# GET TASK STATUS FROM CELERY
# ----------------------------------------------------------------
@router.get("/{task_id}/status")
def get_task_celery_status(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get real-time task status from Celery Result Backend
    
    Returns current state, progress, and logs from the running worker
    """
    # Get task from DB to get celeryTaskId
    task = crud_tasks.get_task(db, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )
    
    # Query Celery Result Backend for current status
    celery_status = get_task_status(task.celeryTaskId)
    
    return {
        "task_id": str(task.taskId),
        "celery_task_id": task.celeryTaskId,
        "deployment_id": str(task.deploymentId),
        "type": task.type,
        "db_status": task.status,
        "celery_status": celery_status
    }
