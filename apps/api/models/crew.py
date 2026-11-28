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


#: R2. Who wrote a crew role: somebody with `parties.edit`, or the team
#: member themselves through `/me/crew/roles`.
ROLE_SET_BY_ADMIN = "admin"
ROLE_SET_BY_SELF = "self"


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
    #: R2. A role a team member set for themselves (`set_by = self`) is
    #: unconfirmed until an admin confirms it, so a self-claimed "DoP" does
    #: not look the same as one an admin vouched for. Every role written
    #: through the contact's own routes (`parties.edit`) is `admin`.
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    set_by: Mapped[str] = mapped_column(String(8), nullable=False, server_default=ROLE_SET_BY_ADMIN)
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
        CheckConstraint("set_by IN ('admin', 'self')", name="ck_party_crew_roles_set_by"),
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


#: B2c-1. An assignment's status. `scheduled` is planned, not confirmed;
#: `booked` is confirmed — marked by hand, with a note on how. `declined`
#: and `cancelled` never count towards a clash.
ASSIGNMENT_SCHEDULED = "scheduled"
ASSIGNMENT_BOOKED = "booked"
ASSIGNMENT_DECLINED = "declined"
ASSIGNMENT_CANCELLED = "cancelled"
ASSIGNMENT_STATUSES: tuple[str, ...] = (
    ASSIGNMENT_SCHEDULED,
    ASSIGNMENT_BOOKED,
    ASSIGNMENT_DECLINED,
    ASSIGNMENT_CANCELLED,
)
#: The statuses that occupy a person's days.
OCCUPYING_STATUSES: tuple[str, ...] = (ASSIGNMENT_SCHEDULED, ASSIGNMENT_BOOKED)


class CrewAssignment(CompanyScoped, Base):
    """A person on a strip (B2c-1): first scheduled, then booked.

    Belongs to ONE strip of ONE project and carries its own days
    (`CrewAssignmentDay`), so somebody can be on days 3 to 5 of a shoot only.
    `day_rate` is copied from the person's effective rate for the role when
    the assignment is made, and can be changed; it is cost data.

    Never deleted: removing somebody sets `cancelled`. One assignment per
    person and strip that is not cancelled (a partial unique index).
    """

    __tablename__ = "crew_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_blocks.id", ondelete="RESTRICT"), nullable=False
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    role_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=ASSIGNMENT_SCHEDULED
    )
    day_rate: Mapped[Optional[Any]] = mapped_column(AmountColumn(), nullable=True)
    #: How a booking was confirmed ("by phone"), why somebody declined, why a
    #: booking was reopened.
    status_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    booked_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    booked_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: L1. Where this person sleeps on this strip: a location of kind
    #: `hotel`, from the strip's hotel list when it has one.
    hotel_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'booked', 'declined', 'cancelled')",
            name="ck_crew_assignments_status",
        ),
        CheckConstraint("day_rate IS NULL OR day_rate >= 0", name="ck_crew_assignments_rate"),
        Index(
            "uq_crew_assignments_block_party_open",
            "block_id",
            "party_id",
            unique=True,
            postgresql_where=text("status <> 'cancelled'"),
        ),
        Index("ix_crew_assignments_project", "project_id"),
        Index("ix_crew_assignments_block", "block_id"),
        Index("ix_crew_assignments_party", "party_id"),
        Index("ix_crew_assignments_hotel", "hotel_location_id"),
    )


class CrewAssignmentDay(CompanyScoped, Base):
    """One day of an assignment, inside its strip's range, with an optional
    call and wrap of this person's own (null = the day's, else the strip's).

    Unticking a day sets `is_active` false; ticking it again reactivates the
    same row. One active row per assignment and date."""

    __tablename__ = "crew_assignment_days"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crew_assignments.id", ondelete="RESTRICT"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    call_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    wrap_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_crew_assignment_days_active",
            "assignment_id",
            "date",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_crew_assignment_days_assignment", "assignment_id"),
        Index("ix_crew_assignment_days_company_date", "company_id", "date"),
    )


class CrewUnavailability(CompanyScoped, Base):
    """A person's own "not available" period: a vacation, a job elsewhere.
    Warns on a clash, never blocks. Removed with `is_active`."""

    __tablename__ = "crew_unavailability"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="ck_crew_unavailability_dates_in_order"),
        Index("ix_crew_unavailability_party", "party_id"),
    )
