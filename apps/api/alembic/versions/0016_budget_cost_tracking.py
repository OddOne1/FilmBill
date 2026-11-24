"""Budget cost tracking: commitments, actuals, and a line's ETC.

SCOPE §8.2 and D72, slice B1. Two tables and one column, every table
company-scoped with `company_id NOT NULL` and an index on it, because
CLAUDE.md rule 6 makes that a property of the table rather than of each
query.

**Four things worth reading before changing this.**

1. **`section_id` is NOT NULL and `line_id` is nullable.** Every cost belongs
   somewhere in the account structure even when it matches no budget row — a
   taxi nobody budgeted is still Travel — and a cost with no section at all
   could only be printed at the bottom of the top sheet under "other". That
   the line belongs to that section is enforced in the service and not by a
   constraint: it is a two-table condition, and a trigger would be a second
   place for the rule to live.

2. **Amounts are `NUMERIC(18,2)`, net, in the company currency, and the only
   CHECK constraints in this migration are `amount > 0`.** A correction is a
   new entry plus a void, never a negative amount — see the model docstring
   — and that is the one invariant cheap enough to state in the schema and
   expensive enough to be worth stating twice. The vocabularies are not
   checked here, for migration 0015's reason: `budget_commitments.status` is
   a `String(16)` whose authority is the Python vocabulary in
   `billing/budget/tracking.py`, and a CHECK would cost a migration every
   time a state was added.

3. **Every foreign key is RESTRICT**, except `company_id`, which cascades
   like every other company-scoped table. Nothing in this slice is ever
   deleted — entries are voided, and `test_budget_tracking_api.py` asserts
   the absence of a DELETE route on the OpenAPI document — and RESTRICT is
   what makes that true at the database level too: a commitment cannot be
   taken away from under the actuals that settle it, and a line cannot take
   the costs booked against it with it.

4. **`budget_lines.etc` is nullable on purpose**, and the nullability is the
   feature. `NULL` means "use the default forecasting rule"; `0.00` means
   "this line is finished". `billing/budget/tracking.py` distinguishes the
   two, and a `NOT NULL DEFAULT 0` would quietly declare every line in every
   existing budget complete.

`downgrade` drops the two tables and the column. It is exercised: CI runs
`alembic downgrade base`, and `test_budget_tracking_migration.py` walks this
revision up and down against a real database.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_budget_cost_tracking"
down_revision: Union[str, Sequence[str], None] = "0015_projects_days_budgets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)


def _company_fk(name: str = "company_id") -> sa.Column:
    return sa.Column(
        name,
        _UUID,
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _entry_columns() -> list[sa.Column]:
    """What a commitment and an actual have in common.

    The same split the `BudgetEntry` mixin makes in `models/project.py`, and
    written once here for the same reason: two copies of "which budget,
    which section, which line, who, how much" is two places for the
    nullability of `line_id` to be decided differently.
    """
    return [
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "budget_id",
            _UUID,
            sa.ForeignKey("budgets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Required: every cost has a place in the account structure.
        sa.Column(
            "section_id",
            _UUID,
            sa.ForeignKey("budget_sections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Nullable: an unbudgeted cost matches no row.
        sa.Column(
            "line_id",
            _UUID,
            sa.ForeignKey("budget_lines.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "party_id",
            _UUID,
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("payee_text", sa.String(255), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("reference", sa.String(128), nullable=True),
        # NUMERIC(18,2) — money (CLAUDE.md rule 1). Net, company currency.
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.String(500), nullable=True),
        *_timestamps(),
    ]


def upgrade() -> None:
    # ── budget_commitments ───────────────────────────────────────────
    op.create_table(
        "budget_commitments",
        *_entry_columns(),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="open"
        ),
        sa.Column("commitment_date", sa.Date(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_budget_commitments_amount_positive"),
    )
    op.create_index(
        "ix_budget_commitments_company_id", "budget_commitments", ["company_id"]
    )
    op.create_index(
        "ix_budget_commitments_budget_id", "budget_commitments", ["budget_id"]
    )
    op.create_index(
        "ix_budget_commitments_section_id", "budget_commitments", ["section_id"]
    )
    op.create_index(
        "ix_budget_commitments_line_id", "budget_commitments", ["line_id"]
    )
    op.create_index(
        "ix_budget_commitments_budget_section",
        "budget_commitments",
        ["budget_id", "section_id"],
    )
    op.create_index(
        "ix_budget_commitments_company_party",
        "budget_commitments",
        ["company_id", "party_id"],
    )
    op.create_index(
        "ix_budget_commitments_budget_date",
        "budget_commitments",
        ["budget_id", "commitment_date"],
    )

    # ── budget_actuals ───────────────────────────────────────────────
    op.create_table(
        "budget_actuals",
        *_entry_columns(),
        # RESTRICT, like every other key here: a commitment cannot be taken
        # away from under the invoices that settle it.
        sa.Column(
            "commitment_id",
            _UUID,
            sa.ForeignKey("budget_commitments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("incurred_on", sa.Date(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_budget_actuals_amount_positive"),
    )
    op.create_index(
        "ix_budget_actuals_company_id", "budget_actuals", ["company_id"]
    )
    op.create_index("ix_budget_actuals_budget_id", "budget_actuals", ["budget_id"])
    op.create_index("ix_budget_actuals_section_id", "budget_actuals", ["section_id"])
    op.create_index("ix_budget_actuals_line_id", "budget_actuals", ["line_id"])
    op.create_index(
        "ix_budget_actuals_commitment_id", "budget_actuals", ["commitment_id"]
    )
    op.create_index(
        "ix_budget_actuals_budget_section",
        "budget_actuals",
        ["budget_id", "section_id"],
    )
    op.create_index(
        "ix_budget_actuals_company_party",
        "budget_actuals",
        ["company_id", "party_id"],
    )
    op.create_index(
        "ix_budget_actuals_budget_incurred",
        "budget_actuals",
        ["budget_id", "incurred_on"],
    )

    # ── budget_lines.etc ─────────────────────────────────────────────
    #
    # Nullable with NO server default. See point 4 of the docstring: NULL is
    # "use the default rule" and 0.00 is "this line is finished", and a
    # default of 0 would declare every existing line complete.
    op.add_column(
        "budget_lines",
        sa.Column("etc", sa.Numeric(precision=18, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("budget_lines", "etc")
    # Actuals first: they point at commitments.
    op.drop_table("budget_actuals")
    op.drop_table("budget_commitments")
