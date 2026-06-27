"""Phase B7: API-level tests for `/me/openstack-credentials`.

Covers the five HTTP endpoints in `app/routers/openstack_credentials.py`:

  * GET    /me/openstack-credentials
  * PUT    /me/openstack-credentials
  * PUT    /me/openstack-credentials/from-yaml
  * POST   /me/openstack-credentials/test
  * DELETE /me/openstack-credentials

The OpenStack Keystone round-trip and the clouds.yaml parser are patched —
we are exercising the router/CRUD/schema wiring, not the SDK. Each
mutation endpoint always calls the validator, so the patch must yield
(ok, error).
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.crud import openstack_credentials as crud_creds
from app.models import OpenStackAuthType, UserOpenStackCredential
from app.schemas import OpenStackCredentialUpsert
from app.utils import crypto


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
_PASSWORD_PAYLOAD = {
    "auth_type": "password",
    "auth_url": "https://keystone.example/v3",
    "region_name": "RegionOne",
    "interface": "public",
    "identity_api_version": "3",
    "project_id": "project-uuid",
    "project_name": "demo",
    "user_domain_name": "Default",
    "project_domain_name": "Default",
    "identifier": "alice",
    "secret": "s3cret",
}

_APPCRED_PAYLOAD = {
    "auth_type": "v3applicationcredential",
    "auth_url": "https://keystone.example/v3",
    "region_name": "RegionOne",
    "interface": "public",
    "identity_api_version": "3",
    "identifier": "appcred-id",
    "secret": "appcred-secret",
}

_CLOUDS_YAML_SINGLE = """\
clouds:
  mycloud:
    auth_type: v3applicationcredential
    auth:
      auth_url: https://keystone.example/v3
      application_credential_id: appcred-id
      application_credential_secret: appcred-secret
    region_name: RegionOne
    interface: public
    identity_api_version: 3
"""


def _seed_credential(db, user, *, identifier="alice", secret="s3cret"):
    row = UserOpenStackCredential(
        credentialId=uuid.uuid4(),
        userId=user.userId,
        auth_type=OpenStackAuthType.PASSWORD,
        auth_url="https://keystone.example/v3",
        region_name="RegionOne",
        interface="public",
        identity_api_version="3",
        project_id="project-uuid",
        project_name="demo",
        user_domain_name="Default",
        project_domain_name="Default",
        encrypted_identifier=crypto.encrypt(identifier),
        encrypted_secret=crypto.encrypt(secret),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ----------------------------------------------------------------
# GET /me/openstack-credentials
# ----------------------------------------------------------------
@pytest.mark.integration
def test_get_my_credentials_returns_redacted_view(client, db, mock_user):
    _seed_credential(db, mock_user, identifier="alice", secret="s3cret")

    response = client.get("/me/openstack-credentials")

    assert response.status_code == 200
    body = response.json()
    assert body["has_credential"] is True
    assert body["auth_url"] == "https://keystone.example/v3"
    assert body["project_id"] == "project-uuid"
    assert body["user_domain_name"] == "Default"
    # Secret material MUST NOT appear in any form on the response.
    serialized = response.text
    assert "alice" not in serialized
    assert "s3cret" not in serialized
    assert "identifier" not in body
    assert "secret" not in body
    assert "encrypted_identifier" not in body
    assert "encrypted_secret" not in body


@pytest.mark.integration
def test_get_my_credentials_404_when_absent(client, mock_user):
    # The endpoint is documented to always return 200 with
    # ``has_credential=False`` so the frontend can render its empty state
    # without an extra round-trip. The test name uses "404" colloquially
    # for "no credential present"; the contract is 200 + flag false.
    response = client.get("/me/openstack-credentials")

    assert response.status_code == 200
    body = response.json()
    assert body["has_credential"] is False
    assert body["auth_url"] is None
    assert body["auth_type"] is None


# ----------------------------------------------------------------
# PUT /me/openstack-credentials
# ----------------------------------------------------------------
@pytest.mark.integration
def test_upsert_credentials_persists_encrypted_password(client, db, mock_user):
    with patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
        return_value=(True, None),
    ) as mock_validate:
        response = client.put("/me/openstack-credentials", json=_PASSWORD_PAYLOAD)

    assert response.status_code == 200, response.text
    mock_validate.assert_called_once()

    body = response.json()
    assert body["has_credential"] is True
    assert body["last_validated_at"] is not None
    assert body["last_validation_error"] is None

    row = crud_creds.get_for_user(db, mock_user.userId)
    assert row is not None
    # Ciphertext MUST round-trip through Fernet, never the plaintext byte string.
    assert row.encrypted_identifier != b"alice"
    assert row.encrypted_secret != b"s3cret"
    assert crypto.decrypt(row.encrypted_identifier) == "alice"
    assert crypto.decrypt(row.encrypted_secret) == "s3cret"


@pytest.mark.integration
def test_upsert_credentials_requires_user_domain_name(client):
    payload = dict(_PASSWORD_PAYLOAD)
    payload["user_domain_name"] = None

    with patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
        return_value=(True, None),
    ) as mock_validate:
        response = client.put("/me/openstack-credentials", json=payload)

    assert response.status_code == 422
    mock_validate.assert_not_called()
    assert "user_domain_name" in response.text


@pytest.mark.integration
def test_upsert_credentials_requires_project_id_or_name(client):
    payload = dict(_PASSWORD_PAYLOAD)
    payload["project_id"] = None
    payload["project_name"] = None

    with patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
        return_value=(True, None),
    ) as mock_validate:
        response = client.put("/me/openstack-credentials", json=payload)

    assert response.status_code == 422
    mock_validate.assert_not_called()
    assert "project_id" in response.text or "project_name" in response.text


@pytest.mark.integration
def test_upsert_credentials_unauthenticated_401(unauth_client):
    response = unauth_client.put("/me/openstack-credentials", json=_PASSWORD_PAYLOAD)
    assert response.status_code in (401, 403)


# ----------------------------------------------------------------
# PUT /me/openstack-credentials/from-yaml
# ----------------------------------------------------------------
@pytest.mark.integration
def test_upsert_from_yaml_parses_clouds_yaml_block(client, db, mock_user):
    fake_payload = OpenStackCredentialUpsert(**_APPCRED_PAYLOAD)

    with patch(
        "app.routers.openstack_credentials.clouds_yaml_parser.parse",
        return_value=fake_payload,
    ) as mock_parse, patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
        return_value=(True, None),
    ) as mock_validate:
        response = client.put(
            "/me/openstack-credentials/from-yaml",
            json={"clouds_yaml": _CLOUDS_YAML_SINGLE, "cloud_name": "mycloud"},
        )

    assert response.status_code == 200, response.text
    mock_parse.assert_called_once_with(_CLOUDS_YAML_SINGLE, "mycloud")
    mock_validate.assert_called_once()

    body = response.json()
    assert body["has_credential"] is True
    assert body["auth_type"] == "v3applicationcredential"

    row = crud_creds.get_for_user(db, mock_user.userId)
    assert row is not None
    assert crypto.decrypt(row.encrypted_identifier) == "appcred-id"
    assert crypto.decrypt(row.encrypted_secret) == "appcred-secret"


@pytest.mark.integration
def test_upsert_from_yaml_unknown_cloud_name_422(client):
    from fastapi import HTTPException, status

    with patch(
        "app.routers.openstack_credentials.clouds_yaml_parser.parse",
        side_effect=HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cloud 'nope' not found in YAML (available: mycloud)",
        ),
    ) as mock_parse, patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
    ) as mock_validate:
        response = client.put(
            "/me/openstack-credentials/from-yaml",
            json={"clouds_yaml": _CLOUDS_YAML_SINGLE, "cloud_name": "nope"},
        )

    assert response.status_code == 422
    mock_parse.assert_called_once()
    mock_validate.assert_not_called()
    assert "nope" in response.text


@pytest.mark.integration
def test_upsert_from_yaml_malformed_yaml_422(client):
    from fastapi import HTTPException, status

    with patch(
        "app.routers.openstack_credentials.clouds_yaml_parser.parse",
        side_effect=HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid YAML: mapping values are not allowed here",
        ),
    ), patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
    ) as mock_validate:
        response = client.put(
            "/me/openstack-credentials/from-yaml",
            json={"clouds_yaml": "::not yaml::", "cloud_name": None},
        )

    assert response.status_code == 422
    mock_validate.assert_not_called()
    assert "Invalid YAML" in response.text


# ----------------------------------------------------------------
# POST /me/openstack-credentials/test
# ----------------------------------------------------------------
@pytest.mark.integration
def test_test_credentials_calls_openstack_validator(client, db, mock_user):
    _seed_credential(db, mock_user)

    with patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
        return_value=(True, None),
    ) as mock_validate:
        response = client.post("/me/openstack-credentials/test")

    assert response.status_code == 200, response.text
    mock_validate.assert_called_once()
    # The validator is invoked with the rebuilt upsert payload —
    # plaintext identifier/secret pulled back out of the encrypted row.
    payload_arg = mock_validate.call_args.args[0]
    assert isinstance(payload_arg, OpenStackCredentialUpsert)
    assert payload_arg.identifier == "alice"
    assert payload_arg.secret == "s3cret"


@pytest.mark.integration
def test_test_credentials_records_last_validation_status(client, db, mock_user):
    _seed_credential(db, mock_user)

    # First a failed validation — error gets recorded, validated_at stays None.
    with patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
        return_value=(False, "Invalid credentials"),
    ):
        response = client.post("/me/openstack-credentials/test")
    assert response.status_code == 200
    body = response.json()
    assert body["last_validation_error"] == "Invalid credentials"
    assert body["last_validated_at"] is None

    # Then a successful one — error clears, validated_at populates.
    with patch(
        "app.routers.openstack_credentials.openstack_validator.validate",
        return_value=(True, None),
    ):
        response = client.post("/me/openstack-credentials/test")
    assert response.status_code == 200
    body = response.json()
    assert body["last_validation_error"] is None
    assert body["last_validated_at"] is not None


# ----------------------------------------------------------------
# DELETE /me/openstack-credentials
# ----------------------------------------------------------------
@pytest.mark.integration
def test_delete_my_credentials_removes_row(client, db, mock_user):
    _seed_credential(db, mock_user)
    assert crud_creds.get_for_user(db, mock_user.userId) is not None

    response = client.delete("/me/openstack-credentials")

    assert response.status_code == 204
    assert response.content == b""
    db.expire_all()
    assert crud_creds.get_for_user(db, mock_user.userId) is None


@pytest.mark.integration
def test_delete_my_credentials_idempotent_when_absent(client, db, mock_user):
    # No row seeded — DELETE should surface 404 cleanly, never 500.
    assert crud_creds.get_for_user(db, mock_user.userId) is None

    response = client.delete("/me/openstack-credentials")

    assert response.status_code == 404
    assert "No OpenStack credentials" in response.text


# ----------------------------------------------------------------
# IDOR
# ----------------------------------------------------------------
@pytest.mark.integration
def test_other_user_cannot_read_my_credentials_404(client, db, mock_user, mock_admin):
    # Seed a credential on the *admin* — the teacher client must see nothing.
    _seed_credential(db, mock_admin, identifier="admin-id", secret="admin-secret")

    response = client.get("/me/openstack-credentials")

    assert response.status_code == 200
    body = response.json()
    assert body["has_credential"] is False
    serialized = response.text
    assert "admin-id" not in serialized
    assert "admin-secret" not in serialized
