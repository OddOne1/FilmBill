"""The lean catalog: units, categories, items, rates, rental factor tables.

SCOPE §4.1–4.3, slice A1. Six tables, every one of them company-scoped with
`company_id NOT NULL` and an index on it, because CLAUDE.md rule 6 makes that
a property of the table and not of each query.

**Three things worth reading before changing this.**

1. **The kind and rate-type literals are repeated here, not imported.** A
   migration is a record of what the schema looked like on a day; importing
   `billing/catalog/kinds.ITEM_KINDS` would make this file change meaning the
   next time a kind is added. Migrations 0008 and 0013 set the precedent and
   rule 9 is the reason. They are not database enums either — a `String(24)`
   with the authority in the Python vocabulary costs nothing to extend and an
   enum type costs a migration.

2. **`items.sku` is unique per company only where it is set.** A partial
   index (`WHERE sku IS NOT NULL`). A plain unique would also work, because
   Postgres treats NULLs as distinct — but it would work by accident, and
   the next person to read it would have to know that detail of NULL
   semantics to see that forty items without article numbers are allowed.

3. **Every foreign key out of `items` is RESTRICT.** Nothing in the catalog
   is ever deleted (there is no DELETE route in `routers/catalog.py` and a
   test asserts the absence on the OpenAPI document), and `RESTRICT` is what
   makes that true at the database level too: a category cannot take forty
   items with it. The one `CASCADE` pair is a factor table and its rows,
   which have no meaning apart from the table they are in.

`downgrade` drops the six tables in dependency order. It is exercised: CI
runs `alembic downgrade base`, and `test_catalog_migration.py` walks this
revision up and down against a real database.

Revision ID: 0014_catalog
Revises: 0013_party_tax_ids
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_catalog"
down_revision: Union[str, Sequence[str], None] = "0013_party_tax_ids"
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


def upgrade() -> None:
    # ── units ────────────────────────────────────────────────────────
    op.create_table(
        "units",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("label_de", sa.String(64), nullable=False),
        sa.Column("label_en", sa.String(64), nullable=True),
        # One of billing/calc/vocabulary.PERIOD_UNITS. See the module
        # docstring for why this is not an enum type.
        sa.Column("period_unit", sa.String(16), nullable=False),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        *_timestamps(),
        sa.UniqueConstraint("company_id", "key", name="uq_units_company_key"),
    )
    op.create_index("ix_units_company_id", "units", ["company_id"])
    op.create_index("ix_units_company_active", "units", ["company_id", "is_active"])

    # ── item_categories ──────────────────────────────────────────────
    op.create_table(
        "item_categories",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column("name", sa.String(255), nullable=False),
        # SCOPE §4.1's system enum: labor, equipment_rental, space_rental,
        # travel, per_diem, storage, product, software_license, fee, other.
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column(
            "parent_id",
            _UUID,
            sa.ForeignKey("item_categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        *_timestamps(),
    )
    op.create_index("ix_item_categories_company_id", "item_categories", ["company_id"])
    op.create_index(
        "ix_item_categories_company_parent",
        "item_categories",
        ["company_id", "parent_id"],
    )
    op.create_index(
        "ix_item_categories_company_kind", "item_categories", ["company_id", "kind"]
    )

    # ── rental_factor_groups ─────────────────────────────────────────
    op.create_table(
        "rental_factor_groups",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "company_id", "name", name="uq_rental_factor_groups_name"
        ),
    )
    op.create_index(
        "ix_rental_factor_groups_company_id", "rental_factor_groups", ["company_id"]
    )

    # ── rental_factors ───────────────────────────────────────────────
    op.create_table(
        "rental_factors",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "group_id",
            _UUID,
            # The one CASCADE in this migration: a factor row has no meaning
            # apart from its table.
            sa.ForeignKey("rental_factor_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("days", sa.Integer(), nullable=False),
        # NUMERIC(18,4) — a rate, per CLAUDE.md rule 1.
        sa.Column("factor", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.UniqueConstraint("group_id", "days", name="uq_rental_factors_group_days"),
    )
    op.create_index("ix_rental_factors_company_id", "rental_factors", ["company_id"])
    op.create_index("ix_rental_factors_group_id", "rental_factors", ["group_id"])

    # ── items ────────────────────────────────────────────────────────
    op.create_table(
        "items",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column("sku", sa.String(64), nullable=True),
        sa.Column("name_de", sa.String(255), nullable=False),
        sa.Column("name_en", sa.String(255), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column(
            "category_id",
            _UUID,
            sa.ForeignKey("item_categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("description_internal", sa.Text(), nullable=True),
        sa.Column("description_customer", sa.Text(), nullable=True),
        sa.Column(
            "unit_id", _UUID, sa.ForeignKey("units.id", ondelete="RESTRICT"), nullable=True
        ),
        # A `tax_classes.key`, not an id — the spelling `tax_rules` already
        # uses for "which class". See models/catalog.py's docstring.
        sa.Column("default_tax_class", sa.String(32), nullable=True),
        sa.Column(
            "rental_factor_group_id",
            _UUID,
            sa.ForeignKey("rental_factor_groups.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # Plain strings: the chart of accounts arrives in P6, and a string
        # now means the field is usable today.
        sa.Column("revenue_account", sa.String(32), nullable=True),
        sa.Column("expense_account", sa.String(32), nullable=True),
        sa.Column(
            "rebilled_cost",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        *_timestamps(),
    )
    op.create_index("ix_items_company_id", "items", ["company_id"])
    op.create_index(
        "uq_items_company_sku",
        "items",
        ["company_id", "sku"],
        unique=True,
        postgresql_where=sa.text("sku IS NOT NULL"),
    )
    op.create_index("ix_items_company_active", "items", ["company_id", "is_active"])
    op.create_index("ix_items_company_kind", "items", ["company_id", "kind"])
    op.create_index("ix_items_company_category", "items", ["company_id", "category_id"])

    # ── item_rates ───────────────────────────────────────────────────
    op.create_table(
        "item_rates",
        sa.Column("id", _UUID, primary_key=True),
        _company_fk(),
        sa.Column(
            "item_id", _UUID, sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False
        ),
        # `bill` or `cost` (SCOPE §4.2).
        sa.Column("rate_type", sa.String(8), nullable=False),
        sa.Column("period_unit", sa.String(16), nullable=False),
        # NUMERIC(18,2) — money, per CLAUDE.md rule 1.
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "item_id", "rate_type", "period_unit", name="uq_item_rates_item_type_period"
        ),
    )
    op.create_index("ix_item_rates_company_id", "item_rates", ["company_id"])
    op.create_index("ix_item_rates_item_id", "item_rates", ["item_id"])


def downgrade() -> None:
    # Dependency order, innermost first: item_rates and items point at
    # categories, units and factor groups, and rental_factors points at its
    # group.
    op.drop_table("item_rates")
    op.drop_table("items")
    op.drop_table("rental_factors")
    op.drop_table("rental_factor_groups")
    op.drop_table("item_categories")
    op.drop_table("units")
