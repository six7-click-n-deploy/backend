"""
Permission and authorization utilities for role-based access control
"""
from functools import wraps
from fastapi import HTTPException, status, Depends
from typing import List, Callable

from app.models import User, UserRole
from app.utils.auth import get_current_user


# ----------------------------------------------------------------
# ROLE CHECKERS
# ----------------------------------------------------------------
def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user"""
    return current_user


def require_role(allowed_roles: List[UserRole]) -> Callable:
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
