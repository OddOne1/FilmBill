"""Gear: `devices` and `device_units` (B2b part 2).

A device is one product type with the quantity owned and a purchase price;
its units are the serial numbers of individual pieces. The two numbers are
independent by design (see `models/device.py`): planning uses the quantity,
serial numbers are records.

Both tables are company-scoped and soft-deleted only — `devices.is_active`,
`device_units.status = 'retired'` — and both foreign keys are RESTRICT,
because nothing here is ever deleted for a cascade to follow.

Case-insensitive uniqueness is carried by stored keys (`name_key`,
`serial_key`) under plain unique indexes, not by expression indexes, which
`alembic check` cannot compare.

No data migration: there is no gear in the system yet.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_devices"
down_revision: Union[str, Sequence[str], None] = "0022_party_crew_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_key", sa.String(255), nullable=False),
        sa.Column("category", sa.String(255), nullable=True),
        sa.Column("quantity_owned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purchase_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column(
            "catalog_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("quantity_owned >= 0", name="ck_devices_quantity_owned"),
    )
    op.create_index("ix_devices_company_id", "devices", ["company_id"])
    op.create_index(
        "uq_devices_company_name_active",
        "devices",
        ["company_id", "name_key"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_devices_company_active", "devices", ["company_id", "is_active"])
    op.create_index("ix_devices_catalog_item", "devices", ["catalog_item_id"])

    op.create_table(
        "device_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("serial_number", sa.String(128), nullable=False),
        sa.Column("serial_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("acquired_on", sa.Date(), nullable=True),
        sa.Column("retired_on", sa.Date(), nullable=True),
        sa.Column("retired_reason", sa.String(16), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'in_repair', 'retired')", name="ck_device_units_status"
        ),
        sa.CheckConstraint(
            "retired_reason IS NULL OR retired_reason IN ('destroyed', 'lost', 'sold', 'kept')",
            name="ck_device_units_retired_reason",
        ),
        sa.CheckConstraint(
            "status = 'retired' OR (retired_reason IS NULL AND retired_on IS NULL)",
            name="ck_device_units_retired_fields",
        ),
    )
    op.create_index("ix_device_units_company_id", "device_units", ["company_id"])
    op.create_index(
        "uq_device_units_device_serial", "device_units", ["device_id", "serial_key"], unique=True
    )
    op.create_index("ix_device_units_device", "device_units", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_device_units_device", table_name="device_units")
    op.drop_index("uq_device_units_device_serial", table_name="device_units")
    op.drop_index("ix_device_units_company_id", table_name="device_units")
    op.drop_table("device_units")
    op.drop_index("ix_devices_catalog_item", table_name="devices")
    op.drop_index("ix_devices_company_active", table_name="devices")
    op.drop_index("uq_devices_company_name_active", table_name="devices")
    op.drop_index("ix_devices_company_id", table_name="devices")
    op.drop_table("devices")
