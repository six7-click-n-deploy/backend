"""Unit tests for :mod:`app.services.openstack_validator`.

The tests mock ``openstack.connect`` so no real Keystone is required.
Pydantic schema validation is exercised in the missing-fields case;
all other cases construct a valid payload and assert on the
authorize round-trip and error-mapping branches.
"""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest
from openstack import exceptions as os_exc
from pydantic import ValidationError

from app.models import OpenStackAuthType
from app.schemas import OpenStackCredentialUpsert
from app.services import openstack_validator

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
def _password_payload(**overrides) -> OpenStackCredentialUpsert:
    base = {
        "auth_type": OpenStackAuthType.PASSWORD,
        "auth_url": "https://keystone.example/v3",
        "region_name": "RegionOne",
        "identifier": "alice",
        "secret": "s3cret",
        "project_name": "demo",
        "user_domain_name": "Default",
    }
    base.update(overrides)
    return OpenStackCredentialUpsert(**base)


def _appcred_payload(**overrides) -> OpenStackCredentialUpsert:
    base = {
        "auth_type": OpenStackAuthType.APPLICATION_CREDENTIAL,
        "auth_url": "https://keystone.example/v3",
        "region_name": "RegionOne",
        "identifier": "ac-id-123",
        "secret": "ac-secret-xyz",
    }
    base.update(overrides)
    return OpenStackCredentialUpsert(**base)


# ----------------------------------------------------------------
# Tests
# ----------------------------------------------------------------
def test_validate_password_payload_calls_keystone_authenticate():
    """Password auth: connect() receives v3password kwargs and authorize() is called."""
    payload = _password_payload()
    conn = MagicMock()
    conn.authorize.return_value = "tok-abc"

    with patch.object(openstack_validator.openstack, "connect", return_value=conn) as mock_connect:
        ok, err = openstack_validator.validate(payload)

    assert ok is True
    assert err is None
    conn.authorize.assert_called_once()
    mock_connect.assert_called_once()
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["auth_type"] == "password"
    assert kwargs["username"] == "alice"
    assert kwargs["password"] == "s3cret"
    assert kwargs["project_name"] == "demo"
    assert kwargs["user_domain_name"] == "Default"
    # project_domain_name falls back to user_domain_name
    assert kwargs["project_domain_name"] == "Default"
    assert kwargs["auth_url"] == "https://keystone.example/v3"


def test_validate_application_credential_payload():
    """Application-credential auth: connect() gets v3applicationcredential kwargs."""
    payload = _appcred_payload()
    conn = MagicMock()
    conn.authorize.return_value = "tok-xyz"

    with patch.object(openstack_validator.openstack, "connect", return_value=conn) as mock_connect:
        ok, err = openstack_validator.validate(payload)

    assert ok is True
    assert err is None
    conn.authorize.assert_called_once()
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["auth_type"] == "v3applicationcredential"
    assert kwargs["application_credential_id"] == "ac-id-123"
    assert kwargs["application_credential_secret"] == "ac-secret-xyz"
    # No username/password leaked into the connect kwargs for appcred auth
    assert "username" not in kwargs
    assert "password" not in kwargs


def test_validate_invalid_credentials_returns_failure():
    """A 401 from Keystone maps to ``Invalid credentials``; secret is not echoed."""
    payload = _password_payload(secret="wrong-password")
    http_err = os_exc.HttpException(message="Unauthorized")
    http_err.status_code = 401

    conn = MagicMock()
    conn.authorize.side_effect = http_err

    with patch.object(openstack_validator.openstack, "connect", return_value=conn):
        ok, err = openstack_validator.validate(payload)

    assert ok is False
    assert err == "Invalid credentials"
    assert "wrong-password" not in (err or "")


def test_validate_network_error_returns_failure_with_reason():
    """gaierror / TimeoutError surface as a short ``Could not reach auth_url`` message."""
    payload = _password_payload(auth_url="https://unreachable.invalid/v3")

    with patch.object(
        openstack_validator.openstack,
        "connect",
        side_effect=socket.gaierror("Name or service not known"),
    ):
        ok, err = openstack_validator.validate(payload)

    assert ok is False
    assert err is not None
    assert err.startswith("Could not reach auth_url")
    assert "Name or service not known" in err


def test_validate_rejects_missing_required_fields():
    """Password auth without a project_* / user_domain_name fails Pydantic validation."""
    with pytest.raises(ValidationError) as exc:
        OpenStackCredentialUpsert(
            auth_type=OpenStackAuthType.PASSWORD,
            auth_url="https://keystone.example/v3",
            identifier="alice",
            secret="s3cret",
            # missing user_domain_name and project_name/project_id
        )

    msg = str(exc.value)
    assert "user_domain_name" in msg or "project" in msg
