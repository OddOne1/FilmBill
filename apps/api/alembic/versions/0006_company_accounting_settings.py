"""Accounting selectors on `companies`. Seven columns, no behaviour.

Every one of them is an ANSWER the company gives once, with its tax advisor,
and nothing in P0b-2 reads any of them: no calculation, no export, no rendered
document. They are stored now so that a company set up today has already
recorded "Ist-Versteuerung, Kleinunternehmer" by the time P6 needs it, rather
than being asked to migrate. SCOPE §10 (D13).

**Defaults are the Austrian ordinary case, not a guess at each company.**
`ear` / `soll` / `invoice_date` / not a Kleinunternehmer is what a Vienna
production company is unless it says otherwise, and every existing row gets
exactly that. The two columns where there IS no sensible default —
`chart_of_accounts_template` and `export_format`, whose real option lists come
from region packs — are NULL, because "nobody has chosen" and "chose the first
one" must stay distinguishable.

`String` rather than a Postgres enum throughout: the vocabularies are fixed by
law in Austria and not everywhere, and an enum type costs a migration per
value while buying nothing the schema's `Literal` does not already give the
API and the generated TypeScript.

Revision ID: 0006_company_accounting_settings
Revises: 0005_companies_and_memberships
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_company_accounting_settings"
down_revision: Union[str, Sequence[str], None] = "0005_companies_and_memberships"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "bookkeeping_mode", sa.String(length=32), nullable=False, server_default="ear"
        ),
    )
    op.add_column(
        "companies",
        sa.Column("vat_timing", sa.String(length=16), nullable=False, server_default="soll"),
    )
    op.add_column(
        "companies",
        sa.Column(
            "kleinunternehmer", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "companies",
        sa.Column("chart_of_accounts_template", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "companies", sa.Column("export_format", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "companies",
        sa.Column(
            "archive_date_basis",
            sa.String(length=32),
            nullable=False,
            server_default="invoice_date",
        ),
    )
    op.add_column(
        "companies",
        sa.Column(
            "month_approval_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "month_approval_enabled")
    op.drop_column("companies", "archive_date_basis")
    op.drop_column("companies", "export_format")
    op.drop_column("companies", "chart_of_accounts_template")
    op.drop_column("companies", "kleinunternehmer")
    op.drop_column("companies", "vat_timing")
    op.drop_column("companies", "bookkeeping_mode")
