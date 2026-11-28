"""Where a location attaches: strips, days, strip hotels, crew (L1 part 2).

* `project_blocks.location_id` — the strip's default location;
* `project_days.location_id` — a day that happens somewhere else;
* `project_block_hotels (block_id, location_id)` — the hotels in use on a
  strip, unique per pair; removing one sets `is_active` false and adding it
  again reactivates the same row;
* `crew_assignments.hotel_location_id` — where this person sleeps.

All nullable, all `ON DELETE RESTRICT` (a location is never deleted, only
archived). **The B2a free-text `location` columns are untouched**, and no
location is created from them: the screen offers "Create location from this
text" and a person decides.

Which kinds fit where (shoot/other on strips and days, hotel for hotels) is
the service's rule, with a sentence.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_location_links"
down_revision: Union[str, Sequence[str], None] = "0033_locations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _location_fk(table: str, column: str) -> None:
    op.add_column(
        table,
        sa.Column(
            column,
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="RESTRICT", name=f"fk_{table}_{column}"),
            nullable=True,
        ),
    )


def upgrade() -> None:
    _location_fk("project_blocks", "location_id")
    op.create_index("ix_project_blocks_location", "project_blocks", ["location_id"])
    _location_fk("project_days", "location_id")
    _location_fk("crew_assignments", "hotel_location_id")
    op.create_index("ix_crew_assignments_hotel", "crew_assignments", ["hotel_location_id"])

    op.create_table(
        "project_block_hotels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "block_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_blocks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("block_id", "location_id", name="uq_project_block_hotels_pair"),
    )
    op.create_index("ix_project_block_hotels_company_id", "project_block_hotels", ["company_id"])
    op.create_index("ix_project_block_hotels_location", "project_block_hotels", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_project_block_hotels_location", table_name="project_block_hotels")
    op.drop_index("ix_project_block_hotels_company_id", table_name="project_block_hotels")
    op.drop_table("project_block_hotels")
    op.drop_index("ix_crew_assignments_hotel", table_name="crew_assignments")
    op.drop_column("crew_assignments", "hotel_location_id")
    op.drop_column("project_days", "location_id")
    op.drop_index("ix_project_blocks_location", table_name="project_blocks")
    op.drop_column("project_blocks", "location_id")
