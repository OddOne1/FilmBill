# B2a report — the project calendar (strips instead of a day table)

Prompt: `docs/prompts/30-filmbill-B2a-project-calendar.md`. Built 2026-11-25.

**In plain words.** The Days tab is now a real calendar: a full-width month
with each phase drawn as a bar ("strip") across its days, strips that may
overlap, and a side panel where any single day — shoot, travel, office, prep,
post — gets its own type, location, start and end time and notes. Your
existing days became strips by themselves, and every count (and every budget
global) is exactly what it was. Below the calendar a compact strip shows only
the booked days of the whole production; click one to jump there.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `ecc1491` | the prompt file and its INDEX line |
| 1 | `1417459` | migration 0021: `project_blocks`, `project_days.block_id`, the new indexes, distinct-date counting, the strip-limited global, the data migration |
| 2 | `3c0b99f` | the calendar and strip API, isolation matrix and staff walk |
| 3 | `6614f64` | the calendar screen |
| 3b | `4dab69e` | preview feedback: booked-days strip, per-day editing from the grid, no List view, time fields save without leaving the field |
| 3c | `0c28284` | preview feedback: an end date starts at its start date (every date pair) |
| 4 | `e3cd125` | SCOPE D99–D104, §11.3, §13 |
| close | (closing commit) | CI floors, INDEX notes, this report |

Each part was green on its own: the full API suite and `-m realdb` were run on
a `git archive` of parts 1, 2 and the final head; the web suite, typecheck and
lint before each web commit. Part 2's first run caught a real gap — the
central isolation matrix had no case for the eight new routes — and the fix
was folded into part 2 before anything was pushed (`3c0b99f` is the amended
commit; the green run is on it).

## Preview before deploying (Mathias's request mid-run)

A local preview ran from this branch before any merge: the API in Docker on
port 8190 against its own scratch database, the web dev server on 3190. The
database was seeded **at revision 0020** with the 1000m days as A2 stored
them, then upgraded with the real migration — so the preview showed exactly
what the server will show. Screenshots were sent; three changes came back
and were built (3b, 3c):

1. **No List view.** The old table is gone instead of kept behind a toggle
   (prompt 30 asked for the toggle; Mathias overruled it). Every day is
   edited in the calendar: click the day in the grid and the panel opens on
   it with its own type, location, start time, end time and notes.
2. **Booked-days strip** under the calendar (replacing the month bars above
   it): only planned days, grouped by month with a gap, `dd.mm.` under each,
   both colours on a date in two strips, click to jump to that month.
3. **End dates start at the start date.** Everywhere a pair of dates is
   entered, the second takes the first when focused empty (so the picker
   opens on that day) and cannot be earlier. Applied beyond the calendar
   too: project end (new and edit), Paid on (from the invoice date), the
   ledger's and audit log's "to" filters.

The preview also found one real bug: in Chrome, Tab inside a time field
moves between hours and minutes instead of leaving it, so a time that saved
only on blur could stay unsaved. Time fields now save shortly after a
complete time is entered (test added).

## Step 0 — answers

1. **State.** Branch `Claude_MS/filmbill-project-calendar-5e6146`, clean, 0
   ahead of `origin/main` (`36d552b`); remote
   `git@github.com:OddOne1/FilmBill.git`; `docs/reports/B1.2-report.md` is
   on `origin/main`; CI on `36d552b` green on all five jobs. **Baselines**
   (clean export, environment below): API **2793 passed, 30 skipped**;
   `-m realdb` **1385**; web **751 / 67 files**. **CI floors:** 2710 / 1345.
2. **The Days tab as built.** `app/(dashboard)/projects/[id]/days-tab.tsx`
   (range form, a 12-month mini strip, one table row per day with blur-save
   and a remove button), `projects-chrome.tsx` (labels). API:
   `GET /projects/{id}/days` (with counts), `POST /days`, `POST /days/range`
   (from/to/type/skip_weekends, one audit event with counts),
   `PATCH /days/{id}` (date and type movable), `POST /days/{id}/remove`.
   Index `uq_project_days_active (project_id, date, type) WHERE is_active`.
   Remove is **soft** (`is_active = false`, `project_day.removed`).
   `count_days_of_type` counted **rows** — `count()` of active rows of the
   type — which equalled distinct dates only because of that index.
3. **Consumers of `project_days`.** `budget_service.computed_value` /
   `sync_computed_globals` / `global_values` (every `days:<type>` global,
   synced before every calculation, read and CSV export);
   `create_example_budget` (the S1 example's `shoot_days`, `prep_days`);
   `routers/projects._day_counts` (the project list and detail `day_counts`)
   and `list_days`' counts; the web Days tab and the globals panel tooltip.
   The CSV export prints the synced global values, not days. Day types are
   shared with the web through the generated OpenAPI `ProjectDayType`
   literal (asserted equal to `PROJECT_DAY_TYPES` in `schemas/project.py`);
   the web's labels live by hand in `projects-chrome.tsx`.
4. **Date/calendar/drag helpers in the web.** None — the Days tab did its
   own UTC date arithmetic inline; `components/shared/sortable.tsx` is table
   sorting, not drag. No calendar dependency installed. **None added**: the
   grid is CSS grid and pointer events (`lib/calendar-dates.ts`,
   `calendar/month-grid.tsx`).

## What was built

**Migration `0021_project_blocks`** (runs at API start with
`alembic upgrade head`): table `project_blocks` (CHECK `start_date <=
end_date`, indexes on company, project, company+project); column
`project_days.block_id` (RESTRICT, indexed); `uq_project_days_active` is now
`(project_id, date, block_id) WHERE is_active`; new
`uq_project_days_loose (project_id, date, type) WHERE is_active AND block_id
IS NULL`; `budget_globals.source` 32 → 80; then the data migration (D101).
`alembic check` clean; upgrade → downgrade base → upgrade run.

**Audit actions:** `project_block.created` (also for duplicate, with
`duplicated_from`), `project_block.updated`, `project_block.moved`,
`project_block.resized`, `project_block.removed`, `project_block.days_set`
(one event with counts). No per-day events from strip actions (a test
asserts none). Permissions unchanged: `projects.view` reads (staff
included), `projects.edit` writes.

**Before/after counts of the example project (1000m)**, from the migration
test and again in the preview database: prep 3 → 3, shoot 8 → 8, travel
4 → 4, post 12 → 12; five strips: prep 30 Nov–2 Dec, shoot 3–4 Dec, shoot
12–14 Jan (travel–shoot–travel), shoot 22–28 Feb (travel, 5 shoot, travel),
post 1–12 Mar. Stored globals equal the counts after the migration.

## Defaults I chose (each in SCOPE where it matters)

- **Distinct-date counting [Guessing, open decision]** (D100), as the
  prompt said.
- **A removed strip's global falls back to all strips** of that type, with
  the warning sentence in the panel (D100). The stored source is not
  rewritten, so re-creating nothing brings it back; picking "All strips"
  clears the warning.
- **The globals picker only switches between "all strips" and one strip of
  the same type**; turning a typed number into a count, or shoot into prep,
  is refused with a sentence (a different global).
- **Bridged weekends stay gaps** in migrated strips (no row, counts
  nothing). New strips have a row on every date (weekends `off` or main
  type, default **same**).
- **Resize confirm** is `200` with `applied: false, needs_confirm: true` and
  the days listed — nothing changes until `confirm: true` — rather than a
  409, so the generated types carry the shape.
- **Changing a strip's main type** carries the days of the old main type;
  overrides stay.
- **Duplicate** copies types, location, times and notes at the same offsets.
- **Old `/days` endpoints**: their days have no strip, keep the
  `(date, type)` rule among themselves, and a strip day's **date** cannot
  be changed through `PATCH /days` (409 with a sentence); its type can.
- **Lanes:** three per week, then "+N".
- **Start/end time** on screen = the stored call/wrap times (no schema
  change); [Likely] what Mathias meant by "start and end for each day".
- **Calendar opens** on today's month while the production runs, otherwise
  on the first strip's month.

## Verification

**Environment (declared):** API in `filmbill-api:latest` (Python 3.11.15)
on the `filmbill_default` network, against the dev stack's Postgres 15 and
Redis 7 (host names `postgres` / `redis`), `CI=1`, CI's env vars, on a
`git archive` export of the commit. Web: macOS, Node 24, pnpm, vitest 4.

**Run and observed**
- API full suite on the final head: **2868 passed, 30 skipped, 0 failed** (2793 before); on part 1
  2813, on part 2 2868.
- `-m realdb` on the final head: **1455** passed, 0 skipped (1385
  before). **Every new API test is a real-database test** (they use
  `pg_db`/`pg_client`, auto-marked `realdb`): `test_project_blocks.py` 14,
  `test_project_calendar_migration.py` 6, `test_project_calendar_api.py` 31,
  and 16 new cases in `test_company_isolation.py` — so they run, not skip,
  in CI's real-database step.
- Web: **780 tests / 70 files** (751 / 67 before); typecheck and lint clean
  (two older warnings in catalog/parties untouched). `pnpm ci:web` (Linux,
  node 20, clean export of `e3cd125`): lint, typecheck, 780 / 70, build — green.
- `alembic upgrade head` → `alembic check` (no pending changes) →
  `downgrade base` → `upgrade head`.
- **Mutations, each a real FAIL line, each reverted:**
  - count adds overlapping same-type days twice (`count(id)` instead of
    `count(distinct date)`): 5 FAILED in `test_project_blocks.py`, e.g.
    `test_overlapping_shoot_strips_count_a_shared_date_once - assert 6 == 4`;
  - the migration changes a per-type count (filling bridged weekends with
    days): `FAILED test_existing_days_become_strips_and_every_count_stays -
    assert {'shoot': 4} == {'shoot': 2}`;
  - a strip accepts two rows on one date (`type` added to the per-strip
    index): `FAILED test_one_strip_refuses_a_second_row_on_a_date_even_of_another_type
    - DID NOT RAISE IntegrityError`;
  - resize drops days with notes without the confirm: `FAILED
    test_shrinking_over_a_day_with_notes_needs_confirm_and_changes_nothing -
    assert True is False`;
  - the screen computes the day counts itself: 3 FAILED in
    `project-days.test.tsx`, incl. "prints the API's counts, not the number
    of days it was sent".
- **In a real browser (Chrome, the local preview)**: the migration's five
  strips and counts; open a strip; mark a middle day as travel (strip and
  project counts moved: 7 shoot · 5 travel); **drag the right edge** over a
  day with a note → the confirm dialog named 27 February; **drag the whole
  strip** a week earlier → travel days and the note moved with it; **drag
  across empty days** → create "BTS shoot" overlapping the main shoot, in its
  own lane, counts 8 shoot · 5 travel (18 Feb shoot in one strip and travel
  in the other counts once each); staff: read-only calendar and panel with
  the sentence; booked-days strip and its jump; a travel day's start/end
  time saved (06:15 / 20:30, read back from the database); the end date
  following the start date.

**Statically checked only**
- The drag code paths on touch devices (implicit pointer capture is handled
  through `elementFromPoint`, not exercised on a phone).
- Whether Chrome's native date picker visually opens on the prefilled
  month — the value is set before the picker opens (observed), the picker
  popup itself is not visible to the automation.

**Not checked**
- The server: migration on the real 1000m data, Watchtower-free deploy.
- Safari and Firefox.

## SCOPE diff

D99–D104 added after D98; §11.3 gains one sentence; §13 gains the B2a row
and B2 is marked "builds on B2a". The exact diff is in commit `e3cd125`
(`git show e3cd125 -- docs/SCOPE.md`), to mirror to the Project.

## CI floors

API **2790** (was 2710), real-database **1415** (was 1345) — both 2.7 %
under the counts above (rule 17e).

## On the server, beyond `git pull`, `build --no-cache`, `up -d`

Nothing extra. Migration **0021_project_blocks** runs when the API starts
(`alembic upgrade head` in the compose command) and turns the existing days
into strips in the same step. Check afterwards: the 1000m Days tab shows
five strips and 3 prep · 8 shoot · 4 travel · 12 post.

## CI on `origin/main`

Merged by fast-forward: `origin/main` `36d552b..08e160a` (no merge commit;
nothing had landed on `main` meanwhile). CI run **45700421959** on
`08e160a`: **all five jobs green** — Web lint, typecheck, test, build ·
API tests · Docker build (no push) · Migrations match the models · API
types match the schema. In the API job, "Run tests", "Guard against a
gutted suite" (floor 2790), "Real-database tests ran, not skipped" (floor
1415, zero skips allowed) and "Alembic history is linear and applies to an
empty database" all passed. The exact CI pass counts are in the job log,
which the public API only serves to repository admins, so they are not
quoted here; the floors passing means at least 2790 and 1415.

This section is the commit after `08e160a`; its own CI run is the one
reported back to Mathias as the final one.
