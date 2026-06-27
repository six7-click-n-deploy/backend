import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    admin_apps,
    apps,
    auth_keycloak,
    courses,
    dashboard,
    deployments,
    openstack_credentials,
    openstack_resources,
    quotas,
    tasks,
    teams,
    users,
)
from app.services.celery_event_listener import start_event_listener
from app.services.deployment_pubsub import pubsub
from app.services.reconciler import run_reconciler

logger = logging.getLogger(__name__)


# ``DISABLE_BACKGROUND_TASKS`` is the test-suite escape hatch. The
# pytest harness spins up the full ``app`` object **per** ``TestClient``
# context (we have ``client`` / ``admin_client`` / ``student_client``
# / ``unauth_client`` fixtures, each entering its own ``with`` block).
# Each lifespan-startup spawns a Celery event-listener thread that
# blocks on ``amqp.read_frame()`` and a reconciler asyncio task — both
# daemons, neither cleaned up between tests. After ~5 tests we'd have
# 5 listeners holding broker sockets and competing with the test
# session for the small SQLAlchemy connection pool; the pool would
# exhaust and the next legit DB query would deadlock. Production
# starts ``app`` exactly once, so it never sees this stacking.
#
# Setting ``DISABLE_BACKGROUND_TASKS=1`` in the test environment
# short-circuits the lifespan body: the FastAPI app is fully wired,
# routes are registered, but no Celery / reconciler threads are
# created. Tests that genuinely need to exercise event-listener
# behaviour stub it at the unit level (see
# ``tests/test_celery_infra_translation.py``).
def _background_tasks_disabled() -> bool:
    return os.getenv("DISABLE_BACKGROUND_TASKS", "").lower() in ("1", "true", "yes")


# ----------------------------------------------------------------
# STARTUP/SHUTDOWN
# ----------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=== Application Starting ===")
    logger.info("ℹ️  Use 'alembic upgrade head' to apply database migrations")

    if _background_tasks_disabled():
        # Test path: keep ``app`` fully functional but skip the Celery
        # listener + reconciler. Without this, repeated ``TestClient``
        # constructs in the suite stack up threads that block on
        # broker reads forever and exhaust the DB connection pool.
        logger.info(
            "DISABLE_BACKGROUND_TASKS set — skipping Celery listener "
            "and reconciler (test mode)"
        )
        try:
            yield
        finally:
            logger.info("=== Application Shutting Down (test mode) ===")
        return

    # Bind the FastAPI event loop to the deployment pubsub *before*
    # spawning the Celery listener. The listener thread pushes into
    # the pubsub from a non-asyncio thread; without a loop reference
    # those pushes would be silently dropped.
    pubsub.set_loop(asyncio.get_running_loop())
    logger.info("Deployment pubsub bound to event loop")

    # Start Celery event listener in background thread
    listener_thread = threading.Thread(target=start_event_listener, daemon=True)
    listener_thread.start()
    logger.info("Celery event listener started in background")

    # Reconciler is the safety net for events the listener missed (lost
    # event, backend restart during dispatch, broker hiccups). It runs
    # as an asyncio task so we can cancel it cleanly on shutdown.
    reconciler_task = asyncio.create_task(run_reconciler())
    logger.info("Reconciler loop scheduled")

    logger.info("Application started")

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
        logger.info("Shutdown complete")


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
app.include_router(admin_apps.router, prefix="/admin", tags=["Admin"])
app.include_router(deployments.router, prefix="/deployments", tags=["Deployments"])
app.include_router(tasks.router, prefix="/tasks", tags=["Tasks"])
app.include_router(teams.router, prefix="/teams", tags=["Teams"])
app.include_router(quotas.router, prefix="/quotas", tags=["Quotas"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
app.include_router(openstack_credentials.router, tags=["OpenStack Credentials"])
# Read-API für OpenStack-Resourcen (Networks, Flavors, Images, ...).
# Wird vom Wizard für Value-Help-Dropdowns genutzt, damit User keine
# UUIDs aus Horizon abtippen müssen.
app.include_router(
    openstack_resources.router,
    prefix="/me/openstack/resources",
    tags=["OpenStack Resources"],
)


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
