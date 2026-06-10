"""Tests for the just-in-time Keycloak refresh used by the notifier."""
import uuid
from unittest.mock import patch

import pytest

from app.models import User, UserRole
from app.utils import keycloak_auth


def _make_user(db, *, email="old@dhbw.de", first="Old", last="Name", kc_id="kc-1"):
    user = User(
        userId=uuid.uuid4(),
        keycloak_id=kc_id,
        email=email,
        username="testuser",
        firstName=first,
        lastName=last,
        role=UserRole.STUDENT,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.mark.unit
def test_refresh_picks_up_new_email(db):
    """Happy path — KC has a newer email than the DB row."""
    user = _make_user(db, email="old@dhbw.de", kc_id="kc-1")
    fake_admin = type("FakeAdmin", (), {
        "get_user": lambda self, kid: {
            "id": kid,
            "email": "new@dhbw.de",
            "username": "testuser",
            "firstName": "New",
            "lastName": "Name",
        },
    })()
    with patch.object(keycloak_auth, "get_keycloak_admin", return_value=fake_admin):
        refreshed = keycloak_auth.refresh_user_from_keycloak(db, user)
    assert refreshed.email == "new@dhbw.de"
    assert refreshed.firstName == "New"
    # DB row was updated in place.
    db.refresh(user)
    assert user.email == "new@dhbw.de"


@pytest.mark.unit
def test_refresh_falls_back_when_keycloak_unreachable(db):
    """KC down → log and return the DB record unchanged."""
    user = _make_user(db, email="old@dhbw.de", kc_id="kc-2")

    class _Boom:
        def get_user(self, _kid):
            raise RuntimeError("connection refused")

    with patch.object(keycloak_auth, "get_keycloak_admin", return_value=_Boom()):
        refreshed = keycloak_auth.refresh_user_from_keycloak(db, user)
    assert refreshed.email == "old@dhbw.de"
    db.refresh(user)
    assert user.email == "old@dhbw.de"


@pytest.mark.unit
def test_refresh_returns_db_row_when_user_deleted_upstream(db):
    """User was deleted in KC — keep the DB record (last known good)."""
    user = _make_user(db, email="old@dhbw.de", kc_id="kc-3")

    class _NoUser:
        def get_user(self, _kid):
            return None

    with patch.object(keycloak_auth, "get_keycloak_admin", return_value=_NoUser()):
        refreshed = keycloak_auth.refresh_user_from_keycloak(db, user)
    assert refreshed.email == "old@dhbw.de"


@pytest.mark.unit
def test_refresh_skips_users_without_keycloak_id(db):
    """Legacy rows without a keycloak_id can't be refreshed — return as-is."""
    user = User(
        userId=uuid.uuid4(),
        keycloak_id=None,
        email="legacy@dhbw.de",
        username="legacy",
        firstName="Legacy",
        lastName="User",
        role=UserRole.STUDENT,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Should not even hit Keycloak.
    with patch.object(keycloak_auth, "get_keycloak_admin") as kc_mock:
        refreshed = keycloak_auth.refresh_user_from_keycloak(db, user)
    kc_mock.assert_not_called()
    assert refreshed.email == "legacy@dhbw.de"


@pytest.mark.unit
def test_refresh_preserves_role(db):
    """``get_user`` doesn't carry roles — the DB role must survive the refresh."""
    user = _make_user(db, kc_id="kc-4")
    user.role = UserRole.TEACHER
    db.commit()

    fake_admin = type("FakeAdmin", (), {
        "get_user": lambda self, kid: {
            "id": kid,
            "email": "new@dhbw.de",
            "username": "testuser",
            "firstName": "New",
            "lastName": "Name",
        },
    })()
    with patch.object(keycloak_auth, "get_keycloak_admin", return_value=fake_admin):
        refreshed = keycloak_auth.refresh_user_from_keycloak(db, user)
    # Role rotation is a separate flow (handled at login); refresh
    # must not silently downgrade a teacher to student.
    assert refreshed.role == UserRole.TEACHER
