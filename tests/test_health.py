def test_health_returns_200(unauth_client):
    response = unauth_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "backend-api"


def test_health_no_auth_required(unauth_client):
    response = unauth_client.get("/health")
    assert response.status_code == 200
