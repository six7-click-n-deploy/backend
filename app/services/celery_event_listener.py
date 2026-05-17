"""
Celery Event Listener Service

Listens to Celery events from RabbitMQ and updates task status in database.
Worker sends events, backend receives and processes them.
"""

import logging
import json
import re
import ast
from datetime import datetime
from celery.events import EventReceiver
from sqlalchemy.orm import Session
from app.celery_app import celery_app
from app.database import SessionLocal
from app.crud import tasks as crud_tasks
from app.models import TaskStatus, TaskType, Task, Deployment
from celery.result import AsyncResult


logger = logging.getLogger(__name__)


def clean_log_line(line: str) -> str:
    """Remove ANSI escape codes and normalize quotes from a log line."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    line = ansi_escape.sub('', line)
    line = line.replace('""', '"')
    return line


def is_verbose_line(line: str) -> bool:
    """Return True if the line is noisy TRACE/DEBUG output from tools."""
    verbose_patterns = [
        r"\[TRACE\]",
        r"\[DEBUG\]",
        r"json.dumps",
        r"plugingetter",
        r"github-getter",
        r"discovering plugins",
        r"BinaryInstallationOptions",
        r"ListInstallationsOptions",
    ]
    return any(re.search(pattern, line, re.IGNORECASE) for pattern in verbose_patterns)


def filter_logs(text: str, max_lines: int = 100) -> str:
    """Filter verbose lines and cap total line count for readability."""
    lines = text.split('\n')
    filtered = []
    for line in lines:
        if line.strip() and not is_verbose_line(line):
            cleaned = clean_log_line(line)
            if cleaned.strip():
                filtered.append(cleaned)

    if len(filtered) > max_lines:
        filtered = filtered[:20] + ["..."] + filtered[-30:]

    return "\n".join(filtered)


def format_logs(logs_data) -> str:
    """Format structured log entries to readable text."""
    if isinstance(logs_data, list):
        formatted = []
        for entry in logs_data:
            if isinstance(entry, dict):
                timestamp = entry.get("timestamp", "")
                message = entry.get("message", "")
                level = entry.get("level", "INFO")
                icon = _get_icon(level)

                message = clean_log_line(message)

                if len(message) > 500:
                    if '\n' in message:
                        lines = message.split('\n')
                        if len(lines) > 20:
                            message = filter_logs(message)
                    else:
                        message = message[:500] + "..."

                formatted.append(f"{icon} [{timestamp}] {message}")
            elif isinstance(entry, str):
                formatted.append(clean_log_line(entry))
        return "\n".join(formatted)
    return str(logs_data)


def _get_icon(level: str) -> str:
    icons = {
        "DEBUG": "[DEBUG]",
        "INFO": "[INFO]",
        "SUCCESS": "[OK]",
        "WARNING": "[WARN]",
        "ERROR": "[ERROR]",
    }
    return icons.get(level, "[-]")


def start_event_listener():
    """Start listening to Celery events from RabbitMQ. Runs in a background thread."""
    logger.info("Starting Celery event listener...")

    def handle_event(event):
        """Process an incoming Celery event and update the corresponding task in the DB."""
        event_type = event.get('type')
        celery_task_id = event.get('uuid')

        if not celery_task_id:
            return

        db: Session = SessionLocal()
        try:
            tasks = crud_tasks.get_tasks(db, celery_task_id=celery_task_id)
            if not tasks:
                logger.warning(f"Task not found for celery_task_id: {celery_task_id}")
                return

            task = tasks[0]
            update_data = {}

            if event_type == 'task-started':
                logger.info(f"Task {celery_task_id} started")
                update_data = {
                    "status": TaskStatus.RUNNING,
                    "started_at": datetime.utcnow()
                }

            elif event_type == 'task-succeeded':
                logger.info(f"Task {celery_task_id} succeeded")

                # Race condition guard: cancel arrived just as terraform apply finished.
                # Auto-trigger destroy to clean up any created resources.
                if task.status == TaskStatus.CANCELLED:
                    logger.warning(
                        f"Task {celery_task_id} succeeded but was already CANCELLED — "
                        "triggering auto-destroy to clean up resources"
                    )
                    try:
                        from app.services.task_service import task_service
                        _new_task, _cid = task_service.destroy_task(db, task.deploymentId, force=True)
                        logger.info(f"Auto-destroy task created ({_cid}) for deployment {task.deploymentId}")
                    except Exception as destroy_err:
                        logger.error(f"Auto-destroy failed for deployment {task.deploymentId}: {destroy_err}")
                    return

                final_status = TaskStatus.DESTROYED if task.type == TaskType.DESTROY else TaskStatus.SUCCESS

                async_result = AsyncResult(celery_task_id, app=celery_app)
                result = async_result.result

                if isinstance(result, dict):
                    logs_data = result.get('logs')
                    tf_state = result.get('tf_state')
                    outputs = result.get('terraform_outputs')
                else:
                    logs_data = None
                    tf_state = None
                    outputs = None

                if isinstance(logs_data, list):
                    logs_str = json.dumps(logs_data, ensure_ascii=False)
                elif isinstance(logs_data, str):
                    logs_str = logs_data
                else:
                    logs_str = None

                update_data = {
                    "status": final_status,
                    "finished_at": datetime.utcnow(),
                    "logs": logs_str,
                    "tf_state": tf_state if isinstance(tf_state, str) else json.dumps(tf_state) if tf_state else None,
                    "outputs": outputs if isinstance(outputs, str) else json.dumps(outputs) if outputs else None,
                }

            elif event_type == 'task-failed':
                logger.info(f"Task {celery_task_id} failed")
                exception_type = event.get('exception', 'Unknown error')
                traceback = event.get('traceback', '')
                update_data = {
                    "status": TaskStatus.FAILED,
                    "finished_at": datetime.utcnow(),
                    "logs": None,
                    "tf_state": None,
                    "outputs": None,
                }
                try:
                    match = re.search(r"Failure\('(.+)'\)", traceback, re.DOTALL)
                    if match:
                        json_str = match.group(1)
                        try:
                            failure_data = json.loads(json_str)
                        except json.JSONDecodeError:
                            json_str = json_str.encode('utf-8').decode('unicode_escape')
                            failure_data = json.loads(json_str)

                        logs_data = failure_data.get('logs')
                        if logs_data is not None:
                            update_data['logs'] = json.dumps(logs_data) if isinstance(logs_data, list) else str(logs_data)

                        if 'error' in failure_data:
                            current_logs = update_data.get('logs', '') or ''
                            update_data['logs'] = current_logs + f"\n\nError: {failure_data['error']}"

                        if 'tf_state' in failure_data:
                            update_data['tf_state'] = failure_data['tf_state']

                        if 'commit_info' in failure_data and failure_data['commit_info']:
                            commit = failure_data['commit_info']
                            if isinstance(commit, dict):
                                commit_str = f"\nCommit: {commit.get('hash', 'N/A')[:8]}"
                                commit_str += f"\n   Message: {commit.get('message', 'N/A')}"
                                commit_str += f"\n   Author: {commit.get('author', 'N/A')}"
                                current_logs = update_data.get('logs', '') or ''
                                update_data['logs'] = current_logs + commit_str

                        if 'terraform_outputs' in failure_data:
                            tf_outputs = failure_data['terraform_outputs']
                            update_data['outputs'] = json.dumps(tf_outputs, indent=2) if isinstance(tf_outputs, dict) else str(tf_outputs)

                        logger.info(f"Extracted structured failure data for {celery_task_id}")
                    else:
                        raise ValueError("Not structured failure format")
                except Exception as parse_error:
                    logger.warning(f"Could not parse structured failure: {parse_error}")
                    update_data['logs'] = f"Task failed: {exception_type}\n{traceback}"

            elif event_type == 'task-revoked':
                logger.info(f"Task {celery_task_id} revoked")
                update_data = {
                    "status": TaskStatus.CANCELLED,
                    "finished_at": datetime.utcnow()
                }

            if update_data:
                crud_tasks.update_task(db, task.taskId, update_data)
                logger.info(f"Updated task {task.taskId} with event {event_type}")

        except Exception as e:
            logger.error(f"Error processing event {event_type} for task {celery_task_id}: {e}")

        finally:
            db.close()

    with celery_app.connection() as connection:
        recv = EventReceiver(
            connection,
            handlers={
                'task-started': handle_event,
                'task-succeeded': handle_event,
                'task-failed': handle_event,
                'task-revoked': handle_event,
            }
        )

        logger.info("Celery event listener ready, waiting for events...")
        recv.capture(limit=None, timeout=None, wakeup=True)


if __name__ == "__main__":
    start_event_listener()
