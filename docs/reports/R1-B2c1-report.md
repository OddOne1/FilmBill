# R1 + B2c-1 report — crew roles and rates, crew on the calendar

Prompt: `docs/prompts/35-filmbill-R1-B2c1-crew-roles-rates-assignments.md`. Built 2026-11-27.

**In plain words.** A crew person's **roles** are now the catalog's labor
items ("DoP", "Gaffer", "Sound"), and each role can carry the person's **own
day rate**. Where they have none, the catalog's cost per day for that item is
the **typical rate**. The Crew tab filters by role and by a rate range on the
rate that applies (own, else typical), sorts by it and shows it. On the
calendar, a strip's panel has a **Crew** section: **Add crew** picks people
while showing whether they are free on the strip's days; they start as
**scheduled** and are marked **booked** by hand once they confirm (with a
note such as "by phone"), one by one or several at once. A clash with another
strip or a person's own "not available" period is a **warning with a
sentence**, never a refusal. Staff still see the calendar and no crew on it.
On the server: pull, build, up — migrations **0029** and **0030** run by
themselves.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `4ccab1e` | the prompt file and its INDEX line |
| 1 | `ed3cd89` | roles and rates: migration 0029, effective rate, filters, import/quick add/export, Roles & rates on the person page, Crew tab rate filter and column |
| 2 | `b279944` | assignments: migration 0030, status path, bulk booking, clashes, availability, unavailability |
| 3 | `2c893eb` | screens: strip Crew section and picker, strip badge, Project → Crew, Contacts → Crew → Availability, Not available on the person |
| 4 | `6ba168f` | SCOPE D130–D135, §4.5 note, §13 rows |
| 3 fix | `36025cd` | a day left outside a shortened strip is dropped on the next save |
| close | (closing commit) | INDEX, this report, CI floors 3100 → 3190 and 1690 → 1780 |

## Step 0 answers

1. **State.** Worktree `Claude_MS/happy-northcutt-b8bdfb` at `96c2f39` =
   `origin/main`, clean but for the copied prompt. Prompts 32, 33 and 34 on
   `origin/main`; CI run 45996328564 on `96c2f39` green on all five jobs;
   `docs/reports/B2b2-report.md` present. Baselines (filmbill-api image,
   dev-stack Postgres + Redis, `CI=1`, from a `git archive` export): API
   **3191 passed, 30 skipped**; `-m realdb` **1740**; web **957 / 80 files**
   (B2b-2's figure). Floors **3100** / **1690**.
2. **Labor items.** `items.kind = 'labor'` (the fixed enum in
   `billing/catalog/kinds.py`). Rates are `item_rates` rows: `rate_type`
   `bill | cost` × `period_unit` (`day` for a day rate), **unique per item,
   type and period, with no dates and no price lists** (A1 left both out) —
   so "the current typical cost rate" is simply the item's `cost`/`day` row.
   A company unit such as "Drehtag" maps to period unit `day`, so it is the
   same row. Categories are free labels (`item_categories`, kind-tagged; the
   seed makes one per kind). Keys: `catalog.view` = everyone but the tax
   advisor (staff included — they read cost rates in the catalog, D124's
   open point); `catalog.edit` = owner, admin, producer; `budgets.view` =
   owner, admin, accountant, producer.
3. **Roles before R1.** Roles were the party's `tags` (`VARCHAR(64)[]`):
   quick add (`POST /crew`, `RolePicker` with suggestions from
   `GET /crew/roles` = tags in use + labor item names), the CSV `roles`
   column split on `|`, the export joining them, and the Crew tab's role
   filter (`?role=`, a case-insensitive `unnest(tags)` match) — free text,
   no link to the catalog.
4. **The calendar (B2a).** `project_blocks` (a strip: type, label, range,
   location, default call/wrap) owns at most one active `project_days` row
   per date; each day has its own type, so `off` and `travel` are
   per-day overrides; a date in the range with no row is a gap. The side
   panel is `calendar/strip-panel.tsx`, opened from `days-tab.tsx`; the
   Crew section now sits at the panel's end (a `children` slot).
5. **Crew and `parties.view`.** Crew are parties with `is_crew`; `/crew`
   lists them. `parties.view` = owner, admin, accountant, producer;
   `parties.edit` = owner, admin, producer. **Staff hold neither** (D111
   open), and **every holder of `parties.view` also holds `budgets.view`** —
   so today nobody can see crew but not rates; the rate gate was tested by
   removing the producer from the real `PERMISSIONS["budgets.view"]`.

## Migrations

- **0029_party_crew_roles** — `party_crew_roles`.
- **0030_crew_assignments** — `crew_assignments`, `crew_assignment_days`,
  `crew_unavailability`.

`alembic upgrade head`, `alembic check` (no drift), `downgrade base` and
`upgrade head` again: run and observed on a scratch database.

## Audit actions

`party_crew_role.added | updated | removed` (rate before/after; `from_tag`
on a converted tag, with `party.updated` for the tags) ·
`crew_assignment.created | updated | status_changed | days_set | removed`
(one `status_changed` event with the counts for a bulk change, on the
project) · `crew_unavailability.created | updated | removed`.

## Verification

### Run and observed

- API suite on the final commit: **3285 passed, 30 skipped** (3191 /
  30 before). `-m realdb`: **1832 passed, none skipped** (1740 before).
  Environment: `filmbill-api:latest`, the dev stack's Postgres 15 and Redis 7
  on `filmbill_default`, `CI=1`, each run from a `git archive` export.
  Parts 1 and 2 were also run on their own commits (Part 1: 3228 passed /
  1776 real-DB; Part 2: 3285 passed / 1832 real-DB).
- New real-DB test files, every test in them `realdb` and run in CI (none
  skipped): `test_crew_roles.py` (22), `test_crew_assignments.py` (24); plus
  21 new isolation cases × 3 checks in `test_company_isolation.py`.
- Web: **980 tests / 82 files**, lint, typecheck and build via
  `pnpm ci:web` (Linux, node 20, clean export of HEAD). New:
  `contacts/__tests__/crew-roles.test.tsx` (7), `projects/__tests__/strip-crew.test.tsx` (16).
- Mutations, each a real FAIL line, each reverted:
  1. effective rate as `COALESCE(typical, own)` → `FAILED test_crew_roles.py::test_the_rate_range_uses_own_else_typical - AssertionError: assert ['Anna Huber'..., 'Cleo Lang'] == ['Ben Ohr']` (and the SQL/response agreement and sort tests).
  2. own rate sent without `budgets.view` → `FAILED test_crew_roles.py::test_without_budgets_view_no_endpoint_carries_a_rate - AssertionError: assert [('own_day_ra...e', '450.00')] == []`.
  3. role accepted on a non-crew party → `FAILED test_crew_roles.py::test_a_role_is_refused_on_a_party_that_is_not_crew - assert 201 == 422`.
  4. a cancelled assignment still clashes → `FAILED test_crew_assignments.py::test_declined_and_cancelled_assignments_do_not_clash - AssertionError: assert [{'assignment...': None, ...}] == []`.
  5. a day outside the strip accepted → `FAILED test_crew_assignments.py::test_a_day_outside_the_strip_is_refused - assert 201 == 422`.
  6. bulk booking books a declined one → `FAILED test_crew_assignments.py::test_bulk_booking_books_the_scheduled_and_leaves_the_declined - assert 3 == 2`.
  7. web: the picker's rate column drawn without `rates_visible` → `FAIL strip-crew.test.tsx > the strip panel: Crew > shows no rate and no rate filter without budgets.view`.
- The three-person numbers. Rates (DoP typical 600): Anna own 650, Ben
  typical 600, Cleo own 520 (and Gaffer own 450). Range 560–640 for DoP →
  **Ben only** (a typical-rate filter would take all three); max 600 → Ben
  and Cleo; any role 400–460 → Cleo. Availability 6–12 July: Anna booked on
  6, 7, 9, 10 (8 is the strip's `off` day); Ben scheduled 11–12 on Doku
  Herbst; Cleo away 6–7 → free days 3 / 5 / 5; DoP with max 620 → Cleo
  (600 typical) only. Clash dates: Anna's Shoot days 9 and 10 July against
  Dreh 9–12 July; Cleo's vacation 9–11 July against her Shoot days → 9 and
  10 July.

CI floors: **3100 → 3190** (2.9 % under 3285) and **1690 → 1780** (2.8 %
under 1832), rule 17e.

### Statically checked only

- `tsc --noEmit` and ESLint on the whole web app (also inside `ci:web`).

### Not checked

- The screens in a real browser (the picker, the badge, the grid, the
  person page sections) — component tests only.
- CI on GitHub until the push (reported below).
- Mathias's acceptance on the server.

## Defaults chosen

- **Roles list** = active labor items; a name match is case-insensitive on
  the German name, then the English one; two items of one name → the older.
- **Typical rate** = the item's `cost` rate with period unit `day`.
- **The export's `day_rate`** is the **own** rate of the person's first role
  (so a re-import gives it back), German decimal comma; the column is left
  out for a member who may not see rates, and the import then ignores it
  (listed under ignored columns).
- **Rate filter/sort/write without `budgets.view`** → 404 (the prompt's
  "missing permission answers 404"), not a silent ignore — a silently
  applied filter would leak rates through who appears.
- **A converted tag leaves the tags**; a matched role in quick add/import is
  not also stored as a tag.
- **An assignment's day rate** is copied when made, and copied again when
  the role changes without a rate being given.
- **Remove** = `cancelled` from scheduled, booked or declined; a cancelled
  person can be added to the strip again. Only one non-cancelled entry per
  strip and person (a declined one must be removed before re-adding).
- **`cancelled` via the status endpoint** only from booked (the prompt's
  path); from scheduled it is "Remove".
- **Bulk status** changes the allowed ones and lists the rest with the
  reason; one audit event on the project with the counts. **Creation** is
  one `crew_assignment.created` per person.
- **Clashes** ignore assignments on removed strips; the availability grid is
  limited to 92 days; a day's state is booked > scheduled > unavailable >
  free, with a clash flag when more than one thing is on it.
- **Times:** a day's effective call/wrap = the person's own, else the
  project day's, else the strip's default.
- **Project → Crew** is read-only and opens the strip on the calendar; the
  tab is drawn only with `parties.view`.
- **Availability view** is a "People | Availability" switch on Contacts →
  Crew, default 7 days from today (7/14/31).

## Flagged

- Every role that can see crew can also see rates today, so the
  "no rates without `budgets.view`" behaviour is proven by tests on a
  narrowed matrix and will only show on the server once D111 (or a custom
  role) separates the two.
- A strip moved or shortened after crew were added keeps their days as
  they were (they still count for clashes); the next save of that person's
  days drops the ones now outside the strip. No automatic shift of crew days
  with the strip — not asked for; open.
- Not built (D135): gear booking (B2c-2), confirmation by link, call sheet,
  hotels, locations, weather, the budget cost link, travel pay, overtime.
