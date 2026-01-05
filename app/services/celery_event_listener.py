"""
Celery Event Listener Service

Listens to Celery events from RabbitMQ and updates task status in database.
Worker sends events, backend receives and processes them.
"""

import logging
from datetime import datetime
from celery.events import EventReceiver
from sqlalchemy.orm import Session
from app.celery_app import celery_app
from app.database import SessionLocal
from app.crud import tasks as crud_tasks
from app.models import TaskStatus

logger = logging.getLogger(__name__)


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
                
                # Extract result data (only for successful tasks now)
                if isinstance(result, dict):
                    if 'logs' in result:
                        # logs is an array of strings, join them
                        logs_array = result['logs']
                        if isinstance(logs_array, list):
                            update_data['logs'] = '\n'.join(logs_array)
                        else:
                            update_data['logs'] = str(logs_array)
                    
                    if 'tf_state' in result:
                        update_data['tf_state'] = result['tf_state']
                    
                    if 'terraform_outputs' in result:
                        tf_outputs = result['terraform_outputs']
                        if isinstance(tf_outputs, dict):
                            import json
                            update_data['outputs'] = json.dumps(tf_outputs, indent=2)
                        else:
                            update_data['outputs'] = str(tf_outputs)
                    
                    if 'commit_info' in result:
                        commit_info = result['commit_info']
                        if commit_info:
                            current_logs = update_data.get('logs', '')
                            update_data['logs'] = current_logs + f"\n\nCommit Info: {commit_info}"
            
            elif event_type == 'task-failed':
                logger.info(f"Task {celery_task_id} failed")
                exception_msg = event.get('exception', 'Unknown error')
                traceback = event.get('traceback', '')
                
                update_data = {
                    "status": TaskStatus.FAILED,
                    "finished_at": datetime.utcnow()
                }
                
                # Try to extract DeploymentFailure JSON data from exception message
                try:
                    import json
                    import re
                    
                    # Exception format: "DeploymentFailure('{...}')" with escaped quotes
                    # Extract JSON string between DeploymentFailure(' and ')
                    match = re.search(r"DeploymentFailure\('(.+)'\)$", exception_msg, re.DOTALL)
                    if match:
                        json_str = match.group(1)
                        # Unescape double quotes: "" -> "
                        json_str = json_str.replace('""', '"')
                        failure_data = json.loads(json_str)
                        
                        # Extract logs array - they're objects with timestamp and message
                        if 'logs' in failure_data and isinstance(failure_data['logs'], list):
                            log_lines = []
                            for log_entry in failure_data['logs']:
                                if isinstance(log_entry, dict):
                                    timestamp = log_entry.get('timestamp', '')
                                    message = log_entry.get('message', '')
                                    # Format: [timestamp] message
                                    log_lines.append(f"[{timestamp}] {message}")
                                elif isinstance(log_entry, str):
                                    log_lines.append(log_entry)
                            update_data['logs'] = '\n'.join(log_lines)
                        
                        # Add error message
                        if 'error' in failure_data:
                            current_logs = update_data.get('logs', '')
                            update_data['logs'] = current_logs + f"\n\n❌ Error: {failure_data['error']}"
                        
                        # Extract tf_state
                        if 'tf_state' in failure_data and failure_data['tf_state']:
                            update_data['tf_state'] = failure_data['tf_state']
                        
                        # Extract commit_info (it's a dict)
                        if 'commit_info' in failure_data and failure_data['commit_info']:
                            commit = failure_data['commit_info']
                            if isinstance(commit, dict):
                                commit_str = f"Commit: {commit.get('hash', 'N/A')}\nMessage: {commit.get('message', 'N/A')}\nAuthor: {commit.get('author', 'N/A')}\nDate: {commit.get('date', 'N/A')}"
                                current_logs = update_data.get('logs', '')
                                update_data['logs'] = current_logs + f"\n\n📝 {commit_str}"
                        
                        # Extract terraform_outputs
                        if 'terraform_outputs' in failure_data and failure_data['terraform_outputs']:
                            tf_outputs = failure_data['terraform_outputs']
                            if isinstance(tf_outputs, dict):
                                update_data['outputs'] = json.dumps(tf_outputs, indent=2)
                            else:
                                update_data['outputs'] = str(tf_outputs)
                        
                        logger.info(f"✓ Extracted DeploymentFailure data for task {celery_task_id}")
                    else:
                        raise ValueError("Could not extract DeploymentFailure JSON")
                    
                except Exception as e:
                    # Not a DeploymentFailure or JSON parse failed - use simple error message
                    logger.warning(f"Could not parse exception as DeploymentFailure: {e}")
                    # Only show exception message, not the full traceback (too verbose)
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
