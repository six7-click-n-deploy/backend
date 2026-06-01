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
from app.models import TaskStatus, Task, Deployment
from celery.result import AsyncResult


logger = logging.getLogger(__name__)


def clean_log_line(line: str) -> str:
    """Clean up log line by removing ANSI escape codes and normalizing quotes"""
    # Remove ANSI color codes
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    line = ansi_escape.sub('', line)
    
    # Normalize escaped quotes
    line = line.replace('""', '"')
    
    return line


def is_verbose_line(line: str) -> bool:
    """Check if line is very verbose (TRACE/DEBUG from tools)"""
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
    """
    Filter and clean logs for better readability
    - Remove very verbose lines (TRACE/DEBUG)
    - Remove ANSI escape codes
    - Limit to max_lines if too long
    """
    lines = text.split('\n')
    
    # Filter verbose lines
    filtered = []
    for line in lines:
        if line.strip() and not is_verbose_line(line):
            cleaned = clean_log_line(line)
            if cleaned.strip():
                filtered.append(cleaned)
    
    # If still too long, keep important parts
    if len(filtered) > max_lines:
        # Keep first 20 lines and last 30 lines
        important = filtered[:20] + ["..."] + filtered[-30:]
        return "\n".join(important)
    
    return "\n".join(filtered)


def format_logs(logs_data) -> str:
    """Format logs from structured format to readable text"""
    if isinstance(logs_data, list):
        formatted = []
        for entry in logs_data:
            if isinstance(entry, dict):
                timestamp = entry.get("timestamp", "")
                message = entry.get("message", "")
                level = entry.get("level", "INFO")
                icon = _get_icon(level)
                
                # Clean up message (remove ANSI codes)
                message = clean_log_line(message)
                
                # For long messages, truncate intelligently
                if len(message) > 500:
                    # If it looks like multiple lines, keep the structure
                    if '\n' in message:
                        lines = message.split('\n')
                        if len(lines) > 20:
                            # Too many lines, filter verbose ones
                            message = filter_logs(message)
                    else:
                        message = message[:500] + "..."
                
                formatted.append(f"{icon} [{timestamp}] {message}")
            elif isinstance(entry, str):
                formatted.append(clean_log_line(entry))
        return "\n".join(formatted)
    return str(logs_data)


def _get_icon(level: str) -> str:
    """Get icon for log level"""
    icons = {
        "DEBUG": "🔍",
        "INFO": "ℹ️",
        "SUCCESS": "✓",
        "WARNING": "⚠️",
        "ERROR": "❌",
    }
    return icons.get(level, "•")


def start_event_listener():
    """
    Start listening to Celery events from RabbitMQ
    This runs in a background thread/process
    """
    logger.info("Starting Celery event listener...")
    
    def handle_event(event):
        """Process incoming Celery events"""
        event_type = event.get('type')
        celery_task_id = event.get('uuid')
        
        if not celery_task_id:
            return
        
        db: Session = SessionLocal()
        try:
            # Find task by celery_task_id
            tasks = crud_tasks.get_tasks(db, celery_task_id=celery_task_id)
            if not tasks:
                logger.warning(f"Task not found for celery_task_id: {celery_task_id}")
                return
            
            task = tasks[0]
            update_data = {}
            
            # Handle different event types
            if event_type == 'task-started':
                logger.info(f"Task {celery_task_id} started")
                update_data = {
                    "status": TaskStatus.RUNNING,
                    "started_at": datetime.utcnow()
                }

            elif event_type == 'task-succeeded':
                logger.info(f"Task {celery_task_id} succeeded")
            
                # Hole das VOLLSTÄNDIGE Result vom Backend
                async_result = AsyncResult(celery_task_id, app=celery_app)
                result = async_result.result  # ← Hier ist das vollständige Result!
                print(f"DEBUG: result type: {type(result)}")
                print(f"DEBUG: result: {result}")
                
                # Jetzt kannst du damit arbeiten
                if isinstance(result, dict):
                    logs_data = result.get('logs')
                    tf_state = result.get('tf_state')
                    outputs = result.get('terraform_outputs')
                    print(f"DEBUG: logs_data type: {type(logs_data)}, len: {len(logs_data) if logs_data else 'None'}")
                    print(f"DEBUG: tf_state type: {type(tf_state)}, len: {len(tf_state) if tf_state else 'None'}")
                    print(f"DEBUG: outputs type: {type(outputs)}, keys: {list(outputs.keys()) if outputs else 'None'}")
                else:
                    logs_data = None
                    tf_state = None
                    outputs = None
                    print("DEBUG: result is not a dict!")
                
                # Logs sind jetzt entweder schon ein String oder ein List
                if isinstance(logs_data, list):
                    logs_str = json.dumps(logs_data, ensure_ascii=False)
                elif isinstance(logs_data, str):
                    logs_str = logs_data
                else:
                    logs_str = None
                
                update_data = {
                    "status": TaskStatus.SUCCESS,
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
                # Try to extract structured failure data.
                #
                # The worker raises ``Failure`` (see worker/app/tasks.py) whose
                # ``args[0]`` is a JSON payload with logs/tf_state/etc. There
                # are two surface forms depending on whether Celery picks up
                # the exception cleanly:
                #
                #  1. clean pickle round-trip (preferred path):
                #     ``Failure: {"error": ..., ...}`` in the traceback's
                #     final line, AND ``Failure('{"error": ...}')`` in
                #     ``event['exception']`` (Celery uses ``safe_repr``).
                #  2. legacy ``UnpickleableExceptionWrapper`` path (before
                #     Failure had ``__reduce__``): ``Failure('...')``
                #     literally inside the traceback.
                #
                # Match both. Search the exception field first because it's a
                # short, well-defined string; fall back to the full traceback.
                try:
                    import re
                    candidates = [str(exception_type or ''), traceback or '']
                    failure_data = None
                    for haystack in candidates:
                        if not haystack:
                            continue
                        # Form 1a: Failure('<json>')  — repr() / wrapper output
                        # Form 1b: Failure: <json>    — format_exception output
                        match = (
                            re.search(r"Failure\('(.+?)'\)", haystack, re.DOTALL)
                            or re.search(r"Failure:\s*(\{.+?\})\s*$", haystack, re.DOTALL)
                        )
                        if not match:
                            continue
                        json_str = match.group(1)
                        try:
                            failure_data = json.loads(json_str)
                        except json.JSONDecodeError:
                            # Form 1a wraps the JSON in repr(), so embedded
                            # quotes are backslash-escaped. Decode once.
                            try:
                                failure_data = json.loads(
                                    json_str.encode('utf-8').decode('unicode_escape')
                                )
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                continue
                        break

                    if failure_data is not None:
                        # logs
                        logs_data = failure_data.get('logs')
                        if logs_data is not None:
                            update_data['logs'] = json.dumps(logs_data) if isinstance(logs_data, list) else str(logs_data)
                        # error
                        if 'error' in failure_data:
                            current_logs = update_data.get('logs', '') or ''
                            update_data['logs'] = current_logs + f"\n\n❌ Error: {failure_data['error']}"
                        # tf_state
                        if 'tf_state' in failure_data:
                            update_data['tf_state'] = failure_data['tf_state']
                        # commit_info
                        if 'commit_info' in failure_data and failure_data['commit_info']:
                            commit = failure_data['commit_info']
                            if isinstance(commit, dict):
                                commit_str = f"\n📝 Commit: {commit.get('hash', 'N/A')[:8]}"
                                commit_str += f"\n   Message: {commit.get('message', 'N/A')}"
                                commit_str += f"\n   Author: {commit.get('author', 'N/A')}"
                                current_logs = update_data.get('logs', '') or ''
                                update_data['logs'] = current_logs + commit_str
                        # terraform_outputs
                        if 'terraform_outputs' in failure_data:
                            tf_outputs = failure_data['terraform_outputs']
                            if isinstance(tf_outputs, dict):
                                update_data['outputs'] = json.dumps(tf_outputs, indent=2)
                            else:
                                update_data['outputs'] = str(tf_outputs)
                        logger.info(f"[FAILED] Extracted structured failure data for {celery_task_id}: {update_data}")
                    else:
                        raise ValueError("Not structured failure format")
                except Exception as parse_error:
                    logger.warning(f"Could not parse structured failure: {parse_error}")
                    update_data['logs'] = f"Task failed: {exception_type}\n{traceback}"
                logger.info(f"[FAILED] Update data for {celery_task_id}: {update_data}")
            
            elif event_type == 'task-revoked':
                logger.info(f"Task {celery_task_id} revoked")
                update_data = {
                    "status": TaskStatus.CANCELLED,
                    "finished_at": datetime.utcnow()
                }
            
            # Update task in database
            if update_data:
                crud_tasks.update_task(db, task.taskId, update_data)
                logger.info(f"Updated task {task.taskId} from event {event_type}")
        
        except Exception as e:
            logger.error(f"Error processing event {event_type} for task {celery_task_id}: {e}")
        
        finally:
            db.close()
    
    # Connect to RabbitMQ and listen for events
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
        
        logger.info("✓ Celery event listener ready, waiting for events...")
        recv.capture(limit=None, timeout=None, wakeup=True)


if __name__ == "__main__":
    start_event_listener()
