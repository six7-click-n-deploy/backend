from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import App
from app.schemas import AppCreate, AppUpdate


def get_app(
    db: Session,
    app_id: UUID,
    include_deleted: bool = False,
) -> App | None:
    """Get app by ID. Hides soft-deleted apps by default.

    ``include_deleted=True`` is reserved for the rare audit lookup —
    the HTTP API never sets it.
    """
    q = db.query(App).filter(App.appId == app_id)
    if not include_deleted:
        q = q.filter(App.deleted_at.is_(None))
    return q.first()


def get_apps(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    user_id: UUID | None = None,
    include_deleted: bool = False,
) -> list[App]:
    """Get apps with optional user filter. Hides soft-deleted by default."""
    query = db.query(App)

    if not include_deleted:
        # Backed by the partial index ix_apps_live so listing stays
        # cheap even with a long history.
        query = query.filter(App.deleted_at.is_(None))
    if user_id:
        query = query.filter(App.userId == user_id)

    return query.offset(skip).limit(limit).all()


def create_app(db: Session, app: AppCreate, user_id: UUID) -> App:
    """Create a new app.

    The image is set via the dedicated ``set_app_image`` helper after
    the row exists so the same code path covers create + update.
    """
    db_app = App(
        name=app.name,
        description=app.description,
        git_link=app.git_link,
        userId=user_id
    )
    db.add(db_app)
    db.commit()
    db.refresh(db_app)
    return db_app


def update_app(db: Session, app_id: UUID, app_update: AppUpdate) -> App | None:
    """Update app information.

    The ``image`` field is intentionally NOT applied here — the router
    decodes the data-URL via ``parse_image_data_url`` and calls
    ``set_app_image`` directly. ``model_dump`` would otherwise pass
    the data-URL string straight into the ``LargeBinary`` column.
    """
    db_app = get_app(db, app_id)
    if not db_app:
        return None

    update_data = app_update.model_dump(exclude_unset=True, exclude={"image"})
    for field, value in update_data.items():
        setattr(db_app, field, value)

    db.commit()
    db.refresh(db_app)
    return db_app


def set_app_image(
    db: Session,
    app_id: UUID,
    image_bytes: bytes | None,
    image_mime: str | None,
) -> App | None:
    """Persist the image bytes + mime atomically.

    Both args ``None`` clears the image. Otherwise both must be set —
    the router enforces that via ``parse_image_data_url`` before
    calling here, so this function trusts its inputs.
    """
    db_app = get_app(db, app_id)
    if not db_app:
        return None
    db_app.image = image_bytes
    db_app.image_mime = image_mime
    db.commit()
    db.refresh(db_app)
    return db_app


def soft_delete_app(db: Session, app_id: UUID) -> bool:
    """Mark an app as deleted without removing the row.

    The row stays so existing ``deployments.appId`` foreign keys
    keep resolving (the audit trail of past deployments referencing
    this app survives), but list queries skip it. Hard-delete is
    no longer exposed — the only way to bring an app back is to
    clear ``deleted_at`` directly via SQL.
    """
    db_app = get_app(db, app_id)
    if not db_app:
        return False
    db_app.deleted_at = datetime.utcnow()
    db.commit()
    return True


# Back-compat alias. Old call sites that imported ``delete_app``
# now soft-delete; new code should call ``soft_delete_app`` directly.
delete_app = soft_delete_app
