"""Team members as crew: a login linked to a contact (R2 part 1).

* `parties.user_id` — the login (a `User` with a membership in this
  company) this person contact IS. Nullable, `ON DELETE SET NULL`. At most
  one contact per user and company (`uq_parties_company_user`, partial over
  the linked rows); a contact has one column, so it has at most one user.
  The person-only rule and the "active membership in the same company" rule
  are the service's, with a sentence, because neither is expressible as a
  CHECK without a join.
* `party_crew_roles.confirmed` / `set_by` — a role a member set for
  themselves (`set_by = 'self'`) is unconfirmed until an admin confirms it.
  Every existing role was written by somebody with `parties.edit`, so it is
  `confirmed = true, set_by = 'admin'` — the server defaults.
* `notificationtype` gains `team`: "<name> wants to work as crew".

No data is rewritten and nothing is merged: existing contacts stay unlinked.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_team_members_as_crew"
down_revision: Union[str, Sequence[str], None] = "0031_gear_bookings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parties",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_parties_user_id"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_parties_company_user",
        "parties",
        ["company_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    op.add_column(
        "party_crew_roles",
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "party_crew_roles",
        sa.Column("set_by", sa.String(8), nullable=False, server_default="admin"),
    )
    op.create_check_constraint(
        "ck_party_crew_roles_set_by", "party_crew_roles", "set_by IN ('admin', 'self')"
    )

    # PostgreSQL 12+ allows this inside a transaction; the value is not used
    # in this one.
    op.execute("ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'team'")


def downgrade() -> None:
    # An enum value cannot be dropped: the type is rebuilt without it, and a
    # `team` notification becomes an `account` one rather than vanishing.
    op.execute("UPDATE notifications SET type = 'account' WHERE type = 'team'")
    op.execute("ALTER TYPE notificationtype RENAME TO notificationtype_r2")
    op.execute("CREATE TYPE notificationtype AS ENUM ('account')")
    op.execute(
        "ALTER TABLE notifications ALTER COLUMN type TYPE notificationtype "
        "USING type::text::notificationtype"
    )
    op.execute("DROP TYPE notificationtype_r2")

    op.drop_constraint("ck_party_crew_roles_set_by", "party_crew_roles", type_="check")
    op.drop_column("party_crew_roles", "set_by")
    op.drop_column("party_crew_roles", "confirmed")
    op.drop_index("uq_parties_company_user", table_name="parties")
    op.drop_column("parties", "user_id")
