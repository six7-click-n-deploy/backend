"""Unit-Tests für app.utils.permissions (Rollen- und Deployment-Helfer)."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, status

from app.models import UserRole
from app.utils.permissions import (
    ADMIN_ROLES,
    STAFF_ROLES,
    check_course_access,
    ensure_course_access,
    ensure_deployment_owner_view,
    get_current_active_user,
    get_current_admin,
    get_current_student,
    get_current_teacher_or_admin,
    is_deployment_owner_view,
    require_role,
    require_roles,
)


def _make_user(role: UserRole, user_id: str = "user-1", course_id: str | None = None) -> MagicMock:
    """Stubt einen User mit role / userId / courseId."""
    user = MagicMock()
    user.role = role
    user.userId = user_id
    user.courseId = course_id
    return user


def _make_deployment(user_id: str = "owner-1", deployment_id: str = "dep-1") -> MagicMock:
    deployment = MagicMock()
    deployment.userId = user_id
    deployment.deploymentId = deployment_id
    return deployment


# ----------------------------------------------------------------
# role tuples
# ----------------------------------------------------------------
@pytest.mark.unit
def test_admin_roles_tuple_contains_only_admin():
    assert ADMIN_ROLES == (UserRole.ADMIN,)


@pytest.mark.unit
def test_staff_roles_tuple_contains_teacher_and_admin():
    assert set(STAFF_ROLES) == {UserRole.TEACHER, UserRole.ADMIN}


# ----------------------------------------------------------------
# get_current_active_user
# ----------------------------------------------------------------
@pytest.mark.unit
def test_get_current_active_user_returns_user_unchanged():
    user = _make_user(UserRole.STUDENT)
    assert get_current_active_user(current_user=user) is user


# ----------------------------------------------------------------
# require_roles factory
# ----------------------------------------------------------------
@pytest.mark.unit
def test_require_roles_without_args_raises_value_error():
    with pytest.raises(ValueError):
        require_roles()


@pytest.mark.unit
def test_require_roles_admin_allows_admin_user():
    dep = require_roles(UserRole.ADMIN)
    admin = _make_user(UserRole.ADMIN)
    assert dep(user=admin) is admin


@pytest.mark.unit
def test_require_roles_admin_denies_student():
    dep = require_roles(UserRole.ADMIN)
    student = _make_user(UserRole.STUDENT)
    with pytest.raises(HTTPException) as exc:
        dep(user=student)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["code"] == "role_required"
    assert exc.value.detail["required"] == [UserRole.ADMIN.value]


@pytest.mark.unit
def test_require_roles_admin_denies_teacher():
    dep = require_roles(UserRole.ADMIN)
    teacher = _make_user(UserRole.TEACHER)
    with pytest.raises(HTTPException) as exc:
        dep(user=teacher)
    assert exc.value.status_code == 403


@pytest.mark.unit
def test_require_roles_staff_allows_teacher_and_admin():
    dep = require_roles(UserRole.TEACHER, UserRole.ADMIN)
    teacher = _make_user(UserRole.TEACHER)
    admin = _make_user(UserRole.ADMIN)
    assert dep(user=teacher) is teacher
    assert dep(user=admin) is admin


@pytest.mark.unit
def test_require_roles_staff_denies_student():
    dep = require_roles(UserRole.TEACHER, UserRole.ADMIN)
    student = _make_user(UserRole.STUDENT)
    with pytest.raises(HTTPException) as exc:
        dep(user=student)
    assert exc.value.status_code == 403
    assert set(exc.value.detail["required"]) == {
        UserRole.TEACHER.value,
        UserRole.ADMIN.value,
    }


# ----------------------------------------------------------------
# get_current_admin
# ----------------------------------------------------------------
@pytest.mark.unit
def test_get_current_admin_allows_admin():
    admin = _make_user(UserRole.ADMIN)
    assert get_current_admin(current_user=admin) is admin


@pytest.mark.unit
def test_get_current_admin_denies_teacher():
    teacher = _make_user(UserRole.TEACHER)
    with pytest.raises(HTTPException) as exc:
        get_current_admin(current_user=teacher)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "role_required"
    assert exc.value.detail["required"] == [UserRole.ADMIN.value]


@pytest.mark.unit
def test_get_current_admin_denies_student():
    student = _make_user(UserRole.STUDENT)
    with pytest.raises(HTTPException) as exc:
        get_current_admin(current_user=student)
    assert exc.value.status_code == 403


# ----------------------------------------------------------------
# get_current_teacher_or_admin
# ----------------------------------------------------------------
@pytest.mark.unit
def test_get_current_teacher_or_admin_allows_teacher():
    teacher = _make_user(UserRole.TEACHER)
    assert get_current_teacher_or_admin(current_user=teacher) is teacher


@pytest.mark.unit
def test_get_current_teacher_or_admin_allows_admin():
    admin = _make_user(UserRole.ADMIN)
    assert get_current_teacher_or_admin(current_user=admin) is admin


@pytest.mark.unit
def test_get_current_teacher_or_admin_denies_student():
    student = _make_user(UserRole.STUDENT)
    with pytest.raises(HTTPException) as exc:
        get_current_teacher_or_admin(current_user=student)
    assert exc.value.status_code == 403
    assert set(exc.value.detail["required"]) == {
        UserRole.TEACHER.value,
        UserRole.ADMIN.value,
    }


# ----------------------------------------------------------------
# get_current_student
# ----------------------------------------------------------------
@pytest.mark.unit
def test_get_current_student_allows_student():
    student = _make_user(UserRole.STUDENT)
    assert get_current_student(current_user=student) is student


@pytest.mark.unit
def test_get_current_student_denies_teacher():
    teacher = _make_user(UserRole.TEACHER)
    with pytest.raises(HTTPException) as exc:
        get_current_student(current_user=teacher)
    assert exc.value.status_code == 403
    assert exc.value.detail["required"] == [UserRole.STUDENT.value]


@pytest.mark.unit
def test_get_current_student_denies_admin():
    admin = _make_user(UserRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        get_current_student(current_user=admin)
    assert exc.value.status_code == 403


# ----------------------------------------------------------------
# require_role decorator
# ----------------------------------------------------------------
@pytest.mark.unit
def test_require_role_decorator_allows_matching_role():
    @require_role([UserRole.ADMIN])
    async def endpoint(*, current_user):
        return "ok"

    admin = _make_user(UserRole.ADMIN)
    result = asyncio.run(endpoint(current_user=admin))
    assert result == "ok"


@pytest.mark.unit
def test_require_role_decorator_denies_non_matching_role():
    @require_role([UserRole.ADMIN])
    async def endpoint(*, current_user):
        return "ok"

    student = _make_user(UserRole.STUDENT)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(current_user=student))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "role_required"


@pytest.mark.unit
def test_require_role_decorator_raises_401_without_current_user():
    @require_role([UserRole.ADMIN])
    async def endpoint(*, current_user=None):
        return "ok"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(current_user=None))
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.unit
def test_require_role_decorator_allows_one_of_multiple():
    @require_role([UserRole.TEACHER, UserRole.ADMIN])
    async def endpoint(*, current_user):
        return current_user.role

    teacher = _make_user(UserRole.TEACHER)
    assert asyncio.run(endpoint(current_user=teacher)) == UserRole.TEACHER


# ----------------------------------------------------------------
# check_course_access / ensure_course_access
# ----------------------------------------------------------------
@pytest.mark.unit
def test_check_course_access_admin_always_true():
    admin = _make_user(UserRole.ADMIN, course_id="other-course")
    assert check_course_access("any-course", admin) is True


@pytest.mark.unit
def test_check_course_access_matches_for_student_in_course():
    student = _make_user(UserRole.STUDENT, course_id="course-42")
    assert check_course_access("course-42", student) is True


@pytest.mark.unit
def test_check_course_access_rejects_student_in_other_course():
    student = _make_user(UserRole.STUDENT, course_id="course-42")
    assert check_course_access("other-course", student) is False


@pytest.mark.unit
def test_check_course_access_rejects_teacher_in_other_course():
    teacher = _make_user(UserRole.TEACHER, course_id="course-42")
    assert check_course_access("other-course", teacher) is False


@pytest.mark.unit
def test_ensure_course_access_passes_for_matching_course():
    student = _make_user(UserRole.STUDENT, course_id="course-1")
    # No exception expected
    ensure_course_access("course-1", student)


@pytest.mark.unit
def test_ensure_course_access_passes_for_admin():
    admin = _make_user(UserRole.ADMIN, course_id=None)
    ensure_course_access("any-course", admin)


@pytest.mark.unit
def test_ensure_course_access_raises_403_for_mismatch():
    student = _make_user(UserRole.STUDENT, course_id="course-1")
    with pytest.raises(HTTPException) as exc:
        ensure_course_access("course-2", student)
    assert exc.value.status_code == 403


# ----------------------------------------------------------------
# is_deployment_owner_view / ensure_deployment_owner_view
# ----------------------------------------------------------------
@pytest.mark.unit
def test_is_deployment_owner_view_owner_returns_true():
    user = _make_user(UserRole.STUDENT, user_id="owner-1")
    deployment = _make_deployment(user_id="owner-1")
    assert is_deployment_owner_view(deployment, user) is True


@pytest.mark.unit
def test_is_deployment_owner_view_non_owner_student_returns_false():
    user = _make_user(UserRole.STUDENT, user_id="someone-else")
    deployment = _make_deployment(user_id="owner-1")
    assert is_deployment_owner_view(deployment, user) is False


@pytest.mark.unit
def test_is_deployment_owner_view_teacher_bypass_returns_true():
    teacher = _make_user(UserRole.TEACHER, user_id="teacher-1")
    deployment = _make_deployment(user_id="owner-1")
    assert is_deployment_owner_view(deployment, teacher) is True


@pytest.mark.unit
def test_is_deployment_owner_view_admin_bypass_returns_true():
    admin = _make_user(UserRole.ADMIN, user_id="admin-1")
    deployment = _make_deployment(user_id="owner-1")
    assert is_deployment_owner_view(deployment, admin) is True


@pytest.mark.unit
def test_ensure_deployment_owner_view_passes_for_owner():
    user = _make_user(UserRole.STUDENT, user_id="owner-1")
    deployment = _make_deployment(user_id="owner-1")
    ensure_deployment_owner_view(deployment, user)


@pytest.mark.unit
def test_ensure_deployment_owner_view_passes_for_staff():
    teacher = _make_user(UserRole.TEACHER, user_id="teacher-1")
    deployment = _make_deployment(user_id="owner-1")
    ensure_deployment_owner_view(deployment, teacher)


@pytest.mark.unit
def test_ensure_deployment_owner_view_raises_for_non_owner_student():
    student = _make_user(UserRole.STUDENT, user_id="member-1")
    deployment = _make_deployment(user_id="owner-1")
    with pytest.raises(HTTPException) as exc:
        ensure_deployment_owner_view(deployment, student)
    assert exc.value.status_code == 403
