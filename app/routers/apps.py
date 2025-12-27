from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.database import get_db
from app.models import User
from app.schemas import AppCreate, AppUpdate, AppResponse, AppWithUser
from app.utils.auth import get_current_user
from app.utils.permissions import ensure_resource_access
from app.crud import apps as crud_apps

router = APIRouter()


# ----------------------------------------------------------------
# GET ALL APPS
# ----------------------------------------------------------------
@router.get("/", response_model=List[AppResponse])
def list_apps(
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all apps with optional user filter
    - **Students**: Can only see their own apps
    - **Teachers/Admins**: Can see all apps
    """
    # Students can only see their own apps
    if current_user.role.value == "student" and not user_id:
        user_id = current_user.userId
    elif current_user.role.value == "student" and user_id != current_user.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own apps"
        )
    
    apps = crud_apps.get_apps(db, skip=skip, limit=limit, user_id=user_id)
    return apps


# ----------------------------------------------------------------
# GET APP BY ID
# ----------------------------------------------------------------
@router.get("/{app_id}", response_model=AppWithUser)
def get_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get app by ID"""
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Check access permission
    ensure_resource_access(app.userId, current_user)
    
    return app


# ----------------------------------------------------------------
# CREATE APP
# ----------------------------------------------------------------
@router.post("/", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
def create_app(
    app: AppCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new app
    - **All authenticated users** can create apps
    """
    return crud_apps.create_app(db, app, current_user.userId)


# ----------------------------------------------------------------
# UPDATE APP
# ----------------------------------------------------------------
@router.put("/{app_id}", response_model=AppResponse)
def update_app(
    app_id: UUID,
    app_update: AppUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update an app
    - **Owner or Teacher/Admin** can update
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Check access permission
    ensure_resource_access(app.userId, current_user)
    
    updated_app = crud_apps.update_app(db, app_id, app_update)
    return updated_app


# ----------------------------------------------------------------
# DELETE APP
# ----------------------------------------------------------------
@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete an app
    - **Owner or Teacher/Admin** can delete
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Check access permission
    ensure_resource_access(app.userId, current_user)
    
    success = crud_apps.delete_app(db, app_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    return None
