from fastapi import APIRouter, Depends

from app.models import User
from app.schemas import Token, UserResponse
from app.utils.auth import create_access_token
from app.utils.keycloak_auth import get_current_user_keycloak

router = APIRouter()


# ----------------------------------------------------------------
# GET CURRENT USER
# ----------------------------------------------------------------
@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user_keycloak)):
    """
    Get current authenticated user
    - Requires valid JWT token
    """
    return current_user

# ----------------------------------------------------------------
# REFRESH TOKEN
# ----------------------------------------------------------------
@router.post("/refresh", response_model=Token)
def refresh_token(current_user: User = Depends(get_current_user_keycloak)):
    """
    Refresh access token
    - Requires valid JWT token
    """
    access_token = create_access_token(data={"sub": current_user.username})

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }
