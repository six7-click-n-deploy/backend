"""Per-user serialization via Postgres advisory locks.

Used to make credential mutation and deployment dispatch atomic from the
caller's perspective: while a user is creating a deployment, their
credential row cannot be flipped underneath them, and vice versa.

The lock is *transaction-scoped* (`pg_advisory_xact_lock`) — it is
released automatically on COMMIT or ROLLBACK. Callers therefore must
hold a single transaction across the protected region; do NOT call this
and then call CRUD functions that auto-commit, because each commit would
release the lock prematurely.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def acquire_user_xact_lock(db: Session, user_id: UUID) -> None:
    """Block until this transaction holds the per-user advisory lock.

    `hashtext()` collapses the UUID string into the int8 key Postgres
    advisory locks expect. Collisions across users are possible in
    principle (32-bit hash space), but harmless: the worst outcome is a
    short serialization between two users who happen to hash to the same
    bucket. No correctness loss.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:uid))"),
        {"uid": str(user_id)},
    )


def acquire_deployment_xact_lock(db: Session, deployment_id: UUID) -> None:
    """Per-deployment advisory lock — serialises lifecycle dispatch.

    Used by every endpoint that prepares a new lifecycle task
    (destroy / pause / resume) so two concurrent requests on the
    *same* deployment can never both pass the
    ``ensure_action_allowed`` matrix check, both call
    ``prepare_task_in_tx``, and one ends up surfacing a raw
    ``IntegrityError`` from the partial unique index instead of a
    clean 409.

    Uses a separate keyspace from ``acquire_user_xact_lock`` —
    ``pg_advisory_xact_lock(int4, int4)`` takes two ints; we pin the
    first to ``1`` for the deployment namespace and hash the UUID
    into the second slot. Same caveat about 32-bit hash collisions
    applies (worst case: two unrelated deployments share a bucket
    and serialize, no correctness loss).
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(1, hashtext(:did))"),
        {"did": str(deployment_id)},
    )
