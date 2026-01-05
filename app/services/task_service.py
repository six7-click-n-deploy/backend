from sqlalchemy.orm import Session
from celery.result import AsyncResult
from app.models import TaskType, TaskStatus
from app.celery_app import celery_app
from app.crud import tasks as crud_tasks
import uuid
import logging

logger = logging.getLogger(__name__)


class TaskService:
    
    def register_new_task(
        self,
        db: Session,
        deployment_id: uuid.UUID,
        task_type: TaskType,
        celery_task_name: str,
        celery_args: list
    ):
        """Start Celery task with policy: max 1 active task per deployment"""
        
        # Policy check: Only one active task per deployment
        existing = crud_tasks.get_tasks(db, deployment_id=deployment_id)
        for task in existing:
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                raise Exception(
                    f"Deployment has active task (ID: {task.taskId}, Type: {task.type}). "
                    f"Wait for completion before starting new task."
                )
        
        # Send task to default queue
        result = celery_app.send_task(celery_task_name, args=celery_args)
        
        # Create DB entry
        task = crud_tasks.create_task(db, {
            "deploymentId": deployment_id,
            "type": task_type,
            "status": TaskStatus.PENDING,
            "celeryTaskId": result.id,
        })
        
        logger.info(f"Task {result.id} created for deployment {deployment_id}")
        return task, result.id

# Singleton instance
task_service = TaskService()
