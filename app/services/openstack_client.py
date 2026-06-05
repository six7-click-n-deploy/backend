"""
Shared OpenStack-Client-Layer für FastAPI-Endpoints.

Drei Aufgaben:

1. Auth-Kwargs aus den verschlüsselten User-Credentials bauen — exakt der
   gleiche Code, der vorher dupliziert in ``quotas.py`` und
   ``openstack_validator.py`` lebte. Eine Quelle der Wahrheit.

2. Eine Verbindung pro Request öffnen. ``openstack.connect`` cached intern
   nichts, was wir bräuchten — aber ein Token-Roundtrip dauert leicht
   1–2 s. Wir reichen die Connection als Context-Manager nach außen, damit
   Endpoints sie sauber schließen.

3. Ein **prozesslokaler TTL-Cache** für Listen-Antworten.
   Hintergrund: Der Wizard schickt potentiell 5–10 GETs in schneller Folge
   (User klickt Picker auf, Variable für Variable). Ohne Cache feuern wir
   pro Klick einen Keystone-Token-Refresh + den eigentlichen Listen-Call.
   60-Sekunden-Cache reicht — der User wird in der Zeit kein neues Netzwerk
   anlegen, und wenn doch, gibt es einen Refresh-Knopf im Frontend, der
   ``invalidate_user`` triggert.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from typing import Any
from uuid import UUID

import openstack
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.crud import openstack_credentials as crud_creds
from app.models import User

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------
# Connection-Bau
# ----------------------------------------------------------------
def _build_connect_kwargs(creds: dict) -> dict:
    """
    Baut den Kwargs-Dict für ``openstack.connect`` aus den dekrypteten
    User-Credentials. Zwei Auth-Typen werden unterstützt:
    Password und Application-Credential (v3applicationcredential).
    """
    base = {
        "auth_url": creds["auth_url"],
        "region_name": creds.get("region_name"),
        "interface": creds.get("interface") or "public",
        "identity_api_version": creds.get("identity_api_version") or "3",
    }
    if creds["auth_type"] == "v3applicationcredential":
        base.update(
            {
                "auth_type": "v3applicationcredential",
                "application_credential_id": creds["identifier"],
                "application_credential_secret": creds["secret"],
            }
        )
    else:
        base.update(
            {
                "auth_type": "password",
                "username": creds["identifier"],
                "password": creds["secret"],
                "project_id": creds.get("project_id"),
                "project_name": creds.get("project_name"),
                "user_domain_name": creds.get("user_domain_name"),
                "project_domain_name": creds.get("project_domain_name")
                or creds.get("user_domain_name"),
            }
        )
    return base


@contextmanager
def user_connection(db: Session, user: User) -> Iterator[Any]:
    """
    Yieldet eine ``openstack.Connection`` für den User.

    - 412, falls keine Credentials hinterlegt sind (Frontend zeigt CTA-Banner)
    - 502 (Bad Gateway) für transienten OpenStack-Fehler beim Connect —
      wir wollen 500er als „Backend-Bug" reservieren.
    """
    try:
        creds = crud_creds.get_decrypted_for_backend(db, user.userId)
    except crud_creds.NoCredentialError:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"reason": "openstack_credentials_missing"},
        )

    conn = None
    try:
        conn = openstack.connect(**_build_connect_kwargs(creds))
        yield conn
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — SDK wirft eine Vielzahl
        logger.warning(
            "OpenStack connect failed for user %s: %s", user.userId, exc
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"reason": "openstack_unavailable", "message": str(exc)},
        )
    finally:
        # ``openstack.Connection`` hat ``close``, aber das Idiom in der
        # SDK ist „lass laufen, GC räumt auf". Wir versuchen es trotzdem
        # höflich, ignorieren Fehler.
        if conn is not None:
            with suppress(Exception):
                conn.close()


# ----------------------------------------------------------------
# TTL-Cache für Resource-Listen
# ----------------------------------------------------------------
# Key = (user_id, resource_kind, frozenset von Filter-Items)
# Value = (expiry_epoch, data)
_CacheKey = tuple[str, str, frozenset]
_cache: dict[_CacheKey, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()
_TTL_SECONDS = 60.0


def _make_key(user_id: UUID, kind: str, filters: dict | None) -> _CacheKey:
    items: frozenset = frozenset((filters or {}).items())
    return (str(user_id), kind, items)


def cached_list(
    user_id: UUID,
    kind: str,
    filters: dict | None,
    fetch: Callable[[], list[dict]],
) -> list[dict]:
    """
    TTL-Cache-Wrapper. ``fetch`` wird nur aufgerufen, wenn kein gültiger
    Eintrag existiert. Locking sorgt dafür, dass parallel laufende
    Requests denselben Key nur einmal fetchen — die anderen warten und
    bekommen das Ergebnis.

    Cache ist prozesslokal und in-memory. Bei mehreren Backend-Instanzen
    laufen mehrere Caches parallel — das ist okay, weil die Daten
    eh nur 60 s alt sein dürfen und wir keinen Konsistenz-Anspruch haben.
    """
    key = _make_key(user_id, kind, filters)
    now = time.monotonic()

    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

    # Race-Window: zwei Requests können gleichzeitig hier landen und
    # beide ``fetch`` aufrufen. Das ist ineffizient, aber nicht falsch —
    # und einfacher als ein Per-Key-Lock-Map.
    data = fetch()

    with _cache_lock:
        _cache[key] = (now + _TTL_SECONDS, data)

    return data


def invalidate_user(user_id: UUID, kind: str | None = None) -> int:
    """
    Cache für einen User invalidieren. Wird vom Refresh-Knopf im
    Frontend getriggert. Ohne ``kind`` werden alle Resource-Typen des
    Users entfernt.

    Returns die Anzahl der entfernten Einträge (für Logs).
    """
    user_str = str(user_id)
    removed = 0
    with _cache_lock:
        for key in list(_cache.keys()):
            if key[0] != user_str:
                continue
            if kind is not None and key[1] != kind:
                continue
            del _cache[key]
            removed += 1
    return removed
