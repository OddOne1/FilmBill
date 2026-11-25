"""The production fee: a company preset, and one fee adjustment per budget.

B1.2 part 2. The fee is NOT a new calculation path: it is a `surcharge`
adjustment like any other, marked with `role = 'production_fee'` so the sheet
can show it as its own control and the company preset can be applied to a
new budget. It moves the price only (D78), so the engine, the tax grouping,
the margin and the CSV need nothing new.

* `companies.default_production_fee_percent` — NUMERIC(7,4), nullable. NULL
  means "no preset", and there is **no default value**: a fee nobody chose
  would be on every new budget of every company that never opened the
  setting. [Guessing] in SCOPE until Mathias says otherwise.
* `companies.default_production_fee_kinds` — the kinds the preset is charged
  on; empty means everything.
* `budget_adjustments.role` — NULL for every existing adjustment, including
  the fee rows of S1 example budgets made before this migration: which of a
  budget's surcharges "is the fee" is not something a migration can tell
  from a label, and guessing would put two fees on a budget that has a
  "Production fee" and a "Production fee (crew)". New example budgets mark
  their own fee.
* `uq_budget_adjustments_one_production_fee` — at most one ACTIVE fee per
  budget, as a partial unique index so the rule holds for two requests that
  race, not only for the one the service saw.

`downgrade` drops the index and the three columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_production_fee"
down_revision: Union[str, Sequence[str], None] = "0018_budget_extras"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "default_production_fee_percent", sa.Numeric(7, 4), nullable=True
        ),
    )
    op.add_column(
        "companies",
        sa.Column(
            "default_production_fee_kinds",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "budget_adjustments", sa.Column("role", sa.String(32), nullable=True)
    )
    op.create_index(
        "uq_budget_adjustments_one_production_fee",
        "budget_adjustments",
        ["budget_id"],
        unique=True,
        postgresql_where=sa.text("role = 'production_fee' AND is_active"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_budget_adjustments_one_production_fee", table_name="budget_adjustments"
    )
    op.drop_column("budget_adjustments", "role")
    op.drop_column("companies", "default_production_fee_kinds")
    op.drop_column("companies", "default_production_fee_percent")
