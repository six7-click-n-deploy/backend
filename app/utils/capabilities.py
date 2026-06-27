"""Capability functions for role-based access control.

This module is the single source of truth for "may this user do X?"
questions. Every router endpoint that goes beyond a plain role check
should reach for a ``can_*`` (boolean) or ``ensure_*`` (raises) helper
here instead of poking at ``user.role`` directly.

Phase 1 contract:
    The functions here must mirror the *current* gates exactly so the
    refactor is a pure code-shape change with no observable behavior
    difference. Tightenings called for by the plan (e.g. removing the
    teacher bypass on app edit/delete, scoping course-teacher rights)
    land in later phases — those spots are marked with explicit
    ``# Phase 1: ...`` comments below.

Conventions:
    - ``can_<verb>_<resource>(user, ..., *, db=None) -> bool`` answers
      the permission question and never raises.
    - ``ensure_<verb>_<resource>(...)`` calls the ``can_*`` form and
      raises :class:`fastapi.HTTPException` with status 403 and a
      structured ``detail`` payload::

          {"code": "<machine_code>", "required": [...optional context...]}

      The ``code`` lets the frontend render specific error messages.
      For role-shaped rejections, ``code="role_required"`` matches the
      payload produced by :func:`app.utils.permissions.require_roles`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.crud import app_version_approvals as crud_approvals
from app.models import App, Course, CourseTeacher, Deployment, User, UserRole
from app.utils.permissions import (
    STAFF_ROLES,
    has_deployment_access,
)


# ----------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------
def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def _is_staff(user: User) -> bool:
    """User has a staff role (Teacher or Admin).

    Note: ``staff`` is the role-shaped check. Course-teacher rights
    are a per-resource concept — for that, use :func:`is_course_teacher`.
    """
    return user.role in STAFF_ROLES


def _is_owner(user: User, owner_id) -> bool:
    return str(owner_id) == str(user.userId)


def _forbidden(code: str, required: list[str] | None = None) -> HTTPException:
    detail: dict = {"code": code}
    if required is not None:
        detail["required"] = required
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# ================================================================
# APPS
# ================================================================
def can_view_app(user: User, app: App, *, db: Session | None = None) -> bool:
    """Whether ``user`` may see ``app`` at all.

    Allowed when:
      - user owns the app, OR
      - user is admin, OR
      - app is public AND has at least one approved version.

    Phase 2: Bug #2 — the teacher bypass is removed. Teachers no
    longer see private third-party apps via this gate; they go through
    the same public+approved path as students for foreign apps. The
    listing endpoint still surfaces their own apps in full, and admins
    keep their plattform-wide visibility.
    """
    if _is_owner(user, app.userId):
        return True
    if _is_admin(user):
        return True
    if app.is_private:
        return False
    if db is None:
        # Without a DB handle we can't verify the approved-version
        # requirement; the safe default is "no". Routers that call
        # can_view_app on a public app must pass ``db``.
        return False
    return crud_approvals.has_any_approved_version(db, app.appId)


def ensure_view_app(user: User, app: App, *, db: Session | None = None) -> None:
    if not can_view_app(user, app, db=db):
        raise _forbidden("app_view_forbidden")


def can_list_all_apps(user: User) -> bool:
    """Whether ``user`` may list every app (including private + unapproved).

    Admin only. Other staff members see "my apps + public approved
    apps" — that is a query-shape concern, not a flat boolean.
    """
    return _is_admin(user)


def ensure_list_all_apps(user: User) -> None:
    if not can_list_all_apps(user):
        raise _forbidden("role_required", [UserRole.ADMIN.value])


def can_edit_app(user: User, app: App) -> bool:
    """Whether ``user`` may edit ``app``'s metadata.

    Phase 2: Bug #2 fix — teacher bypass removed. Only the app owner
    OR an admin may edit. Teachers acting on a foreign app must use
    the admin path (and need the admin role for that).
    """
    return _is_admin(user) or _is_owner(user, app.userId)


def ensure_edit_app(user: User, app: App) -> None:
    if not can_edit_app(user, app):
        raise _forbidden("app_edit_forbidden")


def can_delete_app(user: User, app: App) -> bool:
    """Whether ``user`` may (soft-)delete ``app``.

    Phase 2: Bug #2 fix — same gate as :func:`can_edit_app`. Owner or
    admin only; no teacher bypass.
    """
    return _is_admin(user) or _is_owner(user, app.userId)


def ensure_delete_app(user: User, app: App) -> None:
    if not can_delete_app(user, app):
        raise _forbidden("app_delete_forbidden")


def can_submit_app_version(user: User, app: App) -> bool:
    """Submit a version for approval review.

    Phase 2: tightened to owner-or-admin per the plan. Same gate as
    edit/delete — submitting a version is an authorial action.
    """
    return _is_admin(user) or _is_owner(user, app.userId)


def ensure_submit_app_version(user: User, app: App) -> None:
    if not can_submit_app_version(user, app):
        raise _forbidden("app_submit_forbidden")


def can_approve_app_version(user: User) -> bool:
    """Approve / reject / revoke a submitted version.

    Admin only — already the case today via ``get_current_admin`` on
    the admin_apps router.
    """
    return _is_admin(user)


def ensure_approve_app_version(user: User) -> None:
    if not can_approve_app_version(user):
        raise _forbidden("role_required", [UserRole.ADMIN.value])


# ================================================================
# DEPLOYMENTS
# ================================================================
def can_view_deployment_member(user: User, dep: Deployment, db: Session) -> bool:
    """Member-view access to a deployment.

    Mirrors :func:`app.utils.permissions.has_deployment_access` —
    owner, staff, team-member, or direct UserToDeployment mapping.
    The member view shows the deployment metadata + the user's own
    team + the resend-credentials button for themself, nothing more.
    """
    return has_deployment_access(dep, user, db)


def ensure_view_deployment_member(user: User, dep: Deployment, db: Session) -> None:
    if not can_view_deployment_member(user, dep, db):
        raise _forbidden("deployment_view_forbidden")


def can_view_deployment_owner(user: User, dep: Deployment, db: Session) -> bool:
    """Owner-view access — tasks, logs, terraform state, destroy.

    Phase 3 widens the read-only side of the gate: course-teachers
    may inspect deployments owned by users in courses they teach
    (Logs / Infrastructure / TF state). Operate rights are kept
    separate — see :func:`can_operate_deployment`, which still
    rejects course-teachers.

    Resolution order (precedence):
      1. owner of the deployment           → True
      2. admin                             → True
      3. course-teacher of the deployment-
         owner's course                    → True (inspect only)
      4. (legacy) any other staff bypass   → False after Phase 3

    Step 1+2 are handled by :func:`is_deployment_owner_view`. Step 3
    needs the deployment owner's course, which we read off the
    pre-loaded ``dep.user.courseId`` when present. The check is
    skipped when the owner has no course — a deployment whose
    creator left their course has no course-scope context, so the
    course-teacher right doesn't apply.
    """
    # 1+2: owner / admin paths — unchanged.
    if user.role == UserRole.ADMIN:
        return True
    if str(dep.userId) == str(user.userId):
        return True

    # 3: course-teacher inspect right. Only applies to teachers; for
    # any other role the role gate inside ``is_course_teacher_id``
    # returns False without touching the DB.
    if user.role != UserRole.TEACHER:
        return False
    owner_course_id = getattr(getattr(dep, "user", None), "courseId", None)
    if owner_course_id is None:
        return False
    return is_course_teacher_id(user, owner_course_id, db)


def ensure_view_deployment_owner(user: User, dep: Deployment, db: Session) -> None:
    if not can_view_deployment_owner(user, dep, db):
        raise _forbidden("deployment_owner_view_forbidden")


def can_operate_deployment(user: User, dep: Deployment, db: Session) -> bool:
    """Pause / Resume / Destroy / Redeploy on a deployment.

    Phase 2: tightened to owner-or-admin per the matrix. Teachers no
    longer get operate rights via the staff bypass — a course-teacher
    can inspect a deployment in their course (Phase 3) but cannot
    pause/destroy it.
    """
    del db  # reserved for Phase 3 course-teacher lookups
    return _is_admin(user) or _is_owner(user, dep.userId)


def ensure_operate_deployment(user: User, dep: Deployment, db: Session) -> None:
    if not can_operate_deployment(user, dep, db):
        raise _forbidden("deployment_operate_forbidden")


def can_resend_access(
    user: User,
    dep: Deployment,
    target_user_id: UUID | str,
    db: Session,
) -> bool:
    """Resend access credentials for ``target_user_id`` on ``dep``.

    Phase 1: a user may resend credentials to themself on any
    deployment they can member-view; staff may resend credentials to
    anyone on a deployment they can owner-view. This mirrors the
    behavior of the current ``/deployments/{id}/resend-access``
    endpoint, where the member view shows the resend-self button and
    the owner view shows resend-for-anyone.
    """
    if str(target_user_id) == str(user.userId):
        return can_view_deployment_member(user, dep, db)
    return can_view_deployment_owner(user, dep, db)


def ensure_resend_access(
    user: User,
    dep: Deployment,
    target_user_id: UUID | str,
    db: Session,
) -> None:
    if not can_resend_access(user, dep, target_user_id, db):
        raise _forbidden("deployment_resend_forbidden")


# ================================================================
# COURSES
# ================================================================
def is_course_teacher(user: User, course: Course, db: Session) -> bool:
    """Whether ``user`` is a designated teacher of ``course``.

    Phase 3: the ``course_teachers`` join table is now the source of
    truth. A user is a course-teacher for ``course`` exactly when:

      * their role is ``TEACHER`` (admins are handled separately by
        the admin bypass at each call site — they don't need a
        course-teacher row), AND
      * a ``(course_id, user_id)`` row exists in ``course_teachers``.

    Students never qualify, even if they were somehow inserted into
    the join table — the role gate stays primary so a misconfigured
    backfill can't silently grant a student teacher rights.
    """
    if user.role != UserRole.TEACHER:
        return False
    row = (
        db.query(CourseTeacher)
        .filter(
            CourseTeacher.courseId == course.courseId,
            CourseTeacher.userId == user.userId,
        )
        .first()
    )
    return row is not None


def is_course_teacher_id(user: User, course_id: UUID, db: Session) -> bool:
    """Variant of :func:`is_course_teacher` when only the course id
    is known. Used by helpers that need to filter rows by course
    without materialising the ``Course`` object.
    """
    if user.role != UserRole.TEACHER:
        return False
    row = (
        db.query(CourseTeacher)
        .filter(
            CourseTeacher.courseId == course_id,
            CourseTeacher.userId == user.userId,
        )
        .first()
    )
    return row is not None


def get_my_course_teacher_ids(user: User, db: Session) -> set[UUID]:
    """Load the set of course IDs ``user`` is a designated teacher of.

    Returns the empty set for non-teacher roles — admins use the
    admin-bypass instead of materialising every course id, and
    students cannot become course-teachers. Intended to be called
    ONCE per request and threaded into list-shaping helpers that
    would otherwise issue one ``is_course_teacher`` query per row
    (N+1). For v1 the call sites are few enough that this is mostly
    used as the data source for the ``?scope=course`` filter on the
    deployments list; future endpoints that need per-row visibility
    can reuse it.
    """
    if user.role != UserRole.TEACHER:
        return set()
    rows = (
        db.query(CourseTeacher.courseId)
        .filter(CourseTeacher.userId == user.userId)
        .all()
    )
    return {row[0] for row in rows}


def can_view_course_detail(user: User) -> bool:
    """Whether ``user`` may read course details + member rosters.

    Today: staff only. Students see the courses they're enrolled in
    via a different endpoint shape, not this one.
    """
    return _is_staff(user)


def ensure_view_course_detail(user: User) -> None:
    if not can_view_course_detail(user):
        raise _forbidden("role_required", [r.value for r in STAFF_ROLES])


def can_edit_course(user: User, course: Course, db: Session) -> bool:
    """Edit / delete ``course``.

    Phase 3: narrowed to "course-teacher of THIS course OR admin".
    A teacher who is not a designated teacher of ``course`` cannot
    edit it — the staff-blanket bypass is gone. Admins still pass
    through unconditionally because they're the platform-level
    safety net for course management.
    """
    if _is_admin(user):
        return True
    return is_course_teacher(user, course, db)


def ensure_edit_course(user: User, course: Course, db: Session) -> None:
    if not can_edit_course(user, course, db):
        raise _forbidden("course_edit_forbidden")


# ================================================================
# USERS
# ================================================================
def can_view_user(actor: User, target_id: UUID | str) -> bool:
    """Whether ``actor`` may read the profile at ``target_id``.

    Mirrors today: ``/me`` is always allowed (handled separately by
    the router), seeing someone else requires a staff role.
    """
    if str(actor.userId) == str(target_id):
        return True
    return _is_staff(actor)


def ensure_view_user(actor: User, target_id: UUID | str) -> None:
    if not can_view_user(actor, target_id):
        raise _forbidden("user_view_forbidden")


def can_change_user_role(actor: User) -> bool:
    """Whether ``actor`` may change someone else's role.

    Admin only. Today this is the only way a user transitions between
    student/teacher/admin in the platform DB (Keycloak claim is a
    separate concern handled at login).
    """
    return _is_admin(actor)


def ensure_change_user_role(actor: User) -> None:
    if not can_change_user_role(actor):
        raise _forbidden("role_required", [UserRole.ADMIN.value])


__all__ = [
    # Apps
    "can_view_app",
    "ensure_view_app",
    "can_list_all_apps",
    "ensure_list_all_apps",
    "can_edit_app",
    "ensure_edit_app",
    "can_delete_app",
    "ensure_delete_app",
    "can_submit_app_version",
    "ensure_submit_app_version",
    "can_approve_app_version",
    "ensure_approve_app_version",
    # Deployments
    "can_view_deployment_member",
    "ensure_view_deployment_member",
    "can_view_deployment_owner",
    "ensure_view_deployment_owner",
    "can_operate_deployment",
    "ensure_operate_deployment",
    "can_resend_access",
    "ensure_resend_access",
    # Courses
    "is_course_teacher",
    "is_course_teacher_id",
    "get_my_course_teacher_ids",
    "can_view_course_detail",
    "ensure_view_course_detail",
    "can_edit_course",
    "ensure_edit_course",
    # Users
    "can_view_user",
    "ensure_view_user",
    "can_change_user_role",
    "ensure_change_user_role",
]
