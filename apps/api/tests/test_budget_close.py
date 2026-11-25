"""Closing a budget, with a review — against a real PostgreSQL (B1.1 part 3).

The budget under test is two lines in one section, so every number can be
read without arithmetic:

  * **Camera** — planned cost 1 000.00;
  * **Grip** — planned cost 400.00.

Planned in total: 1 400.00. The two state fixtures:

  * `within_estimate`: Camera invoiced 1 200.00 (200.00 over), Grip invoiced
    100.00 and marked finished (300.00 under). Forecast 1 300.00 ≤ 1 400.00
    — money moved between lines, and Camera is listed as over.
  * `over_estimate`: Camera invoiced 1 200.00, Grip invoiced 400.00 and
    finished. Forecast 1 600.00 > 1 400.00.

And `clean`: Camera invoiced exactly 1 000.00, Grip exactly 400.00, nothing
open, nothing off a line.

The three mutations this file has to catch: a close accepted without the
acknowledgement when something is listed
(`test_discrepancies_refuse_a_close_without_the_acknowledgement`), a stale
digest accepted (`test_a_stale_digest_is_refused_and_nothing_is_written`),
and a closed budget accepting an actual
(`test_a_closed_budget_refuses_cost_entries_and_finishing`).
"""

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.project import Budget

from .company_factories import (
    auth_headers,
    make_budget,
    make_company,
    make_project,
)
from .test_budget_tracking_api import (  # noqa: F401 — fixtures
    _actual,
    _amount,
    _commitment,
    _headers,
    _tracking,
    project,
    world,
)

NOTE = "Approved by the producer on the wrap call."


@pytest.fixture
def two_lines(pg_client, world, project):
    budget = pg_client.post(
        f"/projects/{project['id']}/budgets",
        headers=_headers(world),
        json={"name": "Spot"},
    ).json()
    section = pg_client.post(
        f"/budgets/{budget['id']}/sections",
        headers=_headers(world),
        json={"name": "Camera dept", "code": "1200"},
    ).json()
    lines = {}
    for name, cost in (("Camera", "1000.00"), ("Grip", "400.00")):
        response = pg_client.post(
            f"/budgets/{budget['id']}/lines",
            headers=_headers(world),
            json={
                "section_id": section["id"],
                "description": name,
                "quantity": "1",
                "period_value": "1",
                "period_unit": "flat",
                "factor": "1",
                "unit_price": "2000.00",
                "unit_cost": cost,
            },
        )
        assert response.status_code == 201, response.text
        lines[name] = response.json()
    return {"budget": budget, "section": section, "lines": lines}


def _invoice(pg_client, world, stage, name, amount, **extra):
    return _actual(
        pg_client,
        world,
        stage["budget"]["id"],
        section_id=stage["section"]["id"],
        line_id=stage["lines"][name]["id"],
        amount=amount,
        reference=f"RE-{name}-{amount}",
        **extra,
    )


def _finish(pg_client, world, stage, name, **body):
    response = pg_client.post(
        f"/budgets/{stage['budget']['id']}/lines/{stage['lines'][name]['id']}/finish",
        headers=_headers(world),
        json=body,
    )
    assert response.status_code == 200, response.text


def _lock(pg_client, world, stage):
    response = pg_client.post(
        f"/budgets/{stage['budget']['id']}/lock", headers=_headers(world), json={}
    )
    assert response.status_code == 200, response.text


def _check(pg_client, world, stage):
    response = pg_client.get(
        f"/budgets/{stage['budget']['id']}/close-check", headers=_headers(world)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _close(pg_client, world, stage, **body):
    return pg_client.post(
        f"/budgets/{stage['budget']['id']}/close", headers=_headers(world), json=body
    )


def _reopen(pg_client, world, stage, reason="Late invoice from the rental house."):
    return pg_client.post(
        f"/budgets/{stage['budget']['id']}/reopen",
        headers=_headers(world),
        json={"reason": reason},
    )


def _events(pg_db, action):
    pg_db.expire_all()
    return pg_db.query(AuditEvent).filter(AuditEvent.action == action).all()


def _closed_at(pg_db, stage):
    pg_db.expire_all()
    return pg_db.get(Budget, stage["budget"]["id"]).closed_at


@pytest.fixture
def within(pg_client, world, two_lines):
    _invoice(pg_client, world, two_lines, "Camera", "1200.00")
    _invoice(pg_client, world, two_lines, "Grip", "100.00")
    _finish(pg_client, world, two_lines, "Grip")
    _lock(pg_client, world, two_lines)
    return two_lines


# ─── The check ──────────────────────────────────────────────────────────────


def test_a_clean_budget_closes_without_acknowledgement(
    pg_client, pg_db, world, two_lines
):
    _invoice(pg_client, world, two_lines, "Camera", "1000.00")
    _invoice(pg_client, world, two_lines, "Grip", "400.00")
    _lock(pg_client, world, two_lines)

    check = _check(pg_client, world, two_lines)
    assert check["state"] == "clean"
    for key in (
        "lines_over",
        "lines_under_not_finished",
        "finished_with_open_commitment",
        "open_commitments",
        "unallocated",
    ):
        assert check[key] == [], key
    _amount(check["estimate_cost"], "1400.00", what="planned")
    _amount(check["efc"], "1400.00", what="forecast")

    response = _close(pg_client, world, two_lines)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_closed"] is True
    assert body["closed_by"] == str(world["owner"].id)
    (event,) = _events(pg_db, "budget.closed")
    assert event.changes["close_check"][1]["state"] == "clean"


def test_within_estimate_lists_the_line_over_and_keeps_the_total_within(
    pg_client, world, within
):
    check = _check(pg_client, world, within)
    assert check["state"] == "within_estimate"
    _amount(check["estimate_cost"], "1400.00", what="planned")
    _amount(check["efc"], "1300.00", what="forecast: 1200 + 100")
    _amount(check["variance"], "100.00", what="100.00 under in total")
    assert [row["description"] for row in check["lines_over"]] == ["Camera"]
    assert check["lines_over"][0]["line_id"] == within["lines"]["Camera"]["id"]
    _amount(check["lines_over"][0]["variance"], "-200.00", what="Camera 200 over")
    # Grip is under but finished, so it is explained and not listed.
    assert check["lines_under_not_finished"] == []


def test_over_estimate_when_the_total_forecast_is_above_the_plan(
    pg_client, world, two_lines
):
    _invoice(pg_client, world, two_lines, "Camera", "1200.00")
    _invoice(pg_client, world, two_lines, "Grip", "400.00")
    _finish(pg_client, world, two_lines, "Grip")
    _lock(pg_client, world, two_lines)
    check = _check(pg_client, world, two_lines)
    assert check["state"] == "over_estimate"
    _amount(check["efc"], "1600.00", what="forecast: 1200 + 400")
    _amount(check["variance"], "-200.00", what="200.00 over in total")


def test_every_kind_of_discrepancy_is_listed_with_its_ids(
    pg_client, world, two_lines
):
    budget_id = two_lines["budget"]["id"]
    section_id = two_lines["section"]["id"]
    # Grip: under (100 of 400 spent) and not finished.
    _invoice(pg_client, world, two_lines, "Grip", "100.00")
    # Camera: an open order of 250, then finished anyway.
    order = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=section_id,
        line_id=two_lines["lines"]["Camera"]["id"],
        amount="250.00",
        reference="PO-CAM",
    )
    _invoice(pg_client, world, two_lines, "Camera", "900.00")
    _finish(pg_client, world, two_lines, "Camera", confirm_open_commitment=True)
    # A taxi booked on the section, on no line.
    taxi = _actual(
        pg_client,
        world,
        budget_id,
        section_id=section_id,
        amount="45.00",
        reference="TAXI",
    )
    _lock(pg_client, world, two_lines)

    check = _check(pg_client, world, two_lines)
    assert [row["line_id"] for row in check["lines_under_not_finished"]] == [
        two_lines["lines"]["Grip"]["id"]
    ]
    assert [row["line_id"] for row in check["finished_with_open_commitment"]] == [
        two_lines["lines"]["Camera"]["id"]
    ]
    assert [row["id"] for row in check["open_commitments"]] == [order["id"]]
    _amount(check["open_commitments"][0]["amount"], "250.00", what="open order")
    _amount(check["open_commitments_total"], "250.00", what="open total")
    assert [row["id"] for row in check["unallocated"]] == [taxi["id"]]
    _amount(check["unallocated_total"], "45.00", what="not on a line")
    assert check["state"] in ("within_estimate", "over_estimate")


# ─── Closing over discrepancies ─────────────────────────────────────────────


def test_discrepancies_refuse_a_close_without_the_acknowledgement(
    pg_client, pg_db, world, within
):
    digest = _check(pg_client, world, within)["digest"]
    response = _close(pg_client, world, within, digest=digest, note=NOTE)
    assert response.status_code == 409, response.text
    assert "tick that you approve" in response.json()["detail"]
    assert _closed_at(pg_db, within) is None
    assert _events(pg_db, "budget.closed") == []


def test_discrepancies_refuse_a_close_without_a_note(pg_client, pg_db, world, within):
    digest = _check(pg_client, world, within)["digest"]
    for note in (None, "", "ok", "fine by me"[:7]):
        response = _close(
            pg_client, world, within, acknowledge=True, digest=digest, note=note
        )
        assert response.status_code == 409, (note, response.text)
        assert "at least one sentence" in response.json()["detail"]
    assert _closed_at(pg_db, within) is None


def test_a_stale_digest_is_refused_and_nothing_is_written(
    pg_client, pg_db, world, within
):
    digest = _check(pg_client, world, within)["digest"]
    # Something is booked after the list was read.
    _invoice(pg_client, world, within, "Camera", "50.00")
    assert _check(pg_client, world, within)["digest"] != digest

    response = _close(
        pg_client, world, within, acknowledge=True, digest=digest, note=NOTE
    )
    assert response.status_code == 409, response.text
    assert "Look at the list again" in response.json()["detail"]
    assert _closed_at(pg_db, within) is None
    assert _events(pg_db, "budget.closed") == []


def test_acknowledgement_note_and_a_fresh_digest_close_it(
    pg_client, pg_db, world, within
):
    check = _check(pg_client, world, within)
    response = _close(
        pg_client, world, within, acknowledge=True, digest=check["digest"], note=NOTE
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_closed"] is True
    assert response.json()["close_note"] == NOTE
    (event,) = _events(pg_db, "budget.closed")
    assert event.reason == NOTE
    snapshot = event.changes["close_check"][1]
    assert snapshot["state"] == "within_estimate"
    assert snapshot["digest"] == check["digest"]
    assert [row["description"] for row in snapshot["lines_over"]] == ["Camera"]


def test_an_unlocked_budget_cannot_be_closed(pg_client, pg_db, world, two_lines):
    response = _close(pg_client, world, two_lines)
    assert response.status_code == 409
    assert "not locked" in response.json()["detail"]
    assert _closed_at(pg_db, two_lines) is None


# ─── What a closed budget refuses ───────────────────────────────────────────


@pytest.fixture
def closed(pg_client, world, within):
    check = _check(pg_client, world, within)
    response = _close(
        pg_client, world, within, acknowledge=True, digest=check["digest"], note=NOTE
    )
    assert response.status_code == 200, response.text
    return within


def test_a_closed_budget_refuses_cost_entries_and_finishing(
    pg_client, world, closed
):
    budget_id = closed["budget"]["id"]
    section_id = closed["section"]["id"]
    camera = closed["lines"]["Camera"]["id"]
    attempts = [
        (
            "post",
            f"/budgets/{budget_id}/actuals",
            {"section_id": section_id, "line_id": camera, "amount": "10.00", "incurred_on": "2026-06-03"},
        ),
        (
            "post",
            f"/budgets/{budget_id}/commitments",
            {"section_id": section_id, "amount": "10.00", "commitment_date": "2026-06-03"},
        ),
        ("put", f"/budgets/{budget_id}/lines/{camera}/etc", {"etc": "5.00"}),
        ("post", f"/budgets/{budget_id}/lines/{camera}/finish", {}),
        ("post", f"/budgets/{budget_id}/lines/{closed['lines']['Grip']['id']}/reopen", None),
        ("post", f"/budgets/{budget_id}/sections/{section_id}/finish", None),
        ("post", f"/budgets/{budget_id}/unlock", {}),
    ]
    for method, url, body in attempts:
        response = getattr(pg_client, method)(
            url, headers=_headers(world), **({} if body is None else {"json": body})
        )
        assert response.status_code == 409, f"{method} {url}: {response.status_code} {response.text}"
        assert "Reopen it first" in response.json()["detail"], url

    # And nothing was booked: still the two invoices from the fixture.
    tracking = _tracking(pg_client, world, budget_id)
    _amount(tracking["lines"][camera]["actual"], "1200.00", what="Camera spent")


def test_reopening_needs_a_reason_and_restores_cost_entries(
    pg_client, pg_db, world, closed
):
    blank = _reopen(pg_client, world, closed, reason="   ")
    assert blank.status_code == 409
    assert "needs a reason" in blank.json()["detail"]
    missing = pg_client.post(
        f"/budgets/{closed['budget']['id']}/reopen", headers=_headers(world), json={}
    )
    assert missing.status_code == 422

    response = _reopen(pg_client, world, closed)
    assert response.status_code == 200, response.text
    assert response.json()["is_closed"] is False
    assert response.json()["is_locked"] is True
    (event,) = _events(pg_db, "budget.reopened")
    assert event.reason == "Late invoice from the rental house."
    assert event.changes["closed_at"][1] is None

    _invoice(pg_client, world, closed, "Camera", "30.00")
    _amount(
        _tracking(pg_client, world, closed["budget"]["id"])["lines"][
            closed["lines"]["Camera"]["id"]
        ]["actual"],
        "1230.00",
        what="Camera spent after reopening",
    )


def test_closing_twice_and_reopening_an_open_budget_are_refused(
    pg_client, world, closed
):
    again = _close(pg_client, world, closed)
    assert again.status_code == 409
    assert "already closed" in again.json()["detail"]
    assert _reopen(pg_client, world, closed).status_code == 200
    second = _reopen(pg_client, world, closed)
    assert second.status_code == 409
    assert "not closed" in second.json()["detail"]


# ─── Permissions and isolation ──────────────────────────────────────────────


def test_closing_needs_budgets_edit(pg_client, pg_db, world, within):
    from apps.api.models.company import CompanyRole

    from .company_factories import grant, make_user

    accountant = make_user(pg_db, "close-accountant@example.com")
    grant(pg_db, world["company"], accountant, CompanyRole.accountant)
    pg_db.commit()
    headers = auth_headers(accountant, world["company"])
    # The accountant may read the check (budgets.view) and not close.
    assert (
        pg_client.get(f"/budgets/{within['budget']['id']}/close-check", headers=headers).status_code
        == 200
    )
    response = pg_client.post(
        f"/budgets/{within['budget']['id']}/close", headers=headers, json={}
    )
    assert response.status_code == 404
    assert _closed_at(pg_db, within) is None


def test_the_close_routes_404_for_another_company_s_budget(pg_client, pg_db, world):
    other = make_company(pg_db, "Someone else GmbH")
    their_budget = make_budget(pg_db, other, make_project(pg_db, other))
    pg_db.commit()
    for method, url, body in (
        ("get", f"/budgets/{their_budget.id}/close-check", None),
        ("post", f"/budgets/{their_budget.id}/close", {}),
        ("post", f"/budgets/{their_budget.id}/reopen", {"reason": "x"}),
    ):
        response = getattr(pg_client, method)(
            url, headers=_headers(world), **({} if body is None else {"json": body})
        )
        assert response.status_code == 404, f"{method} {url}: {response.status_code}"
