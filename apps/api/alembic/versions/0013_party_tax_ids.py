"""Tax identifiers become their own table; `parties.vat_id` goes away.

**Why a party cannot have one `vat_id` column.** P1b gave it exactly that,
and three real customers break it: a Swiss company has a UID and no EU VAT
ID, an Australian one an ABN, and an Austrian Kleinunternehmer is a business
with no number at all. One untyped column cannot say which of those it is
holding, so nothing downstream could decide what the number was good for —
and that is the only question anybody asks of it. `party_tax_ids.kind`
answers it (SCOPE D59).

**One source of truth, so the column is dropped in the same migration** that
creates the table and copies the data across. Keeping both for a release
would mean two places to write and a window in which they disagree; the
prompt is explicit that no real data exists yet, so this is the cheap moment
to do it properly. The copy is still written out in full, because the next
installation to run this may well have rows.

**The existing `party_vat_checks` rows are repointed**, not orphaned: each
one is the evidence for an invoice, and a check that no longer says which
identifier it was about is not evidence. The table also gains the columns
P1b-2 needs and keeps its append-only triggers untouched — they are not
re-created here, which is deliberate: `ALTER TABLE ... ADD COLUMN` does not
disturb a row-level trigger, and a test asserts the triggers still hold
after this migration rather than trusting that sentence.

Revision ID: 0013_party_tax_ids
Revises: 0012_parties
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_party_tax_ids"
down_revision: Union[str, Sequence[str], None] = "0012_parties"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)
_JSONB = sa.dialects.postgresql.JSONB

#: The kinds, repeated here rather than imported from the model. Migration
#: 0008's precedent and the reason rule 9 exists: a migration is a record of
#: what the schema looked like on a day, and importing today's constants
#: would make last year's migration change meaning when the model does.
_KINDS = (
    "vat",
    "uid_ch",
    "abn",
    "ein",
    "tax_number",
    "company_register",
    "other",
)
_VAT_LIKE = ("vat", "uid_ch", "abn")


def upgrade() -> None:
    # ── 1. The new table ─────────────────────────────────────────────
    op.create_table(
        "party_tax_ids",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "company_id",
            _UUID,
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "party_id",
            _UUID,
            sa.ForeignKey("parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("value_as_entered", sa.String(80), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "replaces_id",
            _UUID,
            sa.ForeignKey("party_tax_ids.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "initial_checks_done",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "unavailable_streak",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "needs_attention",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("needs_attention_reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "kind IN ('vat', 'uid_ch', 'abn', 'ein', 'tax_number', "
            "'company_register', 'other')",
            name="ck_party_tax_ids_kind",
        ),
        sa.CheckConstraint("value <> ''", name="ck_party_tax_ids_value_not_empty"),
        sa.CheckConstraint(
            "initial_checks_done >= 0 AND unavailable_streak >= 0",
            name="ck_party_tax_ids_counters_not_negative",
        ),
    )
    # The `CompanyScoped` mixin declares this one; every scoped table has it
    # (rule 6 — every query is company-filtered, so the filter needs an index).
    op.create_index("ix_party_tax_ids_company_id", "party_tax_ids", ["company_id"])
    op.create_index(
        "uq_party_tax_ids_party_kind_value",
        "party_tax_ids",
        ["party_id", "kind", "value"],
        unique=True,
    )
    # At most one primary ACTIVE VAT-like identifier per party: the engine
    # takes one buyer VAT ID, and which of two reached it would depend on row
    # order — a wrong invoice nobody could explain afterwards.
    op.create_index(
        "uq_party_tax_ids_primary_vat_like",
        "party_tax_ids",
        ["party_id"],
        unique=True,
        postgresql_where=sa.text(
            "is_primary AND is_active AND kind IN ('vat', 'uid_ch', 'abn')"
        ),
    )
    op.create_index(
        "ix_party_tax_ids_due",
        "party_tax_ids",
        ["next_check_at"],
        postgresql_where=sa.text("next_check_at IS NOT NULL AND is_active"),
    )
    op.create_index(
        "ix_party_tax_ids_party", "party_tax_ids", ["party_id", "kind"]
    )
    op.create_index(
        "ix_party_tax_ids_attention",
        "party_tax_ids",
        ["company_id"],
        postgresql_where=sa.text("needs_attention"),
    )

    # ── 2. The party's new columns ───────────────────────────────────
    op.add_column(
        "parties", sa.Column("tax_status_note", sa.Text(), nullable=True)
    )
    op.add_column(
        "parties",
        sa.Column("recheck_interval_days", sa.Integer(), nullable=True),
    )

    # ── 3. The check table's new columns ─────────────────────────────
    # Added, never re-created: the append-only triggers from 0012 stay in
    # place through an ADD COLUMN, and a test asserts they still hold rather
    # than this comment being the only evidence.
    op.add_column(
        "party_vat_checks",
        sa.Column(
            "tax_id_id",
            _UUID,
            sa.ForeignKey("party_tax_ids.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "party_vat_checks",
        sa.Column("kind", sa.String(24), nullable=False, server_default="vat"),
    )
    op.add_column(
        "party_vat_checks",
        sa.Column(
            "provider", sa.String(24), nullable=False, server_default="vies"
        ),
    )
    op.add_column(
        "party_vat_checks",
        sa.Column(
            "trigger_kind",
            sa.String(16),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "party_vat_checks",
        sa.Column(
            "qualified", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "party_vat_checks", sa.Column("registered_name", sa.Text(), nullable=True)
    )
    op.add_column(
        "party_vat_checks",
        sa.Column("registered_address", sa.Text(), nullable=True),
    )
    op.add_column(
        "party_vat_checks", sa.Column("name_match", sa.String(16), nullable=True)
    )
    op.add_column(
        "party_vat_checks",
        sa.Column("address_match", sa.String(16), nullable=True),
    )
    op.add_column(
        "party_vat_checks",
        sa.Column("registration_active", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "party_vat_checks", sa.Column("gst_registered", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "party_vat_checks", sa.Column("manual_note", sa.Text(), nullable=True)
    )
    op.add_column(
        "party_vat_checks",
        sa.Column("evidence_reference", sa.String(255), nullable=True),
    )
    op.add_column(
        "party_vat_checks",
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "party_vat_checks", sa.Column("change_detected", _JSONB, nullable=True)
    )
    # `vat_id` widens from 32 to 64 and `country` becomes nullable: a Swiss
    # UID with its suffix is longer than any EU VAT ID, and a free-text kind
    # has no country at all.
    op.alter_column(
        "party_vat_checks",
        "vat_id",
        type_=sa.String(64),
        existing_type=sa.String(32),
    )
    op.alter_column(
        "party_vat_checks", "country", existing_type=sa.String(2), nullable=True
    )

    # ── 4. Copy the data across, then drop the column ────────────────
    # A CH-shaped value under a CH party becomes a `uid_ch`; everything else
    # a `vat`. Written as SQL rather than through the ORM for migration
    # 0008's reason: this must keep working when the model has moved on.
    op.execute(
        """
        INSERT INTO party_tax_ids (
            id, company_id, party_id, kind, country, value,
            value_as_entered, is_primary, is_active,
            initial_checks_done, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            p.company_id,
            p.id,
            CASE
                WHEN p.vat_id ~ '^(CHE|ADM)[0-9]{9}$' THEN 'uid_ch'
                ELSE 'vat'
            END,
            CASE
                WHEN p.vat_id ~ '^(CHE|ADM)[0-9]{9}$' THEN 'CH'
                WHEN p.vat_id ~ '^[A-Z]{2}' THEN substring(p.vat_id, 1, 2)
                ELSE p.tax_country
            END,
            p.vat_id,
            p.vat_id,
            true,
            true,
            0,
            p.created_at,
            p.updated_at
        FROM parties p
        WHERE p.vat_id IS NOT NULL AND p.vat_id <> ''
        """
    )
    # Repoint the existing checks at the identifier they were about. Matched
    # on the party AND the value, because a check carries the number it
    # checked and that is precisely what makes it evidence.
    op.execute(
        """
        UPDATE party_vat_checks c
        SET tax_id_id = t.id,
            kind = t.kind
        FROM party_tax_ids t
        WHERE t.party_id = c.party_id AND t.value = c.vat_id
        """
    )
    op.drop_column("parties", "vat_id")

    # ── 5. The company's check policy ────────────────────────────────
    op.add_column(
        "companies",
        sa.Column(
            "tax_id_check_policy", _JSONB, nullable=False, server_default="{}"
        ),
    )

    # ── 6. New constraints on the check table ────────────────────────
    # Added after the data is in place, so the copy above cannot trip them.
    op.drop_constraint(
        "ck_party_vat_checks_status", "party_vat_checks", type_="check"
    )
    op.create_check_constraint(
        "ck_party_vat_checks_status",
        "party_vat_checks",
        "status IN ('valid', 'invalid', 'unavailable', 'not_applicable', "
        "'not_configured', 'not_checkable')",
    )
    op.create_check_constraint(
        "ck_party_vat_checks_trigger_kind",
        "party_vat_checks",
        "trigger_kind IN ('manual', 'initial', 'scheduled', 'retry', "
        "'on_change')",
    )
    op.create_check_constraint(
        "ck_party_vat_checks_name_match",
        "party_vat_checks",
        "name_match IS NULL OR name_match IN ('match', 'partial', "
        "'mismatch', 'unknown')",
    )
    op.create_check_constraint(
        "ck_party_vat_checks_address_match",
        "party_vat_checks",
        "address_match IS NULL OR address_match IN ('match', 'differs', "
        "'unknown')",
    )
    # The claim "a tax authority confirmed this" is the one nobody may make
    # by accident, so the database refuses it rather than only the service.
    op.create_check_constraint(
        "ck_party_vat_checks_manual_is_not_qualified",
        "party_vat_checks",
        "provider <> 'manual' OR qualified = false",
    )
    op.create_index(
        "ix_party_vat_checks_tax_id",
        "party_vat_checks",
        ["tax_id_id", "checked_at"],
    )


def downgrade() -> None:
    # Puts the primary VAT-like value back in the column before dropping the
    # table, so a downgrade loses the typing and the history but not the
    # number itself — which is the one thing a user would notice.
    op.add_column("parties", sa.Column("vat_id", sa.String(32), nullable=True))
    op.execute(
        """
        UPDATE parties p
        SET vat_id = t.value
        FROM party_tax_ids t
        WHERE t.party_id = p.id
          AND t.is_active
          AND t.kind IN ('vat', 'uid_ch', 'abn')
          AND t.is_primary
          AND length(t.value) <= 32
        """
    )

    op.drop_index("ix_party_vat_checks_tax_id", table_name="party_vat_checks")
    for name in (
        "ck_party_vat_checks_manual_is_not_qualified",
        "ck_party_vat_checks_address_match",
        "ck_party_vat_checks_name_match",
        "ck_party_vat_checks_trigger_kind",
    ):
        op.drop_constraint(name, "party_vat_checks", type_="check")
    op.drop_constraint(
        "ck_party_vat_checks_status", "party_vat_checks", type_="check"
    )
    # Rows carrying a status the old constraint forbids would make the
    # constraint un-creatable, so they are folded into the nearest old
    # meaning first. `not_configured` and `not_checkable` both mean "no
    # answer was obtained", which is what `unavailable` meant.
    op.execute(
        "UPDATE party_vat_checks SET status = 'unavailable' "
        "WHERE status IN ('not_configured', 'not_checkable')"
    )
    op.create_check_constraint(
        "ck_party_vat_checks_status",
        "party_vat_checks",
        "status IN ('valid', 'invalid', 'unavailable', 'not_applicable')",
    )

    op.execute("UPDATE party_vat_checks SET country = 'XX' WHERE country IS NULL")
    op.alter_column(
        "party_vat_checks", "country", existing_type=sa.String(2), nullable=False
    )
    op.execute("UPDATE party_vat_checks SET vat_id = left(vat_id, 32)")
    op.alter_column(
        "party_vat_checks",
        "vat_id",
        type_=sa.String(32),
        existing_type=sa.String(64),
    )
    for column in (
        "change_detected",
        "valid_until",
        "evidence_reference",
        "manual_note",
        "gst_registered",
        "registration_active",
        "address_match",
        "name_match",
        "registered_address",
        "registered_name",
        "qualified",
        "trigger_kind",
        "provider",
        "kind",
        "tax_id_id",
    ):
        op.drop_column("party_vat_checks", column)

    op.drop_column("companies", "tax_id_check_policy")
    op.drop_column("parties", "recheck_interval_days")
    op.drop_column("parties", "tax_status_note")

    for name in (
        "ix_party_tax_ids_attention",
        "ix_party_tax_ids_party",
        "ix_party_tax_ids_due",
        "uq_party_tax_ids_primary_vat_like",
        "uq_party_tax_ids_party_kind_value",
        "ix_party_tax_ids_company_id",
    ):
        op.drop_index(name, table_name="party_tax_ids")
    op.drop_table("party_tax_ids")
