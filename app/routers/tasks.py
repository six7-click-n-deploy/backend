from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import User, Task, TaskStatus
from app.schemas import TaskCreate, TaskResponse
from app.utils.auth import get_current_user
from app.services.celery_client import send_custom_task, get_task_status

router = APIRouter()

# ----------------------------------------------------------------
# CREATE TASK (Send to Celery Worker)
# ----------------------------------------------------------------
@router.post("/", response_model=TaskResponse)
def create_task(
    task_req: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new task and send it to Celery worker"""
    
    # Send task to Celery
    celery_task_id = send_custom_task(
        task_req.task_type,
        task_req.data,
        current_user.username
    )
    
    # Save task in database
    new_task = Task(
        celery_task_id=celery_task_id,
        user_id=current_user.id,
        task_type=task_req.task_type,
        status=TaskStatus.PENDING
    )
    
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    
    return new_task

# ----------------------------------------------------------------
# LIST USER'S TASKS
# ----------------------------------------------------------------
@router.get("/", response_model=List[TaskResponse])
def list_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all tasks for current user"""
    tasks = db.query(Task).filter(
        Task.user_id == current_user.id
    ).order_by(Task.created_at.desc()).all()
    
    return tasks

# ----------------------------------------------------------------
# GET TASK STATUS
# ----------------------------------------------------------------
@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get task status and result"""
    
    # Get task from DB
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.user_id == current_user.id
    ).first()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Get status from Celery
    celery_status = get_task_status(task.celery_task_id)
    
    # Update task status in DB
    if celery_status["state"] == "SUCCESS":
        task.status = TaskStatus.SUCCESS
        task.result = str(celery_status.get("result"))
    elif celery_status["state"] == "FAILURE":
        task.status = TaskStatus.FAILED
        task.error = str(celery_status.get("error"))
    elif celery_status["state"] in ["PROGRESS", "STARTED"]:
        task.status = TaskStatus.RUNNING
    
    db.commit()
    db.refresh(task)
    
    return task

# ----------------------------------------------------------------
# GET TASK BY CELERY ID
# ----------------------------------------------------------------
@router.get("/celery/{celery_task_id}")
def get_task_by_celery_id(
    celery_task_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get task status directly from Celery"""
    return get_task_status(celery_task_id)