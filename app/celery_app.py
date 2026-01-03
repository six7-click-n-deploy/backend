from celery import Celery
from celery.signals import worker_ready

from app.crud import deployments as crud_deployments
from app.database import SessionLocal

from app.config import settings

celery_app = Celery(
    "backend",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)

@worker_ready.connect
def bootstrap_worker(sender, **kwargs):
    db = SessionLocal()
    active_deployments = crud_deployments.get_deployments(db, status='running')
    db.close()
    for d in active_deployments:
        celery_app.control.add_consumer(queue=f"deployment-{d.deploymentId}", destination=[sender.hostname])
