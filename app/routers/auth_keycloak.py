"""
Auth Router - Keycloak Version
Minimal endpoints: Only /me for user info
Login/Register/Refresh handled by Keycloak
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import UserResponse
from app.utils.keycloak_auth import get_current_user_keycloak

router = APIRouter()

# ----------------------------------------------------------------
# GET CURRENT USER
# ----------------------------------------------------------------
@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user_keycloak)):
    """
    Get current authenticated user info
    
    - Requires valid Keycloak JWT token in Authorization header
    - Returns user data from local database
    - User is created/updated automatically via Just-in-Time Provisioning
    """
    return current_user

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
