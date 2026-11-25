"""Extras: charges agreed after the quote, as lines in a reserved section.

SCOPE D92–D94 (B1.1 part 4). An extra is not a new calculation path: it is a
budget line in the budget's one Extras section, so the engine, the tax
grouping and the cost tracking apply to it unchanged. Only ACCEPTED extras
enter the budget's totals.

* `budget_sections.is_extras` — `false` for every existing section. At most
  ONE per budget, enforced by a partial unique index so two requests that
  both "create on first use" cannot both win.
* `budget_lines.extra_status` — `proposed`, `accepted` or `declined`; NULL
  for every ordinary line. "A line in the Extras section has a status and a
  line elsewhere does not" is a two-table condition and lives in the
  service, for migration 0015's reason (a trigger would be a second place
  for the rule). The vocabulary is Python's, not a CHECK, for the same one.
* `budget_lines.extra_decided_on` — the day it was accepted or declined.
* `budget_lines.extra_note` — what the customer was told.

`downgrade` drops the index and the four columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_budget_extras"
down_revision: Union[str, Sequence[str], None] = "0017_budget_close"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "budget_sections",
        sa.Column(
            "is_extras", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_index(
        "uq_budget_sections_one_extras",
        "budget_sections",
        ["budget_id"],
        unique=True,
        postgresql_where=sa.text("is_extras"),
    )
    op.add_column(
        "budget_lines", sa.Column("extra_status", sa.String(16), nullable=True)
    )
    op.add_column(
        "budget_lines", sa.Column("extra_decided_on", sa.Date(), nullable=True)
    )
    op.add_column("budget_lines", sa.Column("extra_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("budget_lines", "extra_note")
    op.drop_column("budget_lines", "extra_decided_on")
    op.drop_column("budget_lines", "extra_status")
    op.drop_index("uq_budget_sections_one_extras", table_name="budget_sections")
    op.drop_column("budget_sections", "is_extras")
