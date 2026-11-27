"""Crew on the calendar: assignments, their days, unavailability (B2c-1 part 2).

* `crew_assignments` — a person on a strip of a project, `scheduled` then
  `booked` (marked by hand), or `declined` / `cancelled`. One per strip and
  person that is not cancelled (partial unique index). `day_rate` is copied
  from the effective rate when made; cost data.
* `crew_assignment_days` — the days of the strip the person works, with
  optional call and wrap of their own. Unticked = `is_active` false.
* `crew_unavailability` — a person's own "not available" periods.

Company-scoped, nothing deleted. Clashes are computed, never stored: they
warn and never block. No data migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_crew_assignments"
down_revision: Union[str, Sequence[str], None] = "0029_party_crew_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _company():
    return sa.Column(
        "company_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )


def _stamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _fk(name, target, nullable=False, ondelete="RESTRICT"):
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "crew_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("project_id", "projects.id"),
        _fk("block_id", "project_blocks.id"),
        _fk("party_id", "parties.id"),
        _fk("role_item_id", "items.id", nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="scheduled"),
        sa.Column("day_rate", sa.Numeric(18, 2), nullable=True),
        sa.Column("status_note", sa.Text(), nullable=True),
        sa.Column("booked_on", sa.DateTime(timezone=True), nullable=True),
        _fk("booked_by", "users.id", nullable=True, ondelete="SET NULL"),
        *_stamps(),
        sa.CheckConstraint(
            "status IN ('scheduled', 'booked', 'declined', 'cancelled')",
            name="ck_crew_assignments_status",
        ),
        sa.CheckConstraint("day_rate IS NULL OR day_rate >= 0", name="ck_crew_assignments_rate"),
    )
    op.create_index("ix_crew_assignments_company_id", "crew_assignments", ["company_id"])
    op.create_index(
        "uq_crew_assignments_block_party_open",
        "crew_assignments",
        ["block_id", "party_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelled'"),
    )
    op.create_index("ix_crew_assignments_project", "crew_assignments", ["project_id"])
    op.create_index("ix_crew_assignments_block", "crew_assignments", ["block_id"])
    op.create_index("ix_crew_assignments_party", "crew_assignments", ["party_id"])

    op.create_table(
        "crew_assignment_days",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("assignment_id", "crew_assignments.id"),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("call_time", sa.Time(), nullable=True),
        sa.Column("wrap_time", sa.Time(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_stamps(),
    )
    op.create_index("ix_crew_assignment_days_company_id", "crew_assignment_days", ["company_id"])
    op.create_index(
        "uq_crew_assignment_days_active",
        "crew_assignment_days",
        ["assignment_id", "date"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_crew_assignment_days_assignment", "crew_assignment_days", ["assignment_id"])
    op.create_index(
        "ix_crew_assignment_days_company_date", "crew_assignment_days", ["company_id", "date"]
    )

    op.create_table(
        "crew_unavailability",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("party_id", "parties.id"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_stamps(),
        sa.CheckConstraint("start_date <= end_date", name="ck_crew_unavailability_dates_in_order"),
    )
    op.create_index("ix_crew_unavailability_company_id", "crew_unavailability", ["company_id"])
    op.create_index("ix_crew_unavailability_party", "crew_unavailability", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_crew_unavailability_party", table_name="crew_unavailability")
    op.drop_index("ix_crew_unavailability_company_id", table_name="crew_unavailability")
    op.drop_table("crew_unavailability")
    op.drop_index("ix_crew_assignment_days_company_date", table_name="crew_assignment_days")
    op.drop_index("ix_crew_assignment_days_assignment", table_name="crew_assignment_days")
    op.drop_index("uq_crew_assignment_days_active", table_name="crew_assignment_days")
    op.drop_index("ix_crew_assignment_days_company_id", table_name="crew_assignment_days")
    op.drop_table("crew_assignment_days")
    op.drop_index("ix_crew_assignments_party", table_name="crew_assignments")
    op.drop_index("ix_crew_assignments_block", table_name="crew_assignments")
    op.drop_index("ix_crew_assignments_project", table_name="crew_assignments")
    op.drop_index("uq_crew_assignments_block_party_open", table_name="crew_assignments")
    op.drop_index("ix_crew_assignments_company_id", table_name="crew_assignments")
    op.drop_table("crew_assignments")
