"""Crew are parties with a third flag: `parties.is_crew` (B2b part 1).

A crew member — a DoP, a gaffer, a sound recordist — is a person in the
parties list, flagged, rather than a row in a table of its own. The freelancer
who is crew on one production and invoices you as a supplier on the next is
then ONE record wearing two hats, which is the reason `is_customer` and
`is_supplier` are flags and not types (SCOPE §3).

**No number.** `party_service.allocate_party_number` gives a number only to a
party that is a customer or a supplier, so a crew-only person consumes no
number from either series. Making them a supplier later allocates the supplier
number then, exactly as for any other party.

**No data migration.** Every existing party starts with `is_crew = false`;
nobody is crew until somebody says so.

The partial index serves the crew list, which is the one query that filters
on the flag: a handful of crew among many customers is what it is for.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_party_crew_flag"
down_revision: Union[str, Sequence[str], None] = "0021_project_blocks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parties",
        sa.Column(
            "is_crew", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_index(
        "ix_parties_company_crew",
        "parties",
        ["company_id"],
        postgresql_where=sa.text("is_crew"),
    )


def downgrade() -> None:
    op.drop_index("ix_parties_company_crew", table_name="parties")
    op.drop_column("parties", "is_crew")
