from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import courses as crud_courses
from app.crud import users as crud_users
from app.database import get_db
from app.models import CourseTeacher, User, UserRole
from app.schemas import (
    CourseCreate,
    CourseMembersUpdate,
    CourseResponse,
    CourseUpdate,
    CourseWithUsers,
    UserResponse,
)
from app.utils.capabilities import ensure_edit_course
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import (
    require_admin,
    require_staff,
)

router = APIRouter()


# ----------------------------------------------------------------
# GET ALL COURSES
# ----------------------------------------------------------------
@router.get("/", response_model=list[CourseResponse])
def list_courses(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get all courses
    - **Students**: Can view all courses
    - **Teachers/Admins**: Can view all courses
    """
    courses = crud_courses.get_courses(db, skip=skip, limit=limit)
    return courses


# ----------------------------------------------------------------
# GET COURSE BY ID
# ----------------------------------------------------------------
@router.get("/{course_id}", response_model=CourseWithUsers)
def get_course(
    course_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """Get course by ID with all users"""
    course = crud_courses.get_course(db, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )
    return course


# ----------------------------------------------------------------
# CREATE COURSE (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.post("/", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
def create_course(
    course: CourseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff)
):
    """
    Create a new course
    - **Requires**: TEACHER or ADMIN role

    Phase 3: the creating teacher is automatically registered as a
    course-teacher of the new course via the ``course_teachers`` join
    table. This is what makes the subsequent edit/delete gate pass
    for them on the freshly created row without an extra round trip.
    Admins who create a course are NOT auto-added — admin rights
    already cover edit/delete, and adding them as course-teacher
    would muddy the "course-teacher" semantic with admin presence.
    The admin can opt-in via ``POST /courses/{id}/teachers/{user_id}``
    if they want their name visible on the course roster.
    """
    db_course = crud_courses.create_course(db, course)

    # Phase 3 — auto-register the creating teacher as course-teacher.
    # Skipped for admins because admin rights are role-shaped, not
    # course-scoped.
    if current_user.role == UserRole.TEACHER:
        db.add(
            CourseTeacher(
                courseId=db_course.courseId,
                userId=current_user.userId,
            )
        )
        db.commit()
        db.refresh(db_course)

    return db_course


# ----------------------------------------------------------------
# UPDATE COURSE (course-teacher of THIS course OR admin)
# ----------------------------------------------------------------
@router.put("/{course_id}", response_model=CourseResponse)
def update_course(
    course_id: UUID,
    course_update: CourseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Update a course

    Phase 3: only a designated course-teacher of THIS course (row in
    ``course_teachers``) or an admin may edit. A teacher who is not a
    course-teacher of the course gets 403 with the structured
    ``course_edit_forbidden`` payload. 404 takes precedence over 403
    for nonexistent courses to keep the existence-disclosure surface
    aligned with the rest of the API.
    """
    course = crud_courses.get_course(db, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )
    ensure_edit_course(current_user, course, db)

    updated = crud_courses.update_course(db, course_id, course_update)
    if not updated:
        # Concurrent delete between the 404-check and the update;
        # surface as 404 to keep the response contract stable.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )
    return updated


# ----------------------------------------------------------------
# DELETE COURSE (course-teacher of THIS course OR admin)
# ----------------------------------------------------------------
@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Delete a course

    Phase 3: only a designated course-teacher of THIS course or an
    admin may delete. Members of the course are detached
    (``users.courseId = NULL``) before the row goes away — no user
    account is destroyed; ``course_teachers`` rows for the course
    cascade away via the ON DELETE CASCADE on the join table.
    """
    course = crud_courses.get_course(db, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )
    ensure_edit_course(current_user, course, db)

    success = crud_courses.delete_course(db, course_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )
    return None


# ----------------------------------------------------------------
# COURSE MEMBERS
# ----------------------------------------------------------------
# Members live as ``users.courseId``. The endpoints below treat that
# FK as a small membership API so the UI can manage enrollment without
# poking the user-update endpoint (which has its own role-change rules
# that don't apply to "join a course").

@router.get("/{course_id}/users", response_model=list[UserResponse])
def list_course_members(
    course_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """List the users currently enrolled in ``course_id``.

    Teacher/Admin only — student rosters are management data.
    """
    course = crud_courses.get_course(db, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )
    return crud_courses.get_course_members(db, course_id)


@router.post(
    "/{course_id}/users",
    response_model=list[UserResponse],
    status_code=status.HTTP_200_OK,
)
def add_course_members(
    course_id: UUID,
    payload: CourseMembersUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Add (or move) a batch of users into ``course_id``.

    The frontend's add-modal uses the same Keycloak-backed user search
    as the deployment-team picker, so by the time we get here the
    user-ids exist in our DB. Returns the post-update member list of
    the course so the UI can refresh without a second round trip.
    """
    course = crud_courses.get_course(db, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )

    crud_courses.add_users_to_course(db, course_id, payload.userIds)
    return crud_courses.get_course_members(db, course_id)


@router.delete(
    "/{course_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_course_member(
    course_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Remove ``user_id`` from ``course_id``.

    Returns 404 if the user isn't actually enrolled in this course —
    we never silently detach somebody from a different course.
    """
    course = crud_courses.get_course(db, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )

    removed = crud_courses.remove_user_from_course(db, course_id, user_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of this course"
        )
    return None


# ----------------------------------------------------------------
# COURSE TEACHERS (admin-only management)
# ----------------------------------------------------------------
# The ``course_teachers`` join table is the source of truth for the
# course-teacher capability. ``POST /courses`` auto-registers the
# creating teacher; everything beyond that — adding a second teacher,
# revoking a teacher, swapping the roster around — is admin-only.
#
# Why admin-only: course-teachers already have edit/delete on their
# own course, so letting them mutate the roster would let one teacher
# kick another out at will. That can be relaxed later (e.g. "any
# course-teacher of the course may add another teacher to it") but
# we keep it locked to admin until there's a concrete need.


@router.post(
    "/{course_id}/teachers/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def add_course_teacher(
    course_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Register ``user_id`` as a course-teacher of ``course_id``.

    Admin-only. The target user must already exist and have role
    ``TEACHER`` — adding a student or admin to this table would
    contradict the ``is_course_teacher`` gate (which requires
    ``role == TEACHER``) and create a row that the capability
    function silently ignores. We refuse those up-front with a
    structured 422.

    Idempotent: re-adding an existing pair is a no-op (the composite
    primary key catches the duplicate, but we check up-front to keep
    the success/no-op path distinct from "user not found").
    """
    course = crud_courses.get_course(db, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found",
        )

    target = crud_users.get_user(db, user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if target.role != UserRole.TEACHER:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "role_required",
                "required": [UserRole.TEACHER.value],
            },
        )

    existing = (
        db.query(CourseTeacher)
        .filter(
            CourseTeacher.courseId == course_id,
            CourseTeacher.userId == user_id,
        )
        .first()
    )
    if existing is None:
        db.add(CourseTeacher(courseId=course_id, userId=user_id))
        db.commit()
    return None


@router.delete(
    "/{course_id}/teachers/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_course_teacher(
    course_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Revoke ``user_id``'s course-teacher status on ``course_id``.

    Admin-only. Returns 404 if the user wasn't a course-teacher of
    this course — we never silently delete from a different course.
    Note this does NOT remove the user from ``users.courseId``;
    course membership and course-teacher status are independent
    facets (a teacher can stop teaching a course but stay enrolled
    as a regular member, or vice versa).
    """
    course = crud_courses.get_course(db, course_id)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found",
        )

    row = (
        db.query(CourseTeacher)
        .filter(
            CourseTeacher.courseId == course_id,
            CourseTeacher.userId == user_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a teacher of this course",
        )

    db.delete(row)
    db.commit()
    return None
