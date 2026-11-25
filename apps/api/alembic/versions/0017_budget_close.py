"""Closing a budget: when, by whom, and the note that approved it.

SCOPE D89–D91 (B1.1 part 3). Three nullable columns on `budgets`. A budget is
closed when `closed_at` is set; closing needs it locked first, and a closed
budget refuses cost entries, still-to-spend and finishing lines until it is
reopened. Those are service rules, not constraints: "closed implies locked"
is enforced where the refusal sentence is written, and a CHECK would be a
second place for the rule to live (migration 0015's reasoning).

`closed_by` is `SET NULL`, like `audit_events.user_id`: a user account can be
removed, and the budget's closed state must not go with it — the audit event
`budget.closed` keeps who it was in the log anyway.

What the person approved when closing over discrepancies (the check's
snapshot) lives in the audit event, not here: it is a record of one moment,
and a column would be overwritten by the next close.

`downgrade` drops the three columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_budget_close"
down_revision: Union[str, Sequence[str], None] = "0016_budget_cost_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "budgets", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "budgets",
        sa.Column(
            "closed_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_budgets_closed_by_users"
            ),
            nullable=True,
        ),
    )
    op.add_column("budgets", sa.Column("close_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("budgets", "close_note")
    op.drop_constraint("fk_budgets_closed_by_users", "budgets", type_="foreignkey")
    op.drop_column("budgets", "closed_by")
    op.drop_column("budgets", "closed_at")
