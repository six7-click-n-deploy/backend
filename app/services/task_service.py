"""Task lifecycle helpers.

Two-phase dispatch:

1. `prepare_task_in_tx` — INSERT a PENDING task row in the caller's
   transaction (no commit, no Celery I/O). The caller commits the
   surrounding business state and the new task row atomically.

2. `dispatch_to_celery` — outside the original TX, push the task to
   RabbitMQ. On success the row's `celeryTaskId` is stamped; on
   failure the row is marked FAILED. Either way the user sees a row
   in the deployment list reflecting reality — no splitbrain.

The previous one-shot `register_new_task` was racy: the policy check,
the `send_task` call, and the row insert were three separate steps with
no atomicity. If the worker happened to start before the row insert,
we'd lose the celery_task_id binding; if `send_task` failed after the
deployment was committed, the deployment would hang in PENDING forever
with no task to track it.
"""
from __future__ import annotations

import logging
import uuid
from typing import Tuple

from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.crud import tasks as crud_tasks
from app.models import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)


class ActiveTaskExistsError(Exception):
    """A PENDING/RUNNING task already exists for this deployment."""


def prepare_task_in_tx(
    db: Session,
    deployment_id: uuid.UUID,
    task_type: TaskType,
) -> Task:
    """Insert a PENDING task row in the caller's transaction.

    Does NOT call `db.commit()` — the caller is responsible for
    committing the surrounding state alongside this row, so that
    deployment + teams + task are all visible (or all rolled back)
    atomically.

    Raises `ActiveTaskExistsError` if the deployment already has a
    PENDING/RUNNING task. The Postgres partial unique index on
    `tasks(deploymentId) WHERE status IN ('PENDING','RUNNING')`
    enforces this at the DB level too — defense in depth.
    """
    existing = crud_tasks.get_tasks(db, deployment_id=deployment_id)
    for task in existing:
        if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            raise ActiveTaskExistsError(
                f"Deployment {deployment_id} already has active task {task.taskId}"
            )

    db_task = Task(
        deploymentId=deployment_id,
        type=task_type,
        status=TaskStatus.PENDING,
        celeryTaskId=None,
    )
    db.add(db_task)
    db.flush()
    db.refresh(db_task)
    return db_task


def dispatch_to_celery(
    db: Session,
    task: Task,
    celery_task_name: str,
    celery_args: list,
) -> Tuple[Task, str]:
    """Push a prepared task to Celery.

    MUST be called after the surrounding TX committed. Runs in fresh
    transactions so the task row is updated independently of any later
    request-handling commit.

    On `send_task` failure the task is marked FAILED and the original
    exception is re-raised — the caller turns that into a 503.
    """
    try:
        result = celery_app.send_task(celery_task_name, args=celery_args)
    except Exception:
        logger.exception(
            "Celery dispatch failed for task %s (deployment %s)",
            task.taskId,
            task.deploymentId,
        )
        task.status = TaskStatus.FAILED
        task.logs = "Failed to dispatch to Celery"
        db.commit()
        db.refresh(task)
        raise

    task.celeryTaskId = result.id
    db.commit()
    db.refresh(task)
    logger.info("Task %s dispatched as celery task %s", task.taskId, result.id)
    return task, result.id


class TaskService:
    """Backwards-compat shim for callers that still expect a service.

    The split helpers above are the recommended API; this wrapper exists
    so the original `task_service.register_new_task` import path keeps
    working in case anything outside the deployments router still uses
    it. New code should call `prepare_task_in_tx` + `dispatch_to_celery`
    directly.
    """

    def register_new_task(
        self,
        db: Session,
        deployment_id: uuid.UUID,
        task_type: TaskType,
        celery_task_name: str,
        celery_args: list,
    ):
        task = prepare_task_in_tx(db, deployment_id, task_type)
        db.commit()
        return dispatch_to_celery(db, task, celery_task_name, celery_args)


task_service = TaskService()
