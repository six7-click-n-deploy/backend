import json
import uuid
import logging

from sqlalchemy.orm import Session, joinedload
from celery.result import AsyncResult

from app.models import TaskType, TaskStatus, Deployment, App
from app.celery_app import celery_app
from app.crud import tasks as crud_tasks
from app.crud import deployments as crud_deployments

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
        """Start Celery task with policy: max 1 active task per deployment."""
        existing = crud_tasks.get_tasks(db, deployment_id=deployment_id)
        for task in existing:
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                raise Exception(
                    f"Deployment has active task (ID: {task.taskId}, Type: {task.type}). "
                    f"Wait for completion before starting new task."
                )

        result = celery_app.send_task(celery_task_name, args=celery_args)

        task = crud_tasks.create_task(db, {
            "deploymentId": deployment_id,
            "type": task_type,
            "status": TaskStatus.PENDING,
            "celeryTaskId": result.id,
        })

        logger.info(f"Task {result.id} created for deployment {deployment_id}")
        return task, result.id

    def destroy_task(self, db: Session, deployment_id: uuid.UUID, force: bool = False):
        """
        Start a Terraform destroy task for a successfully deployed deployment.
        Only allowed when the latest task has status SUCCESS (unless force=True for race-condition recovery).
        """
        deployment = db.query(Deployment).filter(Deployment.deploymentId == deployment_id).first()
        if not deployment:
            raise Exception("Deployment not found")

        if not force:
            latest_status = crud_deployments.get_deployment_status(db, deployment_id)
            if latest_status != "success":
                raise Exception(
                    f"Destroy is only allowed for successfully deployed resources (current status: {latest_status})"
                )

        existing = crud_tasks.get_tasks(db, deployment_id=deployment_id)
        for task in existing:
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                raise Exception(
                    f"Deployment has active task (ID: {task.taskId}). "
                    "Wait for completion before starting destroy."
                )

        app = db.query(App).filter(App.appId == deployment.appId).first()
        if not app or not app.git_link:
            raise Exception("Application git link not configured")

        user_vars = {}
        if deployment.userInputVar:
            try:
                user_vars = json.loads(deployment.userInputVar)
            except Exception:
                pass

        teams_data = crud_deployments.get_deployment_teams_with_members(db, deployment_id)
        teams_dict = {
            team["name"]: [{"email": m["email"]} for m in team["members"]]
            for team in teams_data
        }

        tf_state = crud_deployments.get_latest_tf_state(db, deployment_id)

        task, celery_task_id = self.register_new_task(
            db=db,
            deployment_id=deployment_id,
            task_type=TaskType.DESTROY,
            celery_task_name="tasks.destroy_application",
            celery_args=[
                str(deployment_id),
                app.git_link,
                deployment.releaseTag,
                user_vars,
                teams_dict,
                str(deployment.appId),
                tf_state,
            ],
        )
        return task, celery_task_id

    def cancel_task(self, db: Session, deployment_id: uuid.UUID):
        """
        Cancel or stop the active task for a deployment.
        - PENDING tasks: revoke from queue (they will never start)
        - RUNNING tasks: send SIGTERM to kill the running subprocess
        """
        existing = crud_tasks.get_tasks(db, deployment_id=deployment_id)
        active = [t for t in existing if t.status in [TaskStatus.PENDING, TaskStatus.RUNNING]]

        if not active:
            raise Exception("No active task found for this deployment")

        task = active[-1]
        is_running = task.status == TaskStatus.RUNNING

        celery_app.control.revoke(task.celeryTaskId, terminate=is_running, signal="SIGTERM")

        crud_tasks.update_task(db, task.taskId, {"status": TaskStatus.CANCELLED})
        db.commit()

        logger.info(
            f"Task {task.taskId} ({'terminated' if is_running else 'revoked'}) "
            f"for deployment {deployment_id}"
        )
        return task


# Singleton instance
task_service = TaskService()
