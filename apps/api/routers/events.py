from fastapi import APIRouter, Depends, Query, HTTPException, status
from fastapi.responses import StreamingResponse
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from ..database import get_db
from ..middleware.auth import get_optional_user
from ..services.auth_service import decode_token, get_user_by_id
from ..models.user import User, UserStatus
from ..services.event_service import event_stream

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/me")
async def stream_my_events(
    db: Session = Depends(get_db),
    token: Optional[str] = Query(None),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Server-sent events addressed to the calling user.

    FreeFrame's stream was scoped to a project and gated on membership.
    Projects are gone; the channel is the user. P0b re-introduces a scope
    above this one (the active company, CLAUDE.md rule 6) — at which point
    this grows a second, company-wide stream rather than widening this one,
    because "events for me" and "events for everyone in my company" are
    different permission questions.
    """
    # EventSource can't send Authorization headers, so accept token as query param
    user = current_user
    if not user and token:
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            user = get_user_by_id(db, uuid.UUID(payload["sub"]))
    if not user or user.status == UserStatus.deactivated:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authenticated")

    return StreamingResponse(
        event_stream(str(user.id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
