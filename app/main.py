from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database import engine, Base
from app.routers import auth, users, apps
from app.config import settings

# ----------------------------------------------------------------
# STARTUP/SHUTDOWN
# ----------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Database connection check
    # Note: Tables are created via Alembic migrations
    # Run: alembic upgrade head
    print("✓ Application started")
    print("ℹ️  Use 'alembic upgrade head' to apply database migrations")
    yield
    # Shutdown
    print("✓ Shutting down...")

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
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(apps.router, prefix="/apps", tags=["Apps"])

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

@app.get("/")
def root():
    return {
        "message": "Backend API is running",
        "docs": "/docs",
        "health": "/health"
    }