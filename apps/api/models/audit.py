"""The audit trail: what changed, who changed it, and what it was before.

**Append-only, enforced in the database.** Three triggers raise on `UPDATE`,
`DELETE` and `TRUNCATE`. The first two are row triggers and the third has to
be a statement trigger — a row trigger does not fire for `TRUNCATE` at all,
which is the one that would otherwise take the whole table away in a single
statement.

**What that is and is not a claim about.** It is tamper-evidence against the
APPLICATION: no code path in FilmBill can rewrite or remove an audit row, and
a bug or a compromised handler cannot either. It is NOT tamper-proofing. The
table's owner can run `ALTER TABLE audit_events DISABLE TRIGGER USER` and
then do as they please; a superuser can do anything at all; and anyone with
the Postgres data directory or a backup has the rows outside the database
entirely. Saying otherwise would be the more dangerous error, so SCOPE §16
records the weaker, true claim. Real tamper-proofing needs append-only
storage outside this database (a WORM bucket, a hash chain anchored
somewhere), and that is not in v1.

The test harness is itself an instance of the hole, deliberately: the `pg_db`
fixture disables the triggers to clear the table between tests, and says so.

**`company_id` is nullable, so this table is NOT `CompanyScoped`** — an
install-level event (a superadmin changing the mail settings) belongs to no
company. That makes it the one business table `services/permissions.scoped()`
cannot constrain, which is why every read goes through
`services/audit.company_events`: one function, with the filter inside it, so
the filter cannot be the thing a new endpoint forgets.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DDL,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

try:
    from ..database import Base
except ImportError:
    from database import Base


class AuditEvent(Base):
    """One recorded change. Written once, never touched again."""

    __tablename__ = "audit_events"
    __table_args__ = (
        #: The audit log's own screen: this company's events, newest first.
        Index("ix_audit_events_company_created", "company_id", "created_at"),
        #: "What happened to this document" — the question an audit actually
        #: asks, and the one a document's history page will ask per row.
        Index(
            "ix_audit_events_company_entity",
            "company_id",
            "entity_type",
            "entity_id",
        ),
        #: "What did this person do", which is the filter the screen offers.
        Index("ix_audit_events_company_user", "company_id", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: NULL for an install-level event — site settings, mail, a user account.
    #: Those are deliberately NOT shown on a company's audit screen: a company
    #: owner has no business reading what the installation administrator does.
    company_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: NULL when the system did it — a scheduled sweep, a membership that
    #: lapsed on its own. `SET NULL` rather than CASCADE: deleting a user
    #: must not delete the record of what they did.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: `company`, `company_bank_account`, `company_membership`,
    #: `number_series`, … The table name, so an event can be joined back to
    #: the thing it is about without a mapping table of names.
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Nullable, because some events are about a thing with no row of its own
    #: — `number.out_of_order` is about an allocation that may not exist yet.
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    #: `company.updated`, `membership.revoked`, `number.out_of_order`. Dotted
    #: and noun-first, like the permission keys, so a whole area is greppable
    #: by prefix and a filter can be a prefix match.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    #: field → [old, new]. Only the fields that actually changed, and every
    #: value already redacted or masked — see `services/audit.py`. A secret
    #: that reaches this column is a secret in a table nobody can delete
    #: from.
    changes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    #: Why, when the action has a reason — a revision's justification, a
    #: Storno's cause. `Text`, because a reason worth asking for is a reason
    #: worth letting somebody write properly.
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: As seen by the application. Behind a reverse proxy this is whatever
    #: the proxy forwarded, which is a fact about the deployment and not
    #: something this column can fix.
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ─── Append-only, in the database ───────────────────────────────────────────
#
# Defined here and attached to `after_create`, so a schema built by
# `Base.metadata.create_all` — which is how the test suite's scratch database
# is built — has the triggers too. Without that the trigger tests would be
# asserting against a table that has none, and would pass against a migration
# with the triggers deleted: the harness kinder than production, again.
#
# Migration 0009 writes the same SQL for the real database. Not imported from
# here, because a migration that imports today's code changes its own meaning
# at every refactor (see migration 0008's docstring) — and what keeps the two
# honest is that the triggers are tested for their EFFECT on both schemas:
# `test_audit_events.py` against the models' and
# `test_the_migrated_schema_is_append_only_too` against the migration's.

# **No per-cent sign anywhere in these two strings, including in comments.**
# SQLAlchemy's `DDL` runs `statement % context` so a table event can
# substitute the table name, and psycopg2 reads a bare `%` in a parametrised
# statement as a placeholder of its own. Either one turns a trigger definition
# into `ValueError: unsupported format character` or
# `TypeError: %i format: a real number is required, not dict` — errors that
# name neither the function nor the reason, and that appear as 45 setup
# ERRORs in an unrelated test file. Hence `RAISE EXCEPTION USING MESSAGE =`
# with concatenation instead of plpgsql's `%` placeholder.

APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = 'audit_events is append-only; '
                  || TG_OP || ' is not permitted on it',
        ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

#: Rows: UPDATE and DELETE. Statement: TRUNCATE.
#:
#: Both are needed and neither covers the other. A row trigger never fires
#: for TRUNCATE — the statement removes rows without visiting them — so a
#: table protected only by a row trigger can be emptied by one command.
APPEND_ONLY_TRIGGERS = """
DROP TRIGGER IF EXISTS audit_events_no_change ON audit_events;
CREATE TRIGGER audit_events_no_change
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
"""

#: The trigger names, for the harness that has to switch them off and for the
#: tests that assert they are on.
APPEND_ONLY_TRIGGER_NAMES = ("audit_events_no_change", "audit_events_no_truncate")

event.listen(
    AuditEvent.__table__,
    "after_create",
    DDL(APPEND_ONLY_FUNCTION).execute_if(dialect="postgresql"),
)
event.listen(
    AuditEvent.__table__,
    "after_create",
    DDL(APPEND_ONLY_TRIGGERS).execute_if(dialect="postgresql"),
)
