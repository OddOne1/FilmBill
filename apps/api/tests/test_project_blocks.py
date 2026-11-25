"""Strips over days (B2a part 1), against a real PostgreSQL.

What is proved here, and why each needs the real database:

  * **one strip never has two rows on one date**, while two strips MAY cover
    the same date — a partial unique index, which a MagicMock session cannot
    enforce. The two rows in the refusal test have DIFFERENT types on
    purpose: an index that still carried `type` would let them through, and
    that is the regression this test exists for.
  * **`days:<type>` counts distinct dates**, in the three overlap cases:
    the same type in two strips, two types in two strips, and a strip's day
    beside a day outside any strip. Each case uses a date only the
    distinct-date rule gets right.
  * **a global can count one strip**, `days:<type>:<strip id>`; a removed
    strip's global falls back to all strips WITH a warning, never to zero;
    another company's strip is a 404.

The routes that create strips are part 2's; here strips are written through
the ORM, so these tests prove the model and the counting, not a handler.
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from apps.api.models.company import CompanyRole
from apps.api.models.project import ProjectBlock, ProjectDay
from apps.api.models.site_settings import SiteSettings
from apps.api.services import (
    catalog_service,
    number_series,
    project_service,
    tax_service,
)

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_project,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "blocks-world-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    catalog_service.seed_catalog(pg_db, company=company)
    number_series.seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    return {"company": company, "owner": owner, "db": pg_db}


def _headers(world):
    return auth_headers(world["owner"], world["company"])


@pytest.fixture
def project(pg_client, world):
    response = pg_client.post(
        "/projects",
        headers=_headers(world),
        json={"name": "1000m", "start_date": "2027-03-01"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def strip(db, company_id, project_id, start, end, main="shoot", overrides=None, label=None):
    """A strip and one active day per date, `overrides` = {date: type}."""
    block = ProjectBlock(
        company_id=company_id,
        project_id=project_id,
        type=main,
        label=label,
        start_date=start,
        end_date=end,
        is_active=True,
    )
    db.add(block)
    db.flush()
    day = start
    while day <= end:
        db.add(
            ProjectDay(
                company_id=company_id,
                project_id=project_id,
                block_id=block.id,
                date=day,
                type=(overrides or {}).get(day, main),
                is_active=True,
            )
        )
        day += timedelta(days=1)
    db.flush()
    return block


def _pid(project):
    return uuid.UUID(project["id"])


# ─── One row per date per strip ─────────────────────────────────────────────


def test_two_strips_may_cover_the_same_date(pg_db, world, project):
    """The BTS shoot overlapping the main shoot: both rows stand."""
    company_id = world["company"].id
    strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 5))
    strip(pg_db, company_id, _pid(project), date(2027, 3, 4), date(2027, 3, 4), label="BTS")
    pg_db.commit()

    on_the_fourth = (
        pg_db.query(ProjectDay)
        .filter(ProjectDay.project_id == _pid(project), ProjectDay.date == date(2027, 3, 4))
        .count()
    )
    assert on_the_fourth == 2


def test_one_strip_refuses_a_second_row_on_a_date_even_of_another_type(
    pg_db, world, project
):
    """A shoot row and a travel row on one date, in ONE strip: refused.

    Different types deliberately. An index on `(project_id, date, block_id,
    type)` would accept these two, and that is exactly the strip with "two
    days on the 4th" this rule is for.
    """
    company_id = world["company"].id
    block = strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 5))
    pg_db.commit()

    pg_db.add(
        ProjectDay(
            company_id=company_id,
            project_id=_pid(project),
            block_id=block.id,
            date=date(2027, 3, 4),
            type="travel",
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    pg_db.rollback()
    assert "uq_project_days_active" in str(caught.value)


def test_a_removed_row_does_not_block_its_date_in_the_strip(pg_db, world, project):
    company_id = world["company"].id
    block = strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 3))
    pg_db.commit()
    row = pg_db.query(ProjectDay).filter(ProjectDay.block_id == block.id).one()
    row.is_active = False
    pg_db.add(
        ProjectDay(
            company_id=company_id,
            project_id=_pid(project),
            block_id=block.id,
            date=date(2027, 3, 3),
            type="travel",
            is_active=True,
        )
    )
    pg_db.commit()  # no IntegrityError: the index is partial


def test_days_outside_any_strip_keep_the_old_rule(pg_db, world, project):
    """`uq_project_days_loose`: one per (date, type) when there is no strip."""
    company_id = world["company"].id
    for _ in range(2):
        pg_db.add(
            ProjectDay(
                company_id=company_id,
                project_id=_pid(project),
                date=date(2027, 3, 9),
                type="shoot",
                is_active=True,
            )
        )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    pg_db.rollback()
    assert "uq_project_days_loose" in str(caught.value)


# ─── Distinct-date counting ─────────────────────────────────────────────────


def test_overlapping_shoot_strips_count_a_shared_date_once(pg_db, world, project):
    """3–5 March and 4–6 March, both shoot: 3,4,5,6 is FOUR shoot dates.

    Six rows. A count of rows says 6, which is what the mutation produces.
    """
    company_id = world["company"].id
    strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 5))
    strip(pg_db, company_id, _pid(project), date(2027, 3, 4), date(2027, 3, 6))
    pg_db.commit()

    assert project_service.count_days_of_type(pg_db, _pid(project), "shoot") == 4
    assert project_service.day_counts_by_type(pg_db, [_pid(project)])[_pid(project)] == {
        "shoot": 4
    }


def test_a_date_that_is_shoot_in_one_strip_and_travel_in_another_counts_once_each(
    pg_db, world, project
):
    company_id = world["company"].id
    strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 5))
    strip(
        pg_db,
        company_id,
        _pid(project),
        date(2027, 3, 5),
        date(2027, 3, 6),
        main="shoot",
        overrides={date(2027, 3, 5): "travel"},
    )
    pg_db.commit()

    counts = project_service.day_counts_by_type(pg_db, [_pid(project)])[_pid(project)]
    # Shoot: 3, 4, 5 (strip one) and 6 (strip two). Travel: 5 (strip two).
    assert counts == {"shoot": 4, "travel": 1}


def test_a_strip_day_and_a_day_outside_any_strip_on_one_date_count_once(
    pg_db, world, project
):
    company_id = world["company"].id
    strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 4))
    pg_db.add(
        ProjectDay(
            company_id=company_id,
            project_id=_pid(project),
            date=date(2027, 3, 4),
            type="shoot",
            is_active=True,
        )
    )
    pg_db.commit()

    assert project_service.count_days_of_type(pg_db, _pid(project), "shoot") == 2


def test_the_count_is_limited_to_one_strip_when_asked(pg_db, world, project):
    company_id = world["company"].id
    main = strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 7))
    bts = strip(pg_db, company_id, _pid(project), date(2027, 3, 6), date(2027, 3, 9))
    pg_db.commit()

    assert project_service.count_days_of_type(pg_db, _pid(project), "shoot") == 7
    assert (
        project_service.count_days_of_type(pg_db, _pid(project), "shoot", block_id=main.id)
        == 5
    )
    assert (
        project_service.count_days_of_type(pg_db, _pid(project), "shoot", block_id=bts.id)
        == 4
    )


def test_the_project_and_the_day_list_print_the_same_distinct_count(
    pg_client, pg_db, world, project
):
    company_id = world["company"].id
    strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 5))
    strip(pg_db, company_id, _pid(project), date(2027, 3, 4), date(2027, 3, 6))
    pg_db.commit()

    one = pg_client.get(f"/projects/{project['id']}", headers=_headers(world)).json()
    days = pg_client.get(f"/projects/{project['id']}/days", headers=_headers(world)).json()
    assert one["day_counts"] == {"shoot": 4}
    assert days["counts"] == {"shoot": 4}
    assert len(days["days"]) == 6


# ─── The strip-filtered global ──────────────────────────────────────────────


def _budget(pg_client, world, project):
    response = pg_client.post(
        f"/projects/{project['id']}/budgets",
        headers=_headers(world),
        json={"name": "Estimate"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _global(pg_client, world, budget, key, source):
    return pg_client.post(
        f"/budgets/{budget['id']}/globals",
        headers=_headers(world),
        json={"key": key, "label": key, "source": source},
    )


def _globals(pg_client, world, budget):
    sheet = pg_client.get(f"/budgets/{budget['id']}", headers=_headers(world)).json()
    return {row["key"]: row for row in sheet["globals"]}


def test_a_global_counts_one_strip_or_all_of_them(pg_client, pg_db, world, project):
    company_id = world["company"].id
    main = strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 7), label="Main")
    strip(pg_db, company_id, _pid(project), date(2027, 3, 6), date(2027, 3, 9), label="BTS")
    pg_db.commit()
    budget = _budget(pg_client, world, project)

    every = _global(pg_client, world, budget, "shoot_all", "days:shoot")
    one = _global(pg_client, world, budget, "shoot_main", f"days:shoot:{main.id}")
    assert every.status_code == 201, every.text
    assert one.status_code == 201, one.text

    rows = _globals(pg_client, world, budget)
    assert rows["shoot_all"]["value"] == "7.0000"
    assert rows["shoot_all"]["block_id"] is None
    assert rows["shoot_main"]["value"] == "5.0000"
    assert rows["shoot_main"]["block_id"] == str(main.id)
    assert rows["shoot_main"]["block_label"] == "“Main”"
    assert rows["shoot_main"]["warning"] is None


def test_a_removed_strips_global_falls_back_to_all_strips_with_a_warning(
    pg_client, pg_db, world, project
):
    """Never silently to zero: the value is every strip's count, and the
    panel is handed the sentence that says why it changed."""
    company_id = world["company"].id
    main = strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 7), label="Main")
    strip(pg_db, company_id, _pid(project), date(2027, 3, 6), date(2027, 3, 9), label="BTS")
    pg_db.commit()
    budget = _budget(pg_client, world, project)
    assert _global(pg_client, world, budget, "shoot_main", f"days:shoot:{main.id}").status_code == 201

    main_id = main.id
    pg_db.expire_all()
    removed = pg_db.get(ProjectBlock, main_id)
    removed.is_active = False
    for row in pg_db.query(ProjectDay).filter(ProjectDay.block_id == main_id):
        row.is_active = False
    pg_db.commit()

    row = _globals(pg_client, world, budget)["shoot_main"]
    # BTS's 6–9 March are what is left: four, not zero and not five.
    assert row["value"] == "4.0000"
    assert row["block_id"] is None
    assert row["warning"] is not None
    assert "“Main” this global counted was removed" in row["warning"]
    assert "all strips" in row["warning"]


def test_the_picker_switches_between_all_strips_and_one_strip(
    pg_client, pg_db, world, project
):
    company_id = world["company"].id
    main = strip(pg_db, company_id, _pid(project), date(2027, 3, 3), date(2027, 3, 7))
    strip(pg_db, company_id, _pid(project), date(2027, 3, 6), date(2027, 3, 9))
    pg_db.commit()
    budget = _budget(pg_client, world, project)
    created = _global(pg_client, world, budget, "shoot_days", "days:shoot").json()

    narrowed = pg_client.patch(
        f"/budgets/{budget['id']}/globals/{created['id']}",
        headers=_headers(world),
        json={"source": f"days:shoot:{main.id}"},
    )
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["value"] == "5.0000"

    widened = pg_client.patch(
        f"/budgets/{budget['id']}/globals/{created['id']}",
        headers=_headers(world),
        json={"source": "days:shoot"},
    )
    assert widened.status_code == 200, widened.text
    assert widened.json()["value"] == "7.0000"

    other_type = pg_client.patch(
        f"/budgets/{budget['id']}/globals/{created['id']}",
        headers=_headers(world),
        json={"source": f"days:prep:{main.id}"},
    )
    assert other_type.status_code == 422, other_type.text
    assert "shoot_days can only switch" in other_type.json()["detail"]


def test_another_companys_strip_is_a_404_for_a_global(pg_client, pg_db, world, project):
    other = make_company(pg_db, "Someone else GmbH")
    their_project = make_project(pg_db, other)
    pg_db.flush()
    theirs = strip(pg_db, other.id, their_project.id, date(2027, 3, 3), date(2027, 3, 4))
    pg_db.commit()
    budget = _budget(pg_client, world, project)

    response = _global(pg_client, world, budget, "theirs", f"days:shoot:{theirs.id}")

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Strip not found"


def test_a_strip_of_another_project_in_the_same_company_is_refused(
    pg_client, pg_db, world, project
):
    second = pg_client.post(
        "/projects",
        headers=_headers(world),
        json={"name": "Other film", "start_date": "2027-03-01"},
    ).json()
    elsewhere = strip(
        pg_db, world["company"].id, uuid.UUID(second["id"]), date(2027, 3, 3), date(2027, 3, 4)
    )
    pg_db.commit()
    budget = _budget(pg_client, world, project)

    response = _global(pg_client, world, budget, "elsewhere", f"days:shoot:{elsewhere.id}")

    assert response.status_code == 404, response.text
