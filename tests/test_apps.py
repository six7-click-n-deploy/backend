import pytest


@pytest.mark.api
def test_get_apps_authenticated(client):
    response = client.get("/apps/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.api
def test_get_apps_unauthenticated(unauth_client):
    response = unauth_client.get("/apps/")
    assert response.status_code in (401, 403)
