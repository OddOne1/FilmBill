"""Dependencies between devices: `device_associations` (B2b-2 part 3).

SCOPE §4.4.3's associated items, called Dependencies on screen: a camera
needs two batteries, a battery needs a charger. Data and screens only —
booking with them is B2c, which follows one level.

Company-scoped. One ACTIVE link per (parent, associated device), enforced
by a partial unique index so a removed link (is_active false, never
deleted) does not block linking the pair again. A device cannot depend on
itself (CHECK); a longer loop is refused by the service, which walks the
graph. `origin_association_id` remembers the link a setup hint copied.

No data migration: no device has dependencies yet.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_device_associations"
down_revision: Union[str, Sequence[str], None] = "0025_party_sole_proprietor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_associations",
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
        sa.Column(
            "associated_device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity_per_parent", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("booking_mode", sa.String(8), nullable=False, server_default="always"),
        sa.Column("pricing", sa.String(8), nullable=False, server_default="included"),
        sa.Column(
            "show_on_documents", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "origin_association_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_associations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("quantity_per_parent >= 1", name="ck_device_associations_quantity"),
        sa.CheckConstraint(
            "booking_mode IN ('always', 'ask')", name="ck_device_associations_booking_mode"
        ),
        sa.CheckConstraint(
            "pricing IN ('included', 'charged')", name="ck_device_associations_pricing"
        ),
        sa.CheckConstraint(
            "device_id <> associated_device_id", name="ck_device_associations_not_self"
        ),
    )
    op.create_index("ix_device_associations_company_id", "device_associations", ["company_id"])
    op.create_index(
        "uq_device_associations_pair_active",
        "device_associations",
        ["device_id", "associated_device_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_device_associations_device", "device_associations", ["device_id"])
    op.create_index(
        "ix_device_associations_associated", "device_associations", ["associated_device_id"]
    )
    op.create_index(
        "ix_device_associations_origin", "device_associations", ["origin_association_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_device_associations_origin", table_name="device_associations")
    op.drop_index("ix_device_associations_associated", table_name="device_associations")
    op.drop_index("ix_device_associations_device", table_name="device_associations")
    op.drop_index("uq_device_associations_pair_active", table_name="device_associations")
    op.drop_index("ix_device_associations_company_id", table_name="device_associations")
    op.drop_table("device_associations")
