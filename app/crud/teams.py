from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.models import Team, UserToTeam
from app.schemas import TeamCreate, TeamUpdate


def get_team(db: Session, team_id: UUID) -> Optional[Team]:
    """Get team by ID"""
    return db.query(Team).filter(Team.teamId == team_id).first()


def get_teams(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    user_group_id: Optional[UUID] = None
) -> List[Team]:
    """Get teams with optional user group filter"""
    query = db.query(Team)
    
    if user_group_id:
        query = query.filter(Team.userGroupId == user_group_id)
    
    return query.offset(skip).limit(limit).all()


def create_team(db: Session, team: TeamCreate) -> Team:
    """Create a new team"""
    db_team = Team(
        name=team.name,
        userGroupId=team.userGroupId
    )
    db.add(db_team)
    db.commit()
    db.refresh(db_team)
    
    # Add users to team
    for user_id in team.userIds:
        user_to_team = UserToTeam(
            userId=user_id,
            teamId=db_team.teamId
        )
        db.add(user_to_team)
    
    db.commit()
    db.refresh(db_team)
    return db_team


def update_team(db: Session, team_id: UUID, team_update: TeamUpdate) -> Optional[Team]:
    """Update team information"""
    db_team = get_team(db, team_id)
    if not db_team:
        return None
    
    update_data = team_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_team, field, value)
    
    db.commit()
    db.refresh(db_team)
    return db_team


def delete_team(db: Session, team_id: UUID) -> bool:
    """Delete a team"""
    db_team = get_team(db, team_id)
    if not db_team:
        return False
    
    db.delete(db_team)
    db.commit()
    return True


def add_user_to_team(db: Session, team_id: UUID, user_id: UUID) -> bool:
    """Add a user to a team"""
    # Check if already exists
    existing = db.query(UserToTeam).filter(
        UserToTeam.teamId == team_id,
        UserToTeam.userId == user_id
    ).first()
    
    if existing:
        return False
    
    user_to_team = UserToTeam(
        userId=user_id,
        teamId=team_id
    )
    db.add(user_to_team)
    db.commit()
    return True


def remove_user_from_team(db: Session, team_id: UUID, user_id: UUID) -> bool:
    """Remove a user from a team"""
    user_to_team = db.query(UserToTeam).filter(
        UserToTeam.teamId == team_id,
        UserToTeam.userId == user_id
    ).first()
    
    if not user_to_team:
        return False
    
    db.delete(user_to_team)
    db.commit()
    return True
