"""
Auth Router - Keycloak Version

Phase 2 cleanup (Bug #25): the redundant ``/auth/me`` endpoint is gone.
Use ``/users/me`` — it returns the richer ``UserWithCourse`` shape and
is the single source of truth for "who am I".

The only path still served here is the public health check, kept so
operators can probe the auth subsystem without authenticating.
"""
from fastapi import APIRouter

router = APIRouter()


# ----------------------------------------------------------------
# HEALTH CHECK
# ----------------------------------------------------------------
@router.get("/health")
def auth_health():
    """Check if auth service is healthy"""
    return {
        "status": "healthy",
        "auth_method": "keycloak",
        "message": "Authentication via Keycloak"
    }
