"""Alternatives for a device: `device_alternatives` (B2b-2 part 4).

SCOPE §4.4.5: per device an ordered list of devices that can stand in for
it — Alexa 35 → Alexa Mini LF → Sony Venice 2 — with a note and a free-text
price hint. One-directional; the screen offers the reverse. Used by B2c's
overbooking popup, never booked automatically.

One ACTIVE row per pair (partial unique index), no self-link (CHECK),
removal deactivates. No data migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_device_alternatives"
down_revision: Union[str, Sequence[str], None] = "0026_device_associations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_alternatives",
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
            "alternative_device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("price_hint", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "device_id <> alternative_device_id", name="ck_device_alternatives_not_self"
        ),
    )
    op.create_index("ix_device_alternatives_company_id", "device_alternatives", ["company_id"])
    op.create_index(
        "uq_device_alternatives_pair_active",
        "device_alternatives",
        ["device_id", "alternative_device_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_device_alternatives_device", "device_alternatives", ["device_id"])
    op.create_index(
        "ix_device_alternatives_alternative", "device_alternatives", ["alternative_device_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_device_alternatives_alternative", table_name="device_alternatives")
    op.drop_index("ix_device_alternatives_device", table_name="device_alternatives")
    op.drop_index("uq_device_alternatives_pair_active", table_name="device_alternatives")
    op.drop_index("ix_device_alternatives_company_id", table_name="device_alternatives")
    op.drop_table("device_alternatives")
