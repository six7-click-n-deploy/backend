from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.models import Course
from app.schemas import CourseCreate, CourseUpdate


def get_course(db: Session, course_id: UUID) -> Optional[Course]:
    """Get course by ID"""
    return db.query(Course).filter(Course.courseId == course_id).first()


def get_courses(db: Session, skip: int = 0, limit: int = 100) -> List[Course]:
    """Get all courses"""
    return db.query(Course).offset(skip).limit(limit).all()


def create_course(db: Session, course: CourseCreate) -> Course:
    """Create a new course"""
    db_course = Course(name=course.name)
    db.add(db_course)
    db.commit()
    db.refresh(db_course)
    return db_course


def update_course(db: Session, course_id: UUID, course_update: CourseUpdate) -> Optional[Course]:
    """Update course information"""
    db_course = get_course(db, course_id)
    if not db_course:
        return None
    
    update_data = course_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_course, field, value)
    
    db.commit()
    db.refresh(db_course)
    return db_course


def delete_course(db: Session, course_id: UUID) -> bool:
    """Delete a course"""
    db_course = get_course(db, course_id)
    if not db_course:
        return False
    
    db.delete(db_course)
    db.commit()
    return True
