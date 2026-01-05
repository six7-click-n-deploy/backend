"""
Celery Event Listener Service

Listens to Celery events from RabbitMQ and updates task status in database.
Worker sends events, backend receives and processes them.
"""

import logging
from datetime import datetime
from celery import Celery
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
                
                # Extract result data
                if isinstance(result, dict):
                    if 'logs' in result:
                        update_data['logs'] = str(result['logs'])
                    if 'tf_state' in result:
                        update_data['tf_state'] = result['tf_state']
                    if 'terraform_outputs' in result:
                        update_data['outputs'] = str(result['terraform_outputs'])
            
            elif event_type == 'task-failed':
                logger.info(f"Task {celery_task_id} failed")
                exception = event.get('exception', 'Unknown error')
                
                update_data = {
                    "status": TaskStatus.FAILED,
                    "finished_at": datetime.utcnow(),
                    "logs": f"Task failed: {exception}"
                }
            
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
