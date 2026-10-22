"""Tax codes, classes and rules, with every existing company seeded.

Three tables plus a backfill: a company with no tax configuration cannot be
asked what tax a sale carries, and P2's first invoice needs an answer.

**The seed data is written out HERE, not imported from the region packs.**
Same two reasons as migration 0008's `_DEFAULTS`, and they both still hold.
Mechanically, alembic runs with `apps/api` on `sys.path` rather than the
repository root, so a relative import out of this file fails. And in
principle: a migration is a record of what happened to a database on a
particular day. One that reads today's YAML changes its own meaning every
time somebody corrects a rate — so the day the Steuerberater tells us the
Austrian reduced rate is not 10 %, companies seeded by this migration keep
what they were given on 2026-10-22 and new ones get the corrected pack, which
is the honest outcome. Fixing the old ones is then a visible act with its own
migration, not a silent reinterpretation of this one.

What keeps the two honest is a test of the EFFECT, not of the text:
`test_region_packs.py` asserts the YAML packs contain these codes and rates,
and `test_tax_seeding.py::test_the_migration_backfills_existing_companies`
asserts this migration gives an existing company the same ones.

**Which pack a company gets** is decided by `companies.address_country`: `AT`
→ the Austrian pack, `DE` → the German one, anything else → `eu_generic`
alone, whose standard rate is 0 % and INACTIVE so that nothing can propose an
invented rate. A company with no country at all is **skipped** here and seeded
when its country is first set — seeding it with a generic pack would mean the
eventual "AT" never arrived, because seeding never overwrites.

Revision ID: 0010_tax_codes_classes_rules
Revises: 0009_audit_events
"""
import json
import uuid as _uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_tax_codes_classes_rules"
down_revision: Union[str, Sequence[str], None] = "0009_audit_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tax_codes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("category", sa.String(length=2), nullable=False),
        sa.Column(
            "legal_notes",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "notes_reviewed", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "reporting_mapping",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("revenue_account", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("origin_pack", sa.String(length=32), nullable=True),
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
        sa.UniqueConstraint("company_id", "code", name="uq_tax_codes_company_code"),
        sa.CheckConstraint(
            "category IN ('S','Z','E','AE','K','G','O','L','M')",
            name="ck_tax_codes_category",
        ),
        sa.CheckConstraint("rate >= 0", name="ck_tax_codes_rate_not_negative"),
        sa.CheckConstraint(
            "category NOT IN ('Z','E','AE','K','G','O') OR rate = 0",
            name="ck_tax_codes_zero_rate_categories",
        ),
    )
    op.create_index("ix_tax_codes_company_id", "tax_codes", ["company_id"])

    op.create_table(
        "tax_classes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column(
            "code_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_codes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("origin_pack", sa.String(length=32), nullable=True),
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
        sa.UniqueConstraint("company_id", "key", name="uq_tax_classes_company_key"),
    )
    op.create_index("ix_tax_classes_company_id", "tax_classes", ["company_id"])

    op.create_table(
        "tax_rules",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "conditions",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("outcome", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("origin_pack", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
    )
    op.create_index("ix_tax_rules_company_id", "tax_rules", ["company_id"])
    op.create_index(
        "ix_tax_rules_company_position", "tax_rules", ["company_id", "position"]
    )

    _backfill_existing_companies()


# ─── The seed, as it stood on 2026-10-22 ────────────────────────────────────
#
# `(code, name, rate, category, notes, is_active)`. Notes are `{lang: text}`
# and ship generic — no statute numbers, because a statute number nobody
# qualified has checked is worse on an invoice than no sentence at all. Every
# code lands with `notes_reviewed = false`.

_NOTE_AE = {
    "en": "Reverse charge: VAT to be accounted for by the recipient.",
    "de": "Steuerschuld geht auf den Leistungsempfänger über.",
}
_NOTE_K = {
    "en": "Intra-community supply, exempt from VAT.",
    "de": "Innergemeinschaftliche Lieferung, steuerfrei.",
}
_NOTE_G = {
    "en": "Export delivery, exempt from VAT.",
    "de": "Ausfuhrlieferung, steuerfrei.",
}
_NOTE_O = {
    "en": "Not taxable in the country of the supplier; the place of supply is abroad.",
    "de": "Nicht im Inland steuerbar; der Leistungsort liegt im Ausland.",
}
_NOTE_KU = {
    "en": "Small business exemption: no VAT is charged.",
    "de": "Steuerfrei aufgrund der Kleinunternehmerregelung.",
}
_NOTE_E = {"en": "Exempt from VAT.", "de": "Steuerfrei."}

_EU_GENERIC_CODES: tuple[tuple, ...] = (
    ("EU-STD", "Standard rate (set your rate)", "0", "S", {}, False),
    ("EU-AE", "Reverse charge", "0", "AE", _NOTE_AE, True),
    ("EU-K", "Intra-community supply", "0", "K", _NOTE_K, True),
    ("EU-G", "Export", "0", "G", _NOTE_G, True),
    ("EU-O", "Not taxable in this country", "0", "O", _NOTE_O, True),
    ("EU-E", "Exempt", "0", "E", _NOTE_E, True),
    ("EU-Z", "Zero rate", "0", "Z", {}, True),
    ("EU-KU", "Small business exemption", "0", "E", _NOTE_KU, True),
)

_AT_CODES: tuple[tuple, ...] = (
    ("AT-20", "20 % USt (Normalsteuersatz)", "20", "S", {}, True),
    ("AT-13", "13 % USt (ermäßigt)", "13", "S", {}, True),
    ("AT-10", "10 % USt (ermäßigt)", "10", "S", {}, True),
    ("AT-0", "0 % USt", "0", "Z", {}, True),
)

_DE_CODES: tuple[tuple, ...] = (
    ("DE-19", "19 % USt (Regelsteuersatz)", "19", "S", {}, True),
    ("DE-7", "7 % USt (ermäßigt)", "7", "S", {}, True),
    ("DE-0", "0 % USt", "0", "Z", {}, True),
)

#: `(key, label, code, position)`.
_EU_GENERIC_CLASSES: tuple[tuple, ...] = (
    ("standard", "Standard rate", "EU-STD", 10),
    ("zero", "Zero rate", "EU-Z", 40),
)
_AT_CLASSES: tuple[tuple, ...] = (
    ("standard", "Normalsteuersatz (20 %)", "AT-20", 10),
    ("reduced", "Ermäßigt (10 %)", "AT-10", 20),
    ("reduced_2", "Ermäßigt (13 %)", "AT-13", 30),
    ("zero", "Steuersatz 0 %", "AT-0", 40),
)
_DE_CLASSES: tuple[tuple, ...] = (
    ("standard", "Regelsteuersatz (19 %)", "DE-19", 10),
    ("reduced", "Ermäßigt (7 %)", "DE-7", 20),
    ("zero", "Steuersatz 0 %", "DE-0", 40),
)

#: `(position, name, conditions, outcome)`. SCOPE §6.3's shipped list, in
#: order, spaced by ten so a user can insert between two of them. `@item`
#: means "the item's own tax class" — see `billing/tax/vocabulary.py`.
_RULES: tuple[tuple, ...] = (
    (
        10,
        "Small business: no VAT on anything",
        {"seller_is_kleinunternehmer": True},
        {"treatment": "small_business_exemption", "code": {"code": "EU-KU"}},
    ),
    (
        20,
        "Same country: domestic rate of the item's tax class",
        {"buyer_region": "same_country"},
        {"treatment": "domestic", "code": {"class": "@item"}},
    ),
    (
        30,
        "EU business, VAT ID verified, services: reverse charge",
        {
            "buyer_region": "eu_other",
            "buyer_type": "business",
            "buyer_vat_verified": True,
            "supply_kind": "services",
        },
        {"treatment": "eu_reverse_charge", "code": {"code": "EU-AE"}},
    ),
    (
        40,
        "EU business, VAT ID verified, goods: intra-community supply",
        {
            "buyer_region": "eu_other",
            "buyer_type": "business",
            "buyer_vat_verified": True,
            "supply_kind": "goods",
        },
        {"treatment": "intra_community_supply", "code": {"code": "EU-K"}},
    ),
    (
        50,
        "EU business without a verified VAT ID: domestic rate",
        {
            "buyer_region": "eu_other",
            "buyer_type": "business",
            "buyer_vat_verified": False,
        },
        {
            "treatment": "domestic",
            "code": {"class": "@item"},
            "warning": "buyer_vat_id_not_verified",
        },
    ),
    (
        60,
        "Private buyer abroad: domestic rate",
        {"buyer_type": "private"},
        {"treatment": "domestic", "code": {"class": "@item"}},
    ),
    (
        70,
        "Non-EU business, services: not taxable in this country",
        {
            "buyer_region": "non_eu",
            "buyer_type": "business",
            "supply_kind": "services",
        },
        {"treatment": "not_taxable_in_country", "code": {"code": "EU-O"}},
    ),
    (
        80,
        "Non-EU business, goods: export",
        {"buyer_region": "non_eu", "buyer_type": "business", "supply_kind": "goods"},
        {"treatment": "export", "code": {"code": "EU-G"}},
    ),
)

#: `country -> (pack name, country codes, classes)`. Everything else gets the
#: generic pack, and a company with no country gets nothing yet.
_BY_COUNTRY = {
    "AT": ("at", _AT_CODES, _AT_CLASSES),
    "DE": ("de", _DE_CODES, _DE_CLASSES),
}


def _backfill_existing_companies() -> None:
    """Seed every company that has a country. Skip the ones that do not.

    `ON CONFLICT DO NOTHING` on both unique constraints, so a database where
    some of this already exists keeps what it has. That makes the backfill
    safe to re-run and safe on a half-seeded database — and it is the same
    promise the service makes: seeding never overwrites a company's edits.
    """
    connection = op.get_bind()
    companies = connection.execute(
        sa.text("SELECT id, address_country FROM companies")
    ).fetchall()
    if not companies:
        return

    insert_code = sa.text(
        "INSERT INTO tax_codes (id, company_id, code, name, rate, category, "
        "legal_notes, notes_reviewed, reporting_mapping, is_active, origin_pack) "
        "VALUES (:id, :company_id, :code, :name, :rate, :category, "
        "CAST(:legal_notes AS jsonb), false, '{}', :is_active, :origin_pack) "
        "ON CONFLICT (company_id, code) DO NOTHING"
    )
    insert_class = sa.text(
        "INSERT INTO tax_classes (id, company_id, key, label, code_id, "
        "position, origin_pack) "
        "VALUES (:id, :company_id, :key, :label, :code_id, :position, "
        ":origin_pack) "
        "ON CONFLICT (company_id, key) DO NOTHING"
    )
    insert_rule = sa.text(
        "INSERT INTO tax_rules (id, company_id, position, name, conditions, "
        "outcome, origin_pack, is_active) "
        "VALUES (:id, :company_id, :position, :name, "
        "CAST(:conditions AS jsonb), CAST(:outcome AS jsonb), :origin_pack, true)"
    )

    for company_id, country in companies:
        key = (country or "").strip().upper()
        if not key:
            # No country, no pack. Seeded when the country is first set — see
            # the module docstring.
            continue

        pack, country_codes, classes = _BY_COUNTRY.get(
            key, ("eu_generic", (), _EU_GENERIC_CLASSES)
        )

        code_ids: dict[str, _uuid.UUID] = {}
        for code, name, rate, category, notes, is_active in (
            _EU_GENERIC_CODES + country_codes
        ):
            new_id = _uuid.uuid4()
            code_ids[code] = new_id
            connection.execute(
                insert_code,
                {
                    "id": new_id,
                    "company_id": company_id,
                    "code": code,
                    "name": name,
                    "rate": rate,
                    "category": category,
                    "legal_notes": json.dumps(notes),
                    "is_active": is_active,
                    "origin_pack": "eu_generic" if code.startswith("EU-") else pack,
                },
            )

        # The id generated above is not necessarily the id in the table: a
        # company that already had this code kept its own row (ON CONFLICT DO
        # NOTHING), and a class pointing at the id we invented would violate
        # the foreign key. So read the ids back.
        rows = connection.execute(
            sa.text("SELECT code, id FROM tax_codes WHERE company_id = :c"),
            {"c": company_id},
        ).fetchall()
        stored = {row[0]: row[1] for row in rows}

        for class_key, label, code, position in classes:
            code_id = stored.get(code)
            if code_id is None:
                continue
            connection.execute(
                insert_class,
                {
                    "id": _uuid.uuid4(),
                    "company_id": company_id,
                    "key": class_key,
                    "label": label,
                    "code_id": code_id,
                    "position": position,
                    "origin_pack": pack,
                },
            )

        # Rules have no unique key to conflict on — two identical rules are a
        # legitimate state — so they are inserted only when the company has
        # none at all. Anything else would duplicate the shipped list on a
        # re-run.
        existing_rules = connection.execute(
            sa.text("SELECT count(*) FROM tax_rules WHERE company_id = :c"),
            {"c": company_id},
        ).scalar()
        if existing_rules:
            continue
        for position, name, conditions, outcome in _RULES:
            connection.execute(
                insert_rule,
                {
                    "id": _uuid.uuid4(),
                    "company_id": company_id,
                    "position": position,
                    "name": name,
                    "conditions": json.dumps(conditions),
                    "outcome": json.dumps(outcome),
                    "origin_pack": "eu_generic",
                },
            )


def downgrade() -> None:
    op.drop_index("ix_tax_rules_company_position", table_name="tax_rules")
    op.drop_index("ix_tax_rules_company_id", table_name="tax_rules")
    op.drop_table("tax_rules")
    op.drop_index("ix_tax_classes_company_id", table_name="tax_classes")
    op.drop_table("tax_classes")
    op.drop_index("ix_tax_codes_company_id", table_name="tax_codes")
    op.drop_table("tax_codes")
