"""Strips over days: `project_blocks`, `project_days.block_id` (B2a part 1).

What changes, and why.

1. **`project_blocks`** — a strip: one phase of a project (prep, a shoot
   block, post) drawn across the dates it covers. It has a main type, an
   optional label and location, default call and wrap times, notes, a lane
   hint and `is_active`. `start_date <= end_date` is a CHECK constraint, the
   only one in this schema that is not about a vocabulary (0015 point 4 is
   about vocabularies, and still holds for `type`).

2. **`project_days.block_id`**, nullable, RESTRICT. A strip owns at most one
   active day row per date; each row keeps its own type (the strip's main
   type or an override such as travel). A day without a strip — older data
   that this migration could not place, or a day made through the old
   `/days` endpoints afterwards — stays valid.

3. **The unique index is replaced.** `uq_project_days_active` was
   `(project_id, date, type) WHERE is_active`; it becomes
   `(project_id, date, block_id) WHERE is_active`, so two strips may cover
   the same date and one strip never has two rows on one date. Because
   Postgres treats NULLs as distinct, the old rule for days WITHOUT a strip
   moves to a second partial index, `uq_project_days_loose`
   `(project_id, date, type) WHERE is_active AND block_id IS NULL`.

4. **`budget_globals.source` 32 → 80 characters**, for the optional strip
   filter `days:<type>:<strip id>` (a UUID is 36 characters on its own).

5. **The data migration.** Each project's ACTIVE days are walked by date and
   grouped into runs; each run becomes one strip and its days get the strip's
   id. The rule, which `test_project_calendar_migration.py` reproduces on
   Mathias's real project:

   * a run is consecutive dates — a gap made only of Saturdays and Sundays
     with no row is bridged — in which every day is the run's main type, or
     `travel`, or `off`;
   * the main type is the type of the run's first day that is not
     travel/off; a day of any other type ends the run and starts the next;
   * a run made only of travel/off days becomes a strip of the first day's
     type;
   * the strip's location, call and wrap come from the first day that has
     each; its label is NULL;
   * a date with two rows (A2 allowed a prep and a shoot day on one date)
     puts each row in a different run: one strip never gets two rows on
     one date.

   Bridged weekend dates get NO row: they are gaps inside the strip's range
   and count as nothing, so every per-type count — and so every stored
   `days:<type>` global — is identical before and after. Removed days are
   left as they are, outside any strip.

`downgrade` turns strip-filtered globals back into `days:<type>`, takes out
active rows that would collide under the old index (two strips with a shoot
day on the same date — under the distinct-date count they were one shoot
day, so no per-type count changes), drops the column, the table and the new
indexes, and restores the old index and column width.

Revision ID: 0021_project_blocks
Revises: 0020_extra_production_fee
"""

import uuid
from datetime import date, timedelta
from itertools import groupby
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_project_blocks"
down_revision: Union[str, Sequence[str], None] = "0020_extra_production_fee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)

#: Types a run takes in without changing its main type. Spelled here rather
#: than imported: a migration that imported the app's vocabulary would mean
#: something different the day that vocabulary changed.
_FLEX = ("travel", "off")


# ─── The run rule, pure ─────────────────────────────────────────────────────


def _bridged(previous: date, following: date) -> bool:
    """True when nothing but Saturdays and Sundays lies between the two."""
    day = previous + timedelta(days=1)
    while day < following:
        if day.weekday() < 5:
            return False
        day += timedelta(days=1)
    return True


def build_runs(rows: list[dict]) -> list[dict]:
    """Group one project's active days into runs. Pure; see point 5.

    `rows` are dicts with at least `date` and `type`, in any order. Returns
    `[{"type": main, "days": [row, …]}, …]` in the order the runs start.
    """
    ordered = sorted(rows, key=lambda row: (row["date"], row["type"] in _FLEX, row["type"]))
    runs: list[dict] = []
    open_runs: list[dict] = []
    for day, group in groupby(ordered, key=lambda row: row["date"]):
        extended: list[dict] = []
        for row in group:
            eligible = [
                run
                for run in open_runs
                if not any(run is taken for taken in extended)
                and _bridged(run["last"], day)
            ]
            # A run of this very type first; then, for travel/off, any run;
            # then a run that is so far only travel/off and takes this day's
            # type as its main one. Otherwise the day starts a run.
            target = next((run for run in eligible if run["main"] == row["type"]), None)
            if target is None and row["type"] in _FLEX and eligible:
                target = eligible[0]
            if target is None:
                target = next((run for run in eligible if run["main"] is None), None)
            if target is None:
                target = {"main": None, "days": [], "last": day}
                runs.append(target)
            if target["main"] is None and row["type"] not in _FLEX:
                target["main"] = row["type"]
            target["days"].append(row)
            target["last"] = day
            extended.append(target)
        # A run that this date did not extend is over: either the date had a
        # day of another type (which ends it), or the gap was not a weekend.
        open_runs = extended
    return [
        {"type": run["main"] or run["days"][0]["type"], "days": run["days"]}
        for run in runs
    ]


def _first(days: list[dict], field: str):
    for row in days:
        if row.get(field) is not None:
            return row[field]
    return None


# ─── Up ─────────────────────────────────────────────────────────────────────


def upgrade() -> None:
    op.create_table(
        "project_blocks",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "company_id",
            _UUID,
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            _UUID,
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("default_call_time", sa.Time(), nullable=True),
        sa.Column("default_wrap_time", sa.Time(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
            "start_date <= end_date", name="ck_project_blocks_dates_in_order"
        ),
    )
    op.create_index("ix_project_blocks_company_id", "project_blocks", ["company_id"])
    op.create_index("ix_project_blocks_project_id", "project_blocks", ["project_id"])
    op.create_index(
        "ix_project_blocks_company_project",
        "project_blocks",
        ["company_id", "project_id"],
    )

    op.add_column(
        "project_days",
        sa.Column(
            "block_id",
            _UUID,
            sa.ForeignKey(
                "project_blocks.id",
                ondelete="RESTRICT",
                name="project_days_block_id_fkey",
            ),
            nullable=True,
        ),
    )
    op.create_index("ix_project_days_block_id", "project_days", ["block_id"])

    op.drop_index("uq_project_days_active", table_name="project_days")
    op.create_index(
        "uq_project_days_active",
        "project_days",
        ["project_id", "date", "block_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "uq_project_days_loose",
        "project_days",
        ["project_id", "date", "type"],
        unique=True,
        postgresql_where=sa.text("is_active AND block_id IS NULL"),
    )

    op.alter_column(
        "budget_globals",
        "source",
        type_=sa.String(80),
        existing_type=sa.String(32),
        existing_nullable=False,
    )

    _migrate_days_into_strips()


def _migrate_days_into_strips() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, company_id, project_id, date, type, location, "
            "call_time, wrap_time FROM project_days WHERE is_active "
            "ORDER BY project_id, date, type"
        )
    ).mappings().all()

    insert_block = sa.text(
        "INSERT INTO project_blocks (id, company_id, project_id, type, label, "
        "start_date, end_date, location, default_call_time, default_wrap_time, "
        "is_active) VALUES (:id, :company_id, :project_id, :type, NULL, "
        ":start_date, :end_date, :location, :call, :wrap, true)"
    )
    assign = sa.text("UPDATE project_days SET block_id = :block WHERE id = :id")

    for _project_id, group in groupby(rows, key=lambda row: row["project_id"]):
        days = [dict(row) for row in group]
        for run in build_runs(days):
            block_id = uuid.uuid4()
            run_days = run["days"]
            bind.execute(
                insert_block,
                {
                    "id": block_id,
                    "company_id": run_days[0]["company_id"],
                    "project_id": run_days[0]["project_id"],
                    "type": run["type"],
                    "start_date": min(row["date"] for row in run_days),
                    "end_date": max(row["date"] for row in run_days),
                    "location": _first(run_days, "location"),
                    "call": _first(run_days, "call_time"),
                    "wrap": _first(run_days, "wrap_time"),
                },
            )
            for row in run_days:
                bind.execute(assign, {"block": block_id, "id": row["id"]})


# ─── Down ───────────────────────────────────────────────────────────────────


def downgrade() -> None:
    bind = op.get_bind()
    # A strip-filtered global becomes the all-strips one; the old column is
    # 32 characters and knows nothing about strips.
    bind.execute(
        sa.text(
            "UPDATE budget_globals SET source = split_part(source, ':', 1) "
            "|| ':' || split_part(source, ':', 2) "
            "WHERE source LIKE 'days:%:%'"
        )
    )
    # Rows the old index would refuse: the same (project, date, type) active
    # in two strips. Keep the oldest; the others were the same date counted
    # once already, so no per-type count moves.
    bind.execute(
        sa.text(
            "UPDATE project_days SET is_active = false WHERE id IN ("
            " SELECT id FROM ("
            "  SELECT id, row_number() OVER ("
            "   PARTITION BY project_id, date, type ORDER BY created_at, id"
            "  ) AS n FROM project_days WHERE is_active"
            " ) ranked WHERE n > 1)"
        )
    )

    op.alter_column(
        "budget_globals",
        "source",
        type_=sa.String(32),
        existing_type=sa.String(80),
        existing_nullable=False,
    )

    op.drop_index("uq_project_days_loose", table_name="project_days")
    op.drop_index("uq_project_days_active", table_name="project_days")
    op.drop_index("ix_project_days_block_id", table_name="project_days")
    op.drop_constraint("project_days_block_id_fkey", "project_days", type_="foreignkey")
    op.drop_column("project_days", "block_id")

    op.drop_index("ix_project_blocks_company_project", table_name="project_blocks")
    op.drop_index("ix_project_blocks_project_id", table_name="project_blocks")
    op.drop_index("ix_project_blocks_company_id", table_name="project_blocks")
    op.drop_table("project_blocks")

    op.create_index(
        "uq_project_days_active",
        "project_days",
        ["project_id", "date", "type"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
