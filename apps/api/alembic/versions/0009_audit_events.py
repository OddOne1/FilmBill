"""The audit trail, append-only in the database.

One table and three triggers. The triggers are the point: `UPDATE` and
`DELETE` are refused by a ROW trigger, and `TRUNCATE` needs a STATEMENT
trigger of its own because a row trigger never fires for it — the statement
removes rows without visiting them, so a table protected only by a row
trigger can be emptied with one command.

**This is tamper-evidence against the application, not tamper-proofing.** No
code path in FilmBill can rewrite or remove an audit row, and neither can a
bug or a compromised handler. The table's owner can still run
`ALTER TABLE audit_events DISABLE TRIGGER USER`; a superuser can do anything;
and anybody holding a backup or the data directory has the rows outside the
database entirely. SCOPE §16 records the weaker, true claim, because the
stronger one is the more dangerous error.

The SQL is written out here rather than imported from `models/audit.py` — a
migration that imports today's code changes its own meaning at every refactor
(see 0008). The model attaches the same DDL to `after_create` so that the test
suite's `create_all` schema has the triggers too, and what keeps the two
honest is that both schemas are tested for the EFFECT:
`test_audit_events.py` against the models' and
`test_the_migrated_schema_is_append_only_too` against this one.

`company_id` is NULLABLE: an install-level event — the mail settings, a user
account — belongs to no company. That is also why `audit_events` is the one
business table without the `CompanyScoped` mixin; reads go through
`services/audit.company_events`, which carries the filter.

Revision ID: 0009_audit_events
Revises: 0008_number_series
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_audit_events"
down_revision: Union[str, Sequence[str], None] = "0008_number_series"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# No per-cent sign in these two strings, including in comments: psycopg2
# reads a bare `%` in a parametrised statement as a placeholder of its own
# and the CREATE FUNCTION fails with an error that names neither the
# function nor the reason. See models/audit.py for the long version.
FUNCTION = """
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = 'audit_events is append-only; '
                  || TG_OP || ' is not permitted on it',
        ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

TRIGGERS = """
DROP TRIGGER IF EXISTS audit_events_no_change ON audit_events;
CREATE TRIGGER audit_events_no_change
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "changes",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_audit_events_company_created",
        "audit_events",
        ["company_id", "created_at"],
    )
    op.create_index(
        "ix_audit_events_company_entity",
        "audit_events",
        ["company_id", "entity_type", "entity_id"],
    )
    op.create_index(
        "ix_audit_events_company_user",
        "audit_events",
        ["company_id", "user_id", "created_at"],
    )

    op.execute(sa.text(FUNCTION))
    op.execute(sa.text(TRIGGERS))


def downgrade() -> None:
    # The triggers go with the table. The FUNCTION is dropped separately
    # because it is schema-level and would otherwise survive as an orphan
    # that the next `upgrade` would have to `CREATE OR REPLACE` over — which
    # works, and leaves a function nobody can explain.
    op.drop_table("audit_events")
    op.execute(sa.text("DROP FUNCTION IF EXISTS audit_events_append_only()"))
