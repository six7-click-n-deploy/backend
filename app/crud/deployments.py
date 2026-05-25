from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, asc
from typing import List, Optional, Set, Dict, Any
from uuid import UUID
import json

from app.models import Deployment, UserToDeployment, Task, Team, User
from app.schemas import DeploymentCreate

def get_deployment(db: Session, deployment_id: UUID) -> Optional[Deployment]:
    """Get deployment by ID"""
    return db.query(Deployment).filter(Deployment.deploymentId == deployment_id).first()


def get_deployment_with_details(db: Session, deployment_id: UUID) -> Optional[Deployment]:
    """Get deployment by ID with all relations loaded"""
    return (
        db.query(Deployment)
        .options(
            joinedload(Deployment.user),
            joinedload(Deployment.app),
            joinedload(Deployment.teams),
        )
        .filter(Deployment.deploymentId == deployment_id)
        .first()
    )


def get_latest_task(db: Session, deployment_id: UUID) -> Optional[Task]:
    """Get the most recent task for a deployment"""
    return (
        db.query(Task)
        .filter(Task.deploymentId == deployment_id)
        .order_by(desc(Task.created_at))
        .first()
    )


def get_first_task(db: Session, deployment_id: UUID) -> Optional[Task]:
    """Get the first task for a deployment (when deployment was created)"""
    return (
        db.query(Task)
        .filter(Task.deploymentId == deployment_id)
        .order_by(asc(Task.created_at))
        .first()
    )


def get_deployment_status(db: Session, deployment_id: UUID) -> Optional[str]:
    """Get current display status from the latest task, combining type + status."""
    task = get_latest_task(db, deployment_id)
    if not task or not task.status:
        return None

    from app.models import TaskType, TaskStatus

    # Terminal states are always shown as-is regardless of task type
    terminal = {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.DESTROYED}
    if task.status in terminal:
        return task.status.value

    # For active destroy tasks, show "destroying" instead of "pending"/"running"
    if task.type == TaskType.DESTROY:
        return "destroying"

    return task.status.value


def get_deployment_created_at(db: Session, deployment_id: UUID):
    """Get deployment creation time from first task"""
    task = get_first_task(db, deployment_id)
    return task.created_at if task else None


def get_team_members(db: Session, team_id: UUID) -> List[User]:
    """Get all users in a team"""
    from app.models import UserToTeam
    user_ids = (
        db.query(UserToTeam.userId)
        .filter(UserToTeam.teamId == team_id)
        .all()
    )
    user_ids = [uid[0] for uid in user_ids]
    
    if not user_ids:
        return []
    
    return db.query(User).filter(User.userId.in_(user_ids)).all()


def get_deployment_teams_with_members(db: Session, deployment_id: UUID) -> List[Dict[str, Any]]:
    """Get all teams for a deployment with their members"""
    teams = db.query(Team).filter(Team.deploymentId == deployment_id).all()
    
    result = []
    for team in teams:
        members = get_team_members(db, team.teamId)
        result.append({
            "teamId": team.teamId,
            "name": team.name,
            "members": [
                {
                    "userId": member.userId,
                    "email": member.email,
                    "username": member.username
                }
                for member in members
            ]
        })
    
    return result


def get_latest_tf_state(db: Session, deployment_id: UUID) -> Optional[str]:
    """Get the most recent non-null tf_state for a deployment"""
    task = (
        db.query(Task)
        .filter(Task.deploymentId == deployment_id)
        .filter(Task.tf_state.isnot(None))
        .order_by(desc(Task.created_at))
        .first()
    )
    return task.tf_state if task else None


def get_deployment_outputs(db: Session, deployment_id: UUID) -> Optional[Dict[str, Any]]:
    """Get parsed Terraform outputs from the latest successful task"""
    task = (
        db.query(Task)
        .filter(Task.deploymentId == deployment_id)
        .filter(Task.outputs.isnot(None))
        .order_by(desc(Task.created_at))
        .first()
    )
    
    if task and task.outputs:
        try:
            return json.loads(task.outputs)
        except json.JSONDecodeError:
            return None
    return None


def get_deployments(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[UUID] = None,
    app_id: Optional[UUID] = None,
    status: Optional[str] = None,
) -> List[Deployment]:
    """Get deployments with optional filters"""
    query = db.query(Deployment)
    
    if user_id:
        query = query.filter(Deployment.userId == user_id)
    if app_id:
        query = query.filter(Deployment.appId == app_id)
    
    # Order by deploymentId (UUID) - could also join with Task for created_at ordering
    query = query.order_by(desc(Deployment.deploymentId))
    
    deployments = query.offset(skip).limit(limit).all()
    
    # Filter by status if specified (requires checking latest task)
    if status:
        filtered = []
        for deployment in deployments:
            latest_task = get_latest_task(db, deployment.deploymentId)
            if latest_task and latest_task.status and latest_task.status.value == status:
                filtered.append(deployment)
        return filtered
    
    return deployments


def get_deployments_with_status(db: Session, deployments: List[Deployment]) -> List[Dict[str, Any]]:
    """Enrich deployments with their current status from latest task"""
    result = []
    for deployment in deployments:
        status = get_deployment_status(db, deployment.deploymentId)
        result.append({
            "deployment": deployment,
            "status": status
        })
    return result


def create_deployment(db: Session, deployment: DeploymentCreate, user_id: UUID) -> Deployment:
    """Create a new deployment"""
    # Convert userInputVar dict to JSON string for database storage
    user_input_var_json = None
    if deployment.userInputVar is not None:
        user_input_var_json = json.dumps(deployment.userInputVar)
    
    db_deployment = Deployment(
        name=deployment.name,
        appId=deployment.appId,
        userId=user_id,
        releaseTag=deployment.releaseTag,
        userInputVar=user_input_var_json,
    )
    db.add(db_deployment)
    db.commit()
    db.refresh(db_deployment)
    return db_deployment

def delete_deployment(db: Session, deployment_id: UUID) -> bool:
    """Delete a deployment and all related records (tasks, teams, user-mappings)"""
    db_deployment = get_deployment(db, deployment_id)
    if not db_deployment:
        return False

    from app.models import UserToTeam, Team, UserToDeployment, Task

    # 1. Delete tasks
    tasks = db.query(Task).filter(Task.deploymentId == deployment_id).all()
    for task in tasks:
        db.delete(task)

    # 2. Delete team members, then teams
    teams = db.query(Team).filter(Team.deploymentId == deployment_id).all()
    for team in teams:
        db.query(UserToTeam).filter(UserToTeam.teamId == team.teamId).delete()
        db.delete(team)

    # 3. Delete user-deployment mappings
    db.query(UserToDeployment).filter(UserToDeployment.deploymentId == deployment_id).delete()

    db.flush()
    db.delete(db_deployment)
    db.commit()
    return True


def create_user_to_deployments(
    db: Session,
    deployment_id: UUID,
    user_ids: Set[UUID]
) -> List[UserToDeployment]:
    """
    Create UserToDeployment entries for multiple users
    """
    user_to_deployments = []
    
    for user_id in user_ids:
        user_to_deployment = UserToDeployment(
            userId=user_id,
            deploymentId=deployment_id
        )
        db.add(user_to_deployment)
        user_to_deployments.append(user_to_deployment)
    
    return user_to_deployments
