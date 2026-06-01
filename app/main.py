import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    apps,
    auth_keycloak,
    courses,
    deployments,
    openstack_credentials,
    quotas,
    tasks,
    teams,
    users,
)
from app.services.celery_event_listener import start_event_listener
from app.services.reconciler import run_reconciler

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------
# STARTUP/SHUTDOWN
# ----------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=== Application Starting ===")
    logger.info("ℹ️  Use 'alembic upgrade head' to apply database migrations")

    # Start Celery event listener in background thread
    listener_thread = threading.Thread(target=start_event_listener, daemon=True)
    listener_thread.start()
    logger.info("✓ Celery event listener started in background")

    # Reconciler is the safety net for events the listener missed (lost
    # event, backend restart during dispatch, broker hiccups). It runs
    # as an asyncio task so we can cancel it cleanly on shutdown.
    reconciler_task = asyncio.create_task(run_reconciler())
    logger.info("✓ Reconciler loop scheduled")

    logger.info("✓ Application started")

    try:
        yield
    finally:
        # Shutdown
        logger.info("=== Application Shutting Down ===")
        reconciler_task.cancel()
        try:
            await reconciler_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Reconciler task raised on shutdown")
        logger.info("✓ Shutdown complete")


# ----------------------------------------------------------------
# FASTAPI APP
# ----------------------------------------------------------------
app = FastAPI(
    title="Backend API",
    description="FastAPI Backend with Auth, Git & Celery Integration",
    version="1.0.0",
    lifespan=lifespan
)

# ----------------------------------------------------------------
# CORS
# ----------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------
# ROUTERS
# ----------------------------------------------------------------
app.include_router(auth_keycloak.router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(courses.router, prefix="/courses", tags=["Courses"])
app.include_router(apps.router, prefix="/apps", tags=["Apps"])
app.include_router(deployments.router, prefix="/deployments", tags=["Deployments"])
app.include_router(tasks.router, prefix="/tasks", tags=["Tasks"])
app.include_router(teams.router, prefix="/teams", tags=["Teams"])
app.include_router(quotas.router, prefix="/quotas", tags=["Quotas"])
app.include_router(openstack_credentials.router, tags=["OpenStack Credentials"])


# ----------------------------------------------------------------
# HEALTH CHECK
# ----------------------------------------------------------------
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "backend-api",
        "version": "1.0.0"
    }
