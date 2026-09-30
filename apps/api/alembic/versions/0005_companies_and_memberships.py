"""Companies, their bank accounts, and per-company memberships.

Three new tables and one new enum type. Nothing existing is altered, no data
is rewritten, and no session is invalidated by the migration itself — an
install that upgrades into this keeps working exactly as it did, with zero
companies, until somebody creates one.

**The install with no company.** `routers/setup.py` creates the first company
for a *fresh* install, but this migration deliberately creates none. An
existing install has a superadmin already and will never run setup again, so
a migration that invented a company would be guessing its legal name, its
country and its currency — every one of which ends up printed on an invoice.
`POST /companies` exists for exactly this case, and the web app offers it when
the switcher comes back empty.

**Why the enum is created explicitly.** `sa.Enum(...)` inside a `create_table`
emits a CREATE TYPE the first time and nothing on a second table using the
same type, which is fine here (only one table uses it) but breaks confusingly
the moment a second one does. Creating it by name up front, and dropping it by
name in `downgrade`, keeps the type's lifetime visible rather than implicit in
whichever table happens to mention it first.

**`require_2fa_roles` is JSONB with a `'[]'` default, NOT NULL.** A NULL there
would mean "no roles require 2FA" and "nobody has said" at the same time, and
every reader would have to spell `or []`. One of them would forget.

Revision ID: 0005_companies_and_memberships
Revises: 0004_account_security_gate
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_companies_and_memberships"
down_revision: Union[str, Sequence[str], None] = "0004_account_security_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Kept as a module constant so upgrade and downgrade cannot disagree about
#: the name, and so the value list is in one place next to the model's enum.
COMPANY_ROLE = postgresql.ENUM(
    "owner",
    "admin",
    "accountant",
    "producer",
    "staff",
    "tax_advisor",
    name="companyrole",
    create_type=False,
)


def upgrade() -> None:
    COMPANY_ROLE.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("trading_name", sa.String(length=255), nullable=True),
        sa.Column("legal_form", sa.String(length=64), nullable=True),
        sa.Column("register_number", sa.String(length=64), nullable=True),
        sa.Column("register_court", sa.String(length=255), nullable=True),
        sa.Column("vat_id", sa.String(length=32), nullable=True),
        sa.Column("tax_number", sa.String(length=64), nullable=True),
        sa.Column("address_street", sa.String(length=255), nullable=True),
        sa.Column("address_zip", sa.String(length=32), nullable=True),
        sa.Column("address_city", sa.String(length=255), nullable=True),
        sa.Column("address_country", sa.String(length=2), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column(
            "default_currency", sa.String(length=3), nullable=False, server_default="EUR"
        ),
        sa.Column(
            "default_language", sa.String(length=5), nullable=False, server_default="de"
        ),
        sa.Column(
            "fiscal_year_start_month", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("logo_s3_key", sa.String(length=512), nullable=True),
        sa.Column(
            "require_2fa_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "tax_advisor_reports", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "company_bank_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("iban", sa.String(length=64), nullable=False),
        sa.Column("bic", sa.String(length=16), nullable=True),
        sa.Column("bank_name", sa.String(length=255), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_company_bank_accounts_company_id", "company_bank_accounts", ["company_id"]
    )

    op.create_table(
        "company_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", COMPANY_ROLE, nullable=False),
        # SET NULL, not CASCADE: deleting the person who granted a membership
        # must not delete the membership. Who has access is a fact about the
        # grantee.
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_company_memberships_company_id", "company_memberships", ["company_id"]
    )
    # The index the login path leans on — see the model's __table_args__.
    op.create_index(
        "ix_company_memberships_user_revoked",
        "company_memberships",
        ["user_id", "revoked_at"],
    )
    op.create_index(
        "ix_company_memberships_company_revoked",
        "company_memberships",
        ["company_id", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_company_memberships_company_revoked", "company_memberships")
    op.drop_index("ix_company_memberships_user_revoked", "company_memberships")
    op.drop_index("ix_company_memberships_company_id", "company_memberships")
    op.drop_table("company_memberships")

    op.drop_index("ix_company_bank_accounts_company_id", "company_bank_accounts")
    op.drop_table("company_bank_accounts")

    op.drop_table("companies")

    # After the tables, never before: Postgres refuses to drop a type a
    # column still uses, and the failure arrives as a confusing dependency
    # error rather than as "you got the order wrong".
    COMPANY_ROLE.drop(op.get_bind(), checkfirst=True)
