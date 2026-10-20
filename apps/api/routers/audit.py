"""Settings → Company → Audit log.

One endpoint, read-only, and read-only all the way down: there is no write
endpoint for `audit_events` and there cannot be one. Rows appear only as a
side effect of the change they describe (`services/audit.record`, in the
caller's transaction), and the table refuses UPDATE and DELETE at the
database level.

**`audit.view`: Owner, Admin and the tax advisor** (Mathias, 2026-10-20).

**Install-level events are not shown here.** An event with `company_id` NULL
— the mail settings, a user account, the instance-wide 2FA switch — belongs
to the installation, and a company owner has no business reading what the
installation administrator does. `services/audit.company_events` is the only
query, and the filter lives inside it: `audit_events.company_id` is nullable,
so this is the one business table `permissions.scoped()` refuses by design
(rule 6, honoured by a named function instead of the mixin).
"""

import uuid
from datetime import date, datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.company import Membership, require
from ..models.audit import AuditEvent
from ..models.user import User
from ..schemas.audit import AuditEventPage, AuditEventResponse
from ..services import audit

router = APIRouter(tags=["audit"])

#: The page size cap. Fifty by default because the screen is a table somebody
#: reads, and 200 at most so a client cannot ask for the whole history of a
#: company in one response.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _end_of_day(value: date) -> datetime:
    """`to=2026-10-20` means the END of the 20th.

    A date filter that silently meant midnight would exclude everything that
    happened on the day somebody typed, which reads as "there are no events
    today" — the most misleading possible answer from an audit log.
    """
    return datetime.combine(value, time.max, tzinfo=timezone.utc)


@router.get("/company/audit-events", response_model=AuditEventPage)
def list_audit_events(
    entity_type: Optional[str] = Query(default=None, max_length=64),
    entity_id: Optional[uuid.UUID] = None,
    action: Optional[str] = Query(default=None, max_length=64),
    user_id: Optional[uuid.UUID] = None,
    date_from: Optional[date] = Query(default=None, alias="from"),
    date_to: Optional[date] = Query(default=None, alias="to"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("audit.view")),
):
    """This company's audit log, newest first, filterable and paged."""
    query = audit.company_events(db, membership)

    if entity_type:
        query = query.filter(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(AuditEvent.entity_id == entity_id)
    if action:
        query = query.filter(AuditEvent.action == action)
    if user_id is not None:
        query = query.filter(AuditEvent.user_id == user_id)
    if date_from is not None:
        query = query.filter(
            AuditEvent.created_at
            >= datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        )
    if date_to is not None:
        query = query.filter(AuditEvent.created_at <= _end_of_day(date_to))

    total = query.with_entities(func.count(AuditEvent.id)).scalar() or 0
    rows = (
        query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # One lookup for the page's actors rather than one per row.
    user_ids = {row.user_id for row in rows if row.user_id is not None}
    users = (
        {
            user.id: user
            for user in db.query(User).filter(User.id.in_(user_ids)).all()
        }
        if user_ids
        else {}
    )

    # The filter's own options, from what this company's log actually holds.
    # A hardcoded list goes stale the moment a new action is recorded, and a
    # filter offering an action that never happens is a filter people stop
    # trusting.
    vocabulary = audit.company_events(db, membership)
    entity_types = sorted(
        value
        for (value,) in vocabulary.with_entities(AuditEvent.entity_type)
        .distinct()
        .all()
    )
    actions = sorted(
        value
        for (value,) in vocabulary.with_entities(AuditEvent.action).distinct().all()
    )

    return AuditEventPage(
        events=[
            AuditEventResponse(
                id=row.id,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                action=row.action,
                changes=row.changes or {},
                reason=row.reason,
                user_id=row.user_id,
                user_name=(
                    users[row.user_id].name if row.user_id in users else None
                ),
                user_email=(
                    users[row.user_id].email if row.user_id in users else None
                ),
                ip=row.ip,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
        entity_types=entity_types,
        actions=actions,
    )
