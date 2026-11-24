""""Mark finished", against a real PostgreSQL (B1.1 part 2).

Finishing a line sets its still-to-spend (`etc`) to exactly 0.00 — SCOPE
D83's "0.00 means finished and is different from no ETC" — so the line stops
forecasting its full estimate and the profit it came in under shows up.

The centre is `test_finishing_studio_on_the_s1_example_moves_the_forecast`:
the S1 example's Studio A line (estimate cost 2 200.00) with an invoice of
2 000.00 forecasts 2 200.00 by the default rule; finished, it forecasts
2 000.00 and the budget's forecast profit rises by exactly 200.00.

The skip rule is the other half: a bulk finish leaves alone every line that
still has an order open, because a bulk action nobody reads line by line must
not hide money still owed. `test_bulk_finish_skips_a_line_with_an_open_order`
is the test the mutation "bulk finish touches a line with an open
commitment" has to fail.
"""

from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.project import BudgetLine

from .company_factories import make_budget, make_company, make_project
from .test_budget_tracking_api import (  # noqa: F401 — fixtures
    _actual,
    _amount,
    _commitment,
    _headers,
    _tracking,
    example,
    project,
    simple,
    world,
)


def _finish(pg_client, world, budget_id, line_id, **body):
    return pg_client.post(
        f"/budgets/{budget_id}/lines/{line_id}/finish",
        headers=_headers(world),
        json=body,
    )


def _reopen(pg_client, world, budget_id, line_id):
    return pg_client.post(
        f"/budgets/{budget_id}/lines/{line_id}/reopen", headers=_headers(world)
    )


def _events(pg_db, action):
    pg_db.expire_all()
    return pg_db.query(AuditEvent).filter(AuditEvent.action == action).all()


# ─── One line ───────────────────────────────────────────────────────────────


def test_finishing_studio_on_the_s1_example_moves_the_forecast(
    pg_client, world, example
):
    budget_id = example["budget"]["id"]
    studio = example["lines"]["Studio A"]
    studio_section = example["sections"]["Studio"]
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=studio_section["id"],
        line_id=studio["id"],
        amount="2000.00",
        reference="RE-STUDIO",
    )

    before = _tracking(pg_client, world, budget_id)
    line = before["lines"][studio["id"]]
    _amount(line["estimate_cost"], "2200.00", what="Studio estimate cost")
    _amount(line["actual"], "2000.00", what="Studio spent")
    _amount(line["remaining"], "200.00", what="Studio still to spend, default rule")
    _amount(line["efc"], "2200.00", what="Studio forecast before")
    assert line["finished"] is False
    _amount(before["budget"]["price"], "12517.20", what="S1 net price")
    _amount(before["budget"]["efc"], "5380.00", what="budget forecast before")
    _amount(before["budget"]["forecast_margin"], "7137.20", what="profit before")

    response = _finish(pg_client, world, budget_id, studio["id"])
    assert response.status_code == 200, response.text
    assert response.json()["finished"] is True
    _amount(response.json()["efc"], "2000.00", what="Studio forecast in the answer")

    after = _tracking(pg_client, world, budget_id)
    line = after["lines"][studio["id"]]
    _amount(line["etc"], "0.00", what="Studio etc")
    _amount(line["remaining"], "0.00", what="Studio still to spend, finished")
    _amount(line["efc"], "2000.00", what="Studio forecast after")
    _amount(line["variance"], "200.00", what="Studio 200.00 under")
    assert line["finished"] is True
    _amount(after["budget"]["efc"], "5180.00", what="budget forecast after")
    _amount(after["budget"]["forecast_margin"], "7337.20", what="profit after")
    assert Decimal(after["budget"]["forecast_margin"]) - Decimal(
        before["budget"]["forecast_margin"]
    ) == Decimal("200.00")


def test_reopen_clears_the_etc_and_the_forecast_goes_back(
    pg_client, pg_db, world, simple
):
    """The test the mutation "reopen leaves the ETC in place" must fail."""
    budget_id = simple["budget"]["id"]
    line_id = simple["line"]["id"]
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=line_id,
        amount="600.00",
    )
    assert _finish(pg_client, world, budget_id, line_id).status_code == 200
    _amount(
        _tracking(pg_client, world, budget_id)["lines"][line_id]["efc"],
        "600.00",
        what="finished forecast",
    )

    response = _reopen(pg_client, world, budget_id, line_id)
    assert response.status_code == 200, response.text
    assert response.json()["finished"] is False
    assert response.json()["remaining_rule"] == "default"

    pg_db.expire_all()
    assert pg_db.get(BudgetLine, simple["line"]["id"]).etc is None
    line = _tracking(pg_client, world, budget_id)["lines"][line_id]
    _amount(line["efc"], "1000.00", what="forecast back to the plan")
    assert line["etc"] is None


def test_a_line_with_an_open_order_asks_before_it_is_finished(
    pg_client, pg_db, world, simple
):
    budget_id = simple["budget"]["id"]
    line_id = simple["line"]["id"]
    _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=line_id,
        amount="300.00",
    )
    refused = _finish(pg_client, world, budget_id, line_id)
    assert refused.status_code == 409, refused.text
    assert "300.00 ordered and not yet invoiced" in refused.json()["detail"]
    pg_db.expire_all()
    assert pg_db.get(BudgetLine, simple["line"]["id"]).etc is None

    confirmed = _finish(
        pg_client, world, budget_id, line_id, confirm_open_commitment=True
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["finished"] is True
    (event,) = _events(pg_db, "budget_line.finished")
    assert "confirmed" in (event.reason or "")


def test_finishing_twice_and_reopening_an_open_line_are_refused(
    pg_client, world, simple
):
    budget_id = simple["budget"]["id"]
    line_id = simple["line"]["id"]
    refused = _reopen(pg_client, world, budget_id, line_id)
    assert refused.status_code == 409
    assert "not marked finished" in refused.json()["detail"]
    assert _finish(pg_client, world, budget_id, line_id).status_code == 200
    again = _finish(pg_client, world, budget_id, line_id)
    assert again.status_code == 409
    assert "already marked finished" in again.json()["detail"]


def test_finish_and_reopen_are_audited_with_before_and_after(
    pg_client, pg_db, world, simple
):
    budget_id = simple["budget"]["id"]
    line_id = simple["line"]["id"]
    _finish(pg_client, world, budget_id, line_id)
    _reopen(pg_client, world, budget_id, line_id)
    (finished,) = _events(pg_db, "budget_line.finished")
    (reopened,) = _events(pg_db, "budget_line.reopened")
    assert str(finished.entity_id) == line_id
    assert Decimal(finished.changes["etc"][1]) == Decimal("0")
    assert finished.changes["etc"][0] is None
    assert reopened.changes["etc"][1] is None


def test_finish_works_on_a_locked_budget(pg_client, world, simple):
    """D85: a lock freezes the plan; finishing a line measures it."""
    budget_id = simple["budget"]["id"]
    assert (
        pg_client.post(
            f"/budgets/{budget_id}/lock", headers=_headers(world), json={}
        ).status_code
        == 200
    )
    response = _finish(pg_client, world, budget_id, simple["line"]["id"])
    assert response.status_code == 200, response.text
    assert response.json()["finished"] is True


# ─── A section at once ──────────────────────────────────────────────────────


@pytest.fixture
def three_lines(pg_client, world, simple):
    """`simple`'s section with two more lines: three lines, one ordered."""
    budget_id = simple["budget"]["id"]
    section_id = simple["section"]["id"]
    lines = [simple["line"]]
    for name in ("Gaffer", "Best boy"):
        response = pg_client.post(
            f"/budgets/{budget_id}/lines",
            headers=_headers(world),
            json={
                "section_id": section_id,
                "description": name,
                "quantity": "1",
                "period_value": "1",
                "period_unit": "flat",
                "factor": "1",
                "unit_price": "500.00",
                "unit_cost": "400.00",
            },
        )
        assert response.status_code == 201, response.text
        lines.append(response.json())
    # An open order of 150.00 on the Gaffer line — nothing invoiced on it.
    _commitment(
        pg_client,
        world,
        budget_id,
        section_id=section_id,
        line_id=lines[1]["id"],
        amount="150.00",
        reference="PO-GAFFER",
    )
    return {**simple, "lines": lines}


def test_bulk_finish_skips_a_line_with_an_open_order(
    pg_client, pg_db, world, three_lines
):
    budget_id = three_lines["budget"]["id"]
    dop, gaffer, best_boy = three_lines["lines"]
    response = pg_client.post(
        f"/budgets/{budget_id}/sections/{three_lines['section']['id']}/finish",
        headers=_headers(world),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["changed"]) == sorted([dop["id"], best_boy["id"]])
    assert [row["line_id"] for row in body["skipped"]] == [gaffer["id"]]
    assert body["skipped"][0]["description"] == "Gaffer"
    _amount(body["skipped"][0]["committed_open"], "150.00", what="skipped open order")
    assert body["message"] == (
        "2 lines marked finished. 1 line skipped: still ordered, not invoiced."
    )

    tracking = _tracking(pg_client, world, budget_id)
    assert tracking["lines"][dop["id"]]["finished"] is True
    assert tracking["lines"][best_boy["id"]]["finished"] is True
    # The positive marker on the line the skip protects: still forecasting
    # by the default rule, its etc untouched.
    gaffer_row = tracking["lines"][gaffer["id"]]
    assert gaffer_row["finished"] is False
    assert gaffer_row["etc"] is None
    assert gaffer_row["remaining_rule"] == "default"
    pg_db.expire_all()
    assert pg_db.get(BudgetLine, gaffer["id"]).etc is None


def test_bulk_finish_writes_one_audit_event_with_counts(
    pg_client, pg_db, world, three_lines
):
    budget_id = three_lines["budget"]["id"]
    section_id = three_lines["section"]["id"]
    pg_client.post(
        f"/budgets/{budget_id}/sections/{section_id}/finish",
        headers=_headers(world),
    )
    (event,) = _events(pg_db, "budget_line.finished_bulk")
    assert str(event.entity_id) == section_id
    assert event.changes["finished"][1] == 2
    assert event.changes["skipped_open_commitment"][1] == 1
    assert event.changes["already_finished"][1] == 0
    # One bulk event, no per-line events.
    assert _events(pg_db, "budget_line.finished") == []


def test_reopen_all_reopens_only_finished_lines(
    pg_client, pg_db, world, three_lines
):
    budget_id = three_lines["budget"]["id"]
    section_id = three_lines["section"]["id"]
    dop, gaffer, best_boy = three_lines["lines"]
    # A hand-set still-to-spend that is NOT a finish stays as typed.
    pg_client.put(
        f"/budgets/{budget_id}/lines/{gaffer['id']}/etc",
        headers=_headers(world),
        json={"etc": "75.00"},
    )
    pg_client.post(
        f"/budgets/{budget_id}/sections/{section_id}/finish",
        headers=_headers(world),
    )
    response = pg_client.post(
        f"/budgets/{budget_id}/sections/{section_id}/reopen",
        headers=_headers(world),
    )
    assert response.status_code == 200, response.text
    assert sorted(response.json()["changed"]) == sorted([dop["id"], best_boy["id"]])
    assert response.json()["message"] == "2 lines reopened."
    tracking = _tracking(pg_client, world, budget_id)
    assert tracking["lines"][dop["id"]]["etc"] is None
    _amount(tracking["lines"][gaffer["id"]]["etc"], "75.00", what="hand-set kept")
    (event,) = _events(pg_db, "budget_line.reopened_bulk")
    assert event.changes["reopened"][1] == 2


def test_bulk_finish_includes_the_lines_of_sub_sections(pg_client, world, simple):
    budget_id = simple["budget"]["id"]
    child = pg_client.post(
        f"/budgets/{budget_id}/sections",
        headers=_headers(world),
        json={"name": "Grip", "parent_id": simple["section"]["id"]},
    ).json()
    line = pg_client.post(
        f"/budgets/{budget_id}/lines",
        headers=_headers(world),
        json={
            "section_id": child["id"],
            "description": "Dolly",
            "quantity": "1",
            "period_value": "1",
            "period_unit": "flat",
            "factor": "1",
            "unit_price": "100.00",
            "unit_cost": "80.00",
        },
    ).json()
    response = pg_client.post(
        f"/budgets/{budget_id}/sections/{simple['section']['id']}/finish",
        headers=_headers(world),
    )
    assert line["id"] in response.json()["changed"]


# ─── Isolation and permissions ──────────────────────────────────────────────


def test_the_finish_routes_404_for_another_company_s_budget(pg_client, pg_db, world):
    other = make_company(pg_db, "Someone else GmbH")
    their_budget = make_budget(pg_db, other, make_project(pg_db, other))
    pg_db.commit()
    stranger = "00000000-0000-0000-0000-000000000001"
    for url in (
        f"/budgets/{their_budget.id}/lines/{stranger}/finish",
        f"/budgets/{their_budget.id}/lines/{stranger}/reopen",
        f"/budgets/{their_budget.id}/sections/{stranger}/finish",
        f"/budgets/{their_budget.id}/sections/{stranger}/reopen",
    ):
        response = pg_client.post(url, headers=_headers(world), json={})
        assert response.status_code == 404, f"{url}: {response.status_code}"


def test_a_section_of_another_budget_is_a_404(pg_client, world, simple, project):
    other = pg_client.post(
        f"/projects/{project['id']}/budgets",
        headers=_headers(world),
        json={"name": "Scenario B"},
    ).json()
    response = pg_client.post(
        f"/budgets/{other['id']}/sections/{simple['section']['id']}/finish",
        headers=_headers(world),
    )
    assert response.status_code == 404
