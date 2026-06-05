from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import courses as crud_courses
from app.database import get_db
from app.models import User
from app.schemas import (
    CourseCreate,
    CourseMembersUpdate,
    CourseResponse,
    CourseUpdate,
    CourseWithUsers,
    UserResponse,
)
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import get_current_teacher_or_admin

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
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Create a new course
    - **Requires**: TEACHER or ADMIN role
    """
    return crud_courses.create_course(db, course)


# ----------------------------------------------------------------
# UPDATE COURSE (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.put("/{course_id}", response_model=CourseResponse)
def update_course(
    course_id: UUID,
    course_update: CourseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Update a course
    - **Requires**: TEACHER or ADMIN role
    """
    course = crud_courses.update_course(db, course_id, course_update)
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )
    return course


# ----------------------------------------------------------------
# DELETE COURSE (TEACHER/ADMIN)
# ----------------------------------------------------------------
@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Delete a course
    - **Requires**: TEACHER or ADMIN role

    Members of the course are detached (``users.courseId = NULL``)
    before the row goes away — no user account is destroyed.
    """
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
    current_user: User = Depends(get_current_teacher_or_admin),
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
    current_user: User = Depends(get_current_teacher_or_admin),
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
    current_user: User = Depends(get_current_teacher_or_admin),
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
