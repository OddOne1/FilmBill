"""Crew roles and rates, and crew on the calendar (R1 + B2c-1).

**A role is a catalog labor item.** "DoP", "Gaffer", "Sound" are the
catalog's `labor` items — one list, kept where the typical day rate already
lives (the item's cost rate per day). `PartyCrewRole` says that a person
does this kind of work, and optionally what THIS person costs per day in it.
The tags a person had before R1 stay tags until somebody turns one into a
role with one click; nothing converts them by itself.

**Rates are cost data.** Every response that can carry one carries it only
for a role holding `budgets.view` (see `services/crew_roles_service.py`).

**Nothing here is ever deleted.** A role is retired with `is_active`, an
assignment by its status (`cancelled`), an unavailability period with
`is_active`. The unique indexes are partial over the active rows, so a
retired row never blocks its own re-creation.
"""

import uuid
from datetime import date, datetime, time
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

try:
    from ..database import Base
except ImportError:  # pragma: no cover - direct-import path for alembic
    from database import Base

try:
    from .company import CompanyScoped
except ImportError:  # pragma: no cover
    from models.company import CompanyScoped

try:
    from ..core.money import AmountColumn
except ImportError:  # pragma: no cover
    from core.money import AmountColumn


class PartyCrewRole(CompanyScoped, Base):
    """A person does this kind of work (a labor item), at this day rate.

    `own_day_rate` is net, in the company currency, what the person COSTS
    per day in this role. Null means "use the item's typical cost rate".
    A person may hold several roles, each with its own rate — a DoP at one
    rate may also gaffer at another.

    The party must be flagged crew and the item must be a labor item of the
    same company; both are checked by the service, which is the only writer.
    """

    __tablename__ = "party_crew_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="RESTRICT"), nullable=False
    )
    #: `NUMERIC(18,2)` — money (CLAUDE.md rule 1).
    own_day_rate: Mapped[Optional[Any]] = mapped_column(AmountColumn(), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "own_day_rate IS NULL OR own_day_rate >= 0", name="ck_party_crew_roles_rate"
        ),
        Index(
            "uq_party_crew_roles_active",
            "party_id",
            "item_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_party_crew_roles_party", "party_id"),
        Index("ix_party_crew_roles_item", "item_id"),
    )
