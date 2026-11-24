"""Projects, production days, and the Budget sheet.

SCOPE §8.2, §11.3 and §5.3–5.6, slice A2. Seven tables plus one backfill, and
every table company-scoped with `company_id NOT NULL` and an index on it,
because CLAUDE.md rule 6 makes that a property of the table rather than of
each query.

**Four things worth reading before changing this.**

1. **The project number series is backfilled here**, the same way migration
   0008 backfilled the eight document series and for the same reason: a
   company that already exists must be able to number its first project
   without somebody configuring a series first. The literal is written out
   below and NOT imported from `services/number_series.py` — 0008's docstring
   explains both halves of why (alembic's `sys.path` makes the relative
   imports fail, and a migration that imports today's service code changes
   its own meaning the next time that code is refactored).

2. **`project_days` is unique per `(project, date, type)` only among ACTIVE
   rows** — a partial unique index, `WHERE is_active`. A day is a plan, so it
   can be taken out of the plan; nothing is deleted, so the row stays; and a
   plain unique constraint would then make a removed day block its own
   re-creation for ever. The partial index states the rule the schedule
   actually has.

3. **Every foreign key here is RESTRICT**, except `company_id`, which
   cascades like every other company-scoped table. Nothing in this slice is
   ever deleted (there is no DELETE route in `routers/projects.py` or
   `routers/budgets.py`, and a test asserts the absence on the OpenAPI
   document), and RESTRICT is what makes that true at the database level as
   well: a party cannot be taken away from under a project, and a section
   cannot take its lines with it.

4. **No CHECK constraints on the vocabularies.** `projects.status`,
   `project_days.type`, `budget_lines.kind`, `budget_adjustments.base` and
   the rest are `String` columns whose authority is the Python vocabulary —
   migrations 0008, 0013 and 0014 set that precedent, and the reason is the
   same: a database enum or CHECK costs a migration every time a value is
   added, and the API validates against the one list that the engine itself
   reads.

`downgrade` drops the seven tables in dependency order and removes the
backfilled series. It is exercised: CI runs `alembic downgrade base`, and
`test_projects_migration.py` walks this revision up and down against a real
database.

Revision ID: 0015_projects_days_budgets
Revises: 0014_catalog
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_projects_days_budgets"
down_revision: Union[str, Sequence[str], None] = "0014_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)
_JSONB = sa.dialects.postgresql.JSONB

#: The project series, as it is on the day this migration runs. See point 1.
_PROJECT_SERIES = ("project", "PRJ", "Projektnummern", 3)


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


def _is_active() -> sa.Column:
    return sa.Column(
        "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
    )


def upgrade() -> None:
    # ── projects ─────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column("project_number", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "client_party_id",
            _UUID,
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        # planning | active | wrapped | archived. See point 4.
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="planning"
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "company_id", "project_number", name="uq_projects_company_number"
        ),
    )
    op.create_index("ix_projects_company_id", "projects", ["company_id"])
    op.create_index("ix_projects_company_status", "projects", ["company_id", "status"])
    op.create_index(
        "ix_projects_company_client", "projects", ["company_id", "client_party_id"]
    )

    # ── project_days ─────────────────────────────────────────────────
    op.create_table(
        "project_days",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "project_id",
            _UUID,
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        # prep | shoot | travel | wrap | post | office | off (SCOPE §11.3).
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        # Wall-clock times on that day, not instants. A call time has no
        # timezone of its own — it is what the sheet says.
        sa.Column("call_time", sa.Time(), nullable=True),
        sa.Column("wrap_time", sa.Time(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _is_active(),
        *_timestamps(),
    )
    op.create_index("ix_project_days_company_id", "project_days", ["company_id"])
    op.create_index("ix_project_days_project_id", "project_days", ["project_id"])
    op.create_index(
        "ix_project_days_company_date", "project_days", ["company_id", "date"]
    )
    # Point 2: one day per (project, date, type) among the ACTIVE rows.
    op.create_index(
        "uq_project_days_active",
        "project_days",
        ["project_id", "date", "type"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    # ── budgets ──────────────────────────────────────────────────────
    op.create_table(
        "budgets",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "project_id",
            _UUID,
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        _is_active(),
        # NULL means unlocked. A timestamp rather than a boolean because
        # "when was this estimate frozen" is the question somebody asks.
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_budgets_company_id", "budgets", ["company_id"])
    op.create_index("ix_budgets_project_id", "budgets", ["project_id"])
    op.create_index("ix_budgets_company_project", "budgets", ["company_id", "project_id"])

    # ── budget_sections ──────────────────────────────────────────────
    op.create_table(
        "budget_sections",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "budget_id",
            _UUID,
            sa.ForeignKey("budgets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # The user-visible account number ("1100"). Free text until P6
        # brings a real chart of accounts — see the model's docstring.
        sa.Column("code", sa.String(32), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "parent_id",
            _UUID,
            sa.ForeignKey("budget_sections.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        _is_active(),
        *_timestamps(),
    )
    op.create_index("ix_budget_sections_company_id", "budget_sections", ["company_id"])
    op.create_index("ix_budget_sections_budget_id", "budget_sections", ["budget_id"])
    op.create_index(
        "ix_budget_sections_budget_parent",
        "budget_sections",
        ["budget_id", "parent_id"],
    )

    # ── budget_lines ─────────────────────────────────────────────────
    op.create_table(
        "budget_lines",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "section_id",
            _UUID,
            sa.ForeignKey("budget_sections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "item_id",
            _UUID,
            sa.ForeignKey("items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False, server_default="other"),
        # NUMERIC(18,4) — quantities, periods, factors (CLAUDE.md rule 1).
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("quantity_ref", sa.String(64), nullable=True),
        sa.Column("period_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("period_ref", sa.String(64), nullable=True),
        sa.Column("period_unit", sa.String(16), nullable=False, server_default="day"),
        sa.Column("factor", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            "factor_source", sa.String(16), nullable=False, server_default="manual"
        ),
        # NUMERIC(18,2) — money.
        sa.Column("unit_price", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("discount_mode", sa.String(8), nullable=True),
        sa.Column("discount_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("tax_ref", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "tags",
            sa.dialects.postgresql.ARRAY(sa.String(length=64)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "allow_negative",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        _is_active(),
        *_timestamps(),
    )
    op.create_index("ix_budget_lines_company_id", "budget_lines", ["company_id"])
    op.create_index("ix_budget_lines_section_id", "budget_lines", ["section_id"])
    op.create_index(
        "ix_budget_lines_company_item", "budget_lines", ["company_id", "item_id"]
    )

    # ── budget_globals ───────────────────────────────────────────────
    op.create_table(
        "budget_globals",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "budget_id",
            _UUID,
            sa.ForeignKey("budgets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit", sa.String(32), nullable=True),
        # `manual` or `days:<type>`. A closed vocabulary, never a formula.
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        _is_active(),
        *_timestamps(),
        sa.UniqueConstraint("budget_id", "key", name="uq_budget_globals_budget_key"),
    )
    op.create_index("ix_budget_globals_company_id", "budget_globals", ["company_id"])
    op.create_index("ix_budget_globals_budget_id", "budget_globals", ["budget_id"])

    # ── budget_adjustments ───────────────────────────────────────────
    op.create_table(
        "budget_adjustments",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "budget_id",
            _UUID,
            sa.ForeignKey("budgets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # NULL = document level (SCOPE §5.4).
        sa.Column(
            "section_id",
            _UUID,
            sa.ForeignKey("budget_sections.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("base", sa.String(32), nullable=False, server_default="subtotal"),
        sa.Column("kinds", _JSONB(), nullable=False, server_default="[]"),
        _is_active(),
        *_timestamps(),
    )
    op.create_index(
        "ix_budget_adjustments_company_id", "budget_adjustments", ["company_id"]
    )
    op.create_index(
        "ix_budget_adjustments_budget_id", "budget_adjustments", ["budget_id"]
    )
    op.create_index(
        "ix_budget_adjustments_budget_section",
        "budget_adjustments",
        ["budget_id", "section_id"],
    )

    _backfill_project_series()


def _backfill_project_series() -> None:
    """Give every existing company the `project` number series.

    `ON CONFLICT DO NOTHING` against 0008's partial unique index
    (`uq_number_series_active_per_type`), so a company that somehow already
    has an active `project` series keeps it. Safe to re-run, and safe on a
    half-seeded database — the same promise 0008's backfill makes.
    """
    import uuid as _uuid

    connection = op.get_bind()
    companies = [
        row[0]
        for row in connection.execute(sa.text("SELECT id FROM companies")).fetchall()
    ]
    if not companies:
        return

    document_type, prefix, name, padding = _PROJECT_SERIES
    insert = sa.text(
        "INSERT INTO number_series (id, company_id, document_type, name, "
        "pattern, reset, next_value, current_period, padding, is_active) "
        "VALUES (:id, :company_id, :document_type, :name, :pattern, "
        "'yearly', 1, NULL, :padding, true) "
        "ON CONFLICT DO NOTHING"
    )
    for company_id in companies:
        connection.execute(
            insert,
            {
                "id": _uuid.uuid4(),
                "company_id": company_id,
                "document_type": document_type,
                "name": name,
                "pattern": f"{prefix}-{{YYYY}}-{{SEQ}}",
                "padding": padding,
            },
        )


def downgrade() -> None:
    connection = op.get_bind()
    # The allocations first: a project number that was issued points at the
    # series row, and the series row is about to go.
    connection.execute(
        sa.text(
            "DELETE FROM number_allocations WHERE series_id IN "
            "(SELECT id FROM number_series WHERE document_type = :t)"
        ),
        {"t": _PROJECT_SERIES[0]},
    )
    connection.execute(
        sa.text("DELETE FROM number_series WHERE document_type = :t"),
        {"t": _PROJECT_SERIES[0]},
    )

    # Dependency order, innermost first.
    op.drop_table("budget_adjustments")
    op.drop_table("budget_globals")
    op.drop_table("budget_lines")
    op.drop_table("budget_sections")
    op.drop_table("budgets")
    op.drop_table("project_days")
    op.drop_table("projects")
