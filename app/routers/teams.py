from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.database import get_db
from app.models import User
from app.schemas import TeamCreate, TeamUpdate, TeamResponse, TeamWithMembers
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import get_current_teacher_or_admin
from app.crud import teams as crud_teams

router = APIRouter()


# ----------------------------------------------------------------
# GET ALL TEAMS
# ----------------------------------------------------------------
@router.get("/", response_model=List[TeamResponse])
def list_teams(
    skip: int = 0,
    limit: int = 100,
    user_group_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get all teams with optional user group filter
    - **All authenticated users** can view teams
    """
    teams = crud_teams.get_teams(db, skip=skip, limit=limit, user_group_id=user_group_id)
    return teams


# ----------------------------------------------------------------
# GET TEAM BY ID
# ----------------------------------------------------------------
@router.get("/{team_id}", response_model=TeamWithMembers)
def get_team(
    team_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """Get team by ID with all members"""
    team = crud_teams.get_team(db, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    return team


# ----------------------------------------------------------------
# CREATE TEAM (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.post("/", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(
    team: TeamCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Create a new team
    - **Requires**: TEACHER or ADMIN role
    """
    return crud_teams.create_team(db, team)


# ----------------------------------------------------------------
# UPDATE TEAM (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.put("/{team_id}", response_model=TeamResponse)
def update_team(
    team_id: UUID,
    team_update: TeamUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Update a team
    - **Requires**: TEACHER or ADMIN role
    """
    team = crud_teams.update_team(db, team_id, team_update)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    return team


# ----------------------------------------------------------------
# DELETE TEAM (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(
    team_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Delete a team
    - **Requires**: TEACHER or ADMIN role
    """
    success = crud_teams.delete_team(db, team_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    return None


# ----------------------------------------------------------------
# ADD USER TO TEAM (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.post("/{team_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_user_to_team(
    team_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Add a user to a team
    - **Requires**: TEACHER or ADMIN role
    """
    success = crud_teams.add_user_to_team(db, team_id, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already in team or team not found"
        )
    return None


# ----------------------------------------------------------------
# REMOVE USER FROM TEAM (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.delete("/{team_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_from_team(
    team_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Remove a user from a team
    - **Requires**: TEACHER or ADMIN role
    """
    success = crud_teams.remove_user_from_team(db, team_id, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not in team or team not found"
        )
    return None
