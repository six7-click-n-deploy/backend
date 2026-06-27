"""Tests für den prozesslokalen TTL-Cache und die ``user_connection``-
Brücke in ``app.services.openstack_client``.

Der Cache ist Modul-State; jeder Test räumt ihn vorher leer, damit
Reihenfolge-Effekte ausgeschlossen sind.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services import openstack_client
from app.services.openstack_client import (
    cached_list,
    invalidate_user,
    user_connection,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Modul-globaler Cache wird vor und nach jedem Test geleert,
    damit Tests sich gegenseitig nicht beeinflussen."""
    with openstack_client._cache_lock:
        openstack_client._cache.clear()
    yield
    with openstack_client._cache_lock:
        openstack_client._cache.clear()


@pytest.mark.integration
def test_cached_list_returns_same_object_within_ttl():
    """Zweiter Aufruf innerhalb der TTL ruft ``fetch`` nicht erneut auf
    und liefert dasselbe Listen-Objekt zurück."""
    user_id = uuid.uuid4()
    sentinel = [{"id": "net-1", "name": "shared"}]

    fetch = MagicMock(return_value=sentinel)

    first = cached_list(user_id, "networks", None, fetch)
    second = cached_list(user_id, "networks", None, fetch)

    assert first is sentinel
    assert second is sentinel
    assert fetch.call_count == 1


@pytest.mark.integration
def test_cached_list_refreshes_after_ttl_expires():
    """Nach Ablauf der TTL wird ``fetch`` erneut aufgerufen und der
    Cache mit dem neuen Ergebnis aktualisiert."""
    user_id = uuid.uuid4()
    first_payload = [{"id": "net-1"}]
    second_payload = [{"id": "net-2"}]

    fetch = MagicMock(side_effect=[first_payload, second_payload])

    # Erster Aufruf bei t=1000.0 → Eintrag läuft bei 1000 + TTL ab.
    with patch(
        "app.services.openstack_client.time.monotonic", return_value=1000.0
    ):
        first = cached_list(user_id, "networks", None, fetch)

    # Zweiter Aufruf weit nach Ablauf der TTL → muss neu fetchen.
    future = 1000.0 + openstack_client._TTL_SECONDS + 1.0
    with patch(
        "app.services.openstack_client.time.monotonic", return_value=future
    ):
        second = cached_list(user_id, "networks", None, fetch)

    assert first is first_payload
    assert second is second_payload
    assert fetch.call_count == 2


@pytest.mark.integration
def test_invalidate_user_drops_cache_for_that_user_only():
    """``invalidate_user`` darf nur Einträge des angegebenen Users
    entfernen — andere Benutzer behalten ihren Cache."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    fetch_a = MagicMock(return_value=[{"id": "a"}])
    fetch_b = MagicMock(return_value=[{"id": "b"}])

    cached_list(user_a, "networks", None, fetch_a)
    cached_list(user_b, "networks", None, fetch_b)

    removed = invalidate_user(user_a)
    assert removed == 1

    # User A muss erneut fetchen, User B behält den gecachten Wert.
    cached_list(user_a, "networks", None, fetch_a)
    cached_list(user_b, "networks", None, fetch_b)

    assert fetch_a.call_count == 2
    assert fetch_b.call_count == 1


@pytest.mark.integration
def test_user_connection_uses_credentials_from_envelope(db, mock_user):
    """``user_connection`` muss die dekrypteten Credentials des Users
    auflösen und an ``openstack.connect`` als Auth-Kwargs übergeben."""
    fake_creds = {
        "auth_url": "https://keystone.example/v3",
        "auth_type": "v3applicationcredential",
        "identifier": "app-cred-id",
        "secret": "app-cred-secret",
        "region_name": "RegionOne",
        "interface": "public",
        "identity_api_version": "3",
    }

    fake_conn = MagicMock()

    with patch(
        "app.services.openstack_client.crud_creds.get_decrypted_for_backend",
        return_value=fake_creds,
    ) as mock_get_creds, patch(
        "app.services.openstack_client.openstack.connect",
        return_value=fake_conn,
    ) as mock_connect, user_connection(db, mock_user) as conn:
        assert conn is fake_conn

    mock_get_creds.assert_called_once_with(db, mock_user.userId)
    mock_connect.assert_called_once()
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["auth_url"] == "https://keystone.example/v3"
    assert kwargs["auth_type"] == "v3applicationcredential"
    assert kwargs["application_credential_id"] == "app-cred-id"
    assert kwargs["application_credential_secret"] == "app-cred-secret"
    assert kwargs["region_name"] == "RegionOne"
    # Die Connection wird im Finally-Block höflich geschlossen.
    fake_conn.close.assert_called_once()
