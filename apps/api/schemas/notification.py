from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional
from ..models.activity import NotificationType


class NotificationResponse(BaseModel):
    id: uuid.UUID
    type: NotificationType
    title: str
    body: Optional[str] = None
    #: Relative in-app path, or null when the notification is informational
    #: and has nowhere to go.
    link: Optional[str] = None
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
