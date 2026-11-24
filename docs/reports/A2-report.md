# A2 — Projects, production days and the Budget sheet: report

**In plain words.** FilmBill now has projects with a yearly project number and
a calendar of production days, and each project can hold budgets: a
spreadsheet-like sheet whose every number comes from the same calculation
engine that will price quotes and invoices. There is a ready-made "Example
(S1)" budget that reproduces SCOPE's S1 scenario exactly (net 12,517.20),
CSV export and import, locking, and a Projects section in the web app; staff
can see projects and days but not budgets.

Prompt: `docs/prompts/26-filmbill-A2-projects-days-budget.md`.
Commits: `d676093` (prompt + index line) · `02bc1e6` (Part 1) · `c12c6b7`
(Part 2) · `6686dce` (Part 3) · `22dbc88` (Part 4) · closing commit (CI floors,
INDEX notes, this report).

---

## Step 0 — answers

1. **Remote.** `git remote -v` → `git@github.com:OddOne1/FilmBill.git`
   (fetch and push). Matches; work continued.
2. **State at start.** `git status -sb`: branch
   `Claude_MS/filmbill-a2-projects-days-d1a691` at `3d4a32b` (= `origin/main`),
   clean apart from the gitignored `.env`. `git log origin/main..HEAD`: empty.
   The prompt file existed only as an untracked file in the main checkout, so
   it was copied into the worktree and committed with its INDEX line as the
   first commit (`d676093`), unchanged.
3. **Declared test environment and baselines.** API: the project's own
   `filmbill-api:latest` image (`python:3.11`) against the dev stack's real
   PostgreSQL 15 and Redis, with `CI=1`, run through a wrapper script that
   mounts the whole worktree. Baseline **2248 passed, 30 skipped** (226 s);
   `-m realdb` **931 passed**. Web: **624 passed / 55 files** — taken from A1's
   INDEX notes, not re-run before starting (after A2 the suite is 649, of
   which 25 are new, which agrees). CI floors were **2200** and **900**.
4. **A1's engine API.** `billing/calc`: `document_from_data`, `calculate`,
   `apply_vat`, `MAX_GROUP_DEPTH` (2), `PERIOD_UNITS`,
   `ADJUSTMENT_BASES/MODES/TYPES`; results carry per-line `net`/`cost_net`/
   `margin`/`margin_percent` and per-group `subtotal`/`cost_subtotal`/`margin`.
   **Adjustments act on the price only** — `_apply_adjustments` changes the
   group/document subtotal and never `cost_subtotal`. The engine contract was
   **not** changed; contingency/fees that must raise cost are modelled as `fee`
   lines (D78). One additive, pure helper was added: `percent_share(part,
   whole)` (with three tests, one asserting it changes nothing about a
   calculation), so the top sheet's shares are not computed in a service or
   the browser.
5. **How `/calc/preview` builds its input.** The router parsed the request into
   engine data and mapped the result back. That was extracted unchanged into
   `services/calc_mapping.py` (`calculate_document`, `response_from_result`);
   `routers/calc.py` now calls it, and the budget's `GET .../calculated` calls
   the same two functions — one mapping, not a copy.
6. **Number series.** `allocate_number(db, company_id, document_type,
   document_date, entity_type, entity_id, actor)` takes `SELECT … FOR UPDATE`
   on the series row, never commits, and uses a SAVEPOINT for collisions.
   Series are seeded per company from `DEFAULT_SERIES` (8) and `PARTY_SERIES`
   (2); A2 adds `PROJECT_SERIES` (1, with its own padding), so a company now has
   11.
7. **Permissions.** `middleware/company.py::require(key)` → `Membership`; a
   missing key answers **404**, not 403 (deliberate: no endpoint leaks that a
   thing exists). Keys are sets of roles in `services/permissions.py`.
8. **Reference registry / archive guard.** P1b's party archive refused archiving
   a party with active children; A2 added a registry of archive blockers in
   `party_service` (`register_archive_blocker`, `refuse_archive_with_references`)
   which `project_service` registers into, returning a `Referrer` named
   `"PRJ-2026-001 <name>"`. A test asserts the registration itself, not only the
   refusal.
9. **Catalog.** Items carry `rental_factor_group_id`; rates are `ItemRate`
   rows (`bill`/`cost` × period unit). `explain_factor(table, days)` returns
   `(factor, matched_days, rule)`; the budget uses it before the engine call
   and returns the explanation with the number.
10. **Web patterns.** Existing list/detail screens use `useEffect` + `api.*` +
    local state (not SWR, despite the stack list), `usePermissions()`, a
    `*-chrome.tsx` with read-only/no-access notices (17c), `data-testid`
    hooks, and `formatMoney/formatAmount/formatQuantity` from `lib/money.ts`
    (formatting only). A2 followed those patterns.

---

## What was built

### Migration
- `0015_projects_days_budgets` (down_revision `0014_catalog`): `projects`,
  `project_days`, `budgets`, `budget_sections`, `budget_lines`,
  `budget_globals`, `budget_adjustments`; backfills the `project` number series
  for every existing company (`ON CONFLICT DO NOTHING`); downgrade removes the
  series rows and allocations, then the tables. Verified up → `alembic check`
  clean → down → up → clean; also covered by a real-DB test on a scratch
  database.

### Permission keys (all [Guessing], D76/D77)
| Key | Roles |
|---|---|
| `projects.view` | owner, admin, accountant, producer, **staff** |
| `projects.edit` | owner, admin, producer |
| `budgets.view` | owner, admin, accountant, producer (**not staff**) |
| `budgets.edit` | owner, admin, producer |

### Audit actions
`project.created`, `project.updated`, `project.status_changed`,
`project_day.created`, `project_day.updated`, `project_day.removed`,
`budget.created`, `budget.updated`, `budget.duplicated`, `budget.locked`,
`budget.unlocked`, `budget.set_primary`, `budget.import_applied`,
`budget_section.created|updated|removed`,
`budget_line.created|updated|moved|removed`,
`budget_global.created|updated|removed`,
`budget_adjustment.created|updated|removed`. A day range and an import each
write **one** event with counts.

### API
- Projects (10 operations): `GET/POST /projects`, `GET/PATCH /projects/{id}`,
  `POST /projects/{id}/status`, `GET/POST /projects/{id}/days`,
  `POST /projects/{id}/days/range`, `PATCH /projects/{id}/days/{day_id}`,
  `POST /projects/{id}/days/{day_id}/remove`.
- Budgets (26 operations): list/create/example under a project; read, update,
  `calculated`, `export.csv`, lock, unlock, set-primary, duplicate; sections,
  lines (incl. duplicate), globals, adjustments (create, update, remove);
  `import/preview` and `import`.
- **No DELETE route** under either; asserted on the OpenAPI document. The
  OpenAPI snapshot is now 163 operations. `api.gen.ts` regenerated (pure
  additions).

### Web
`/projects` (list with search, status and client filters, New project),
`/projects/[id]` (Overview · Days · Budgets), `/projects/[id]/budgets/[budgetId]`
(Sheet and Top sheet). Sidebar entry "Projects" behind `projects.view`.
`lib/api.ts` gained `api.download` for the CSV. Without an edit permission the
buttons are not drawn and a sentence says why; a locked budget says it is
locked and how to unlock; staff opening Budgets get a sentence, not an empty
tab.

---

## The S1 numbers the example budget produced
Asserted by `test_the_example_budget_reproduces_the_s1_numbers` against
`tests/fixtures/calc/s1-quote.json` itself (real Postgres, through
`GET /budgets/{id}/calculated`):

| | Price | Cost | Margin | Margin % | Share of subtotal |
|---|---|---|---|---|---|
| 1100 Crew (after 5 % discount) | 3,990.00 | 3,060.00 | 930.00 | 23.31 | 34.43 % |
| 1200 Equipment | 4,680.00 | 0.00 | 4,680.00 | 100.00 | 40.38 % |
| 1300 Studio | 2,800.00 | 2,200.00 | 600.00 | 21.43 | 24.16 % |
| 1400 Travel | 120.00 | 120.00 | 0.00 | 0.00 | 1.04 % |
| Subtotal | 11,590.00 | | | | |
| Production fee 8 % | 927.20 | | | | |
| **Net** | **12,517.20** | **5,380.00** | **7,137.20** | **57.02** | |

The shares are each rounded on their own by the engine's `percent_share`, so
they add up to 100.01 % — the top sheet shows them as they come and does not
force them to 100.

Every section's price, cost, margin and share of the subtotal, and every
line, is compared with the fixture's own values, so the table cannot drift
from the file. The unit costs are the fixture's illustrative ones and the
budget's notes say so.

---

## Tests

| Suite | Before | After | Environment |
|---|---|---|---|
| API (all) | 2248 passed, 30 skipped | **2450 passed, 30 skipped** | `python:3.11` image, real PostgreSQL 15 + Redis, `CI=1` |
| API `-m realdb` | 931 | **1124** | same |
| Web | 624 / 55 files | **649 / 58 files** | macOS vitest, and Linux `node:20` via `pnpm ci:web` from `git archive HEAD` (lint, typecheck, test, build all green) |

New API files: `test_projects_api.py` (40 collected), `test_budgets_api.py`
(51 collected), plus 36 new cross-company cases in
`test_company_isolation.py` (10 project, 26 budget) and three `percent_share`
tests. Of the +202 API tests, +193 are real-DB (`pg_client`/`pg_db`); the
rest read the OpenAPI document or the engine. Whether they **ran, not
skipped, in CI** is in the CI section below.

Covered behaviour includes: no DELETE; number from the series inside the
transaction (rolled-back create consumes none); cross-company client = 404;
day uniqueness and re-adding a removed day; range with weekends and existing
dates; archive guard naming the project; migration up/down with the backfill;
S1 reproduction; depth 3 refused naming §5.4; positions distinct under four
concurrent real-thread inserts; ref resolution, dangling ref = 422, referenced
global not removable; `days:shoot` following the Days tab (0 → 3600.00 →
2400.00); catalog factor 2.8 for 6 days with its explanation; rate copied and
the "differs" report; lock/unlock/name-while-locked; duplicate numerically
identical and independent; CSV export, round trip into a duplicate (6 updates,
0 creates), preview == applied, stale preview refused, apply without a digest
refused (422, nothing written), unknown section code is an error, German
decimal comma; permission matrices; **a staff user walks all 26 budget
operations and gets 404 with no "cost" or "margin" in any body**, with a
second test asserting 26 is the whole surface.

### Mutations — each with a real FAIL line, each reverted

Part 1 (from the Part 1 run):
1. Cross-company client accepted → `201` instead of `404`.
2. Number allocated in its own transaction → `assert 2 == 1` on the counter.
3. Project archive guard removed → `200` instead of `409`.

Part 2:
1. Refs resolved after the engine call (engine given the stored numbers) →
   `test_a_line_takes_its_period_from_the_global_it_names` —
   `AssertionError: assert '1.0000' == '3.0000'`; and
   `test_the_shoot_days_global_follows_the_days_tab` —
   `AssertionError: assert '1200.00' == '0.00'`.
2. Locked budget accepts a line edit (`_editable` bypassed on the line PATCH) →
   `test_a_locked_budget_refuses_a_line_edit_and_says_why` — the PATCH
   returned 200 with the edited line.
3. Staff get `budgets.view` → `test_a_staff_user_gets_no_cost_and_no_margin_from_any_budget_endpoint`
   — `AssertionError: staff reached GET /projects/…/budgets: 200 …`.
4. Referenced global removable → `test_a_global_a_line_points_at_cannot_be_removed`
   — the remove returned the global with `"is_active":false`.
5. Example creator writes one rounded line per section instead of each line
   (net unchanged, so only the per-line assertions can catch it) →
   `test_the_example_budget_reproduces_the_s1_numbers` —
   `AssertionError: assert {'Crew', 'Equ...io', 'Travel'} == {'Alexa 35', ...io A', 'Taxi'}`.
6. Import ignores the preview digest → `test_a_preview_that_no_longer_matches_is_refused`
   — the apply returned `"applied":true` with a create the preview never showed.

Part 3:
1. The sheet sums a section's line prices in the browser →
   `budget-sheet.test.tsx > a section subtotal is the engine's, not the sum of its rows` —
   `AssertionError: expected '4,200.00' to be '3,990.00'`.

---

## Defaults chosen
- Permission matrices as above ([Guessing], D76/D77); a missing permission is 404.
- Project number `PRJ-{YYYY}-{SEQ}`, yearly, padding 3; the year is the
  **start date**'s.
- Project statuses `planning · active · wrapped · archived`; archived projects
  are hidden from the list unless asked for. Day types `prep · shoot · travel ·
  wrap · post · office · off`.
- A day range is at most 730 days; public holidays are not skipped (region-pack
  data, P7).
- Positions step by 10; concurrent inserts serialise on `SELECT … FOR UPDATE`
  of the budget row.
- Computed globals are counted on every read and serialised at 4 places like
  every stored quantity.
- Lock allows name, notes and set-primary; a duplicate is unlocked, not
  primary, named "<name> (copy)".
- CSV: UTF-8 with BOM, `;`, CRLF; match key `(section_code, description)`,
  case-insensitive; a blank column keeps the stored value; no deletes; unknown
  section code = error row.
- **Import digest required**, as the `expected_digest` query parameter (A1's
  catalog import takes `digest` as a form field — flagged for unification).
- Example budget: tax code = the company's standard tax class; globals
  `shoot_days` (`days:shoot`) and `prep_days` (`days:prep`).
- `post-auth-redirects.test.tsx` asserted `/projects` must not exist (a
  FreeFrame leftover); it now asserts FilmBill's `/projects` does, and keeps
  the `/share` checks.

---

## SCOPE diff (Part 4, `22dbc88`) — mirror this to the Project

```diff
diff --git a/docs/SCOPE.md b/docs/SCOPE.md
index 1c334f4..e1c2dc2 100644
--- a/docs/SCOPE.md
+++ b/docs/SCOPE.md
@@ -546,10 +546,12 @@ Final invoice:
 | Slice | Content | Where it comes from |
 |---|---|---|
 | **A1** | The pure calculation engine (`billing/calc`: lines, groups two deep, adjustments, discounts, cost and margin, VAT per tax category) and the **lean catalog**: items with kinds, categories, units, bill/cost rates, rental factor tables, a CSV import, and `POST /calc/preview`. | P1c catalog, plus P2's calc engine pulled forward — the engine has no document in it, so it does not need P2 |
-| **A2** | **Budget** (draft-only, phases Estimate / Committed / Actual, no numbering or finalize — D72), built on A1's engine. | P3, pulled forward: a budget is the first thing that wants the engine and the last thing that wants a document |
+| **A2** ✅ built 2026-11-24 (prompt 26) | **Projects and production days** (D75) and the **Budget** (draft-only, no numbering or finalize — D72), built on A1's engine: sections two deep, copied rates, globals incl. `days:<type>`, adjustments, top sheet, lock (D79), duplicate, CSV in/out and the S1 example (D80). Only the **Estimate** phase is built. | P3, pulled forward: a budget is the first thing that wants the engine and the last thing that wants a document |
 | **B** | Documents proper: quotes, invoices, credit notes, snapshots, finalize, numbering, revisions, PDF. | P2 as written |
 | **C** | The rest of §4: devices with serial numbers, kits, several price lists per recipient, resources and reservations. | P1c's remainder plus P7's planner — each of these changes what an item is (D69), so they come after B has settled |
 
+**B's scope, as recorded at the end of A2:** besides documents proper, the Budget's **Committed** and **Actual** phases (D72) — committed costs from purchase orders and supplier invoices, actuals from booked documents — and **cost-to-complete** built on them, **deliverables**, **scheduling**, and **devices**. Two of these overlap other rows and are listed here as the A2 prompt named them, not re-decided: scheduling touches P7's planner and P9's production workflow, and devices are in slice C above (D69). Which slice each finally lands in is an open question for Mathias.
+
 A1 and A2 ship before B. That order is deliberate: the engine and the catalog are what B and P3 both sit on, and getting them wrong later means changing every document that already used them.
 ---
 
@@ -663,6 +665,13 @@ Compliance sources: [Brandauer – E-Rechnung AT 2026](https://brandauer-rechtsa
 | D71 | **A rental factor lookup never rounds up: it takes the next *lower* listed row.** With rows at 1, 2, 3, 5 and 7 days, six days is billed at the **five-day** factor and four days at the **three-day** factor. Above the highest row the highest row applies, so a nine-day rental costs what seven days cost. Below the lowest row the factor is 1 — a table starting at two days has said nothing about one day, and 1 is the only answer that invents nothing. Zero days is refused rather than answered, because it is an unfilled field and not a cheap rental. **This is [Guessing] and an open question for Mathias**: it is the conservative reading — a gap in the table costs us money rather than the customer — but the other reading (six days bills as seven) is defensible and is what some rental houses do. It is therefore reported, not just applied: `GET /catalog/rental-factor-groups/{id}/factor` returns the factor, the row it came from and the rule it used, and the Rental factors screen prints that sentence, so the rule can be seen and disagreed with. Changing it is a one-line change in `explain_factor` plus its table of cases. | 2026-11-23 |
 | D72 | **A Budget is a draft-only object built on the same engine, and never a document.** It has phases — **Estimate**, **Committed**, **Actual** — and it has no number, no finalize, no PDF, no ledger posting and no revisions. It is where a production is planned and re-planned, which means it must stay editable to the end; the moment a budget could be finalized it would need a number series and immutability, and it would have become a quote with a different name. It uses `POST /calc/preview` like every other screen, so a budget line and a quote line are priced by the same code and a budget can be compared with the invoices that came out of it. A1 does not build it (A2 does); it is recorded here because it is the reason the engine is stateless rather than a method on a document. | 2026-11-23 |
 | D73 | **Catalog permissions: `catalog.view` for owner, admin, accountant, producer and staff; `catalog.edit` for owner, admin and producer.** Staff can read the price list and change nothing, because quoting work needs the rates and editing them is not the same job. The **accountant is deliberately out of edit**: a rate and a `default_tax_class` are snapshotted onto a document at finalize, so changing one is changing what the next invoice will say, which is a commercial decision and not a bookkeeping one. The tax advisor holds neither key — an outside party reads documents, not the price list. `catalog.view` is also what `POST /calc/preview` requires, so somebody planning a shoot can get numbers without the right to change prices. **[Guessing]** — the accountant's exclusion is the one most likely to be wrong in practice, and it is one line in `services/permissions.py`. | 2026-11-23 |
+| D74 | **A Budget is sections two deep, lines with copied rates, globals from a closed vocabulary, and adjustments — and everything is resolved before the engine is called.** Sections nest at most two levels, because that is the engine's group limit (D70, §5.4); a third level is refused with a sentence naming the limit rather than flattened. A line added from the catalog **copies** the item's bill and cost rate at that moment and keeps `item_id`, so a later catalog change never moves a budget silently — the API reports the difference instead (`catalog_difference`, shown as "≠ catalog") and nothing is written back either way. A line's factor is typed or comes from the item's rental factor table, looked up with A1's `explain_factor` (D71) before the call, and the explanation travels with the number. **Globals are named numbers, not formulas**: `manual` (typed) or `days:<type>` (counted from the project's active production days, read-only, never cached). A line points at one by key in its quantity or period; every reference is resolved to a number **before** the engine runs, a reference to a missing global is a 422 naming the key, and a global a line still uses cannot be removed. The budget is calculated through the same mapping as `POST /calc/preview` (`services/calc_mapping.py`), so a budget line and a quote line are priced by the same code; the top sheet's share of the subtotal comes from the engine (`percent_share`), not from the browser. | 2026-11-24 |
+| D75 | **A project has a yearly number and a list of production days; a day is unique per date and type, and is never deleted.** The number comes from a per-company series `project` (`PRJ-{YYYY}-{SEQ}`, yearly, padding 3), seeded for new companies and backfilled for existing ones, and is allocated inside the transaction that creates the project — a rolled-back create consumes no number. The year is the project's **start date**. A `ProjectDay` has a date, a type (`prep`, `shoot`, `travel`, `wrap`, `post`, `office`, `off`), a location, call and wrap times and notes. Two active days may share a date only with different types (a travel day and a shoot day on the same date is normal); removing a day sets it inactive, and a partial unique index (`WHERE is_active`) lets the same date and type be planned again afterwards while the removed row stays for the audit log. `POST /projects/{id}/days/range` adds a run of days in one transaction, optionally without weekends, and reports which dates already existed and which were skipped rather than creating fewer days than asked for in silence. Public holidays are **not** skipped: a holiday calendar is region-pack data (P7), and a guessed one would put Austrian holidays on a German shoot. Projects and days have no DELETE route; a project is archived by status. A party a project names as its client cannot be archived, and the refusal names the project. | 2026-11-24 |
+| D76 | **Project permissions: `projects.view` for owner, admin, accountant, producer and staff; `projects.edit` for owner, admin and producer.** Staff see projects and their days because a crew member needs to know which days are shoot days; editing the plan is the producer's job. The accountant reads and does not plan. The tax advisor holds neither. **[Guessing]** — the A2 prompt's proposal with staff kept on view; one line each in `services/permissions.py`. | 2026-11-24 |
+| D77 | **Budgets show cost and margin, and are therefore closed to staff: `budgets.view` for owner, admin, accountant and producer; `budgets.edit` for owner, admin and producer.** A budget is where cost rates and margins live, and a crew member reading what the production pays everybody else is the leak this rule exists to prevent — so staff get no budget endpoint at all (a missing permission answers 404 like everywhere else), and a test walks all 26 budget operations as a staff user and asserts that no response contains a cost or a margin. There is no crew-safe budget view yet; when one is wanted it is a separate endpoint without those fields, not a filter on this one. The accountant reads budgets (planned cost is bookkeeping work) and does not edit them. **[Guessing]**, one line each in `services/permissions.py`. | 2026-11-24 |
+| D78 | **A contingency or fee that must raise the cost side is a line of kind `fee`, not an adjustment — because adjustments move the price only.** Found in A2's Step 0: in A1's engine an adjustment (discount or surcharge, percent or amount, on a group or the whole document) changes the **price** and never `cost_subtotal`, so a "10 % contingency" entered as a surcharge raises what the customer is charged and leaves the cost — and therefore the margin — overstated. That contract was **not** changed for the Budget: the engine is shared with every document (D70), and a cost-side adjustment would change what B's quotes and invoices mean. A contingency that is a real expected cost is entered as an ordinary line of kind `fee` with its own price and cost; a production fee that is pure markup stays an adjustment. The Adjustments panel says this in a sentence. | 2026-11-24 |
+| D79 | **An approved estimate is locked, not finalized.** `POST /budgets/{id}/lock` stamps `locked_at`; while it is set every edit that changes a number — lines, sections, globals, adjustments, import — is refused with a 409 that says so and says how to unlock. The budget's **name and notes stay editable** ("Estimate v3, approved 24.11." is what people write on a frozen sheet), and so does marking it primary. Unlocking is an ordinary action with its own audit event and an optional reason: the lock prevents editing by accident, not editing at all, and the audit log is what makes an unlock visible afterwards. A duplicate of a locked budget is unlocked and not primary — that is how a new version of an approved estimate is started. There is still no number, no PDF and no revision (D72). | 2026-11-24 |
+| D80 | **A budget goes out and comes back as CSV, and every project can start from the S1 example.** `GET /budgets/{id}/export.csv` writes one row per line with the engine's own price, cost and margin (UTF-8 with BOM, `;` separator, CRLF — what Excel in a German locale opens without an import dialog). The import is A1's plan → apply: `POST .../import/preview` returns what each row would do and a digest, and `POST .../import` **requires** that digest and refuses, writing nothing, if the file now plans something different. A row is matched to a line by `(section code, description)`; it updates that line or creates a new one, never deletes, and a blank column keeps the stored value. An unknown section code is an **error row**, not a new section (a typo would otherwise become a permanent section), and the German decimal comma is accepted. `POST /projects/{id}/budgets/example` creates **"Example (S1)"** from §12's S1: Crew 1100 (DoP, Gaffer, 5 % section discount), Equipment 1200 (Alexa 35, lens set), Studio 1300, Travel 1400 and an 8 % production fee, with the computed globals `shoot_days` and `prep_days`. Its net is **12,517.20**, cost 5,380.00 and margin 7,137.20, and a test asserts those numbers and every section's against the A1 S1 fixture itself. The unit costs are illustrative, and the budget's notes say so. | 2026-11-24 |
 
 ## 17. Questions for the Steuerberater
 
```

---

## CI floors (rule 17e)
- API passed-count floor: **2200 → 2410**. Local count 2450 (1.6 % above);
  CI ran 27 more tests than local at A1, so CI is expected near 2477 (2.7 %
  above).
- Real-DB floor: **900 → 1095** (local and CI count 1124; 2.6 % above).

## Server (`docker-compose.server-test.yml`, `.env.server-test.example`)
Neither file changed. Nothing beyond the usual three commands: the `api`
container runs `alembic upgrade head` on start, so migration 0015 (and the
`project` number-series backfill) applies itself during `up -d`.

## Verification tiers

**Run and observed**
- Full API suite 2450 passed / 30 skipped and `-m realdb` 1124 passed, on the
  declared environment.
- Migration 0015 up → `alembic check` clean → down → up.
- All ten mutations above produced the FAIL lines quoted, and were reverted
  (files compared byte-for-byte with the saved originals).
- Web: `tsc --noEmit`, `pnpm lint`, 649 vitest tests on macOS; and
  `pnpm ci:web` (Linux, `node:20`, clean export of HEAD at `6686dce`): lint,
  typecheck, 649 tests and `next build` all green.
- `pnpm gen:api` output committed; diff is pure additions.
- CI on GitHub for the pushed commit — see below.

**Statically checked only**
- The web screens' look (layout, colours, the month strip) — typechecked,
  built and exercised by component tests, never opened in a browser against
  a running API.
- `api.download` (CSV export in the browser) — typechecked and built; no test
  drives a real download.

**Not checked**
- The acceptance walk on the server (http://192.168.1.100:3100) — that is
  Mathias's step.
- Behaviour with many hundreds of budget lines (the sheet re-fetches the whole
  sheet and calculation after every edit).

## CI

GitHub Actions run **45410891382** on `df24a41` (the closing commit, pushed
to `main`): **success**, all five jobs green —

| Job | Result |
|---|---|
| API tests | success |
| Migrations match the models | success |
| Web lint, typecheck, test, build | success |
| API types match the schema | success |
| Docker build (no push) | success |

Inside "API tests", the steps **"Guard against a gutted suite"** and
**"Real-database tests ran, not skipped"** both succeeded. By their own code
that means at least 2410 API tests passed, at least 1095 `-m realdb` tests
passed, and **no real-database test was skipped**. The exact counts are in
the job log, which this session could not download (GitHub answers 403 to
an unauthenticated log request) — so they are *inferred from the guards*,
not read. To see them: Actions → run 45410891382 → API tests → the two steps
above print "Tests passed: …" and "Real-database tests passed: …, skipped: …".
