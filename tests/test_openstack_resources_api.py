"""API-Level Tests für die OpenStack-Resources-Read-API.

Abgedeckt:
 * GET  /me/openstack/resources/flavors        (auth + payload-shape)
 * GET  /me/openstack/resources/flavors        (401 ohne Auth)
 * GET  /me/openstack/resources/images?status= (Visibility/Status-Filter)
 * POST /me/openstack/resources/refresh        (nur eigener Cache)
 * POST /me/openstack/resources/refresh        (invalidiert nachfolgende GETs)
 * GET  /me/openstack/resources/subnets        (network_id-Query optional)

OpenStack wird wie in ``test_deployment_resources_endpoint.py`` über
``user_connection`` gepatcht — kein realer Cloud-Call.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.services import openstack_client

PREFIX = "/me/openstack/resources"


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
def _flavor_mock(**overrides):
    f = MagicMock()
    defaults = {
        "id": "flavor-uuid-1",
        "name": "m1.small",
        "vcpus": 1,
        "ram": 2048,
        "disk": 20,
        "is_public": True,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(f, k, v)
    return f


def _image_mock(**overrides):
    img = MagicMock()
    defaults = {
        "id": "img-uuid-1",
        "name": "ubuntu-22.04",
        "status": "active",
        "visibility": "public",
        "size": 1024,
        "disk_format": "qcow2",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(img, k, v)
    return img


def _subnet_mock(**overrides):
    s = MagicMock()
    defaults = {
        "id": "subnet-uuid-1",
        "name": "subnet-a",
        "cidr": "10.0.0.0/24",
        "ip_version": 4,
        "network_id": "net-uuid-1",
        "gateway_ip": "10.0.0.1",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


@pytest.fixture
def patched_user_connection():
    """Patcht den Context-Manager ``user_connection`` im Resources-Router.
    Liefert ein MagicMock, das pro Test mit den passenden SDK-Antworten
    konfiguriert wird."""
    conn = MagicMock()

    @contextmanager
    def _fake_conn(_db, _user):
        yield conn

    with patch(
        "app.routers.openstack_resources.openstack_client.user_connection",
        _fake_conn,
    ):
        yield conn


@pytest.fixture(autouse=True)
def _clear_openstack_cache():
    """Cache ist prozesslokal — zwischen Tests säubern, damit
    ein vorheriger ``cached_list``-Eintrag nicht den nächsten Test
    am Fetch-Pfad vorbeischleust."""
    openstack_client._cache.clear()
    yield
    openstack_client._cache.clear()


# ----------------------------------------------------------------
# 1. flavors — happy path
# ----------------------------------------------------------------
@pytest.mark.integration
def test_list_flavors_authenticated_returns_payload(
    client, db, mock_user, patched_user_connection
):
    patched_user_connection.compute.flavors.return_value = [
        _flavor_mock(id="f1", name="m1.small", vcpus=1, ram=2048, disk=20),
        _flavor_mock(id="f2", name="m1.large", vcpus=4, ram=8192, disk=80, is_public=False),
    ]

    response = client.get(f"{PREFIX}/flavors")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert body[0] == {
        "id": "f1",
        "name": "m1.small",
        "vcpus": 1,
        "ram": 2048,
        "disk": 20,
        "is_public": True,
    }
    assert body[1]["is_public"] is False
    # Compute-flavors muss mit get_extra_specs=False aufgerufen werden
    patched_user_connection.compute.flavors.assert_called_once_with(get_extra_specs=False)


# ----------------------------------------------------------------
# 2. flavors — unauthenticated 401
# ----------------------------------------------------------------
@pytest.mark.integration
def test_list_flavors_unauthenticated_401(unauth_client):
    response = unauth_client.get(f"{PREFIX}/flavors")
    assert response.status_code == 401


# ----------------------------------------------------------------
# 3. images — status filter
# ----------------------------------------------------------------
@pytest.mark.integration
def test_list_images_filters_by_visibility(
    client, db, mock_user, patched_user_connection
):
    patched_user_connection.image.images.return_value = [
        _image_mock(id="i1", name="ubuntu", status="active"),
        _image_mock(id="i2", name="debian", status="active"),
    ]

    response = client.get(f"{PREFIX}/images", params={"status": "active"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert all(item["status"] == "active" for item in body)
    # Der SDK-Call muss explizit den status-Filter durchreichen
    patched_user_connection.image.images.assert_called_once_with(status="active")


# ----------------------------------------------------------------
# 4. refresh — wirkt nur auf den aufrufenden User
# ----------------------------------------------------------------
@pytest.mark.integration
def test_refresh_cache_owner_only(
    client, db, mock_user, mock_admin, patched_user_connection
):
    # Cache-Einträge für beide User vorpopulieren
    openstack_client._cache[
        openstack_client._make_key(mock_user.userId, "flavors", None)
    ] = (float("inf"), [{"id": "f-mock-user"}])
    openstack_client._cache[
        openstack_client._make_key(mock_admin.userId, "flavors", None)
    ] = (float("inf"), [{"id": "f-mock-admin"}])

    # mock_user triggert den Refresh
    response = client.post(f"{PREFIX}/refresh")
    assert response.status_code == 204

    # Eigener Eintrag weg, fremder Eintrag bleibt
    assert (
        openstack_client._make_key(mock_user.userId, "flavors", None)
        not in openstack_client._cache
    )
    assert (
        openstack_client._make_key(mock_admin.userId, "flavors", None)
        in openstack_client._cache
    )


# ----------------------------------------------------------------
# 5. refresh — invalidiert nachfolgende GETs (Re-Fetch)
# ----------------------------------------------------------------
@pytest.mark.integration
def test_refresh_cache_invalidates_subsequent_list_calls(
    client, db, mock_user, patched_user_connection
):
    # Erster Call — füllt den Cache
    patched_user_connection.compute.flavors.return_value = [
        _flavor_mock(id="f1", name="initial"),
    ]
    response1 = client.get(f"{PREFIX}/flavors")
    assert response1.status_code == 200
    assert response1.json()[0]["name"] == "initial"
    assert patched_user_connection.compute.flavors.call_count == 1

    # Zweiter Call — soll aus dem Cache kommen, kein neuer SDK-Hit
    patched_user_connection.compute.flavors.return_value = [
        _flavor_mock(id="f1", name="updated"),
    ]
    response2 = client.get(f"{PREFIX}/flavors")
    assert response2.status_code == 200
    assert response2.json()[0]["name"] == "initial"  # Cache served
    assert patched_user_connection.compute.flavors.call_count == 1

    # Refresh-Knopf — invalidiert den Cache
    refresh = client.post(f"{PREFIX}/refresh")
    assert refresh.status_code == 204

    # Dritter Call — muss frisch fetchen
    response3 = client.get(f"{PREFIX}/flavors")
    assert response3.status_code == 200
    assert response3.json()[0]["name"] == "updated"
    assert patched_user_connection.compute.flavors.call_count == 2


# ----------------------------------------------------------------
# 6. subnets — network_id Query optional, wird aber durchgereicht
# ----------------------------------------------------------------
@pytest.mark.integration
def test_list_subnets_requires_network_id_query_param(
    client, db, mock_user, patched_user_connection
):
    # Ohne network_id: alle Subnets — kein Filter an die SDK
    patched_user_connection.network.subnets.return_value = [
        _subnet_mock(id="s1", network_id="net-1"),
        _subnet_mock(id="s2", network_id="net-2"),
    ]
    response_all = client.get(f"{PREFIX}/subnets")
    assert response_all.status_code == 200
    body_all = response_all.json()
    assert len(body_all) == 2
    patched_user_connection.network.subnets.assert_called_once_with()

    # Mit network_id: Filter wird serverseitig an OpenStack durchgereicht
    patched_user_connection.network.subnets.reset_mock()
    patched_user_connection.network.subnets.return_value = [
        _subnet_mock(id="s1", network_id="net-1"),
    ]
    response_filtered = client.get(
        f"{PREFIX}/subnets", params={"network_id": "net-1"}
    )
    assert response_filtered.status_code == 200
    body_filtered = response_filtered.json()
    assert len(body_filtered) == 1
    assert body_filtered[0]["network_id"] == "net-1"
    patched_user_connection.network.subnets.assert_called_once_with(network_id="net-1")
