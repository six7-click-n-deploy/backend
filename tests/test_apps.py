import uuid

import pytest
from fastapi.testclient import TestClient

from app.crud import apps as crud_apps
from app.database import get_db
from app.main import app as fastapi_app
from app.models import User, UserRole
from app.schemas import AppCreate
from app.utils.keycloak_auth import get_current_user_keycloak
from tests.conftest import TestingSessionLocal


@pytest.mark.api
def test_get_apps_authenticated(client):
    response = client.get("/apps/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.api
def test_get_apps_unauthenticated(unauth_client):
    response = unauth_client.get("/apps/")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PUT /apps/{app_id} — backend#49
# ---------------------------------------------------------------------------
# ``git_link`` must be immutable on update: once an app has deployments,
# changing the repo would make existing deployments point at a different
# repo than they originally deployed.

ORIGINAL_GIT_LINK = "https://example.com/orig.git"


@pytest.fixture
def existing_app(db, mock_user):
    """Persist an app owned by ``mock_user`` and yield it."""
    return crud_apps.create_app(
        db,
        AppCreate(
            name="Original Name",
            description="Original description",
            git_link=ORIGINAL_GIT_LINK,
        ),
        mock_user.userId,
    )


@pytest.mark.api
def test_update_app_changes_name_and_description(client, existing_app, db):
    response = client.put(
        f"/apps/{existing_app.appId}",
        json={"name": "Renamed", "description": "New description"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["description"] == "New description"
    # git_link round-trips unchanged
    assert body["git_link"] == ORIGINAL_GIT_LINK

    db.expire_all()
    refreshed = crud_apps.get_app(db, existing_app.appId)
    assert refreshed.name == "Renamed"
    assert refreshed.description == "New description"
    assert refreshed.git_link == ORIGINAL_GIT_LINK


@pytest.mark.api
def test_update_app_ignores_git_link(client, existing_app, db):
    """Sending ``git_link`` in the request body must be silently ignored."""
    response = client.put(
        f"/apps/{existing_app.appId}",
        json={
            "name": "Renamed",
            "git_link": "https://evil.example/hijack.git",
        },
    )
    assert response.status_code == 200
    assert response.json()["git_link"] == ORIGINAL_GIT_LINK

    db.expire_all()
    refreshed = crud_apps.get_app(db, existing_app.appId)
    assert refreshed.git_link == ORIGINAL_GIT_LINK
    assert refreshed.name == "Renamed"


@pytest.mark.api
def test_update_app_not_found(client):
    response = client.put(
        f"/apps/{uuid.uuid4()}",
        json={"name": "Anything"},
    )
    assert response.status_code == 404


@pytest.mark.api
def test_update_app_unauthorized_user_rejected(db, existing_app):
    """A STUDENT who is not the owner must not be able to update the app."""
    other_student = User(
        userId=uuid.uuid4(),
        keycloak_id="other-student-keycloak-id",
        email="other@dhbw.de",
        username="otherstudent",
        firstName="Other",
        lastName="Student",
        role=UserRole.STUDENT,
    )
    db.add(other_student)
    db.commit()
    db.refresh(other_student)

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_current_user_keycloak] = lambda: other_student
    fastapi_app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(fastapi_app) as student_client:
            response = student_client.put(
                f"/apps/{existing_app.appId}",
                json={"name": "Hijacked"},
            )
        assert response.status_code == 403
    finally:
        fastapi_app.dependency_overrides.clear()
