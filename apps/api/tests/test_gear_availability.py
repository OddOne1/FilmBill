"""The gear availability rule, as a table of numbers (B2c-2 part 1).

`billing/planning/gear_availability.py` is pure, so the rule is pinned by
the golden fixtures in `fixtures/gear_availability/`, one scenario per file
with every number written out. Three guards keep the walk honest: the
directory may not be empty, every fixture the prompt asked for is there,
and the comparison is run against a deliberately wrong number and must
fail.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from apps.api.billing.planning import Holding, day, period

FIXTURES = Path(__file__).parent / "fixtures" / "gear_availability"
DAY_KEYS = ("owned", "scheduled", "booked", "available", "overbooked", "shortage")
PERIOD_KEYS = ("min_available", "worst_day", "overbooked", "shortage")


def _files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def _run(fixture: dict):
    by_day: dict[date, list[Holding]] = {}
    for row in fixture["bookings"]:
        by_day.setdefault(date.fromisoformat(row["date"]), []).append(
            Holding(booking_id=row["id"], quantity=row["quantity"], status=row["status"])
        )
    return period(
        fixture["owned"],
        by_day,
        [date.fromisoformat(d) for d in fixture["dates"]],
        exclude=fixture.get("exclude"),
    )


def _got(result) -> dict:
    return {
        "days": {d.date.isoformat(): {key: getattr(d, key) for key in DAY_KEYS} for d in result.days},
        "period": {
            "min_available": result.min_available,
            "worst_day": result.worst_day.isoformat() if result.worst_day else None,
            "overbooked": result.overbooked,
            "shortage": result.shortage,
        },
    }


def _compare(fixture: dict) -> None:
    assert _got(_run(fixture)) == fixture["expect"], fixture["why"]


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.stem)
def test_golden_fixture(path):
    _compare(json.loads(path.read_text()))


def test_the_fixture_directory_is_not_empty():
    assert len(_files()) >= 7, [p.name for p in _files()]


def test_the_scenarios_the_prompt_asked_for_are_all_here():
    stems = {p.stem[:2] for p in _files()}
    assert {"a-", "b-", "c-", "d-", "e-", "f-", "g-"} <= stems


def test_every_fixture_names_every_number():
    for path in _files():
        fixture = json.loads(path.read_text())
        assert fixture["expect"]["days"], path.name
        for numbers in fixture["expect"]["days"].values():
            assert set(numbers) == set(DAY_KEYS), path.name
        assert set(fixture["expect"]["period"]) == set(PERIOD_KEYS), path.name


def test_the_comparison_notices_a_wrong_number():
    fixture = json.loads((FIXTURES / "c-short-by-one.json").read_text())
    fixture["expect"]["days"]["2026-07-06"]["available"] = 0
    with pytest.raises(AssertionError):
        _compare(fixture)


def test_a_day_without_bookings_is_simply_owned():
    result = day(date(2026, 7, 6), 4, [])
    assert (result.available, result.overbooked, result.shortage) == (4, False, 0)


def test_an_empty_period_has_no_worst_day():
    result = period(2, {}, [])
    assert (result.min_available, result.worst_day, result.days) == (2, None, ())


def test_equal_days_name_the_earliest_as_worst():
    holdings = {date(2026, 7, 6): [Holding("x", 1, "booked")], date(2026, 7, 7): [Holding("x", 1, "booked")]}
    assert period(1, holdings, list(holdings)).worst_day == date(2026, 7, 6)


@pytest.mark.parametrize("bad", [Holding("x", 0, "booked"), Holding("x", 1, "option")])
def test_nonsense_is_refused(bad):
    with pytest.raises(ValueError):
        day(date(2026, 7, 6), 1, [bad])
