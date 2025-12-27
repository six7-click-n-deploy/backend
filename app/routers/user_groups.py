from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.database import get_db
from app.models import User
from app.schemas import UserGroupCreate, UserGroupResponse, UserGroupWithMembers
from app.utils.auth import get_current_user
from app.utils.permissions import get_current_teacher_or_admin
from app.crud import user_groups as crud_user_groups

router = APIRouter()


# ----------------------------------------------------------------
# GET ALL USER GROUPS
# ----------------------------------------------------------------
@router.get("/", response_model=List[UserGroupResponse])
def list_user_groups(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all user groups
    - **All authenticated users** can view user groups
    """
    user_groups = crud_user_groups.get_user_groups(db, skip=skip, limit=limit)
    return user_groups


# ----------------------------------------------------------------
# GET USER GROUP BY ID
# ----------------------------------------------------------------
@router.get("/{user_group_id}", response_model=UserGroupWithMembers)
def get_user_group(
    user_group_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user group by ID with all members"""
    user_group = crud_user_groups.get_user_group(db, user_group_id)
    if not user_group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User group not found"
        )
    return user_group


# ----------------------------------------------------------------
# CREATE USER GROUP (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.post("/", response_model=UserGroupResponse, status_code=status.HTTP_201_CREATED)
def create_user_group(
    user_group: UserGroupCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Create a new user group
    - **Requires**: TEACHER or ADMIN role
    """
    return crud_user_groups.create_user_group(db, user_group)


# ----------------------------------------------------------------
# DELETE USER GROUP (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.delete("/{user_group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_group(
    user_group_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Delete a user group
    - **Requires**: TEACHER or ADMIN role
    """
    success = crud_user_groups.delete_user_group(db, user_group_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User group not found"
        )
    return None


# ----------------------------------------------------------------
# ADD USER TO GROUP (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.post("/{user_group_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_user_to_group(
    user_group_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Add a user to a user group
    - **Requires**: TEACHER or ADMIN role
    """
    success = crud_user_groups.add_user_to_group(db, user_group_id, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already in group or group not found"
        )
    return None


# ----------------------------------------------------------------
# REMOVE USER FROM GROUP (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.delete("/{user_group_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_from_group(
    user_group_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Remove a user from a user group
    - **Requires**: TEACHER or ADMIN role
    """
    success = crud_user_groups.remove_user_from_group(db, user_group_id, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not in group or group not found"
        )
    return None
