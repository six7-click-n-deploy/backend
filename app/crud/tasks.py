from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from app.models import Task, TaskType, TaskStatus
from app.schemas import TaskCreate, TaskUpdate

def get_task(db: Session, task_id: UUID) -> Optional[Task]:
    """Get task by ID"""
    return db.query(Task).filter(Task.taskId == task_id).first()


def get_tasks(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    deployment_id: Optional[UUID] = None,
    celery_task_id: Optional[str] = None,
    status: Optional[TaskStatus] = None
) -> List[Task]:
    """Get tasks with optional filters"""
    query = db.query(Task)
    if deployment_id:
        query = query.filter(Task.deploymentId == deployment_id)
    if celery_task_id:
        query = query.filter(Task.celeryTaskId == celery_task_id)
    if status:
        query = query.filter(Task.status == status)
    return query.offset(skip).limit(limit).all()


def create_task(db: Session, task: TaskCreate) -> Task:
    """Create a new task"""
    db_task = Task(**task)
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


def update_task(db: Session, task_id: UUID, task_update: TaskUpdate) -> Optional[Task]:
    """Update task information"""
    db_task = get_task(db, task_id)
    if not db_task:
        return None
    update_data = task_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_task, field, value)
    db.commit()
    db.refresh(db_task)
    return db_task


def delete_task(db: Session, task_id: UUID) -> bool:
    """Delete a task"""
    db_task = get_task(db, task_id)
    if not db_task:
        return False
    db.delete(db_task)
    db.commit()
    return True
