from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.database import get_db
from app.models import User, UserRole
from app.schemas import (
    UserResponse, UserWithCourse, UserUpdate, UserPasswordUpdate,
    UserStatistics
)
from app.utils.keycloak_auth import get_current_user_keycloak, search_keycloak_users
from app.utils.auth import verify_password
from app.utils.permissions import get_current_admin, get_current_teacher_or_admin, ensure_resource_access
from app.crud import users as crud_users
from app.crud import apps as crud_apps
from app.crud import deployments as crud_deployments

router = APIRouter()

# ----------------------------------------------------------------
# GET CURRENT USER
# ----------------------------------------------------------------
@router.get("/me", response_model=UserWithCourse)
def get_me(current_user: User = Depends(get_current_user_keycloak)):
    """Get current authenticated user with course information"""
    return current_user

# ----------------------------------------------------------------
# GET ALL USERS (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.get("/", response_model=List[UserResponse])
def list_users(
    skip: int = 0,
    limit: int = 100,
    role: Optional[UserRole] = None,
    course_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Get all users with optional filters
    - **Requires**: TEACHER or ADMIN role
    """
    users = crud_users.get_users(db, skip=skip, limit=limit, role=role, course_id=course_id)
    return users

# ----------------------------------------------------------------
# SEARCH USERS FROM KEYCLOAK
# ----------------------------------------------------------------
@router.get("/search")
def search_users_keycloak(
    query: str,
    limit: int = 10,
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Search users directly from Keycloak by username, email, or name
    - **Requires**: TEACHER or ADMIN role
    - Returns users from Keycloak (not local DB)
    
    Response:
    - id: Keycloak user ID
    - username: Username
    - email: Email address
    - firstName: First name
    - lastName: Last name
    - enabled: Account enabled status
    """
    if not query or len(query) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query must be at least 2 characters"
        )
    
    users = search_keycloak_users(query, limit)
    return users

# ----------------------------------------------------------------
# GET USER BY ID
# ----------------------------------------------------------------
@router.get("/{user_id}", response_model=UserWithCourse)
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get user by ID
    - **Students**: Can only view their own profile
    - **Teachers/Admins**: Can view any profile
    """
    user = crud_users.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check access permission
    if current_user.role == UserRole.STUDENT and user_id != current_user.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own profile"
        )
    
    return user

# ----------------------------------------------------------------
# GET USER STATISTICS
# ----------------------------------------------------------------
@router.get("/{user_id}/statistics", response_model=UserStatistics)
def get_user_statistics(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get user statistics
    - **Owner or Teacher/Admin** can view
    """
    user = crud_users.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check access permission
    ensure_resource_access(user_id, current_user)
    
    # Get statistics
    apps = crud_apps.get_apps(db, user_id=user_id, limit=1000)
    deployments = crud_deployments.get_deployments(db, user_id=user_id, limit=1000)
    
    return UserStatistics(
        total_apps=len(apps),
        total_deployments=len(deployments),
        successful_deployments=len([d for d in deployments if d.status.value == "success"]),
        failed_deployments=len([d for d in deployments if d.status.value == "failed"]),
        pending_deployments=len([d for d in deployments if d.status.value == "pending"])
    )

# ----------------------------------------------------------------
# UPDATE USER
# ----------------------------------------------------------------
@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: UUID,
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Update user information
    - **Students**: Can only update their own profile (no role change)
    - **Teachers/Admins**: Can update any profile
    """
    user = crud_users.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Students can only update their own profile
    if current_user.role == UserRole.STUDENT and user_id != current_user.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own profile"
        )
    
    # Students cannot change their role
    if current_user.role == UserRole.STUDENT and user_update.role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change your role"
        )
    
    updated_user = crud_users.update_user(db, user_id, user_update)
    return updated_user

# ----------------------------------------------------------------
# CHANGE PASSWORD
# ----------------------------------------------------------------
@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    user_id: UUID,
    password_update: UserPasswordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Change user password
    - **Owner only** can change password
    - Requires current password verification
    """
    if user_id != current_user.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only change your own password"
        )
    
    # Verify current password
    if not verify_password(password_update.current_password, current_user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Update password
    crud_users.update_user_password(db, user_id, password_update.new_password)
    return None

# ----------------------------------------------------------------
# DELETE USER (ADMIN ONLY)
# ----------------------------------------------------------------
@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Delete a user
    - **Requires**: ADMIN role
    """
    success = crud_users.delete_user(db, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return None