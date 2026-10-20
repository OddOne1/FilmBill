"""Number series and the record of every number they issue.

Two tables plus a backfill: every company that already exists gets the eight
default series, so no company is left unable to number its first invoice.

**The eight defaults are written out HERE, not imported from
`services/number_series.py`.** The first version imported `seed_rows` from
the service, on the reasoning that two literals are two answers to "how is an
invoice numbered". That was wrong twice over. Mechanically, alembic runs with
`apps/api` on `sys.path` rather than the repository root, so the service's
relative imports fail with "attempted relative import beyond top-level
package" — three migration tests said so. And in principle: a migration is a
historical record of what happened to a database on a particular day, and one
that imports today's service code changes its own meaning every time that
code is refactored. If `DEFAULT_SERIES` gains a ninth document type next
year, companies created after that get nine and companies backfilled by this
migration keep the eight they were given, which is correct.

What keeps the two honest is a test of the EFFECT rather than of the text:
`test_number_series.py` asserts a newly created company gets
`DEFAULT_SERIES`, and `test_the_migration_backfills_existing_companies`
asserts this migration gives an existing one the eight listed below.

**Why `number_allocations` exists at all.** A counter can answer "what is
next" and nothing else. "Has this number been issued", "what did 2025 reach"
and "is lowering this counter safe" are all questions the law asks and a
counter cannot answer. So every number issued is a row, and the two unique
constraints on it are what make CLAUDE.md rule 5's "gap-free, issued once"
checkable rather than aspirational.

**One ACTIVE series per company and document type**, as a PARTIAL unique
index. A plain unique index on the pair would make it impossible to keep a
superseded series, and a superseded series is the only explanation of the
numbers it issued — so they are deactivated, never deleted.

Revision ID: 0008_number_series
Revises: 0007_bank_account_iban_unique
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_number_series"
down_revision: Union[str, Sequence[str], None] = "0007_bank_account_iban_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "number_series",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("pattern", sa.String(length=64), nullable=False),
        sa.Column(
            "reset", sa.String(length=16), nullable=False, server_default="yearly"
        ),
        sa.Column("next_value", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_period", sa.String(length=8), nullable=True),
        sa.Column("padding", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_number_series_company_id", "number_series", ["company_id"]
    )
    op.create_index(
        "uq_number_series_active_per_type",
        "number_series",
        ["company_id", "document_type"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "number_allocations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "series_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("number_series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=64), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column(
            "allocated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("series_id", "number", name="uq_number_allocations_number"),
        sa.UniqueConstraint(
            "series_id", "period", "value", name="uq_number_allocations_value"
        ),
    )
    op.create_index(
        "ix_number_allocations_company_id", "number_allocations", ["company_id"]
    )
    op.create_index(
        "ix_number_allocations_series_period",
        "number_allocations",
        ["series_id", "period"],
    )
    op.create_index(
        "ix_number_allocations_series_document_date",
        "number_allocations",
        ["series_id", "document_date"],
    )

    _backfill_existing_companies()


#: The eight defaults, frozen as of this revision. See the module docstring
#: for why they are a literal here and not an import.
#:
#: (document_type, prefix, name). All eight start `yearly` with a `{YYYY}`
#: token, padding 4, which is what an Austrian production company's
#: accountant expects to see — and all eight are user-editable the moment
#: they exist.
_DEFAULTS: tuple[tuple[str, str, str], ...] = (
    ("quote", "AN", "Angebote"),
    ("order_confirmation", "AB", "Auftragsbestätigungen"),
    ("delivery_note", "LS", "Lieferscheine"),
    ("invoice", "AR", "Ausgangsrechnungen"),
    ("credit_note", "KR", "Rechnungskorrekturen"),
    ("self_billing", "GS", "Gutschriften (Self-Billing)"),
    ("dunning", "MA", "Mahnungen"),
    ("letter", "BR", "Briefe"),
)


def _backfill_existing_companies() -> None:
    """Give every existing company the eight default series.

    `ON CONFLICT DO NOTHING` against the partial unique index, so a company
    that somehow already has an active series for a document type keeps it
    and this adds only what is missing. That makes the backfill safe to
    re-run and safe on a half-seeded database.
    """
    import uuid as _uuid

    connection = op.get_bind()
    companies = [
        row[0]
        for row in connection.execute(sa.text("SELECT id FROM companies")).fetchall()
    ]
    if not companies:
        return

    insert = sa.text(
        "INSERT INTO number_series (id, company_id, document_type, name, "
        "pattern, reset, next_value, current_period, padding, is_active) "
        "VALUES (:id, :company_id, :document_type, :name, :pattern, "
        "'yearly', 1, NULL, 4, true) "
        "ON CONFLICT DO NOTHING"
    )
    for company_id in companies:
        for document_type, prefix, name in _DEFAULTS:
            connection.execute(
                insert,
                {
                    "id": _uuid.uuid4(),
                    "company_id": company_id,
                    "document_type": document_type,
                    "name": name,
                    "pattern": f"{prefix}-{{YYYY}}-{{SEQ}}",
                },
            )


def downgrade() -> None:
    op.drop_table("number_allocations")
    op.drop_index("uq_number_series_active_per_type", table_name="number_series")
    op.drop_index("ix_number_series_company_id", table_name="number_series")
    op.drop_table("number_series")
