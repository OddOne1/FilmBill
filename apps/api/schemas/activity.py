from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional

from .auth import JsonObject


class ActivityLogResponse(BaseModel):
    id: uuid.UUID
    action: str
    user_id: Optional[uuid.UUID]
    org_id: Optional[uuid.UUID]
    project_id: Optional[uuid.UUID]
    asset_id: Optional[uuid.UUID]
    #: See `JsonObject` in schemas/auth.py — a bare `dict` generates a
    #: TypeScript `Record<string, never>`, which nothing can read.
    payload: JsonObject
    created_at: datetime
    model_config = {"from_attributes": True}
