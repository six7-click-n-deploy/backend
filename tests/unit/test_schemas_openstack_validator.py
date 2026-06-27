"""Unit tests for the Pydantic model validator on
:class:`app.schemas.OpenStackCredentialUpsert`.

The validator enforces field presence based on ``auth_type``:

* ``password`` auth requires ``user_domain_name`` AND at least one of
  ``project_id`` / ``project_name``.
* ``v3applicationcredential`` auth skips those checks — the application
  credential itself carries the project scope.

These tests are DB-less and exercise the validator directly through
Pydantic instantiation.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import OpenStackAuthType
from app.schemas import OpenStackCredentialUpsert

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
def _password_payload(**overrides):
    """Return a baseline password-auth payload; let tests override fields."""
    base = {
        "auth_type": OpenStackAuthType.PASSWORD,
        "auth_url": "https://keystone.example.com:5000/v3",
        "identifier": "alice",
        "secret": "s3cret",
        "user_domain_name": "Default",
        "project_id": "abc123",
    }
    base.update(overrides)
    return base


def _app_cred_payload(**overrides):
    """Return a baseline application-credential payload."""
    base = {
        "auth_type": OpenStackAuthType.APPLICATION_CREDENTIAL,
        "auth_url": "https://keystone.example.com:5000/v3",
        "identifier": "appcred-id-1234",
        "secret": "appcred-secret-xyz",
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------
# Tests
# ----------------------------------------------------------------
def test_password_auth_requires_user_domain_name():
    """Password auth without ``user_domain_name`` must fail validation."""
    payload = _password_payload(user_domain_name=None)

    with pytest.raises(ValidationError) as exc_info:
        OpenStackCredentialUpsert(**payload)

    assert "user_domain_name" in str(exc_info.value)


def test_password_auth_requires_project_id_or_project_name():
    """Password auth without either ``project_id`` or ``project_name`` must fail."""
    payload = _password_payload(project_id=None, project_name=None)

    with pytest.raises(ValidationError) as exc_info:
        OpenStackCredentialUpsert(**payload)

    assert "project_id or project_name" in str(exc_info.value)


def test_password_auth_accepts_project_id_only():
    """Password auth with ``project_id`` (and no ``project_name``) is valid."""
    payload = _password_payload(project_id="proj-id-42", project_name=None)

    creds = OpenStackCredentialUpsert(**payload)

    assert creds.auth_type == OpenStackAuthType.PASSWORD
    assert creds.project_id == "proj-id-42"
    assert creds.project_name is None
    assert creds.user_domain_name == "Default"


def test_password_auth_accepts_project_name_with_project_domain():
    """Password auth with ``project_name`` + ``project_domain_name`` is valid."""
    payload = _password_payload(
        project_id=None,
        project_name="my-project",
        project_domain_name="Default",
    )

    creds = OpenStackCredentialUpsert(**payload)

    assert creds.auth_type == OpenStackAuthType.PASSWORD
    assert creds.project_id is None
    assert creds.project_name == "my-project"
    assert creds.project_domain_name == "Default"


def test_application_credential_auth_skips_password_field_checks():
    """Application-credential auth must not enforce password-only field rules.

    Even without ``user_domain_name``, ``project_id`` or ``project_name``,
    the payload must validate — the credential itself carries the scope.
    """
    payload = _app_cred_payload()

    creds = OpenStackCredentialUpsert(**payload)

    assert creds.auth_type == OpenStackAuthType.APPLICATION_CREDENTIAL
    assert creds.user_domain_name is None
    assert creds.project_id is None
    assert creds.project_name is None
    assert creds.identifier == "appcred-id-1234"
    assert creds.secret == "appcred-secret-xyz"
