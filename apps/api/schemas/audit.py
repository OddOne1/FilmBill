"""Response shapes for the audit log."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from .auth import JsonObject


class AuditEventResponse(BaseModel):
    """One row, as the Audit log screen shows it.

    The actor's name and email are joined in rather than left to the client
    to resolve: the screen needs them on every row, and a request per row is
    how a page of fifty becomes fifty requests. They are the user's CURRENT
    name — the row records who, not what they were called — and the user may
    since have been deleted, in which case `user_id` survives (the FK is
    `SET NULL`… on the user's side it is not, see models/audit.py) and the
    name is None.
    """

    id: uuid.UUID
    entity_type: str
    entity_id: Optional[uuid.UUID] = None
    action: str
    #: field → [old, new]. Already redacted and masked server-side; see
    #: `services/audit.py`. The client renders these verbatim and must not
    #: try to interpret a `[redacted]` marker as a value.
    #:
    #: `JsonObject`, not a bare `dict`: Pydantic emits a bare
    #: `{"type": "object"}` for the latter and openapi-typescript reads that,
    #: correctly, as `Record<string, never>` — a map that can hold nothing,
    #: so every read of a changed field fails to typecheck. See
    #: `schemas/auth.py` for the long version. The generator is right; the
    #: schema was vague.
    changes: JsonObject
    reason: Optional[str] = None
    user_id: Optional[uuid.UUID] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    ip: Optional[str] = None
    created_at: datetime


class AuditEventPage(BaseModel):
    """One page of events, newest first.

    `total` as well as the rows, because "page 3 of 40" is the only paging
    control that tells somebody whether the thing they are looking for is
    plausibly in here at all. It costs one COUNT.
    """

    events: list[AuditEventResponse]
    total: int
    limit: int
    offset: int
    #: Every `entity_type` and `action` this company's log actually contains,
    #: so the filter offers what is there rather than a hardcoded list that
    #: goes stale the moment a new action is recorded.
    entity_types: list[str] = []
    actions: list[str] = []
