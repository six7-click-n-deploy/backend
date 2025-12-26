from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.database import get_db
from app.models import App, User
from app.schemas import AppCreate, AppUpdate, AppResponse, AppDetailResponse
from app.utils.auth import get_current_user

router = APIRouter()

# ----------------------------------------------------------------
# LIST ALL APPS
# ----------------------------------------------------------------
@router.get("/", response_model=List[AppDetailResponse])
def list_apps(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all apps.
    Returns a list of all apps including image data.
    """
    apps = db.query(App).all()
    return apps


# ----------------------------------------------------------------
# CREATE APP
# ----------------------------------------------------------------
@router.post("/", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
def create_app(
    app_data: AppCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new app.
    The userId is automatically set from the authenticated user.
    """
    new_app = App(
        name=app_data.name,
        description=app_data.description,
        git_link=app_data.git_link,
        image=app_data.image,
        userId=current_user.userId
    )
    
    db.add(new_app)
    db.commit()
    db.refresh(new_app)
    
    return new_app


# ----------------------------------------------------------------
# GET APP BY ID
# ----------------------------------------------------------------
@router.get("/{app_id}", response_model=AppDetailResponse)
def get_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific app by ID.
    Returns full details including image.
    """
    app = db.query(App).filter(App.appId == app_id).first()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    return app


# ----------------------------------------------------------------
# UPDATE APP
# ----------------------------------------------------------------
@router.put("/{app_id}", response_model=AppResponse)
def update_app(
    app_id: UUID,
    app_data: AppUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update an existing app.
    Only the owner or an admin can update the app.
    """
    app = db.query(App).filter(App.appId == app_id).first()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Check permission: only owner or admin
    if app.userId != current_user.userId and current_user.role.value != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this app"
        )
    
    # Update only provided fields
    update_data = app_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(app, field, value)
    
    db.commit()
    db.refresh(app)
    
    return app


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
    Delete an app.
    Only the owner or an admin can delete the app.
    """
    app = db.query(App).filter(App.appId == app_id).first()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Check permission: only owner or admin
    if app.userId != current_user.userId and current_user.role.value != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this app"
        )
    
    db.delete(app)
    db.commit()
    
    return None
