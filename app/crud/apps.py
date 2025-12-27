from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.models import App
from app.schemas import AppCreate, AppUpdate


def get_app(db: Session, app_id: UUID) -> Optional[App]:
    """Get app by ID"""
    return db.query(App).filter(App.appId == app_id).first()


def get_apps(
    db: Session, 
    skip: int = 0, 
    limit: int = 100,
    user_id: Optional[UUID] = None
) -> List[App]:
    """Get apps with optional user filter"""
    query = db.query(App)
    
    if user_id:
        query = query.filter(App.userId == user_id)
    
    return query.offset(skip).limit(limit).all()


def create_app(db: Session, app: AppCreate, user_id: UUID) -> App:
    """Create a new app"""
    db_app = App(
        name=app.name,
        description=app.description,
        git_link=app.git_link,
        userId=user_id
    )
    db.add(db_app)
    db.commit()
    db.refresh(db_app)
    return db_app


def update_app(db: Session, app_id: UUID, app_update: AppUpdate) -> Optional[App]:
    """Update app information"""
    db_app = get_app(db, app_id)
    if not db_app:
        return None
    
    update_data = app_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_app, field, value)
    
    db.commit()
    db.refresh(db_app)
    return db_app


def delete_app(db: Session, app_id: UUID) -> bool:
    """Delete an app"""
    db_app = get_app(db, app_id)
    if not db_app:
        return False
    
    db.delete(db_app)
    db.commit()
    return True
