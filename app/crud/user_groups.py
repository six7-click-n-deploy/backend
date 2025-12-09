from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.models import UserGroup, UserToUserGroup, CourseToUserGroup
from app.schemas import UserGroupCreate


def get_user_group(db: Session, user_group_id: UUID) -> Optional[UserGroup]:
    """Get user group by ID"""
    return db.query(UserGroup).filter(UserGroup.userGroupId == user_group_id).first()


def get_user_groups(db: Session, skip: int = 0, limit: int = 100) -> List[UserGroup]:
    """Get all user groups"""
    return db.query(UserGroup).offset(skip).limit(limit).all()


def create_user_group(db: Session, user_group: UserGroupCreate) -> UserGroup:
    """Create a new user group"""
    db_user_group = UserGroup(deploymentId=user_group.deploymentId)
    db.add(db_user_group)
    db.commit()
    db.refresh(db_user_group)
    
    # Add users to group
    for user_id in user_group.userIds:
        user_to_group = UserToUserGroup(
            userId=user_id,
            userGroupId=db_user_group.userGroupId
        )
        db.add(user_to_group)
    
    # Add courses to group
    for course_id in user_group.courseIds:
        course_to_group = CourseToUserGroup(
            courseId=course_id,
            userGroupId=db_user_group.userGroupId
        )
        db.add(course_to_group)
    
    db.commit()
    db.refresh(db_user_group)
    return db_user_group


def delete_user_group(db: Session, user_group_id: UUID) -> bool:
    """Delete a user group"""
    db_user_group = get_user_group(db, user_group_id)
    if not db_user_group:
        return False
    
    db.delete(db_user_group)
    db.commit()
    return True


def add_user_to_group(db: Session, user_group_id: UUID, user_id: UUID) -> bool:
    """Add a user to a user group"""
    # Check if already exists
    existing = db.query(UserToUserGroup).filter(
        UserToUserGroup.userGroupId == user_group_id,
        UserToUserGroup.userId == user_id
    ).first()
    
    if existing:
        return False
    
    user_to_group = UserToUserGroup(
        userId=user_id,
        userGroupId=user_group_id
    )
    db.add(user_to_group)
    db.commit()
    return True


def remove_user_from_group(db: Session, user_group_id: UUID, user_id: UUID) -> bool:
    """Remove a user from a user group"""
    user_to_group = db.query(UserToUserGroup).filter(
        UserToUserGroup.userGroupId == user_group_id,
        UserToUserGroup.userId == user_id
    ).first()
    
    if not user_to_group:
        return False
    
    db.delete(user_to_group)
    db.commit()
    return True
