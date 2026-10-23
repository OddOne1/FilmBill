"""Parties, their addresses, contacts, bank accounts and VAT-check history.

Five tables plus two number series, and a backfill for the series.

**The tree is three deep and the database says so where it can.** An
organisation has no parent — that is a CHECK, because it needs no join. A
department's parent must be an organisation and a person's an organisation or
a department; those need the parent's type, so they are the service's with a
readable sentence (`services/party_service.validate_parent`) and a test.

**`party_vat_checks` is append-only**, with the same function-and-two-triggers
shape as `audit_events` (migration 0009): the row is the evidence for a
reverse charge, and evidence that can be edited afterwards is not evidence.
Written out here rather than imported from the model, for migration 0008's
reason — a migration is a record of what happened on a day.

**Customer and supplier numbers** are number series like everything else
(P0b-3), seeded for new companies by `seed_number_series` and backfilled here
for the ones that already exist. `reset: never` and no year token: a customer
number that restarted every January would give two customers the same number.

Revision ID: 0012_parties
Revises: 0011_tax_class_is_active
"""
import uuid as _uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_parties"
down_revision: Union[str, Sequence[str], None] = "0011_tax_class_is_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)
_JSONB = sa.dialects.postgresql.JSONB


def _timestamps() -> list:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _company_column() -> sa.Column:
    return sa.Column(
        "company_id",
        _UUID,
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )


def _party_column() -> sa.Column:
    return sa.Column(
        "party_id",
        _UUID,
        sa.ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "parties",
        sa.Column("id", _UUID, primary_key=True),
        _company_column(),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column(
            "parent_id",
            _UUID,
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("trading_name", sa.String(length=255), nullable=True),
        sa.Column("legal_form", sa.String(length=64), nullable=True),
        sa.Column("register_number", sa.String(length=64), nullable=True),
        sa.Column("register_court", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=64), nullable=True),
        sa.Column("position", sa.String(length=255), nullable=True),
        sa.Column(
            "is_customer", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "is_supplier", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "is_partner", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "tags",
            sa.dialects.postgresql.ARRAY(sa.String(length=64)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("party_number", sa.String(length=32), nullable=True),
        sa.Column("language", sa.String(length=5), nullable=True),
        sa.Column("default_currency", sa.String(length=3), nullable=True),
        sa.Column("payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("payment_terms_text", sa.Text(), nullable=True),
        sa.Column("po_reference", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "business_status",
            sa.String(length=16),
            nullable=False,
            server_default="auto",
        ),
        sa.Column("tax_country", sa.String(length=2), nullable=True),
        sa.Column("vat_id", sa.String(length=32), nullable=True),
        sa.Column("tax_override", _JSONB(), nullable=True),
        sa.Column(
            "einvoice_routing", _JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        *_timestamps(),
        sa.CheckConstraint(
            "type IN ('organisation', 'department', 'person')",
            name="ck_parties_type",
        ),
        sa.CheckConstraint(
            "business_status IN ('auto', 'business', 'private')",
            name="ck_parties_business_status",
        ),
        sa.CheckConstraint(
            "type <> 'organisation' OR parent_id IS NULL",
            name="ck_parties_organisation_has_no_parent",
        ),
        sa.CheckConstraint("id <> parent_id", name="ck_parties_not_its_own_parent"),
    )
    op.create_index("ix_parties_company_id", "parties", ["company_id"])
    op.create_index(
        "uq_parties_company_number",
        "parties",
        ["company_id", "party_number"],
        unique=True,
        postgresql_where=sa.text("party_number IS NOT NULL"),
    )
    op.create_index(
        "ix_parties_company_parent", "parties", ["company_id", "parent_id"]
    )
    op.create_index("ix_parties_company_name", "parties", ["company_id", "name"])

    op.create_table(
        "party_addresses",
        sa.Column("id", _UUID, primary_key=True),
        _company_column(),
        _party_column(),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("street", sa.String(length=255), nullable=True),
        sa.Column("street2", sa.String(length=255), nullable=True),
        sa.Column("zip", sa.String(length=32), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('billing', 'delivery', 'postal')",
            name="ck_party_addresses_kind",
        ),
    )
    op.create_index(
        "ix_party_addresses_company_id", "party_addresses", ["company_id"]
    )
    op.create_index(
        "uq_party_addresses_default_per_kind",
        "party_addresses",
        ["party_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_index(
        "ix_party_addresses_party", "party_addresses", ["party_id", "kind"]
    )

    op.create_table(
        "party_contact_points",
        sa.Column("id", _UUID, primary_key=True),
        _company_column(),
        _party_column(),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column(
            "purpose", sa.String(length=16), nullable=False, server_default="general"
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('email', 'phone')", name="ck_party_contact_points_kind"
        ),
        sa.CheckConstraint(
            "purpose IN ('general', 'invoice')",
            name="ck_party_contact_points_purpose",
        ),
    )
    op.create_index(
        "ix_party_contact_points_company_id", "party_contact_points", ["company_id"]
    )
    op.create_index(
        "uq_party_contact_points_primary",
        "party_contact_points",
        ["party_id", "kind", "purpose"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_index(
        "ix_party_contact_points_party", "party_contact_points", ["party_id"]
    )
    op.create_index(
        "ix_party_contact_points_value",
        "party_contact_points",
        ["company_id", "value"],
    )

    op.create_table(
        "party_bank_accounts",
        sa.Column("id", _UUID, primary_key=True),
        _company_column(),
        _party_column(),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column("iban", sa.String(length=34), nullable=False),
        sa.Column("bic", sa.String(length=11), nullable=True),
        sa.Column("bank_name", sa.String(length=255), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        *_timestamps(),
        sa.UniqueConstraint(
            "party_id", "iban", name="uq_party_bank_accounts_party_iban"
        ),
    )
    op.create_index(
        "ix_party_bank_accounts_company_id", "party_bank_accounts", ["company_id"]
    )
    op.create_index(
        "uq_party_bank_accounts_default",
        "party_bank_accounts",
        ["party_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "party_vat_checks",
        sa.Column("id", _UUID, primary_key=True),
        _company_column(),
        _party_column(),
        sa.Column("vat_id", sa.String(length=32), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response", _JSONB(), nullable=False, server_default="{}"),
        sa.Column("consultation_ref", sa.String(length=64), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "checked_by",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('valid', 'invalid', 'unavailable', 'not_applicable')",
            name="ck_party_vat_checks_status",
        ),
    )
    op.create_index(
        "ix_party_vat_checks_company_id", "party_vat_checks", ["company_id"]
    )
    op.create_index(
        "ix_party_vat_checks_party", "party_vat_checks", ["party_id", "checked_at"]
    )

    # Append-only, exactly like `audit_events`. Its own copy of the DDL —
    # a migration is a record of what happened on a day.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION party_vat_checks_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING
                MESSAGE = 'party_vat_checks is append-only; '
                          || TG_OP || ' is not permitted on it',
                ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER party_vat_checks_no_update_delete
            BEFORE UPDATE OR DELETE ON party_vat_checks
            FOR EACH ROW EXECUTE FUNCTION party_vat_checks_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER party_vat_checks_no_truncate
            BEFORE TRUNCATE ON party_vat_checks
            FOR EACH STATEMENT EXECUTE FUNCTION party_vat_checks_append_only();
        """
    )

    _backfill_party_number_series()


#: `(document_type, prefix, name)`, as they stood on 2026-10-23.
_PARTY_SERIES: tuple[tuple[str, str, str], ...] = (
    ("customer", "K", "Kundennummern"),
    ("supplier", "L", "Lieferantennummern"),
)


def _backfill_party_number_series() -> None:
    """Give every existing company a customer and a supplier series.

    `ON CONFLICT DO NOTHING` against the partial unique index on
    `(company_id, document_type) WHERE is_active`, so a company that somehow
    has one keeps it — the same shape as migration 0008's backfill.
    """
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
        "'never', 1, NULL, 4, true) "
        "ON CONFLICT DO NOTHING"
    )
    for company_id in companies:
        for document_type, prefix, name in _PARTY_SERIES:
            connection.execute(
                insert,
                {
                    "id": _uuid.uuid4(),
                    "company_id": company_id,
                    "document_type": document_type,
                    "name": name,
                    "pattern": f"{prefix}-{{SEQ}}",
                },
            )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS party_vat_checks_no_truncate ON party_vat_checks")
    op.execute(
        "DROP TRIGGER IF EXISTS party_vat_checks_no_update_delete ON party_vat_checks"
    )
    op.execute("DROP FUNCTION IF EXISTS party_vat_checks_append_only()")
    op.drop_table("party_vat_checks")
    op.drop_table("party_bank_accounts")
    op.drop_table("party_contact_points")
    op.drop_table("party_addresses")
    op.drop_table("parties")
    op.execute(
        "DELETE FROM number_series WHERE document_type IN ('customer', 'supplier')"
    )
