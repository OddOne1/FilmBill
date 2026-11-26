"""A person can also be a business: the sole proprietor (P1b-4 part 2).

An Austrian Einzelunternehmen is a person and a business at once. Some trade
under a business name, some under their own. One record has to represent
that, not a person plus an organisation that drift apart — so it is a flag on
the person, `is_sole_proprietor`, plus an optional `business_name`.

`business_status = auto` resolves a sole proprietor to business; a hand-set
`business` or `private` still wins (the resolver, D55 extended). No data
migration: nobody is a sole proprietor until somebody ticks the box.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_party_sole_proprietor"
down_revision: Union[str, Sequence[str], None] = "0024_party_name_parts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parties",
        sa.Column(
            "is_sole_proprietor",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("parties", sa.Column("business_name", sa.String(255), nullable=True))
    op.create_check_constraint(
        "ck_parties_sole_proprietor_person_only",
        "parties",
        "type = 'person' OR (NOT is_sole_proprietor AND business_name IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_parties_sole_proprietor_person_only", "parties", type_="check")
    op.drop_column("parties", "business_name")
    op.drop_column("parties", "is_sole_proprietor")
