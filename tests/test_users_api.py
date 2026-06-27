"""Phase B1 — Tests für die ``/users``-Endpoints.

Deckt die wichtigsten Pfade aus :mod:`app.routers.users` ab:

* ``GET /users/me`` — eigenes Profil
* ``GET /users/`` — Listen-Endpoint mit RBAC (Staff = Teacher/Admin)
* ``GET /users/search`` — Keycloak-Auto-Complete
* ``GET /users/{id}`` — Detailzugriff inkl. Cross-User-Schutz
* ``GET /users/{id}/statistics`` — Self-View
* ``PUT /users/{id}`` — Role-Change (Bug #1 Regression)

Alle Tests laufen gegen die echte FastAPI-App (kein Mock-Routing) und
nutzen die Standard-Fixtures aus ``tests/conftest.py``. Keycloak-Calls
werden auf Service-Ebene über ``unittest.mock.patch`` ersetzt — die
Keycloak-API selbst ist im Test-Setup nicht erreichbar.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.models import User, UserRole


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
def _make_user(
    db,
    *,
    role: UserRole = UserRole.STUDENT,
    username: str | None = None,
    email: str | None = None,
    keycloak_id: str | None = None,
) -> User:
    """Persistiert einen User mit zufälligen, eindeutigen Default-Werten."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        userId=uuid.uuid4(),
        keycloak_id=keycloak_id or f"kc-{suffix}",
        email=email or f"user-{suffix}@dhbw.de",
        username=username or f"user-{suffix}",
        firstName="Some",
        lastName="One",
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ----------------------------------------------------------------
# GET /users/me
# ----------------------------------------------------------------
@pytest.mark.integration
def test_me_returns_current_user_profile(client, mock_user):
    """``/users/me`` liefert das Profil des authentifizierten Users."""
    response = client.get("/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["userId"] == str(mock_user.userId)
    assert body["email"] == mock_user.email
    assert body["username"] == mock_user.username
    assert body["role"] == mock_user.role.value


@pytest.mark.integration
def test_me_unauthenticated_401(unauth_client):
    """Ohne Bearer-Token verweigert die API den Zugriff auf ``/users/me``."""
    response = unauth_client.get("/users/me")
    # FastAPI's HTTPBearer mappt fehlende Credentials auf 403; mit Token,
    # aber ungültiger Signatur kommt 401. Beide sind „nicht erlaubt".
    assert response.status_code in (401, 403)


# ----------------------------------------------------------------
# GET /users/
# ----------------------------------------------------------------
@pytest.mark.integration
def test_list_users_admin_only(admin_client, db, mock_admin):
    """Admin darf die User-Liste lesen."""
    _make_user(db, role=UserRole.STUDENT, keycloak_id=None)
    with patch(
        "app.routers.users.get_keycloak_users_by_ids", return_value={}
    ):
        response = admin_client.get("/users/")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    # Admin selbst plus der oben angelegte Student.
    usernames = {u["username"] for u in body}
    assert mock_admin.username in usernames


@pytest.mark.integration
def test_list_users_teacher_or_admin_ok(client, db, mock_user):
    """Teacher zählt als Staff — RBAC erlaubt den Listen-Zugriff."""
    _make_user(db, role=UserRole.STUDENT, keycloak_id=None)
    with patch(
        "app.routers.users.get_keycloak_users_by_ids", return_value={}
    ):
        response = client.get("/users/")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    usernames = {u["username"] for u in body}
    assert mock_user.username in usernames


@pytest.mark.integration
def test_list_users_student_forbidden(student_client):
    """Student darf die Liste *nicht* sehen — 403 aus ``require_staff``."""
    response = student_client.get("/users/")
    assert response.status_code == 403


# ----------------------------------------------------------------
# GET /users/search
# ----------------------------------------------------------------
@pytest.mark.integration
def test_search_users_authenticated_ok(client):
    """Keycloak-Suche liefert die gemockten KC-User als Liste zurück.

    Die Route ``GET /users/search`` ruft ``search_keycloak_users`` ab und
    legt jeden Treffer per ``sync_user_from_keycloak`` lokal an. Beide
    Aufrufe werden gepatcht, weil im Test-Setup kein Keycloak läuft.
    """
    kc_hit = {
        "id": "kc-search-hit",
        "username": "alice",
        "email": "alice@dhbw.de",
        "firstName": "Alice",
        "lastName": "Example",
        "enabled": True,
    }

    def _fake_sync(db, kc_user):
        # Realer Code legt einen User an und gibt das ORM-Objekt zurück.
        user = User(
            userId=uuid.uuid4(),
            keycloak_id=kc_user["id"],
            email=kc_user["email"],
            username=kc_user["username"],
            firstName=kc_user.get("firstName"),
            lastName=kc_user.get("lastName"),
            role=UserRole.STUDENT,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    with patch(
        "app.routers.users.search_keycloak_users", return_value=[kc_hit]
    ), patch(
        "app.utils.keycloak_auth.sync_user_from_keycloak", side_effect=_fake_sync
    ):
        response = client.get("/users/search", params={"query": "ali"})

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["username"] == "alice"
    assert body[0]["firstName"] == "Alice"


# ----------------------------------------------------------------
# GET /users/{id}
# ----------------------------------------------------------------
@pytest.mark.integration
def test_get_user_self_ok_for_student(student_client, mock_student):
    """Student darf das eigene Profil per ``GET /users/{id}`` lesen."""
    response = student_client.get(f"/users/{mock_student.userId}")
    assert response.status_code == 200
    body = response.json()
    assert body["userId"] == str(mock_student.userId)


@pytest.mark.integration
def test_get_user_other_student_blocked_403(student_client, db):
    """Student darf *keinen* anderen User abrufen."""
    other = _make_user(db, role=UserRole.STUDENT)
    response = student_client.get(f"/users/{other.userId}")
    assert response.status_code == 403


@pytest.mark.integration
def test_get_user_other_teacher_ok(client, db):
    """Teacher darf das Profil eines anderen Users sehen."""
    other = _make_user(db, role=UserRole.STUDENT)
    response = client.get(f"/users/{other.userId}")
    assert response.status_code == 200
    body = response.json()
    assert body["userId"] == str(other.userId)


# ----------------------------------------------------------------
# GET /users/{id}/statistics
# ----------------------------------------------------------------
@pytest.mark.integration
def test_get_user_statistics_self_ok(student_client, mock_student):
    """Owner darf die eigene Statistik abrufen — auch ohne Apps/Deployments."""
    response = student_client.get(
        f"/users/{mock_student.userId}/statistics"
    )
    assert response.status_code == 200
    body = response.json()
    # Frisch angelegter Student: alle Zähler stehen auf 0.
    assert body == {
        "total_apps": 0,
        "total_deployments": 0,
        "successful_deployments": 0,
        "failed_deployments": 0,
        "pending_deployments": 0,
    }


# ----------------------------------------------------------------
# PUT /users/{id}
# ----------------------------------------------------------------
@pytest.mark.integration
def test_update_user_admin_can_change_role(admin_client, db):
    """Admin darf die Rolle eines Students auf Teacher heben."""
    target = _make_user(db, role=UserRole.STUDENT)
    response = admin_client.put(
        f"/users/{target.userId}",
        json={"role": UserRole.TEACHER.value},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["userId"] == str(target.userId)
    assert body["role"] == UserRole.TEACHER.value


@pytest.mark.integration
def test_update_user_non_admin_cannot_change_role_403(client, db):
    """Bug #1 Regression: Teacher darf ``role`` *nicht* setzen — 403.

    Erwartete Payload aus :func:`ensure_change_user_role`:
    ``{"code": "role_required", "required": ["admin"]}``.
    """
    target = _make_user(db, role=UserRole.STUDENT)
    response = client.put(
        f"/users/{target.userId}",
        json={"role": UserRole.ADMIN.value},
    )
    assert response.status_code == 403
    detail = response.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "role_required"
    assert detail.get("required") == [UserRole.ADMIN.value]
