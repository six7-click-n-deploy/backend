from celery import Celery
from app.config import settings

# ----------------------------------------------------------------
# CELERY APP (Client-Side)
# ----------------------------------------------------------------
celery_app = Celery(
    "backend",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Berlin",
    enable_utc=True,
)

# ----------------------------------------------------------------
# TASK SIGNATURES (für Worker Service)
# ----------------------------------------------------------------
def send_git_clone_task(repo_url: str, branch: str, repo_id: int):
    """Send git clone task to worker"""
    task = celery_app.send_task(
        "worker.clone_repository",
        args=[repo_url, branch, repo_id],
        queue="default"
    )
    return task.id

def send_terraform_task(action: str, working_dir: str):
    """Send terraform task to worker"""
    task = celery_app.send_task(
        "worker.run_terraform",
        args=[action, working_dir],
        queue="default"
    )
    return task.id

def send_custom_task(task_name: str, *args, **kwargs):
    """Send custom task to worker"""
    task = celery_app.send_task(
        task_name,
        args=args,
        kwargs=kwargs,
        queue="default"
    )
    return task.id

def get_task_status(task_id: str):
    """Get status of a celery task"""
    task = celery_app.AsyncResult(task_id)
    
    response = {
        "task_id": task_id,
        "state": task.state,
    }
    
    if task.state == "PENDING":
        response["status"] = "Task is waiting to be executed"
    elif task.state == "PROGRESS":
        response["status"] = task.info.get("status", "Processing...")
        response["meta"] = task.info
    elif task.state == "SUCCESS":
        response["result"] = task.result
    elif task.state == "FAILURE":
        response["error"] = str(task.info)
    else:
        response["status"] = str(task.info)
    
    return response