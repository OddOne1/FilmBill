"""Gear on the calendar: bookings of devices on strips, with days (B2c-2 part 1).

* `gear_bookings` — a quantity of one device on a strip of a project,
  `scheduled` then `booked` (marked by hand), or `cancelled` with a note.
  A dependency added through a parent points at it (`added_via_booking_id`),
  at the link (`association_id`) and copies the link's quantity per parent;
  `follows_parent` while its quantity and days are still the parent's.
  One active booking per strip, device and route (two partial unique
  indexes, NULL parent and non-NULL parent).
* `gear_booking_days` — the days of the strip the booking holds the device.
  Unticked = `is_active` false.

Company-scoped, nothing deleted. Availability and overbooking are computed,
never stored: they warn and never block. No data migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_gear_bookings"
down_revision: Union[str, Sequence[str], None] = "0030_crew_assignments"
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
        "gear_bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("project_id", "projects.id"),
        _fk("block_id", "project_blocks.id"),
        _fk("device_id", "devices.id"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(12), nullable=False, server_default="scheduled"),
        sa.Column("status_note", sa.Text(), nullable=True),
        sa.Column("booked_on", sa.DateTime(timezone=True), nullable=True),
        _fk("booked_by", "users.id", nullable=True, ondelete="SET NULL"),
        _fk("added_via_booking_id", "gear_bookings.id", nullable=True),
        _fk("association_id", "device_associations.id", nullable=True),
        sa.Column("quantity_per_parent", sa.Integer(), nullable=True),
        sa.Column("follows_parent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("overbooking_note", sa.Text(), nullable=True),
        sa.Column("removal_note", sa.Text(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "status IN ('scheduled', 'booked', 'cancelled')", name="ck_gear_bookings_status"
        ),
        sa.CheckConstraint("quantity >= 1", name="ck_gear_bookings_quantity"),
        sa.CheckConstraint(
            "quantity_per_parent IS NULL OR quantity_per_parent >= 1",
            name="ck_gear_bookings_quantity_per_parent",
        ),
        sa.CheckConstraint(
            "added_via_booking_id IS NULL OR added_via_booking_id <> id",
            name="ck_gear_bookings_not_own_parent",
        ),
    )
    op.create_index("ix_gear_bookings_company_id", "gear_bookings", ["company_id"])
    op.create_index(
        "uq_gear_bookings_direct_open",
        "gear_bookings",
        ["block_id", "device_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelled' AND added_via_booking_id IS NULL"),
    )
    op.create_index(
        "uq_gear_bookings_dependent_open",
        "gear_bookings",
        ["block_id", "device_id", "added_via_booking_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelled' AND added_via_booking_id IS NOT NULL"),
    )
    op.create_index("ix_gear_bookings_project", "gear_bookings", ["project_id"])
    op.create_index("ix_gear_bookings_block", "gear_bookings", ["block_id"])
    op.create_index("ix_gear_bookings_device", "gear_bookings", ["device_id"])
    op.create_index("ix_gear_bookings_parent", "gear_bookings", ["added_via_booking_id"])
    op.create_index("ix_gear_bookings_association", "gear_bookings", ["association_id"])

    op.create_table(
        "gear_booking_days",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("booking_id", "gear_bookings.id"),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_stamps(),
    )
    op.create_index("ix_gear_booking_days_company_id", "gear_booking_days", ["company_id"])
    op.create_index(
        "uq_gear_booking_days_active",
        "gear_booking_days",
        ["booking_id", "date"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_gear_booking_days_booking", "gear_booking_days", ["booking_id"])
    op.create_index("ix_gear_booking_days_company_date", "gear_booking_days", ["company_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_gear_booking_days_company_date", table_name="gear_booking_days")
    op.drop_index("ix_gear_booking_days_booking", table_name="gear_booking_days")
    op.drop_index("uq_gear_booking_days_active", table_name="gear_booking_days")
    op.drop_index("ix_gear_booking_days_company_id", table_name="gear_booking_days")
    op.drop_table("gear_booking_days")
    op.drop_index("ix_gear_bookings_association", table_name="gear_bookings")
    op.drop_index("ix_gear_bookings_parent", table_name="gear_bookings")
    op.drop_index("ix_gear_bookings_device", table_name="gear_bookings")
    op.drop_index("ix_gear_bookings_block", table_name="gear_bookings")
    op.drop_index("ix_gear_bookings_project", table_name="gear_bookings")
    op.drop_index("uq_gear_bookings_dependent_open", table_name="gear_bookings")
    op.drop_index("uq_gear_bookings_direct_open", table_name="gear_bookings")
    op.drop_index("ix_gear_bookings_company_id", table_name="gear_bookings")
    op.drop_table("gear_bookings")
