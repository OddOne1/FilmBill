"""Locations as records: shoot, hotel, hospital, other (L1 part 1).

One table, `locations`, company-scoped, never deleted (`is_active`):

* `name` + `name_key` (lower-cased, inner whitespace collapsed) with a
  partial unique index over the ACTIVE rows of a company, so an archived
  location never blocks its own name;
* `kind` in `shoot | hotel | hospital | other`;
* the address (`street`, `street2`, `zip`, `city`, `country` alpha-2);
* `latitude` / `longitude` `NUMERIC(9,6)`, both or neither, in range — the
  database says so as well as the service;
* `phone`, `email`, `website`, `emergency_phone`, `access_notes`, `notes`;
* an optional on-site contact (`contact_party_id`, RESTRICT) and
  `contact_text`;
* a hospital's verification: `verified_on`, `verified_by` (SET NULL),
  `verification_note`.

Nothing is attached to strips, days or crew here; that is part 2 (0034).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_locations"
down_revision: Union[str, Sequence[str], None] = "0032_team_members_as_crew"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_key", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("street", sa.String(255), nullable=True),
        sa.Column("street2", sa.String(255), nullable=True),
        sa.Column("zip", sa.String(32), nullable=True),
        sa.Column("city", sa.String(255), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("website", sa.String(512), nullable=True),
        sa.Column("emergency_phone", sa.String(64), nullable=True),
        sa.Column("access_notes", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "contact_party_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("contact_text", sa.Text(), nullable=True),
        sa.Column("verified_on", sa.Date(), nullable=True),
        sa.Column(
            "verified_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("verification_note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('shoot', 'hotel', 'hospital', 'other')", name="ck_locations_kind"),
        sa.CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_locations_coordinates_pair"),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)", name="ck_locations_latitude"
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)", name="ck_locations_longitude"
        ),
    )
    op.create_index("ix_locations_company_id", "locations", ["company_id"])
    op.create_index(
        "uq_locations_company_name_active",
        "locations",
        ["company_id", "name_key"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_locations_company_kind", "locations", ["company_id", "kind"])
    op.create_index("ix_locations_contact_party", "locations", ["contact_party_id"])


def downgrade() -> None:
    op.drop_index("ix_locations_contact_party", table_name="locations")
    op.drop_index("ix_locations_company_kind", table_name="locations")
    op.drop_index("uq_locations_company_name_active", table_name="locations")
    op.drop_index("ix_locations_company_id", table_name="locations")
    op.drop_table("locations")
