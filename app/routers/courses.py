from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import courses as crud_courses
from app.database import get_db
from app.models import User
from app.schemas import CourseCreate, CourseResponse, CourseUpdate, CourseWithUsers
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
# DELETE COURSE (ADMIN ONLY)
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
    """
    success = crud_courses.delete_course(db, course_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found"
        )
    return None
