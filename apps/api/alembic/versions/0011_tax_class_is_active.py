"""`tax_classes.is_active` — a rate can be retired, never deleted.

One column. P1a-2 lets a company add its own domestic rates, and a company
that can add one needs a way to stop using it: an Austrian rate that applied
until a reform, a rate added by mistake, a 4.9 % food rate a production
company never rebills.

**Deactivated, not deleted** (Mathias, 2026-10-23). A class that any item has
ever been labelled with is the only explanation of that item's tax, and a
document line snapshots the code it used — the same reasoning that makes
`tax_codes.is_active` a flag rather than a `DELETE`, and
`company_memberships.revoked_at` a timestamp rather than a missing row. There
is no delete endpoint for either, and there is not going to be one.

Defaults to true, so every row that already exists stays exactly as it was.
No backfill: the three shipped packs' classes are all active, and the one
class this release adds as INACTIVE (`reduced_3`, the Austrian 4.9 % food
rate) is seeded rather than migrated — no company in existence has Austrian
tax rows yet, because both the dev database and the LAN server-test instance
still have no country set (D42).

Revision ID: 0011_tax_class_is_active
Revises: 0010_tax_codes_classes_rules
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_tax_class_is_active"
down_revision: Union[str, Sequence[str], None] = "0010_tax_codes_classes_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tax_classes",
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default="true"
        ),
    )


def downgrade() -> None:
    op.drop_column("tax_classes", "is_active")
