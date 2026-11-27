"""Crew roles and rates: `party_crew_roles` (R1 part 1).

A person flagged crew holds roles; a role is a catalog labor item; the
person may have their own day rate in it (net, company currency, what they
cost per day). The typical rate is the item's cost rate per day and is NOT
copied here — the effective rate is read, own else typical, every time.

Company-scoped, never deleted: one ACTIVE row per (party, item), by a
partial unique index, so a removed role can be added again.

No data migration: existing tags stay tags. The person page offers to turn
a tag that matches a labor item into a role, one click per tag.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_party_crew_roles"
down_revision: Union[str, Sequence[str], None] = "0028_device_size_weight_image"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "party_crew_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "party_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("own_day_rate", sa.Numeric(18, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "own_day_rate IS NULL OR own_day_rate >= 0", name="ck_party_crew_roles_rate"
        ),
    )
    op.create_index("ix_party_crew_roles_company_id", "party_crew_roles", ["company_id"])
    op.create_index(
        "uq_party_crew_roles_active",
        "party_crew_roles",
        ["party_id", "item_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_party_crew_roles_party", "party_crew_roles", ["party_id"])
    op.create_index("ix_party_crew_roles_item", "party_crew_roles", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_party_crew_roles_item", table_name="party_crew_roles")
    op.drop_index("ix_party_crew_roles_party", table_name="party_crew_roles")
    op.drop_index("uq_party_crew_roles_active", table_name="party_crew_roles")
    op.drop_index("ix_party_crew_roles_company_id", table_name="party_crew_roles")
    op.drop_table("party_crew_roles")
