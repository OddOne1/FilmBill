"""Locations inside projects, uses, hotel stays, booking confirmations (L1b).

Mathias's review of L1 (2026-11-28):

* `locations.kind` becomes `locations.uses` (one or more of shoot, hotel,
  hospital, restaurant, other). Each row gets `[kind]`; a location already
  used on a strip or a day also gets `shoot` (it IS a shooting location).
  `source_link` (the pasted map link) and `osm_ref` (an OpenStreetMap
  hospital) are new.
* `project_locations` — what a project's Locations tab lists. Filled from
  every location a strip, a day, a strip's hotel list or a crew hotel of the
  project used.
* `hotel_stays` — a crew member at a hotel, check-in to check-out. Each L1
  crew hotel (`crew_assignments.hotel_location_id`) becomes a stay from the
  assignment's first active day to the day after its last; an assignment
  with no active day keeps no stay (it has no dates to give it).
* `location_attachments` — booking confirmations: a stored file or text.
* L1's `project_block_hotels` and `crew_assignments.hotel_location_id` are
  dropped once copied: the project's hotels and their guests replace them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_project_locations_stays"
down_revision: Union[str, Sequence[str], None] = "0034_location_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USES = "ARRAY['shoot', 'hotel', 'hospital', 'restaurant', 'other']::varchar(16)[]"


def _timestamps():
    return [
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _company():
    return sa.Column(
        "company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )


def _fk(name, table, nullable=False, ondelete="RESTRICT"):
    return sa.Column(name, postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    # ── uses ────────────────────────────────────────────────────────────
    op.add_column("locations", sa.Column("uses", postgresql.ARRAY(sa.String(16)), nullable=True))
    op.add_column("locations", sa.Column("source_link", sa.Text(), nullable=True))
    op.add_column("locations", sa.Column("osm_ref", sa.String(64), nullable=True))
    op.execute("UPDATE locations SET uses = ARRAY[kind]::varchar(16)[]")
    op.execute(
        "UPDATE locations SET uses = array_append(uses, 'shoot') "
        "WHERE NOT ('shoot' = ANY(uses)) AND id IN ("
        "  SELECT location_id FROM project_blocks WHERE location_id IS NOT NULL"
        "  UNION SELECT location_id FROM project_days WHERE location_id IS NOT NULL)"
    )
    op.alter_column("locations", "uses", nullable=False)
    op.create_check_constraint("ck_locations_uses", "locations", f"cardinality(uses) >= 1 AND uses <@ {USES}")
    op.drop_index("ix_locations_company_kind", table_name="locations")
    op.drop_constraint("ck_locations_kind", "locations", type_="check")
    op.drop_column("locations", "kind")
    op.create_index(
        "uq_locations_company_osm_active",
        "locations",
        ["company_id", "osm_ref"],
        unique=True,
        postgresql_where=sa.text("is_active AND osm_ref IS NOT NULL"),
    )

    # ── project_locations ───────────────────────────────────────────────
    op.create_table(
        "project_locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("project_id", "projects"),
        _fk("location_id", "locations"),
        *_timestamps(),
        sa.UniqueConstraint("project_id", "location_id", name="uq_project_locations_pair"),
    )
    op.create_index("ix_project_locations_company_id", "project_locations", ["company_id"])
    op.create_index("ix_project_locations_location", "project_locations", ["location_id"])
    op.execute(
        "INSERT INTO project_locations (id, company_id, project_id, location_id) "
        "SELECT gen_random_uuid(), l.company_id, u.project_id, u.location_id FROM ("
        "  SELECT project_id, location_id FROM project_blocks WHERE location_id IS NOT NULL AND is_active"
        "  UNION SELECT project_id, location_id FROM project_days WHERE location_id IS NOT NULL AND is_active"
        "  UNION SELECT b.project_id, h.location_id FROM project_block_hotels h"
        "        JOIN project_blocks b ON b.id = h.block_id WHERE h.is_active AND b.is_active"
        "  UNION SELECT project_id, hotel_location_id FROM crew_assignments"
        "        WHERE hotel_location_id IS NOT NULL AND status <> 'cancelled'"
        ") u JOIN locations l ON l.id = u.location_id"
    )

    # ── hotel_stays ─────────────────────────────────────────────────────
    op.create_table(
        "hotel_stays",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("project_id", "projects"),
        _fk("location_id", "locations"),
        _fk("party_id", "parties"),
        sa.Column("check_in", sa.Date(), nullable=False),
        sa.Column("check_out", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("check_in < check_out", name="ck_hotel_stays_dates"),
    )
    op.create_index("ix_hotel_stays_company_id", "hotel_stays", ["company_id"])
    op.create_index("ix_hotel_stays_project", "hotel_stays", ["project_id"])
    op.create_index("ix_hotel_stays_location", "hotel_stays", ["location_id"])
    op.create_index("ix_hotel_stays_party", "hotel_stays", ["party_id"])
    op.execute(
        "INSERT INTO hotel_stays (id, company_id, project_id, location_id, party_id, check_in, check_out) "
        "SELECT gen_random_uuid(), a.company_id, a.project_id, a.hotel_location_id, a.party_id, "
        "       MIN(d.date), MAX(d.date) + 1 "
        "FROM crew_assignments a JOIN crew_assignment_days d ON d.assignment_id = a.id AND d.is_active "
        "WHERE a.hotel_location_id IS NOT NULL AND a.status <> 'cancelled' "
        "GROUP BY a.id, a.company_id, a.project_id, a.hotel_location_id, a.party_id"
    )

    # ── location_attachments ────────────────────────────────────────────
    op.create_table(
        "location_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("project_id", "projects"),
        _fk("location_id", "locations"),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("content_type", sa.String(127), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("s3_key", sa.String(512), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        _fk("created_by", "users", nullable=True, ondelete="SET NULL"),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('file', 'text')", name="ck_location_attachments_kind"),
        sa.CheckConstraint(
            "(kind = 'file' AND s3_key IS NOT NULL) OR (kind = 'text' AND body_text IS NOT NULL)",
            name="ck_location_attachments_content",
        ),
    )
    op.create_index("ix_location_attachments_company_id", "location_attachments", ["company_id"])
    op.create_index("ix_location_attachments_project_location", "location_attachments", ["project_id", "location_id"])

    # ── L1's hotel structures, now copied ───────────────────────────────
    op.drop_index("ix_crew_assignments_hotel", table_name="crew_assignments")
    op.drop_column("crew_assignments", "hotel_location_id")
    op.drop_index("ix_project_block_hotels_location", table_name="project_block_hotels")
    op.drop_index("ix_project_block_hotels_company_id", table_name="project_block_hotels")
    op.drop_table("project_block_hotels")


def downgrade() -> None:
    op.create_table(
        "project_block_hotels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _company(),
        _fk("block_id", "project_blocks"),
        _fk("location_id", "locations"),
        *_timestamps(),
        sa.UniqueConstraint("block_id", "location_id", name="uq_project_block_hotels_pair"),
    )
    op.create_index("ix_project_block_hotels_company_id", "project_block_hotels", ["company_id"])
    op.create_index("ix_project_block_hotels_location", "project_block_hotels", ["location_id"])
    op.add_column(
        "crew_assignments",
        sa.Column(
            "hotel_location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="RESTRICT", name="fk_crew_assignments_hotel_location_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_crew_assignments_hotel", "crew_assignments", ["hotel_location_id"])
    # A stay back onto the assignment it overlaps (the earliest stay wins).
    op.execute(
        "UPDATE crew_assignments a SET hotel_location_id = s.location_id FROM ("
        "  SELECT DISTINCT ON (project_id, party_id) project_id, party_id, location_id FROM hotel_stays"
        "  WHERE is_active ORDER BY project_id, party_id, check_in) s "
        "WHERE a.project_id = s.project_id AND a.party_id = s.party_id AND a.status <> 'cancelled'"
    )
    op.drop_table("location_attachments")
    op.drop_table("hotel_stays")
    op.drop_table("project_locations")
    op.drop_index("uq_locations_company_osm_active", table_name="locations")
    op.add_column("locations", sa.Column("kind", sa.String(16), nullable=True))
    op.execute(
        "UPDATE locations SET kind = CASE WHEN uses[1] = 'restaurant' THEN 'other' ELSE uses[1] END"
    )
    op.alter_column("locations", "kind", nullable=False)
    op.create_check_constraint(
        "ck_locations_kind", "locations", "kind IN ('shoot', 'hotel', 'hospital', 'other')"
    )
    op.create_index("ix_locations_company_kind", "locations", ["company_id", "kind"])
    op.drop_constraint("ck_locations_uses", "locations", type_="check")
    op.drop_column("locations", "osm_ref")
    op.drop_column("locations", "source_link")
    op.drop_column("locations", "uses")
