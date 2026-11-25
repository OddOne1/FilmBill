"""Migration 0021: existing days become strips, and no count moves (B2a).

Run for real, with alembic, against a scratch database at revision 0020 —
the only version of this check that proves what the server will do at API
start. The data is Mathias's real project (1000m) as the B2a prompt gives
it, plus a second project with a weekend bridged inside a shoot run, because
the 1000m days contain no bridged weekend and so could not tell a migration
that fills a gap with a day from one that leaves it a gap.

The pure run rule (`build_runs`) is checked on its own below on the cases a
real project has not produced yet: two rows on one date, a run made only of
travel days.
"""

import importlib.util
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from .test_projects_api import _alembic, _scratch_database

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0021_project_blocks.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0021", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _range(start: date, end: date, day_type: str) -> list[tuple[date, str]]:
    out = []
    day = start
    while day <= end:
        out.append((day, day_type))
        day += timedelta(days=1)
    return out


#: 1000m, as Mathias described it on 2026-11-25.
THOUSAND_METRES = (
    _range(date(2026, 11, 30), date(2026, 12, 2), "prep")
    + _range(date(2026, 12, 3), date(2026, 12, 4), "shoot")
    + [
        (date(2027, 1, 12), "travel"),
        (date(2027, 1, 13), "shoot"),
        (date(2027, 1, 14), "travel"),
        (date(2027, 2, 22), "travel"),
    ]
    + _range(date(2027, 2, 23), date(2027, 2, 27), "shoot")
    + [(date(2027, 2, 28), "travel")]
    + _range(date(2027, 3, 1), date(2027, 3, 12), "post")
)

#: Friday 5 June and Monday 8 June 2026, shoot, nothing on the weekend:
#: ONE strip 5–8 June with two days in it, not four.
BRIDGED = [(date(2026, 6, 5), "shoot"), (date(2026, 6, 8), "shoot")]


@pytest.fixture
def at_revision_0020(pg_engine):
    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0020_extra_production_fee")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0020:\n{result.stderr}")
    db_engine = create_engine(url)
    try:
        yield db_engine, url
    finally:
        db_engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _seed(conn, days, *, number, removed=()):
    company_id = conn.execute(text("SELECT id FROM companies LIMIT 1")).scalar()
    project_id = conn.execute(
        text(
            "INSERT INTO projects (id, company_id, project_number, name, status, "
            "start_date) VALUES (gen_random_uuid(), :company, :number, :name, "
            "'active', :start) RETURNING id"
        ),
        {"company": company_id, "number": number, "name": number, "start": days[0][0]},
    ).scalar()
    for day, day_type in days:
        conn.execute(
            text(
                "INSERT INTO project_days (id, company_id, project_id, date, type, "
                "location, is_active) VALUES (gen_random_uuid(), :company, :project, "
                ":date, :type, :location, true)"
            ),
            {
                "company": company_id,
                "project": project_id,
                "date": day,
                "type": day_type,
                # Only the shoot days carry a location, so "location from the
                # first day that has one" is checked against a strip whose
                # FIRST day (a travel day) has none.
                "location": "Kaprun" if day_type == "shoot" else None,
            },
        )
    for day, day_type in removed:
        conn.execute(
            text(
                "INSERT INTO project_days (id, company_id, project_id, date, type, "
                "is_active) VALUES (gen_random_uuid(), :company, :project, :date, "
                ":type, false)"
            ),
            {"company": company_id, "project": project_id, "date": day, "type": day_type},
        )
    budget_id = conn.execute(
        text(
            "INSERT INTO budgets (id, company_id, project_id, name, currency) "
            "VALUES (gen_random_uuid(), :company, :project, 'Estimate', 'EUR') "
            "RETURNING id"
        ),
        {"company": company_id, "project": project_id},
    ).scalar()
    return project_id, budget_id


def _counts(conn, project_id) -> dict:
    """Per-type distinct dates over active days — what a global counts."""
    return {
        row[0]: row[1]
        for row in conn.execute(
            text(
                "SELECT type, count(DISTINCT date) FROM project_days "
                "WHERE project_id = :p AND is_active GROUP BY type"
            ),
            {"p": project_id},
        )
    }


def _store_globals(conn, company_id, budget_id, counts):
    for day_type, total in counts.items():
        conn.execute(
            text(
                "INSERT INTO budget_globals (id, company_id, budget_id, key, label, "
                "value, source) VALUES (gen_random_uuid(), :company, :budget, :key, "
                ":key, :value, :source)"
            ),
            {
                "company": company_id,
                "budget": budget_id,
                "key": f"{day_type}_days",
                "value": total,
                "source": f"days:{day_type}",
            },
        )


def test_existing_days_become_strips_and_every_count_stays(at_revision_0020):
    db_engine, url = at_revision_0020

    with db_engine.begin() as conn:
        company_id = conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, "
                "default_language, fiscal_year_start_month, timezone, "
                "require_2fa_roles, tax_advisor_reports) VALUES "
                "(gen_random_uuid(), 'YON Studio OG', 'EUR', 'de', 1, "
                "'Europe/Vienna', '[]', false) RETURNING id"
            )
        ).scalar()
        thousand, thousand_budget = _seed(
            conn,
            THOUSAND_METRES,
            number="PRJ-2026-001",
            # A shoot day taken out of the plan before B2a: stays out, and
            # in no strip.
            removed=[(date(2026, 12, 5), "shoot")],
        )
        bridged, bridged_budget = _seed(conn, BRIDGED, number="PRJ-2026-002")
        before = {thousand: _counts(conn, thousand), bridged: _counts(conn, bridged)}
        _store_globals(conn, company_id, thousand_budget, before[thousand])
        _store_globals(conn, company_id, bridged_budget, before[bridged])

    assert before[thousand] == {"prep": 3, "shoot": 8, "travel": 4, "post": 12}
    assert before[bridged] == {"shoot": 2}

    up = _alembic(url, "upgrade", "head")
    assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

    with db_engine.connect() as conn:
        strips = conn.execute(
            text(
                "SELECT id, type, start_date, end_date, label, location "
                "FROM project_blocks WHERE project_id = :p ORDER BY start_date"
            ),
            {"p": thousand},
        ).all()
        assert [(row.type, row.start_date, row.end_date) for row in strips] == [
            ("prep", date(2026, 11, 30), date(2026, 12, 2)),
            ("shoot", date(2026, 12, 3), date(2026, 12, 4)),
            ("shoot", date(2027, 1, 12), date(2027, 1, 14)),
            ("shoot", date(2027, 2, 22), date(2027, 2, 28)),
            ("post", date(2027, 3, 1), date(2027, 3, 12)),
        ]
        assert all(row.label is None for row in strips)
        # The first shoot-with-travel strip starts on a travel day that has
        # no location; the location comes from the first day that has one.
        assert strips[2].location == "Kaprun"
        assert strips[0].location is None

        # The two travel-framed shoots: a travel day at each end, the
        # middle their main type.
        for row, inside in ((strips[2], 1), (strips[3], 5)):
            types = [
                day.type
                for day in conn.execute(
                    text(
                        "SELECT type FROM project_days WHERE block_id = :b "
                        "AND is_active ORDER BY date"
                    ),
                    {"b": row.id},
                )
            ]
            assert types == ["travel"] + ["shoot"] * inside + ["travel"]

        unplaced = conn.execute(
            text(
                "SELECT count(*) FROM project_days WHERE is_active AND block_id IS NULL"
            )
        ).scalar()
        assert unplaced == 0
        still_removed = conn.execute(
            text(
                "SELECT block_id, is_active FROM project_days "
                "WHERE project_id = :p AND date = '2026-12-05'"
            ),
            {"p": thousand},
        ).one()
        assert still_removed.block_id is None and still_removed.is_active is False

        bridged_strips = conn.execute(
            text(
                "SELECT start_date, end_date FROM project_blocks WHERE project_id = :p"
            ),
            {"p": bridged},
        ).all()
        assert [(row.start_date, row.end_date) for row in bridged_strips] == [
            (date(2026, 6, 5), date(2026, 6, 8))
        ]

        # THE check: not one per-type count moved, and the stored globals
        # still equal what the days count to.
        assert _counts(conn, thousand) == before[thousand]
        assert _counts(conn, bridged) == before[bridged]
        for project_id, budget_id in ((thousand, thousand_budget), (bridged, bridged_budget)):
            stored = {
                row.source[5:]: int(row.value)
                for row in conn.execute(
                    text("SELECT source, value FROM budget_globals WHERE budget_id = :b"),
                    {"b": budget_id},
                )
            }
            assert stored == _counts(conn, project_id)

        index = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_project_days_active'")
        ).scalar()
        assert "(project_id, date, block_id)" in index and "WHERE is_active" in index

    # Down: strips and the column go, the old index comes back, and the
    # counts are still the same.
    down = _alembic(url, "downgrade", "0020_extra_production_fee")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"
    with db_engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
        assert "project_blocks" not in tables
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'project_days'"
                )
            )
        }
        assert "block_id" not in columns
        index = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_project_days_active'")
        ).scalar()
        assert "(project_id, date, type)" in index
        assert _counts(conn, thousand) == before[thousand]

    again = _alembic(url, "upgrade", "head")
    assert again.returncode == 0, f"{again.stdout}\n{again.stderr}"


def test_the_downgrade_folds_overlapping_strips_back_under_the_old_index(
    at_revision_0020,
):
    """After B2a two strips may both have a shoot day on 4 March. The old
    index would refuse that, so the downgrade keeps one — and the shoot
    count, which was of distinct dates, does not move."""
    db_engine, url = at_revision_0020
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, "
                "default_language, fiscal_year_start_month, timezone, "
                "require_2fa_roles, tax_advisor_reports) VALUES "
                "(gen_random_uuid(), 'YON Studio OG', 'EUR', 'de', 1, "
                "'Europe/Vienna', '[]', false)"
            )
        )
        project_id, budget_id = _seed(conn, [(date(2027, 3, 4), "shoot")], number="P-1")
    assert _alembic(url, "upgrade", "head").returncode == 0

    with db_engine.begin() as conn:
        company_id = conn.execute(text("SELECT id FROM companies LIMIT 1")).scalar()
        block_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO project_blocks (id, company_id, project_id, type, "
                "start_date, end_date) VALUES (:id, :c, :p, 'shoot', '2027-03-04', "
                "'2027-03-04')"
            ),
            {"id": block_id, "c": company_id, "p": project_id},
        )
        conn.execute(
            text(
                "INSERT INTO project_days (id, company_id, project_id, block_id, date, "
                "type, is_active) VALUES (gen_random_uuid(), :c, :p, :b, '2027-03-04', "
                "'shoot', true)"
            ),
            {"c": company_id, "p": project_id, "b": block_id},
        )
        conn.execute(
            text(
                "INSERT INTO budget_globals (id, company_id, budget_id, key, label, "
                "value, source) VALUES (gen_random_uuid(), :c, :b, 'bts', 'bts', 1, "
                ":source)"
            ),
            {"c": company_id, "b": budget_id, "source": f"days:shoot:{block_id}"},
        )
        assert _counts(conn, project_id) == {"shoot": 1}

    down = _alembic(url, "downgrade", "0020_extra_production_fee")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"
    with db_engine.connect() as conn:
        assert _counts(conn, project_id) == {"shoot": 1}
        source = conn.execute(
            text("SELECT source FROM budget_globals WHERE key = 'bts'")
        ).scalar()
        assert source == "days:shoot"


# ─── The run rule on its own ────────────────────────────────────────────────


def _runs(days):
    module = _load_migration()
    return [
        (run["type"], [row["date"] for row in run["days"]], [row["type"] for row in run["days"]])
        for run in module.build_runs([{"date": d, "type": t} for d, t in days])
    ]


def test_a_run_of_only_travel_days_is_a_travel_strip():
    assert _runs([(date(2027, 1, 12), "travel"), (date(2027, 1, 13), "travel")]) == [
        ("travel", [date(2027, 1, 12), date(2027, 1, 13)], ["travel", "travel"])
    ]


def test_a_weekday_gap_ends_the_run_and_a_weekend_gap_does_not():
    # Thu 4 → Mon 8 June 2026: Friday is a weekday with no row, so two runs.
    assert len(_runs([(date(2026, 6, 4), "shoot"), (date(2026, 6, 8), "shoot")])) == 2
    # Fri 5 → Mon 8: only the weekend between, one run.
    assert len(_runs([(date(2026, 6, 5), "shoot"), (date(2026, 6, 8), "shoot")])) == 1


def test_two_rows_on_one_date_go_to_two_strips():
    """A2 allowed a prep and a shoot day on one date; one strip may not have
    both, so each continues its own run."""
    runs = _runs(
        [
            (date(2027, 3, 1), "prep"),
            (date(2027, 3, 2), "prep"),
            (date(2027, 3, 2), "shoot"),
            (date(2027, 3, 3), "shoot"),
        ]
    )
    assert runs == [
        ("prep", [date(2027, 3, 1), date(2027, 3, 2)], ["prep", "prep"]),
        ("shoot", [date(2027, 3, 2), date(2027, 3, 3)], ["shoot", "shoot"]),
    ]


def test_a_day_of_another_type_ends_the_run():
    runs = _runs(
        [
            (date(2027, 3, 1), "shoot"),
            (date(2027, 3, 2), "prep"),
            (date(2027, 3, 3), "shoot"),
        ]
    )
    assert [run[0] for run in runs] == ["shoot", "prep", "shoot"]
