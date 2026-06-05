from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import apps as crud_apps
from app.crud import deployments as crud_deployments
from app.crud import users as crud_users
from app.database import get_db
from app.models import User, UserRole
from app.schemas import UserResponse, UserStatistics, UserUpdate, UserWithCourse
from app.utils.keycloak_auth import (
    get_current_user_keycloak,
    get_keycloak_users_by_ids,
    search_keycloak_users,
)
from app.utils.permissions import (
    ensure_resource_access,
    get_current_admin,
    get_current_teacher_or_admin,
)

router = APIRouter()

# ----------------------------------------------------------------
# GET CURRENT USER
# ----------------------------------------------------------------
@router.get("/me", response_model=UserWithCourse)
def get_me(current_user: User = Depends(get_current_user_keycloak)):
    """Get current authenticated user with course information"""
    return current_user

# ----------------------------------------------------------------
# GET ALL USERS (TEACHER/ADMIN ONLY)
# ----------------------------------------------------------------
@router.get("/", response_model=list[UserResponse])
def list_users(
    skip: int = 0,
    limit: int = 100,
    role: UserRole | None = None,
    course_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Get all users with optional filters
    - **Requires**: TEACHER or ADMIN role
    """
    users = crud_users.get_users(db, skip=skip, limit=limit, role=role, course_id=course_id)
    # Enrich users with Keycloak names when keycloak_id is present
    kc_ids = [u.keycloak_id for u in users if getattr(u, 'keycloak_id', None)]
    kc_map = {}
    if kc_ids:
        try:
            kc_map = get_keycloak_users_by_ids(kc_ids)
        except HTTPException:
            # If enrichment fails, continue returning base users
            kc_map = {}

    result = []
    for u in users:
        user_obj = {
            "userId": u.userId,
            "email": u.email,
            "username": u.username,
            "role": u.role,
            "courseId": u.courseId,
            "created_at": u.created_at,
            "keycloak_id": getattr(u, 'keycloak_id', None),
            # default empty strings if not available
            "firstName": None,
            "lastName": None,
        }
        if user_obj["keycloak_id"] and user_obj["keycloak_id"] in kc_map:
            kc = kc_map[user_obj["keycloak_id"]]
            user_obj["firstName"] = kc.get("firstName")
            user_obj["lastName"] = kc.get("lastName")
        result.append(user_obj)

    return result

# ----------------------------------------------------------------
# SEARCH USERS FROM KEYCLOAK
# ----------------------------------------------------------------
@router.get("/search")
def search_users_keycloak(
    query: str,
    limit: int = 10,
    current_user: User = Depends(get_current_teacher_or_admin)
):
    """
    Search users directly from Keycloak by username, email, or name
    - **Requires**: TEACHER or ADMIN role
    - Returns users from Keycloak (not local DB)

    Response:
    - id: Keycloak user ID
    - username: Username
    - email: Email address
    - firstName: First name
    - lastName: Last name
    - enabled: Account enabled status
    """
    if not query or len(query) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query must be at least 2 characters"
        )

    from app.database import get_db
    db = next(get_db())
    from app.utils.keycloak_auth import sync_user_from_keycloak

    keycloak_users = search_keycloak_users(query, limit)
    results = []
    for kc_user in keycloak_users:
        # User in lokaler DB anlegen/aktualisieren (zentral)
        db_user = sync_user_from_keycloak(db, kc_user)
        results.append({
            "userId": db_user.userId,
            "email": db_user.email,
            "username": db_user.username,
            "role": db_user.role,
            "courseId": db_user.courseId,
            "created_at": db_user.created_at,
            "keycloak_id": db_user.keycloak_id,
            "firstName": kc_user.get("firstName"),
            "lastName": kc_user.get("lastName"),
        })
    return results

# ----------------------------------------------------------------
# GET USER BY ID
# ----------------------------------------------------------------
@router.get("/{user_id}", response_model=UserWithCourse)
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get user by ID
    - **Students**: Can only view their own profile
    - **Teachers/Admins**: Can view any profile
    """
    user = crud_users.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Check access permission
    if current_user.role == UserRole.STUDENT and user_id != current_user.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own profile"
        )

    return user

# ----------------------------------------------------------------
# GET USER STATISTICS
# ----------------------------------------------------------------
@router.get("/{user_id}/statistics", response_model=UserStatistics)
def get_user_statistics(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get user statistics
    - **Owner or Teacher/Admin** can view
    """
    user = crud_users.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Check access permission
    ensure_resource_access(user_id, current_user)

    # Get statistics
    apps = crud_apps.get_apps(db, user_id=user_id, limit=1000)
    deployments = crud_deployments.get_deployments(db, user_id=user_id, limit=1000)

    return UserStatistics(
        total_apps=len(apps),
        total_deployments=len(deployments),
        successful_deployments=len([d for d in deployments if d.status.value == "success"]),
        failed_deployments=len([d for d in deployments if d.status.value == "failed"]),
        pending_deployments=len([d for d in deployments if d.status.value == "pending"])
    )

# ----------------------------------------------------------------
# UPDATE USER
# ----------------------------------------------------------------
@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: UUID,
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Update user information
    - **Students**: Can only update their own profile (no role change)
    - **Teachers/Admins**: Can update any profile
    """
    user = crud_users.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Students can only update their own profile
    if current_user.role == UserRole.STUDENT and user_id != current_user.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own profile"
        )

    # Students cannot change their role
    if current_user.role == UserRole.STUDENT and user_update.role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change your role"
        )

    updated_user = crud_users.update_user(db, user_id, user_update)
    return updated_user


# ----------------------------------------------------------------
# DELETE USER (ADMIN ONLY)
# ----------------------------------------------------------------
@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Delete a user
    - **Requires**: ADMIN role
    """
    success = crud_users.delete_user(db, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return None
