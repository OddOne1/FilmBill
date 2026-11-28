"""Migration 0035 (L1b) on rows shaped like L1's. Real PostgreSQL, a
scratch database migrated to 0034 first.

L1 world: a "other"-kind barn used as a strip's location, a hotel on the
strip's hotel list, Anna's crew hotel (her assignment has 7–9 July active,
10 July unticked), and an unused hospital.
"""

import os

import pytest
from sqlalchemy import create_engine, text

from .test_projects_api import _alembic, _scratch_database


@pytest.fixture
def at_revision_0034(pg_engine):
    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0034_location_links")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0034:\n{result.stderr}")
    engine = create_engine(url)
    try:
        yield engine, url
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _one(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


def test_l1_rows_become_uses_project_locations_and_stays_and_go_back(at_revision_0034):
    engine, url = at_revision_0034
    with engine.begin() as conn:
        c = _one(conn, "INSERT INTO companies (id, legal_name, default_currency, default_language, fiscal_year_start_month, "
                       "timezone, require_2fa_roles, tax_advisor_reports) VALUES (gen_random_uuid(), 'Mig GmbH', 'EUR', 'de', 1, "
                       "'Europe/Vienna', '[]', false) RETURNING id")
        p = _one(conn, "INSERT INTO projects (id, company_id, project_number, name, status, start_date) VALUES "
                       "(gen_random_uuid(), :c, 'PRJ-1', '1000m', 'planning', '2026-07-01') RETURNING id", c=c)
        b = _one(conn, "INSERT INTO project_blocks (id, company_id, project_id, type, start_date, end_date) VALUES "
                       "(gen_random_uuid(), :c, :p, 'shoot', '2026-07-06', '2026-07-10') RETURNING id", c=c, p=p)
        loc = lambda name, kind: _one(conn, "INSERT INTO locations (id, company_id, name, name_key, kind) VALUES "  # noqa: E731
                                             "(gen_random_uuid(), :c, :n, lower(:n), :k) RETURNING id", c=c, n=name, k=kind)
        barn, hotel, crew_hotel, akh = loc("Barn", "other"), loc("Hotel A", "hotel"), loc("Hotel B", "hotel"), loc("AKH", "hospital")
        conn.execute(text("UPDATE project_blocks SET location_id = :l WHERE id = :b"), {"l": barn, "b": b})
        conn.execute(text("INSERT INTO project_block_hotels (id, company_id, block_id, location_id) VALUES "
                          "(gen_random_uuid(), :c, :b, :l)"), {"c": c, "b": b, "l": hotel})
        anna = _one(conn, "INSERT INTO parties (id, company_id, type, name, is_crew) VALUES "
                          "(gen_random_uuid(), :c, 'person', 'Anna', true) RETURNING id", c=c)
        a = _one(conn, "INSERT INTO crew_assignments (id, company_id, project_id, block_id, party_id, status, hotel_location_id) "
                       "VALUES (gen_random_uuid(), :c, :p, :b, :a, 'booked', :h) RETURNING id", c=c, p=p, b=b, a=anna, h=crew_hotel)
        for day, active in (("2026-07-07", True), ("2026-07-08", True), ("2026-07-09", True), ("2026-07-10", False)):
            conn.execute(text("INSERT INTO crew_assignment_days (id, company_id, assignment_id, date, is_active) VALUES "
                              "(gen_random_uuid(), :c, :a, :d, :x)"), {"c": c, "a": a, "d": day, "x": active})

    up = _alembic(url, "upgrade", "0035_project_locations_stays")
    assert up.returncode == 0, up.stderr
    with engine.connect() as conn:
        uses = dict(conn.execute(text("SELECT name, uses FROM locations")).all())
        # The barn was a strip's location, so it is a shooting location now.
        assert uses == {"Barn": ["other", "shoot"], "Hotel A": ["hotel"], "Hotel B": ["hotel"], "AKH": ["hospital"]}
        listed = conn.execute(text(
            "SELECT l.name FROM project_locations pl JOIN locations l ON l.id = pl.location_id ORDER BY l.name"
        )).scalars().all()
        assert listed == ["Barn", "Hotel A", "Hotel B"]
        stays = conn.execute(text(
            "SELECT l.name, s.check_in::text, s.check_out::text FROM hotel_stays s JOIN locations l ON l.id = s.location_id"
        )).all()
        # 7–9 July active (the unticked 10th does not count): out on the 10th.
        assert stays == [("Hotel B", "2026-07-07", "2026-07-10")]
        tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars().all()
        assert "project_block_hotels" not in tables
        columns = conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'crew_assignments'"
        )).scalars().all()
        assert "hotel_location_id" not in columns

    down = _alembic(url, "downgrade", "0034_location_links")
    assert down.returncode == 0, down.stderr
    with engine.connect() as conn:
        assert dict(conn.execute(text("SELECT name, kind FROM locations")).all()) == {
            "Barn": "other", "Hotel A": "hotel", "Hotel B": "hotel", "AKH": "hospital",
        }
        assert _one(conn, "SELECT l.name FROM crew_assignments a JOIN locations l ON l.id = a.hotel_location_id") == "Hotel B"
