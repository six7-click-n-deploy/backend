"""
Celery Event Listener Service

Listens to Celery events from RabbitMQ and updates task status in database.
Worker sends events, backend receives and processes them.
"""

import logging
import json
import re
from datetime import datetime
from celery.events import EventReceiver
from sqlalchemy.orm import Session
from app.celery_app import celery_app
from app.database import SessionLocal
from app.crud import tasks as crud_tasks
from app.models import TaskStatus, Task, Deployment

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
                result = event.get('result', {})
                
                update_data = {
                    "status": TaskStatus.SUCCESS,
                    "finished_at": datetime.utcnow()
                }
                
                # Extract result data
                if isinstance(result, dict):
                    logs_data = None
                    
                    if 'logs' in result:
                        logs_data = result['logs']
                        update_data['logs'] = json.dumps(logs_data) if isinstance(logs_data, list) else str(logs_data)
                    
                    if 'tf_state' in result:
                        update_data['tf_state'] = result['tf_state']
                    
                    if 'terraform_outputs' in result:
                        tf_outputs = result['terraform_outputs']
                        if isinstance(tf_outputs, dict):
                            update_data['outputs'] = json.dumps(tf_outputs, indent=2)
                        else:
                            update_data['outputs'] = str(tf_outputs)
                    
                    # Send structured logs to Elasticsearch
                    if logs_data and isinstance(logs_data, list):
                        pass
                    
                    if 'commit_info' in result and result['commit_info']:
                        commit = result['commit_info']
                        if isinstance(commit, dict):
                            commit_str = f"\n📝 Commit: {commit.get('hash', 'N/A')[:8]}"
                            commit_str += f"\n   Message: {commit.get('message', 'N/A')}"
                            commit_str += f"\n   Author: {commit.get('author', 'N/A')}"
                            current_logs = update_data.get('logs', '')
                            update_data['logs'] = current_logs + commit_str
            
            elif event_type == 'task-failed':
                logger.info(f"Task {celery_task_id} failed")
                exception_msg = event.get('exception', 'Unknown error')
                
                update_data = {
                    "status": TaskStatus.FAILED,
                    "finished_at": datetime.utcnow()
                }
                
                # Try to extract structured failure data from exception message
                try:
                    import re
                    
                    # Exception format: JSON string in the message
                    match = re.search(r"DeploymentFailure\('(.+)'\)$", exception_msg, re.DOTALL)
                    if match:
                        json_str = match.group(1)
                        
                        # Unescape the JSON string properly
                        # The string comes escaped from Python, so we need to unescape it
                        json_str = json_str.encode('utf-8').decode('unicode_escape')
                        
                        failure_data = json.loads(json_str)
                        
                        # Format logs
                        logs_data = None
                        if 'logs' in failure_data and failure_data['logs']:
                            logs_data = failure_data['logs']
                            update_data['logs'] = json.dumps(logs_data) if isinstance(logs_data, list) else str(logs_data)
                        
                        # Send to Elasticsearch
                        if logs_data and isinstance(logs_data, list):
                            pass
                        
                        # Add error message
                        if 'error' in failure_data:
                            current_logs = update_data.get('logs', '')
                            update_data['logs'] = current_logs + f"\n\n❌ Error: {failure_data['error']}"
                        
                        # Extract tf_state
                        if 'tf_state' in failure_data and failure_data['tf_state']:
                            update_data['tf_state'] = failure_data['tf_state']
                        
                        # Extract commit_info
                        if 'commit_info' in failure_data and failure_data['commit_info']:
                            commit = failure_data['commit_info']
                            if isinstance(commit, dict):
                                commit_str = f"\n📝 Commit: {commit.get('hash', 'N/A')[:8]}"
                                commit_str += f"\n   Message: {commit.get('message', 'N/A')}"
                                commit_str += f"\n   Author: {commit.get('author', 'N/A')}"
                                current_logs = update_data.get('logs', '')
                                update_data['logs'] = current_logs + commit_str
                        
                        # Extract terraform_outputs
                        if 'terraform_outputs' in failure_data and failure_data['terraform_outputs']:
                            tf_outputs = failure_data['terraform_outputs']
                            if isinstance(tf_outputs, dict):
                                update_data['outputs'] = json.dumps(tf_outputs, indent=2)
                            else:
                                update_data['outputs'] = str(tf_outputs)
                        
                        logger.info(f"Extracted structured failure data for task {celery_task_id}")
                    else:
                        raise ValueError("Not structured failure format")
                    
                except Exception as parse_error:
                    # Not structured format - use simple error message
                    logger.warning(f"Could not parse structured failure: {parse_error}")
                    update_data['logs'] = f"Task failed: {exception_msg}"
            
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
