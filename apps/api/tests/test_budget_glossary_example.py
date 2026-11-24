"""The "How to read this" worked example, checked against the real module.

The budget screen's panel (B1.1 part 1) prints a worked example — a line
planned at 1 000.00, 600.00 invoiced, 200.00 ordered and not yet invoiced —
and the figures it prints come from `apps/web/lib/budget-glossary-example.json`.
The web app computes nothing (CLAUDE.md rule 2), so a figure in that file is
only right if `billing/budget/tracking.py` says so. This test runs the file's
inputs through `track_line` and compares every output.

It reads a DATA file shared by both apps, not source text (rule 11): the
JSON is what the panel imports, and this test is the only thing that
connects the panel's numbers to the arithmetic.
"""

import json
from decimal import Decimal
from pathlib import Path

from apps.api.billing.budget.tracking import (
    Actual,
    Commitment,
    LineInput,
    track_line,
)

EXAMPLE = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "web"
    / "lib"
    / "budget-glossary-example.json"
)


def _example() -> dict:
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    # A file whose `outputs` were emptied would make every comparison below
    # vacuous; name the figures this test is about.
    assert set(data["outputs"]) >= {
        "planned",
        "ordered_open",
        "spent",
        "still_to_spend",
        "forecast",
        "variance",
        "finished_forecast",
        "finished_variance",
    }
    return data


def _line(data: dict, *, etc=None) -> LineInput:
    inputs = data["inputs"]
    return LineInput(
        id="example",
        estimate_cost=Decimal(inputs["planned"]),
        # The 600.00 invoice is one actual; the 200.00 order is a separate
        # open commitment nothing has been invoiced against yet.
        actuals=(Actual(amount=Decimal(inputs["spent"]), id="a1"),),
        commitments=(
            Commitment(amount=Decimal(inputs["ordered_not_invoiced"]), id="c1"),
        ),
        etc=etc,
    )


def test_the_worked_example_is_what_the_tracking_module_says():
    data = _example()
    out = data["outputs"]
    result = track_line(_line(data))
    assert str(result.estimate_cost) == out["planned"]
    assert str(result.committed_open) == out["ordered_open"]
    assert str(result.actual) == out["spent"]
    assert str(result.remaining) == out["still_to_spend"]
    assert str(result.efc) == out["forecast"]
    assert str(result.variance) == out["variance"]


def test_the_finished_sentence_is_what_the_tracking_module_says():
    """"Mark finished" is still-to-spend 0.00 (SCOPE D83)."""
    data = _example()
    out = data["outputs"]
    result = track_line(_line(data, etc=Decimal("0.00")))
    assert str(result.efc) == out["finished_forecast"]
    assert str(result.variance) == out["finished_variance"]
