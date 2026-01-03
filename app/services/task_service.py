
from sqlalchemy.orm import Session
from app.models import TaskType, TaskStatus
import uuid

from app.celery_app import celery_app
from app.crud import tasks as crud_tasks

class TaskService:
    """Service für Task-Handling und Celery-Integration"""
    def __init__(self):
        pass

    def start_and_register_task(
        self,
        db: Session,
        deployment_id: uuid.UUID,
        task_type: TaskType,
        celery_task_name: str,
        celery_args: list,
        queue: str = None,
        extra_fields: dict = None
    ):
        """
        Startet eine Celery-Task, legt einen Task-DB-Eintrag an und gibt die Task-DB-Instanz zurück.
        """
        send_kwargs = {"args": celery_args}
        if queue:
            send_kwargs["queue"] = queue
        result = celery_app.send_task(celery_task_name, **send_kwargs)
        celery_task_id = result.id

        if queue:
            celery_app.control.add_consumer(queue=queue)

        task_data = {
            "deploymentId": deployment_id,
            "type": task_type,
            "status": TaskStatus.PENDING,
            "celeryTaskId": celery_task_id,
        }
        if extra_fields:
            task_data.update(extra_fields)

        task = crud_tasks.create_task(db, task_data)
        return task, celery_task_id

# Singleton-Instanz
task_service = TaskService()
