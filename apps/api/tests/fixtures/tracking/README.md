# Golden cost-tracking fixtures

One JSON file per scenario for `billing/budget/tracking.py`. Each has four
keys:

* `why` — what the scenario is evidence about, in a sentence.
* `budget` — the module's input: sections, lines, commitments, actuals, ETCs
  and the estimate-phase prices from the calculation engine.
* `expect` — every number, written out by hand: `lines` and `sections` keyed
  by id, and `budget` for the whole sheet.
* `closes_commitment` — optional, `commitment id → true | false`: whether
  `tracking.closes_commitment` says the linked actuals have settled it. The
  automatic close and reopen in `services/budget_tracking_service.py` is this
  one comparison, so it is pinned here rather than only in a database test.

**They are data files on purpose**, for `fixtures/calc/README.md`'s reason: a
forecasting rule written as a Python expression is reviewable by a
programmer, and the same rule as a table of inputs and expected cents is
reviewable by Mathias — which is the only review that settles whether the
default `remaining` rule is the right one (SCOPE D83 records it as
`[Guessing]`).

`test_budget_tracking.py` walks this directory, and three guards keep the
walk from going quiet: the directory may not be empty, every file must name
numbers in `expect`, and the comparison helper is run against a deliberately
wrong expectation and has to fail.

Money is a **string** everywhere, in the input and in the expectations, for
the reason the calc fixtures give: a JSON number is a binary float and has
already lost the exact value. `null` in an expectation means "no value" —
`variance_percent` on a line whose estimate is zero — and is asserted as the
absence of a number rather than as zero.

The file `s1-example.json` is scenario (k) of the B1 prompt: the A1/A2 S1
example budget with three entries on it. Its estimate prices, cost subtotals
and estimate margins are the ones `fixtures/calc/s1-quote.json` already
asserts against the engine, so the two cannot drift.
