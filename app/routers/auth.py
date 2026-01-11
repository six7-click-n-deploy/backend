from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserRole
from app.schemas import UserCreate, UserLogin, Token, UserResponse
from app.utils.auth import get_password_hash, authenticate_user, create_access_token, get_current_user
from app.crud import users as crud_users

router = APIRouter()

# ----------------------------------------------------------------
# REGISTER
# ----------------------------------------------------------------
@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(user: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user
    - Default role is STUDENT
    - Email and username must be unique
    """
    
    # Check if username exists
    if crud_users.get_user_by_username(db, user.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email exists
    if crud_users.get_user_by_email(db, user.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    new_user = crud_users.create_user(db, user)
    
    # Create access token
    access_token = create_access_token(data={"sub": new_user.username})
    
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

# ----------------------------------------------------------------
# LOGIN
# ----------------------------------------------------------------
@router.post("/login", response_model=Token)
def login(credentials: UserLogin, db: Session = Depends(get_db)):
    """
    Login user and return access token
    - Requires username and password
    """
    
    user = authenticate_user(db, credentials.username, credentials.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(data={"sub": user.username})
    
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

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