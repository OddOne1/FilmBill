# B2c-2 report — gear bookings: availability, overbooking with alternatives, dependencies

Prompt: `docs/prompts/36-filmbill-B2c2-gear-bookings.md`. Built 2026-11-27.

**In plain words.** Gear is now booked on the production calendar the way
crew is: on a strip's panel, **Add gear** shows for every device how many
you **own**, how many are **available**, **booked** and **scheduled** on the
strip's days (the lowest day counts; hover shows each day). A camera brings
its `always` dependencies with it (2 batteries per camera × 2 cameras = 4);
`ask` dependencies are offered in a small popup first. When another project
already holds the gear, the booking is **saved anyway** and a popup says who
holds it and offers the camera's **alternatives** with their own
availability (**Swap**), **Book anyway** with a note, or **Cancel**. Bookings
start **scheduled** and are marked **booked** by hand. There is a Gear tab on
the project with the total weight, a **Planner** on the Gear page and a
**Bookings** tab on each device. No prices anywhere. On the server: pull,
build, up — migration **0031** runs by itself.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `4015a84` | the prompt file and its INDEX line |
| 1 | `e25fe72` | the pure availability rule and its fixtures, migration 0031, the loader |
| 2 | `d307f05` | the API: availability, bookings, preview, dependencies, warnings, removal, summary |
| 3 | `e36bbec` | screens: strip Gear section, picker, both popups, badge, Project → Gear, planner, Bookings tab |
| 4 | `a7c3a71` | SCOPE D136–D141, §4.5 note, §13 row |
| close | (closing commit) | INDEX, this report, CI floors |

## Step 0 answers

1. **State.** Worktree `Claude_MS/filmbill-b2c2-gear-bookings-b7f0fd` at
   `74eb664` = `origin/main`, clean but for the copied prompt. Prompts 34
   and 35 on `origin/main` (`docs/reports/B2b2-report.md`,
   `docs/reports/R1-B2c1-report.md` present); CI run 46037516836 on
   `74eb664` green on all five jobs. Baselines (filmbill-api image,
   dev-stack Postgres 15 + Redis 7 on `filmbill_default`, `CI=1`, from a
   `git archive` export): API **3285 passed, 30 skipped**; `-m realdb`
   **1832**; web **983 tests / 82 files** (local vitest). Floors **3190** /
   **1780**.
2. **The crew pattern (B2c-1), mirrored.** `crew_assignments` (project,
   strip, party, status `scheduled | booked | declined | cancelled`, note,
   booked on/by; one open per strip and person) + `crew_assignment_days`
   (`is_active` for unticked). Clashes are computed per request from
   `occupations()` and never stored; the strip panel's `StripCrew` section
   (picker, editor, bulk book), Project → Crew renders the same component
   per strip, the availability grid was an inline table in
   `contacts/crew-availability.tsx`. **Shared now:** the strip's default
   days (`crew_assignment_service.default_dates`, "every day of the strip
   that is not off"), `say_dates`, the warning-sentence style, and the
   planner grid — extracted to `components/shared/planner-grid.tsx`, which
   Contacts → Crew → Availability now uses too (its tests unchanged and
   green). Gear does **not** share crew's status path: gear has no
   `declined`.
3. **The device after B2b-2.** `devices.quantity_owned` (what planning
   books against), units independent; `device_associations` (parent,
   associated device, `quantity_per_parent ≥ 1`, `booking_mode always|ask`,
   `pricing included|charged`, `show_on_documents`, note,
   `origin_association_id`, active flag; loops refused by
   `device_links.py`); `device_alternatives` (position, note, `price_hint`,
   one-directional); `weight_kg NUMERIC(8,3)`, size in cm; one picture with a
   160 px thumbnail through the token proxy (`routers/devices.thumbnail_url`).
   Keys: `devices.view` = everyone but the tax advisor (staff included),
   `devices.edit` = owner, admin, producer.
4. **Strips' days.** A strip (`project_blocks`) owns at most one active
   `project_days` row per date with its own type, so `off`/`travel` are
   per-day overrides; a date with no row is a gap. The project has
   `start_date` and an optional `end_date`. Gear days are dates inside the
   strip's range.

## What was built

* **Pure rule** `billing/planning/gear_availability.py`, inside the import
  boundary (the probe now imports `billing.planning`; the guard proving the
  probe can fail is unchanged and green). Golden fixtures
  `tests/fixtures/gear_availability/`:
  (a) owned 3, nothing → 3; (b) 2 booked + 1 scheduled → 0, not overbooked;
  (c) 2 + 2 → −1, shortage 1; (d) a cancelled 2 ignored → 1;
  (e) editing b1 (2) with b2 (1) booked → 2 free for b1;
  (f) 6 and 8 July 2 free, 7 July −1 → worst day 7 July, shortage 1;
  (g) owned 0, 2 scheduled → −2, shortage 2.
* **The three-device example** (real DB, `test_gear_availability_db.py`):
  Camera A 3 owned, 2 booked on "Shoot" 6–8 July and 2 scheduled on "Dreh"
  7–9 July → 1 / −1 / −1 / 1, worst day 7 July, short 1. Battery 6 owned, 4
  scheduled, 2 cancelled → 2 / 2 / 2 / 6. Light 2 owned, 2 booked on 6–7
  July (8 July unticked) and 1 on a removed strip → 0 / 0 / 2 / 2, not
  overbooked. The same plain data through the pure module gives identical
  numbers.
* **Migration 0031** `gear_bookings`, `gear_booking_days` (see D136).
* **Routes** (`routers/gear_bookings.py`): `GET /devices/{id}/availability`,
  `GET /gear/availability`, `GET /projects/{id}/gear`,
  `GET /devices/{id}/bookings`, `POST /projects/{id}/blocks/{block}/gear`,
  `POST …/gear/preview`, `PATCH /projects/{id}/gear/{booking}`,
  `POST …/gear/{booking}/status`, `POST /projects/{id}/gear/status`,
  `POST …/gear/{booking}/remove`. No DELETE.
* **Audit actions:** `gear_booking.created` (one per device added directly;
  the dependencies added with it as one event on the parent listing the
  rows), `gear_booking.updated` (quantity, swap with device before/after,
  overbooking note; recalculated followers as one event; "kept" dependents),
  `gear_booking.status_changed` (single; bulk = one event on the project
  with counts), `gear_booking.days_set`, `gear_booking.removed` (with the
  dependents choice; cancelled dependents as one event with counts).
* **Screens** as in the summary; see D136–D140.

## Defaults chosen (say if any is wrong)

1. **Followers follow the quantity only.** A parent's later change of days
   or status does not move its dependents; tick them for "Mark selected as
   booked". A dependent's own quantity **or days** set by hand stops it
   following.
2. **The link's quantity per parent is copied onto the booking** when booked
   (`quantity_per_parent`), so a link changed later never changes a booking
   by itself (the "dependencies changed, review?" notice is still open).
3. **Cancel and Remove are one action** ("Cancel booking", optional note):
   the stored status is the same `cancelled`.
4. **A swap keeps the booking's dependents** and adds none of the new
   device's.
5. **Cancel in the overbooking popup** cancels the booking and the
   dependencies added with it (note "Not booked: overbooked").
6. **Counts are pieces** (2 cameras + 4 batteries = 6); "items without
   weight" counts pieces too.
7. **The picker's numbers cover the strip's first to last day**, off days
   included; the warning on save uses the booking's own days.
8. **A preview endpoint** (`…/gear/preview`) does the dependency step without
   storing, so the popup shows the API's quantities; the client never
   multiplies.
9. **The `ask` popup opens only when there is an `ask` dependency**; with
   only `always` ones the booking is made at once and the notice names them.
10. **Staff** see bookings, the planner and the Bookings tab (they hold
    `projects.view` and `devices.view`); no prices are in any of it.
11. **An archived device cannot be booked** (sentence); dependencies to
    archived devices are skipped.

## Verification

### Run and observed

- API suite on the final API commit (`d307f05`; Parts 3, 4 and the close change no file under `apps/api`): **3371 passed, 30 skipped**
  (3285 / 30 before). `-m realdb`: **1902 passed, none skipped** (1832
  before). Environment: `filmbill-api:latest`, the dev stack's Postgres 15
  and Redis 7 on `filmbill_default` (each run its own database and Redis
  db), `CI=1`, from a `git archive` export of the commit.
  Part 1 alone: 3305 passed / 1837 real-DB — plus one failure of
  `test_rate_limiter_is_live` that was two concurrent runs sharing Redis
  db 0 [Likely]; it passed alone on the same commit (16/16), and every run
  since uses its own Redis db.
- New real-DB tests, run as `realdb` (none skipped): `test_gear_bookings.py`
  (35), `test_gear_availability_db.py` (5), plus 10 new isolation cases × 3
  checks in `test_company_isolation.py`. Pure: `test_gear_availability.py`
  (16).
- Web: **1002 tests / 83 files**, lint, typecheck and build via
  `pnpm ci:web` (Linux, node 20, clean export of HEAD). New:
  `projects/__tests__/strip-gear.test.tsx` (19).
- Mutations, each a real FAIL line, each reverted:
  1. a cancelled booking counted → `FAILED test_gear_availability.py::test_golden_fixture[d-cancelled-ignored] - AssertionError: Owned 3, booked 2 and a cancelled booking of 2: the cancelled one never counts, 1 available.` (and the three-device DB test).
  2. the edited booking counts against itself → `FAILED test_gear_availability.py::test_golden_fixture[e-edited-booking-not-counted-against-itself]` and `FAILED test_gear_availability_db.py::test_editing_a_booking_does_not_count_it_against_itself - assert (-1, …, True) == (1, …, False)`.
  3. the period uses the first day → `FAILED test_gear_availability.py::test_golden_fixture[f-middle-day-governs]` and `FAILED test_gear_availability_db.py::test_the_three_device_example_gives_the_numbers_written_out - assert (1, 2026-07-06, 0) == (-1, 2026-07-07, 1)`.
  4. overbooking refuses the save → `FAILED test_gear_bookings.py::test_overbooking_warns_names_who_holds_it_offers_the_alternatives_and_saves - AssertionError: {"detail":"Not enough free."}` (and the swap and book-anyway tests).
  5. the dependency step follows a second level → `FAILED test_gear_bookings.py::test_booking_follows_one_level_only_even_through_a_chain_or_a_loop - AssertionError: assert ['Battery', '...A', 'Charger'] == ['Battery', 'Camera A']`.
  6. an `ask` link added without being chosen → `FAILED test_gear_bookings.py::test_a_new_booking_gets_every_strip_day_but_the_off_day_and_its_always_dependencies - AssertionError: assert {'Battery', '... A', 'Filter'} == {'Battery', 'Camera A'}`.
  7. a parent removed without the question → `FAILED test_gear_bookings.py::test_removing_a_parent_first_asks_about_its_dependents - assert True is False`.
  8. web: the picker computes availability in the browser (from the first day) → `FAIL strip-gear.test.tsx > the strip panel: Gear > the picker shows owned · available · booked · scheduled as the API counted them, with the days on hover`.
  Mutation 1 first did NOT fail: the per-status sums ignored cancelled a
  second time. The rule was changed so the one filter is the only guard
  (`available = owned − Σ counted`), then the mutation failed as above.
- Found and fixed on the way: two surface-guard tests (`test_devices_api`
  staff walk, `test_project_calendar_api` calendar surface) listed every
  route of their area; Part 2 was amended so it is green on its own. Two new
  tests were order-dependent (rows made in one transaction share a
  timestamp); the project summary now orders parent then its dependents.
- `alembic upgrade head`, `alembic check`, `alembic downgrade base` on an
  empty database: clean.

### Statically checked only

- The generated web types (`pnpm gen:api` equivalent: `dump_openapi` in the
  API image, then `openapi-typescript`) — CI's drift job is the real check.

### Not checked

- The screens in a real browser (only component tests with the API's
  shapes).
- Migration 0031 on a copy of the server's database.

## CI floors (rule 17e)

API passed-count floor **3190 → 3270** (3371 local, 3.0 % under); real-DB
floor **1780 → 1845** (1902, 3.0 % under). Both in `.github/workflows/ci.yml`
with their history.

## On the server

Nothing beyond the usual: `git pull`, `build --no-cache` for
`api worker email_worker beat web`, then `up -d` (two separate commands).
Migration **0031_gear_bookings** runs by itself on start; it adds two empty
tables and changes no existing row.
