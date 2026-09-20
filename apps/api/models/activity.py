import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional
from sqlalchemy import String, Enum, DateTime, ForeignKey, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
try:
    from ..database import Base
except ImportError:
    from database import Base


class NotificationType(str, PyEnum):
    """What a notification is about.

    FreeFrame's set (mention, comment, approval, new_version, assignment,
    due_soon) described a media-review product and is gone with it. FilmBill
    starts with the one kind of event that exists in P0a — something happened
    to your account — and grows a case per business event as those ship:
    document revised, quote expiring, invoice overdue, receipt needs review
    (SCOPE §5.5, §9, §14). Each addition is an Alembic migration on this
    enum, deliberately, so the set stays a decision rather than free text.
    """
    account = "account"


class ActivityLog(Base):
    """Append-only record of who did what.

    In P0a this carries platform events only (logins, invites, role changes,
    2FA enrolment). CLAUDE.md rule 3 makes it load-bearing from P2 on: every
    state change of a document writes one of these, and P0b adds the
    company_id scope and the immutability guarantees the audit trail needs.
    Nothing reads `payload` structurally yet — it is deliberately schemaless
    so a new event type needs no migration.
    """
    __tablename__ = "activity_logs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Actor-side -- keep the log row (payload/action still tell the story),
    # just lose the ability to attribute it to a specific (deleted) user.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    """One in-app notification for one user.

    FreeFrame's version pointed at an asset and a comment with NOT NULL FKs;
    both tables are gone. What replaces them is deliberately generic: a title,
    an optional body, and an optional in-app link. A notification is a message
    to a person, not a foreign key, and the business objects it will point at
    (documents, projects, parties) do not exist yet.
    """
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Recipient-side -- a notification with no one left to receive it is
    # meaningless, so it goes away with the user rather than being nulled.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    #: In-app path the notification links to, e.g. "/settings/profile".
    #: Relative on purpose: an absolute URL would bake this deployment's
    #: origin into a row that outlives it.
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
