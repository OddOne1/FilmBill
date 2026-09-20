"""Service helper for creating ActivityLog entries."""

import uuid
from typing import Optional
from sqlalchemy.orm import Session

from ..models.activity import ActivityLog


def log_activity(
    db: Session,
    action: str,
    user_id: Optional[uuid.UUID] = None,
    payload: Optional[dict] = None,
) -> None:
    """Create an ActivityLog entry. Call before ``db.commit()``.

    `user_id` is the ACTOR — who did it — not who it was done to. Anything
    about the target belongs in `payload`, which is schemaless on purpose so
    a new event type needs no migration.
    """
    entry = ActivityLog(
        action=action,
        user_id=user_id,
        payload=payload or {},
    )
    db.add(entry)
