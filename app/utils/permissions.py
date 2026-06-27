"""
Permission and authorization utilities for role-based access control.

Phase 1 of the RBAC refactor — this module keeps every public name it
exposed before so no router has to change. The internal implementation
is reshaped around two ideas:

* a ``require_roles()`` FastAPI-dependency factory that produces a
  callable enforcing one or more :class:`UserRole` values, and
* two role tuples (``ADMIN_ROLES`` and ``STAFF_ROLES``) that name the
  two common groupings used across the app.

The historical helpers (``get_current_admin``, ``get_current_teacher_or_admin``,
``get_current_student``) are kept as thin aliases that delegate to
``require_roles()``. They will be removed in a later phase once every
router has been migrated to either ``require_admin`` / ``require_staff``
or a capability check from :mod:`app.utils.capabilities`.
"""
from collections.abc import Callable
from functools import wraps

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    Deployment,
    Team,
    User,
    UserRole,
    UserToDeployment,
    UserToTeam,
)
from app.utils.keycloak_auth import get_current_user_keycloak as get_current_user

# ----------------------------------------------------------------
# ROLE GROUPINGS
# ----------------------------------------------------------------
# ``STAFF_ROLES`` covers everyone with elevated privileges in the
# product vocabulary — that's the set of users for whom the UI shows
# the staff-only chrome (admin app review, course management, …). It
# explicitly does NOT mean "anyone with course-teacher rights" — that
# is a per-resource check handled in :mod:`app.utils.capabilities`.
STAFF_ROLES: tuple[UserRole, ...] = (UserRole.TEACHER, UserRole.ADMIN)
ADMIN_ROLES: tuple[UserRole, ...] = (UserRole.ADMIN,)


# ----------------------------------------------------------------
# ROLE DEPENDENCY FACTORY
# ----------------------------------------------------------------
def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user"""
    return current_user


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """FastAPI dependency factory enforcing a role allow-list.

    Returns a dependency that resolves the current user and raises 403
    with a structured ``detail`` payload when the user's role is not in
    ``roles``. The payload shape is::

        {"code": "role_required", "required": ["admin", ...]}

    so the frontend can render a precise "you need role X" message and
    distinguish role-based 403s from resource-based 403s.

    Usage::

        @router.get("/admin-only", dependencies=[Depends(require_admin)])
        def admin_only(): ...

        @router.get("/staff-only")
        def staff_only(user: User = Depends(require_staff)):
            ...
    """
    if not roles:
        raise ValueError("require_roles() needs at least one role")

    allowed = tuple(roles)

    def _dep(user: User = Depends(get_current_active_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "role_required",
                    "required": [r.value for r in allowed],
                },
            )
        return user

    return _dep


# Aliases used directly on router dependencies. These are the new
# canonical way to require a role — prefer them over the historical
# `get_current_*` helpers below.
require_admin = require_roles(UserRole.ADMIN)
require_staff = require_roles(UserRole.TEACHER, UserRole.ADMIN)


# ----------------------------------------------------------------
# LEGACY ROLE HELPERS  (Phase 1: kept as thin aliases)
# ----------------------------------------------------------------
# These names are still imported across the codebase. To honour the
# "no external behavior change" constraint of Phase 1, we keep the same
# names and call signatures and delegate to ``require_roles()``. They
# will be removed in Phase 2 once every router is migrated.
def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require ADMIN role.

    Phase 1 shim around ``require_admin``. Kept so existing routers
    don't have to change yet. Raises 403 with the new structured
    ``role_required`` payload — same status, slightly richer body.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "role_required",
                "required": [UserRole.ADMIN.value],
            },
        )
    return current_user


def get_current_teacher_or_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require TEACHER or ADMIN role.

    Phase 1 shim around ``require_staff``.
    """
    if current_user.role not in STAFF_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "role_required",
                "required": [r.value for r in STAFF_ROLES],
            },
        )
    return current_user


def get_current_student(current_user: User = Depends(get_current_user)) -> User:
    """Require STUDENT role."""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "role_required",
                "required": [UserRole.STUDENT.value],
            },
        )
    return current_user


def require_role(allowed_roles: list[UserRole]) -> Callable:
    """Legacy decorator. Kept for backwards compatibility.

    Prefer ``Depends(require_roles(...))`` on the route signature —
    decorators on FastAPI routes are fragile because they bypass the
    dependency injection system. This is only here so callers that
    still use ``@require_role([...])`` keep working in Phase 1.
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
                    detail={
                        "code": "role_required",
                        "required": [r.value for r in allowed_roles],
                    },
                )

            return await func(*args, **kwargs)
        return wrapper
    return decorator


# ----------------------------------------------------------------
# COURSE-LEVEL ACCESS (legacy — kept for Phase 1 compatibility)
# ----------------------------------------------------------------
# Note: ``check_resource_ownership`` and ``ensure_resource_access``
# wurden in Phase 2 entfernt. Sie waren der Ursprung von Bug #2
# (Teacher-Bypass auf fremde Apps). Alle Konsumenten sind auf
# Capability-Funktionen aus ``app.utils.capabilities`` umgestellt.

def check_course_access(course_id: str, current_user: User) -> bool:
    """
    Check if user has access to a course
    Returns True if user is in the course or is ADMIN
    """
    if current_user.role == UserRole.ADMIN:
        return True
    return str(current_user.courseId) == str(course_id)


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
    if user.role in STAFF_ROLES:
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



def is_deployment_owner_view(deployment: Deployment, user: User) -> bool:
    """True if ``user`` should see the *owner view* of ``deployment``.

    The owner view shows everything: tasks, logs, terraform state,
    full team rosters, the destroy/delete button. The member view
    (anything else with deployment access) only shows the deployment
    metadata, the user's own team, and the resend-credentials button
    for themself.

    Teachers and admins always see the owner view — they're effectively
    superusers across deployments. The deployment creator sees the
    owner view of their own deployment. Everyone else who reaches
    ``has_deployment_access`` (team members, direct UserToDeployment
    mappings) gets the member view.
    """
    if user.role in STAFF_ROLES:
        return True
    return str(deployment.userId) == str(user.userId)


def ensure_deployment_owner_view(deployment: Deployment, user: User) -> None:
    """Raise 403 unless ``user`` has the owner view of ``deployment``.

    Use on endpoints that expose deployment-internals (tasks, logs,
    state, destroy/delete) — members have read-access to the
    deployment itself but not to those.
    """
    if not is_deployment_owner_view(deployment, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the deployment owner or staff can perform this action",
        )
