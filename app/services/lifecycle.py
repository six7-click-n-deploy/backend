"""Deployment lifecycle gating — single source of truth for which
actions are allowed in which state.

The previous code spread state checks across the routers (``if status
== 'success': allow_destroy = True`` etc.), which made it easy to drift
between frontend and backend rules. This module centralises the matrix
so the API and the UI can both consult one canonical mapping.

Status values follow ``crud_deployments.get_deployment_status``:

* ``pending`` / ``running`` — a deploy task is in flight
* ``success`` — deploy finished, resources live in OpenStack
* ``failed`` — last task ended in error (deploy or destroy)
* ``cancelled`` — last task was revoked
* ``destroying`` — a destroy task is in flight (synthetic)
* ``destroyed`` — a destroy task finished successfully (synthetic)
"""

from __future__ import annotations

from enum import Enum

from fastapi import HTTPException, status

from app.crud import deployments as crud_deployments


class DeploymentAction(str, Enum):
    """Lifecycle actions a user can request.

    Pause/resume are out of scope for now (see backend#36); the enum
    keeps room for them so adding them later is just a matrix entry.
    """

    DESTROY = "destroy"
    DELETE = "delete"


# Status → set of allowed actions. Anything not listed here gets the
# empty set, which is the safe default: a status we don't recognise
# should not allow any destructive action.
_ALLOWED: dict[str, set[DeploymentAction]] = {
    "success": {DeploymentAction.DESTROY},
    # ``failed`` is the interesting case. The deploy may have created
    # *some* OpenStack resources before failing (e.g. plan succeeded but
    # apply broke half-way), so Destroy is offered to reconcile. If the
    # user knows there's nothing to clean up they can pick Delete
    # instead — both end at "row hidden from UI", just via different
    # routes.
    "failed": {DeploymentAction.DESTROY, DeploymentAction.DELETE},
    # No ``destroyed`` entry: a successful destroy auto-soft-deletes
    # the deployment, so this status only exists transiently between
    # the worker's task-succeeded event and the listener's
    # ``soft_delete_deployment`` call (sub-second). The row is hidden
    # from the default queries before the user could click anything.
    "cancelled": {DeploymentAction.DELETE},
    # pending / running / destroying — no action allowed: an active
    # task is doing something with the resources, and the partial-unique
    # index on ``tasks(deploymentId) WHERE status IN ('PENDING','RUNNING')``
    # in the DB enforces this at insert time too.
}

# Human-readable explanation for the 409 we throw when an action isn't
# allowed. Keys match the action; the message lists the statuses where
# the action is valid.
_REQUIRED_STATES: dict[DeploymentAction, str] = {
    DeploymentAction.DESTROY: "success or failed",
    DeploymentAction.DELETE: "failed or cancelled",
}


def allowed_actions(db, deployment) -> set[DeploymentAction]:
    """Return the set of actions allowed for the given deployment.

    ``deployment`` can be a Deployment ORM instance or just its id —
    we only need the id to look up its status. We pass the ORM instance
    in routers because they already have it loaded.
    """
    deployment_id = getattr(deployment, "deploymentId", deployment)
    current = crud_deployments.get_deployment_status(db, deployment_id)
    if current is None:
        return set()
    return _ALLOWED.get(current, set())


def ensure_action_allowed(db, deployment, action: DeploymentAction) -> None:
    """Raise ``HTTPException(409)`` if ``action`` isn't allowed right now.

    Defense in depth on top of the DB-level partial unique index — the
    index would catch a parallel destroy/deploy collision but its error
    message is opaque. This check produces a friendly status mismatch
    message before we even open a transaction.
    """
    if action in allowed_actions(db, deployment):
        return
    deployment_id = getattr(deployment, "deploymentId", deployment)
    current = crud_deployments.get_deployment_status(db, deployment_id) or "unknown"
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Cannot {action.value} a deployment in status '{current}'. "
            f"Required status: {_REQUIRED_STATES[action]}."
        ),
    )
