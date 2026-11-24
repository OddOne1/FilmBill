# B1 report — cost planning (Committed, Actual, cost-to-complete)

Prompt: `docs/prompts/27-filmbill-B1-cost-tracking.md`. Built 2026-11-24.

**In plain words.** The budget now tracks money going out. You can book
orders (commitments) and invoices (actuals) against any line — or against a
section alone for a cost nobody budgeted — and the sheet shows, per line,
per section and in total, what is committed, what is spent, what the budget
will really cost at the end (EFC) and the variance, plus the forecast margin
against the price the client agreed. A locked budget still accepts costs.
The rule for "what is still to be spent" is provisional and needs your
decision (D83).

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `ff47f99` | the prompt file and its INDEX line |
| 1 | `e0aef7c` | `billing/budget/tracking.py` (pure), 11 golden fixtures, 5 mutations |
| 2 | `00b6e0f` | models, migration 0016, service, router, CSV, tests, 4 mutations |
| 2b | `2ea3d7d` | `GET /budgets/{id}/duplicates` — the pre-save duplicate check |
| 3 | `c7a785a` | phase switch, line drawer, Costs tab, cost forecast; 1 mutation |
| 4 | `e41f666` | SCOPE D81–D88, §13 B1/B2/B3 |
| close | (this commit) | CI floors, INDEX notes, this report |

Part 2b is separate because the screens showed the prompt's "duplicate
warnings shown before saving" needs a read-only question the dialog can ask
first; part 2 was already committed. Each commit is green on its own.

## Step 0 — answers

1. **State.** Branch `Claude_MS/filmbill-cost-tracking-b6eef1` in a worktree,
   clean, 0 commits ahead of `origin/main` at start (`2cd5eb5`). Remote
   `git@github.com:OddOne1/FilmBill.git`. **Declared environment:** API in
   the project's `filmbill-api:latest` image (`python:3.11`) against the dev
   stack's real PostgreSQL 15 and Redis, `CI=1` (a missing DB fails rather
   than skips); web vitest on macOS for iteration and `pnpm ci:web`
   (`node:20`, Linux, clean `git archive HEAD`) for the closing count.
   **Baselines:** API 2459 passed / 30 skipped; `-m realdb` 1133 passed; web
   CI-equivalent count as recorded at A2's close (649/650 — the worktree had
   no `node_modules` at start, so no fresh local web baseline before B1's
   first commit). **CI floors:** 2410 / 1095.
2. **The A2 budget model.** `budgets`, `budget_sections` (two deep),
   `budget_lines`, `budget_globals`, `budget_adjustments`. A line's
   estimate cost reaches the calculated view through
   `budget_service.engine_input` → `calc_mapping.calculate_document` →
   `CalcResult.lines[id].cost_net`; section cost is `GroupResult.cost_subtotal`,
   document cost `CalcResult.cost`. The lock: `refuse_if_locked` (409)
   called by the router's `_editable`, which also takes `SELECT … FOR
   UPDATE` on the budget. The sheet fetches `GET /budgets/{id}` and
   `GET /budgets/{id}/calculated` together after every write. Top-sheet
   shares come from the engine's `percent_share` inside
   `budget_service.calculated`.
3. **Party picker / `is_supplier`.** There is no shared picker component:
   screens fill a `<select>` from `GET /parties?role=<role>&limit=200`
   (`role=supplier` filters `is_supplier`). Elsewhere a party is referenced
   by a RESTRICT foreign key (`projects.client_party_id`). B1 does the same
   (`party_id`, RESTRICT, same-company checked → 404) with `payee_text` as
   the free-text alternative; never both.
4. **The import pattern.** Pure planner (`billing/budget/csv_lines.py`,
   `plan_from_file` → plan with `digest`), service gathers an
   `ImportContext`, `preview` returns the digest, `apply` recomputes and
   refuses on mismatch, error rows skipped, one audit event with counts. B1
   reuses the shape and the shared helpers (`detect_delimiter`,
   `normalise_decimal_text`, `to_decimal`, A2's ambiguous-dot refusal) in
   `billing/budget/csv_actuals.py`, and writes rows through `create_actual`.

## What was built

**Migration `0016_budget_cost_tracking`** — `budget_commitments`,
`budget_actuals` (company-scoped, all FKs RESTRICT except company CASCADE,
`amount NUMERIC(18,2)` with `CHECK amount > 0`, section NOT NULL, line
nullable, void columns), and `budget_lines.etc NUMERIC(18,2)` nullable with
no default.

**Audit actions:** `budget_commitment.created|updated|closed|cancelled|voided`,
`budget_commitment.reopened` (the automatic reopen — added so "owed again"
is its own event), `budget_actual.created|updated|voided`,
`budget_line.etc_set`, `budget.actuals_import_applied` (counts). All in the
caller's transaction, with before/after diffs.

**API (17 operations, no new permission keys):**
`GET /budgets/{id}/tracking`, `GET .../ledger`, `GET .../duplicates`,
`GET|POST .../commitments`, `PATCH .../commitments/{commitment_id}`,
`POST .../commitments/{commitment_id}/close|cancel|void`,
`GET|POST .../actuals`, `PATCH .../actuals/{actual_id}`,
`POST .../actuals/{actual_id}/void`, `PUT .../lines/{line_id}/etc`,
`GET .../actuals/export.csv`, `POST .../actuals/import/preview`,
`POST .../actuals/import`. `GET .../calculated` is unchanged on the wire;
`calc_result` was extracted from it so `tracking` reads the same
`CalcResult` instead of pricing the budget again.

**Screens:** phase switch (Estimate is A2's component, unchanged), line
drawer, Costs tab, cost forecast under A2's top sheet. Details in the part 3
commit message.

## Fixture (k) — the S1 example with three entries

Invoice 3 200.00 on Studio A, open order 1 000.00 on Alexa 35 (cost 0.00),
ETC 500.00 on Taxi. Net price 12 517.20.

| Section | Estimate cost | Committed open | Actual | EFC | Variance | Var % | Price | Forecast margin | Est. margin |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Crew | 3 060.00 | 0.00 | 0.00 | 3 060.00 | 0.00 | 0.00 | 3 990.00 | 930.00 (23.31 %) | 930.00 (23.31 %) |
| Equipment | 0.00 | 1 000.00 | 0.00 | 1 000.00 | −1 000.00 | — | 4 680.00 | 3 680.00 (78.63 %) | 4 680.00 (100.00 %) |
| Studio | 2 200.00 | 0.00 | 3 200.00 | 3 200.00 | −1 000.00 | −45.45 | 2 800.00 | −400.00 (−14.29 %) | 600.00 (21.43 %) |
| Travel | 120.00 | 0.00 | 0.00 | 500.00 | −380.00 | −316.67 | 120.00 | −380.00 (−316.67 %) | 0.00 (0.00 %) |
| **Budget** | **5 380.00** | **1 000.00** | **3 200.00** | **7 760.00** | **−2 380.00** | **−44.24** | **12 517.20** | **4 757.20 (38.01 %)** | **7 137.20 (57.02 %)** |

Exposure 4 200.00. The estimate prices, cost subtotals and estimate margins
are exactly `fixtures/calc/s1-quote.json`'s engine numbers. The same file
pins the pure module (`test_budget_tracking.py`) and the API end to end
(`test_the_tracking_view_reproduces_the_golden_fixture`).

## Mutations (each a real FAIL line, each reverted)

| # | Mutation | Caught by | FAIL line |
|---|---|---|---|
| 1 | exposure uses `committed_gross` | fixture (e) and others | `line l.exposure: expected 700.00, got 1000.00` |
| 2 | a voided entry still counted | fixture (i) only | `line l.committed_open: expected 600.00, got 200.00` |
| 3 | `remaining` ignores the ETC | fixtures (h), (k), named test | `line l.remaining: expected 200.00, got 600.00` |
| 4 | negative open amount on over-invoicing | fixture (g), named test | `line l.committed_open: expected 0.00, got -50.00` |
| 5 | percent on a zero estimate raises instead of None | fixture (k), named test | `decimal.DivisionByZero` in `test_a_zero_estimate_gives_no_variance_percent` |
| 6 | a locked budget refuses an actual | the both-ways lock test | `{"detail":"Estimate was locked on 2026-11-24 …` |
| 7 | actual linked to another budget's commitment accepted | the cross-budget test | 201 instead of 409 |
| 8 | import applies a row the preview marked duplicate | the hold-back test | `assert 1 == 0` |
| 9 | a voided actual does not reopen its commitment | the reopen test | `the commitment did not reopen` |
| 10 | top sheet computes section variance in the browser | the top-sheet test | `expected '-240.00' to be '-390.00'` |

Mutation 5 fails by the exception the prompt specified ("raises instead of
None"), raised in the module under test, not in setup.

## Defaults I chose (all recorded in SCOPE)

- **D83 default rule** `max(estimate − actual, committed_open)` — as the
  prompt gave it; **[Guessing], your decision**.
- **D87 unallocated `remaining`**: an unallocated *open order* adds its open
  amount to `remaining` so `EFC = actual + remaining` holds at every level.
  The prompt said "remaining is 0 for them"; literally that leaves an
  unallocated open order out of the EFC. For an unallocated *invoice* (your
  acceptance step 2) both readings agree.
- Duplicate key `(payee, reference, amount)`, not the date; voided entries
  never count as duplicates; cancelled commitments neither.
- Cancel is refused once anything is invoiced against an order (close it);
  void of a commitment is refused while live invoices point at it.
- Duplicating a budget copies no entries **and no ETCs**.
- The actuals export leaves voided entries out; dates are ISO; the import
  also reads `24.11.2026`.
- The amount CHECK is on the models as well as in the migration: the
  real-DB suite builds its schema from the models, and the constraint test
  found it missing there (`alembic check` does not compare CHECKs).
- Router tag `budgets` (the surface test keeps the tag set closed);
  path params `{commitment_id}` / `{actual_id}` so the isolation suite
  points each at a real row of the other company.
- The spend bar's widths are computed from the API's numbers in the
  browser — a picture, it prints no figure. Flagged against rule 2 so you
  can disagree.

## Counts and floors

| Suite | Before B1 | After B1 | Environment |
|---|---|---|---|
| API (all) | 2459 passed, 30 skipped | **2626 passed, 30 skipped** | `python:3.11` image, PostgreSQL 15 + Redis, `CI=1` |
| API `-m realdb` | 1133 passed | **1244 passed**, 0 skipped | same |
| Web | 650 passed (A2 close) | **668 passed / 59 files** | vitest; `pnpm ci:web` green from HEAD |

**CI floors raised (rule 17e):** passed-count **2410 → 2580** (1.8 % under
2626 local, ~2.8 % under the ~2653 CI usually counts); real-DB **1095 →
1210** (2.7 % under 1244).

## Server

`docker-compose.server-test.yml` and `.env.server-test.example` are
**unchanged**. Migration 0016 runs on API start (`alembic upgrade head` is
in the API service's command). Nothing beyond `git pull`, `build --no-cache`
and `up -d`.

## SCOPE diff (to mirror to the Project)

See commit `e41f666` (`git show e41f666 -- docs/SCOPE.md`): eight new rows
D81–D88 after D80; D77's "26 budget operations" → "every budget operation
(26 at A2, 43 since B1's cost entries)"; in §13, three rows after A2 (B1
built, B2 next, B3 after B2 — not yet scoped) and a paragraph before "B's
scope, as recorded at the end of A2".

## Verification, three tiers

**Run and observed** (declared environment above):
- API full suite **2626 passed, 30 skipped** (2459 before B1; the 30 skips
  are the pre-existing docker-CLI / `ss` ones in `test_server_test_*`).
- `-m realdb` **1244 passed, 0 skipped** (1133 before). New real-DB tests:
  all of `test_budget_tracking_api.py` (65, including the migration
  up/down, the CHECK firing, isolation, permissions, CSV, lock both ways),
  plus B1's additions to `test_company_isolation.py` (17 operations × the
  isolation parametrisations) and `test_budgets_api.py`'s staff walk.
- Pure, no DB: `test_budget_tracking.py` (39) and `test_budget_csv_actuals.py` (12).
- Migration 0016: `alembic upgrade head`, `alembic check`, `alembic
  downgrade base` on a scratch database; and the up/down test.
- Web: `pnpm ci:web` green from `git archive HEAD` at `c7a785a` (lint,
  typecheck, tests, build on Linux/node:20); vitest **668 passed / 59
  files** (650 before; 18 new in `cost-tracking.test.tsx`).
- All ten mutations above, each with its FAIL line, each reverted and the
  suite re-run green.

**Statically checked only:** `tsc --noEmit` and `next lint` on the new
screens; the generated types are regenerated from the OpenAPI document.

**Not checked:** the screens in a real browser against a running stack — no
click-through of the dev or server-test instance was done; the acceptance
walk is yours. CI on GitHub for the pushed commit: see the closing section
of the INDEX notes for the run result.
