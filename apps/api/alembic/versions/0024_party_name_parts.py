"""People get first, middle and last names; organisations a second line (P1b-4 part 1).

`parties.name` stays NOT NULL and stays the display name every existing query
reads. For a person with name parts it is derived on write as
"First Middle Last" (`models.party.derive_person_name`).

**No data migration, on purpose.** Existing people are NOT split
automatically: the order of names varies ("Reisinger Philipp Maximilian" is
surname first), and a guess would be wrong often enough to make every
existing person suspect. They keep their stored `name`, have no parts, and
show in the list's Needs attention view as "name not split" until somebody
enters the parts by hand.

`name_addition` is the second line of an organisation or department
("Marketing department", "c/o …"). Two CHECKs keep the parts on people and
the second line off them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_party_name_parts"
down_revision: Union[str, Sequence[str], None] = "0023_devices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in ("first_name", "middle_name", "last_name", "name_addition"):
        op.add_column("parties", sa.Column(column, sa.String(255), nullable=True))
    op.create_check_constraint(
        "ck_parties_name_parts_person_only",
        "parties",
        "type = 'person' OR (first_name IS NULL AND middle_name IS NULL "
        "AND last_name IS NULL)",
    )
    op.create_check_constraint(
        "ck_parties_name_addition_not_person",
        "parties",
        "type <> 'person' OR name_addition IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_parties_name_addition_not_person", "parties", type_="check")
    op.drop_constraint("ck_parties_name_parts_person_only", "parties", type_="check")
    for column in ("name_addition", "last_name", "middle_name", "first_name"):
        op.drop_column("parties", column)
