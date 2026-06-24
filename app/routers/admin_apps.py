from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import app_version_approvals as crud_approvals
from app.crud import apps as crud_apps
from app.database import get_db
from app.models import User
from app.routers.apps import _serialize_app
from app.schemas import (
    AppResponse,
    AppVersionApprovalDecision,
    AppVersionApprovalResponse,
    AppVersionApprovalWithApp,
)
from app.utils.permissions import get_current_admin

router = APIRouter()


# ----------------------------------------------------------------
# PENDING REVIEW QUEUE
# ----------------------------------------------------------------
@router.get(
    "/apps/versions/pending",
    response_model=list[AppVersionApprovalWithApp],
    tags=["Admin"],
)
def list_pending_versions(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Return all version submissions awaiting admin review, oldest first."""
    return crud_approvals.get_pending_approvals(db)


# ----------------------------------------------------------------
# APPROVE VERSION
# ----------------------------------------------------------------
@router.post(
    "/apps/{app_id}/versions/{version_tag}/approve",
    response_model=AppVersionApprovalResponse,
    tags=["Admin"],
)
def approve_version(
    app_id: UUID,
    version_tag: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Approve a PENDING version — makes it deployable by all users."""
    _require_app(db, app_id)
    return crud_approvals.approve(db, app_id, version_tag, current_user.userId)


# ----------------------------------------------------------------
# REJECT VERSION
# ----------------------------------------------------------------
@router.post(
    "/apps/{app_id}/versions/{version_tag}/reject",
    response_model=AppVersionApprovalResponse,
    tags=["Admin"],
)
def reject_version(
    app_id: UUID,
    version_tag: str,
    body: AppVersionApprovalDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Reject a PENDING version with a mandatory reason."""
    _require_app(db, app_id)
    return crud_approvals.reject(
        db, app_id, version_tag, current_user.userId, body.rejection_reason
    )


# ----------------------------------------------------------------
# REVOKE APPROVED VERSION
# ----------------------------------------------------------------
@router.post(
    "/apps/{app_id}/versions/{version_tag}/revoke",
    response_model=AppVersionApprovalResponse,
    tags=["Admin"],
)
def revoke_version(
    app_id: UUID,
    version_tag: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Revoke a previously APPROVED version (sets status to REJECTED)."""
    _require_app(db, app_id)
    return crud_approvals.revoke(db, app_id, version_tag, current_user.userId)


# ----------------------------------------------------------------
# EMERGENCY DEACTIVATION
# ----------------------------------------------------------------
@router.put(
    "/apps/{app_id}",
    response_model=AppResponse,
    tags=["Admin"],
)
def deactivate_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Emergency deactivation: set app to private so it disappears from
    the store immediately. Does not delete the app or its deployments."""
    from app.schemas import AppUpdate

    _require_app(db, app_id)
    updated = crud_apps.update_app(db, app_id, AppUpdate(is_private=True))
    return _serialize_app(updated)


# ----------------------------------------------------------------
# HELPER
# ----------------------------------------------------------------
def _require_app(db: Session, app_id: UUID):
    app = crud_apps.get_app(db, app_id, include_deleted=True)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found",
        )
    return app
