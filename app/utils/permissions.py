"""
Permission and authorization utilities for role-based access control
"""
from collections.abc import Callable
from functools import wraps

from fastapi import Depends, HTTPException, status

from sqlalchemy.orm import Session

from app.models import (
    User,
    UserRole,
    Deployment,
    UserToDeployment,
    UserToTeam,
    Team,
)
from app.utils.keycloak_auth import get_current_user_keycloak as get_current_user


# ----------------------------------------------------------------
# ROLE CHECKERS
# ----------------------------------------------------------------
def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user"""
    return current_user


def require_role(allowed_roles: list[UserRole]) -> Callable:
    """
    Decorator to require specific roles for an endpoint

    Usage:
        @router.get("/admin-only")
        @require_role([UserRole.ADMIN])
        def admin_endpoint(current_user: User = Depends(get_current_user)):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_user = kwargs.get('current_user')
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Not authenticated"
                )

            if current_user.role not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access forbidden. Required role: {[r.value for r in allowed_roles]}"
                )

            return await func(*args, **kwargs)
        return wrapper
    return decorator


# ----------------------------------------------------------------
# DEPENDENCY INJECTIONS FOR ROLES
# ----------------------------------------------------------------
def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require ADMIN role"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


def get_current_teacher_or_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require TEACHER or ADMIN role"""
    if current_user.role not in [UserRole.TEACHER, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher or Admin access required"
        )
    return current_user


def get_current_student(current_user: User = Depends(get_current_user)) -> User:
    """Require STUDENT role"""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student access required"
        )
    return current_user


# ----------------------------------------------------------------
# PERMISSION CHECKERS
# ----------------------------------------------------------------
def check_resource_ownership(resource_user_id: str, current_user: User) -> bool:
    """
    Check if user owns the resource or has elevated permissions
    Returns True if user owns resource or is TEACHER/ADMIN
    """
    if current_user.role in [UserRole.TEACHER, UserRole.ADMIN]:
        return True
    return str(resource_user_id) == str(current_user.userId)


def check_course_access(course_id: str, current_user: User) -> bool:
    """
    Check if user has access to a course
    Returns True if user is in the course or is ADMIN
    """
    if current_user.role == UserRole.ADMIN:
        return True
    return str(current_user.courseId) == str(course_id)


def ensure_resource_access(resource_user_id: str, current_user: User):
    """
    Raise exception if user doesn't have access to resource
    """
    if not check_resource_ownership(resource_user_id, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this resource"
        )


def ensure_course_access(course_id: str, current_user: User):
    """
    Raise exception if user doesn't have access to course
    """
    if not check_course_access(course_id, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this course"
        )


# ----------------------------------------------------------------
# DEPLOYMENT ACCESS (Owner / Team-Member / Teacher / Admin)
# ----------------------------------------------------------------
def has_deployment_access(deployment: Deployment, user: User, db: Session) -> bool:
    """
    Return True if `user` may read/manage `deployment`.

    Allowed when any of:
      - user is teacher or admin
      - user is the deployment owner
      - user is part of any team assigned to this deployment
        (via UserToTeam joined to Team.deploymentId)
      - user appears in UserToDeployment for this deployment
    """
    if user.role in (UserRole.TEACHER, UserRole.ADMIN):
        return True
    if str(deployment.userId) == str(user.userId):
        return True

    team_match = (
        db.query(UserToTeam.userToTeamId)
        .join(Team, Team.teamId == UserToTeam.teamId)
        .filter(
            Team.deploymentId == deployment.deploymentId,
            UserToTeam.userId == user.userId,
        )
        .first()
    )
    if team_match:
        return True

    direct_match = (
        db.query(UserToDeployment.userToDeploymentId)
        .filter(
            UserToDeployment.deploymentId == deployment.deploymentId,
            UserToDeployment.userId == user.userId,
        )
        .first()
    )
    return direct_match is not None


def ensure_deployment_access(deployment: Deployment, user: User, db: Session) -> None:
    """
    Raise 403 unless `user` may access `deployment`.

    Use this in every endpoint that takes a deployment_id from the URL/body
    to prevent IDOR. Pass the loaded Deployment, not just the ID — callers
    should already have fetched it (and should return 404 if missing before
    calling this).
    """
    if not has_deployment_access(deployment, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this deployment",
        )

