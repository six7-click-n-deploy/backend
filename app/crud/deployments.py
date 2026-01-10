from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.models import Deployment
from app.schemas import DeploymentCreate


def get_deployment(db: Session, deployment_id: UUID) -> Optional[Deployment]:
    """Get deployment by ID"""
    return db.query(Deployment).filter(Deployment.deploymentId == deployment_id).first()


def get_deployments(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[UUID] = None,
    app_id: Optional[UUID] = None,
) -> List[Deployment]:
    """Get deployments with optional filters"""
    query = db.query(Deployment)
    
    if user_id:
        query = query.filter(Deployment.userId == user_id)
    if app_id:
        query = query.filter(Deployment.appId == app_id)
    if status:
        query = query.filter(Deployment.status == status)
    
    return query.offset(skip).limit(limit).all()


def create_deployment(db: Session, deployment: DeploymentCreate, user_id: UUID) -> Deployment:
    """Create a new deployment"""
    db_deployment = Deployment(
        name=deployment.name,
        appId=deployment.appId,
        userId=user_id,
        releaseTag=deployment.releaseTag,
        userInputVar=deployment.userInputVar,
    )
    db.add(db_deployment)
    db.commit()
    db.refresh(db_deployment)
    return db_deployment

def delete_deployment(db: Session, deployment_id: UUID) -> bool:
    """Delete a deployment"""
    db_deployment = get_deployment(db, deployment_id)
    if not db_deployment:
        return False
    
    db.delete(db_deployment)
    db.commit()
    return True
