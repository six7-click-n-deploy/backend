"""Tests für Postgres-Advisory-Locks (Phase C7).

Diese Tests verifizieren, dass ``acquire_user_xact_lock`` und
``acquire_deployment_xact_lock`` tatsächlich konkurrierende Schreiber
serialisieren und beim Rollback freigegeben werden.

Pattern: pro Test werden zwei separate ``TestingSessionLocal()``-
Instanzen geöffnet, sodass jede ihre eigene DB-Connection (und damit
Transaktion) bekommt. Das Blockier-Verhalten wird via
``pg_try_advisory_xact_lock`` aus der zweiten Session geprüft — gibt
``true`` zurück, wenn der Lock frei ist, sonst ``false``. Damit
brauchen wir kein Threading + Timeout-Gefrickel, um "blockiert"
nachzuweisen.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.crud.locks import (
    acquire_deployment_xact_lock,
    acquire_user_xact_lock,
)
from tests.conftest import TestingSessionLocal


def _try_user_lock(db) -> bool:
    """True wenn der User-Lock JETZT in dieser Session frei aufnehmbar wäre.

    Spiegelt die Keyspace-Konvention aus ``acquire_user_xact_lock``
    (single-int Variante, hashtext der UUID).
    """
    row = db.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:uid))"),
        {"uid": str(db.info["uid"])},
    ).scalar()
    return bool(row)


def _try_deployment_lock(db) -> bool:
    """True wenn der Deployment-Lock JETZT frei aufnehmbar wäre.

    Spiegelt die two-int-Variante (Namespace=1) aus
    ``acquire_deployment_xact_lock``.
    """
    row = db.execute(
        text("SELECT pg_try_advisory_xact_lock(1, hashtext(:did))"),
        {"did": str(db.info["did"])},
    ).scalar()
    return bool(row)


@pytest.mark.integration
def test_acquire_user_xact_lock_blocks_second_session_until_commit():
    """Session B kann den gleichen User-Lock erst nach Commit von Session A halten."""
    user_id = uuid.uuid4()

    session_a = TestingSessionLocal()
    session_b = TestingSessionLocal()
    session_a.info["uid"] = user_id
    session_b.info["uid"] = user_id
    try:
        # A nimmt den Lock — Transaktion läuft jetzt.
        acquire_user_xact_lock(session_a, user_id)

        # B versucht denselben Lock ohne Warten zu nehmen — muss fehlschlagen.
        assert _try_user_lock(session_b) is False

        # A schließt die Transaktion ab und gibt damit den xact-Lock frei.
        session_a.commit()

        # Frischer Tx-Kontext in B, damit der vorherige Fehlversuch nicht
        # noch im selben Snapshot hängt. (pg_try_advisory_xact_lock ist
        # transaktional und wird beim ROLLBACK ebenfalls freigegeben —
        # ein expliziter Rollback reicht.)
        session_b.rollback()
        assert _try_user_lock(session_b) is True
    finally:
        session_a.rollback()
        session_b.rollback()
        session_a.close()
        session_b.close()


@pytest.mark.integration
def test_acquire_deployment_xact_lock_serializes_concurrent_writers():
    """Zwei Sessions auf derselben deploymentId können den Lock nicht gleichzeitig halten."""
    deployment_id = uuid.uuid4()

    session_a = TestingSessionLocal()
    session_b = TestingSessionLocal()
    session_a.info["did"] = deployment_id
    session_b.info["did"] = deployment_id
    try:
        acquire_deployment_xact_lock(session_a, deployment_id)

        # Solange A die Transaktion offen hält, ist der Lock für B nicht
        # frei verfügbar — try-Variante kehrt false zurück.
        assert _try_deployment_lock(session_b) is False
        session_b.rollback()

        # Verschiedener Deployment-Key -> kein Konflikt: B darf den
        # Lock für eine andere deploymentId nehmen. Das zeigt, dass die
        # Serialisierung deploymentId-spezifisch ist und nicht global.
        other_id = uuid.uuid4()
        session_b.info["did"] = other_id
        assert _try_deployment_lock(session_b) is True
        session_b.rollback()

        # Nach Commit von A ist auch der ursprüngliche Lock wieder frei.
        session_a.commit()
        session_b.info["did"] = deployment_id
        assert _try_deployment_lock(session_b) is True
    finally:
        session_a.rollback()
        session_b.rollback()
        session_a.close()
        session_b.close()


@pytest.mark.integration
def test_lock_released_on_rollback():
    """Ein ROLLBACK gibt den xact-Lock genauso frei wie ein COMMIT."""
    user_id = uuid.uuid4()

    session_a = TestingSessionLocal()
    session_b = TestingSessionLocal()
    session_a.info["uid"] = user_id
    session_b.info["uid"] = user_id
    try:
        acquire_user_xact_lock(session_a, user_id)
        # Während A hält, ist der Lock nicht aufnehmbar.
        assert _try_user_lock(session_b) is False
        session_b.rollback()

        # Rollback statt Commit — die Doku von locks.py garantiert
        # Freigabe in beiden Fällen.
        session_a.rollback()

        # Jetzt muss B den Lock erfolgreich greifen können.
        assert _try_user_lock(session_b) is True
    finally:
        session_a.rollback()
        session_b.rollback()
        session_a.close()
        session_b.close()
