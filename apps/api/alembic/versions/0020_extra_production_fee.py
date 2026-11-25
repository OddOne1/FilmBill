"""The production fee on an extra is chosen per extra (B1.2 part 2b).

Mathias's decision of 2026-11-25, replacing D92's "the fee applies to
extras": each extra carries an "add production fee" choice, ticked by
default; unticked, the extra is billed at its own price with no fee.

* `budget_lines.apply_production_fee` — `true` for every existing line, so
  every accepted extra keeps the fee it already carried (D92) and nothing on
  any budget moves. Meaningful only for lines in the Extras section: an
  ordinary line always carries the fee the budget's fee adjustment says.

How the engine skips one line is NOT a second calculation path: a line may
list adjustment roles it is exempt from, and the budget maps an unticked
extra to `exempt_from = ["production_fee"]` — see `billing/calc/model.py`.

`downgrade` drops the column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_extra_production_fee"
down_revision: Union[str, Sequence[str], None] = "0019_production_fee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "budget_lines",
        sa.Column(
            "apply_production_fee",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("budget_lines", "apply_production_fee")
