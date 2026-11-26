# B2b report — crew and gear records

Prompt: `docs/prompts/31-filmbill-B2b-crew-and-gear-records.md`. Built 2026-11-26.

**In plain words.** There is now a **Crew** page: people you can book,
typed in quickly (only the name is required) or imported from a CSV file,
each one a person in your parties list with a "crew" tick and their roles.
There is a **Gear** page: each device has the quantity you own (what
planning will book) and, separately, the serial numbers of its units. The two
numbers may disagree, and the page says so in words. Mid-run you changed the
unit model: a unit can have its own price, a seller and an invoice number;
it is active, needs check, in repair, lost, retired or sold; and removing one
always says exactly what happened. Nothing is booked yet; that is B2c.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `b9110e3` | the prompt file and its INDEX line |
| 1 | `7c1cef8` | migration 0022 `parties.is_crew`; `/crew` API; Crew page; crew flag and roles on the party page |
| 2 | `b59e747` | migration 0023 `devices` / `device_units`; `devices.*` keys; `/devices` API; Gear pages |
| 2b | `2db6743` | your answers mid-run: unit price, six statuses, removal reason, seller (0023 changed in place, never deployed) |
| 3 | `7471c10` | SCOPE D105–D111, §3, §4.4 note, §13 |
| close | (closing commit) | CI floors, INDEX notes, this report |

Each part was green on its own: the full API suite and `-m realdb` ran on a
`git archive` of parts 1, 2 and 2b (part 3 is documentation only); the web
suite, typecheck and lint before each web commit.

## Your answers during the run (2026-11-26)

I stopped before writing SCOPE, as CLAUDE.md asks, because SCOPE §4.4
(2026-09-17) already described Device → Unit and differed from prompt 31 in
three places. Your answers, and what was built from them:

1. **Price per device and per unit.** The device keeps its purchase price;
   each unit may carry its own (a later purchase, a discount). Empty means
   the device's price applies (D108).
2. **Statuses** `active / needs check / in repair / lost / retired / sold`.
   Lost, retired and sold remove the unit, and **removing always needs what
   happened, in words** (a database CHECK ties status, date and reason
   together). The quantity question stays, ticked by default (D107).
3. **Seller per unit** for repairs, returns and insurance: a party on file
   or a typed name, plus the seller's invoice or order number. **Optional
   [my default]**, because units entered from a pasted list or a CSV often
   have no seller yet. A pasted list can carry one purchase for all of them
   (D108).
4. **Crew visibility** needs levels (names only / contacts / everything,
   incl. a birthday field that does not exist yet). That is a design of its
   own, so it is recorded as an open decision (D111). Until it is decided,
   the Crew page needs `parties.view`, so **staff do not see crew**. Prompt
   31's acceptance step 5 ("as Staff: crew … visible") does not hold. The
   Part 1 rule was followed instead.

## Step 0 — answers

1. **State.** Branch `Claude_MS/filmbill-b2b-crew-gear-95f2ef`, clean, 0
   ahead of `origin/main` (`515c367`); remote `git@github.com:OddOne1/FilmBill.git`;
   `docs/reports/B2a-report.md` on `origin/main`; CI run 45702131290 on
   `515c367` green on all five jobs. **Baselines** (clean export, environment
   below): API **2868 passed, 30 skipped**; `-m realdb` **1455**; web
   **780 / 70 files**. **CI floors:** 2790 / 1415.
2. **The party model.** One table, `type = organisation | department |
   person` (person-versus-organisation is the type). Names: one `name`, plus
   `trading_name`, `title`, `position`; **no first/last split**. `tags` is a
   Postgres `VARCHAR(64)[]` (JSON on SQLite for one test file), not yet
   filtered by any endpoint. **The number:** `allocate_party_number` gives a
   number only when `is_customer` (customer series) or else `is_supplier`
   (supplier series) is set, in the party's own transaction. **A party that
   is neither gets none**, so a crew-only person consumes no number and no
   design change was needed. Pickers: the web asks
   `GET /parties?role=customer` (projects) and `?role=supplier` (budget
   costs); `role` filters on that one flag. Permissions: `parties.view` =
   owner, admin, accountant, producer; `parties.edit` = owner, admin,
   producer; **staff and the tax advisor hold neither**.
3. **Catalog.** Items have a fixed `kind`; equipment is `equipment_rental`.
   `item_categories` are user labels, each hung on a kind, three deep, under
   `catalog.edit`. **A device could point at one, but I chose free text with
   suggestions [my default]**: those categories label price-list items, and
   a gear import would otherwise have to create price-list categories. The
   suggestions include the catalog's active equipment categories. A device
   can link to an equipment **item** (`catalog_item_id`); another kind is
   refused with a sentence.
4. **A new permission key.** One line in `services/permissions.PERMISSIONS`
   (role sets as data; a missing key denies everyone); served to the web in
   `GET /companies` → `permissions`; `require("key")` on the route answers
   404 without it (`middleware/company.py`). Web: a `permission` on the
   sidebar entry (`components/layout/sidebar.tsx`) hides it via
   `usePermissions().can`. The surface assertions are
   `tests/snapshots/openapi_paths.txt` (regenerated deliberately: 203 → 222
   operations), `test_openapi_surface.EXPECTED_TAGS` (`devices` added), the
   central isolation matrix (`/crew`, `/devices` added to the scoped
   prefixes; 19 new cases; `{device_id}` and `{device_unit_id}` rows in the
   other company) and a walk test per surface that counts its routes against
   OpenAPI.
5. **CSV import pattern (A1/A2).** A pure planner returns a plan value
   (rows with `create/update/error`), the preview endpoint returns it with a
   16-character digest over the actionable rows, and apply recomputes the
   plan from the same file, refuses a different digest with 409, then
   executes exactly the plan. `read_rows` (BOM, cp1252, `;`/`,`/tab sniffing,
   header aliases) now takes its columns as parameters, and both new imports
   use it. The ambiguous-dot rule is A2's `_GROUPED_WITHOUT_DECIMALS`.

## What was built

**Migrations** (run at API start with `alembic upgrade head`):
`0022_party_crew_flag` adds `parties.is_crew` (default false, partial index);
`0023_devices` creates `devices` and `device_units` (all FKs RESTRICT, CHECKs
on quantity ≥ 0, the six statuses, removal fields, unit price ≥ 0;
case-insensitive uniqueness through stored `name_key` / `serial_key`).
`alembic check` clean; upgrade → downgrade → upgrade run in the migration
tests and by hand.

**Audit actions.** Crew: `party.created` with `source: crew_quick_add |
crew_import` (and the email and phone), `party.crew_flag_set`. Gear:
`device.created` (with `source: devices_import` from an import),
`device.updated`, `device.archived`, `device.quantity_set`,
`device_unit.created`, `device_unit.updated`, `device_unit.removed`,
`device_unit.reactivated` (both with `quantity_owned` before/after and
`quantity_adjusted`), `devices.import_applied` (counts). The prompt's
`device_unit.retired` became `device_unit.removed`, because lost and sold
are removals too.

**Permission keys** **[Guessing]**: `devices.view` = owner, admin, producer,
accountant, staff; `devices.edit` = owner, admin, producer. Purchase price,
unit price, seller and invoice number are served and accepted only with
`budgets.view` (`price_visible` / `purchase_visible` say which). Crew uses
`parties.view` / `parties.edit`.

**Routes.** `GET /crew`, `GET /crew/roles`, `GET /crew/export.csv`,
`POST /crew`, `POST /crew/import/preview`, `POST /crew/import`;
`GET|POST /devices`, `GET /devices/export.csv`,
`POST /devices/import/preview`, `POST /devices/import`,
`GET|PATCH /devices/{id}`, `POST …/archive`, `POST …/reactivate`,
`POST …/units`, `PATCH …/units/{id}`, `POST …/units/{id}/remove`,
`POST …/units/{id}/reactivate`; `GET /parties?role=crew`. **No DELETE** on
any of them (asserted on the OpenAPI document).

## Defaults I chose

- **Crew-only party type is `person`**, with email and phone as primary
  `general` contact points.
- **Duplicates** match every party of the company, archived ones included,
  by email (case-insensitive) or name (case and spacing ignored), and an
  earlier line of the same file. Quick add creates anyway and lists the
  look-alikes; the import holds them back unless ticked.
- **Device category is free text** with suggestions (Step 0 item 3).
- **A gear import line naming an ACTIVE device cannot be ticked** (one
  active name per company), and the sentence says to rename the line; a line
  naming an ARCHIVED device can be ticked to add a new one. A name repeated
  within the file is an error.
- **Empty quantity in a gear CSV is 0**; quantity is whole units.
- **Export of gear** lists the serials still in hand (not lost/retired/sold);
  the price column is empty without `budgets.view`.
- **Seller, unit price and invoice number are optional** (your point 3).
- **The web turns `65.000,00` into `65000.00`** before sending (a separator
  swap, no arithmetic) and refuses `1.500` with a sentence, as the imports do.

## Verification

**Environment (declared):** API in `filmbill-api:latest` (Python 3.11.15) on
the `filmbill_default` network against the dev stack's Postgres 15 and
Redis 7 (`postgres` / `redis`), `CI=1`, CI's env vars, on a `git archive`
export of each commit. Web: macOS, Node 24, pnpm, vitest 4; and `pnpm ci:web`
(Linux, node 20, clean export of HEAD).

**Run and observed**
- API full suite on the final code (`2db6743`; part 3 and the close change
  no code): **2984 passed, 30 skipped, 0 failed** (2868 before). Part 1
  (`7c1cef8`) 2911; part 2 (`b59e747`) 2981.
- `-m realdb`: **1568 passed, 0 skipped** (1455 before); part 1 1497,
  part 2 1565. **113 of the 116 new API tests are real-database tests** and
  so run, not skip, in CI's real-database step: `test_crew_api.py` 24 of 25,
  `test_devices_api.py` 32 of 34, and 57 new cases in
  `test_company_isolation.py` (19 routes × 3). The other three read the
  OpenAPI document or the permission table.
- Web: **811 tests / 73 files** (780 / 70 before); typecheck and lint clean
  (the two older warnings in catalog/parties untouched). `pnpm ci:web`
  (Linux, node 20, clean export of `3c46b57`, which has the same web code as
  the final head): lint, typecheck, 811 / 73, build. All green.
- `alembic upgrade head` → `alembic check` (no pending changes); the
  migration tests run 0021 → 0022 → 0021 → 0022 and 0022 → 0023 → 0022 →
  0023 with real rows, and the CHECKs and unique index fire in the migrated
  schema itself.
- **The full run on the first 2b commit caught a real bug**: the removal
  CHECK used Postgres's `btrim`, and `test_email_case_insensitive.py` builds
  the whole schema on SQLite. 9 errors, no failure in the gear tests
  themselves. Fixed with `trim` (both have it) and folded into 2b before
  any push; the numbers above are from the fixed commit.
- **Mutations, each a real FAIL line, each reverted** (run on a scratch
  copy, never the worktree):
  - a crew-only party in the customer picker (`role=customer` also
    matching `is_crew`): `FAILED test_crew_never_appears_in_a_customer_or_supplier_picker_and_back
    - assert {'Crew And Su...ustomer Only'} == {'Customer Only'}`;
  - an import updates an existing party (ticked duplicates overwrite its
    tags and notes): `FAILED test_an_import_never_updates_an_existing_party_even_when_its_line_is_ticked
    - assert ('Anna Huber'...e, 'New note') == ('Anna Huber'..., False, None)`;
  - preview and apply disagree (apply creates every duplicate, ticked or
    not): `FAILED test_the_import_creates_exactly_what_the_preview_showed - assert (4 == 3)`
    and `FAILED test_duplicates_by_email_and_by_name_are_held_back_unless_ticked`;
  - adding a serial changes the quantity: `FAILED test_adding_serial_numbers_never_changes_the_quantity_owned
    - AssertionError: assert 8 == 3` (11 failed in all);
  - removing with the box unticked still reduces: `FAILED test_removing_with_the_box_unticked_keeps_the_quantity
    - assert (3, 2) == (3, 3)`;
  - staff response contains `purchase_price`: `FAILED test_the_gear_permission_matrix[staff-True-False-False]
    - AssertionError: assert '65000.00' == None` and the staff walk;
  - staff response contains a unit's seller (2b): `FAILED test_staff_see_devices_and_counts_but_no_price_and_can_change_nothing
    - AssertionError: assert (None is None and 'Seller Secret KG' is None)`;
  - a duplicate serial in different case accepted (`serial_key` without
    case-folding): `FAILED test_a_serial_in_another_case_is_the_same_serial_everywhere`
    and `FAILED test_a_pasted_list_skips_repeats_and_serials_already_recorded`.
  The gear mutations were run again after 2b changed the code; every one
  still failed.

**Statically checked only**
- Nothing that matters for this release: every new route and screen has a
  behavioural test.

**Not checked**
- The pages in a real browser (no local preview this time; the screens are
  tested with mocked API responses in jsdom).
- The server: migrations 0022/0023 on the real data.
- Safari and Firefox.

## SCOPE diff

D105–D111 after D104; §3 names the crew flag; §4.4 gets a note saying where
D106–D108 amend it; §13 gains the B2b row and B2c replaces the B2 row. The
exact diff is commit `7471c10` (`git show 7471c10 -- docs/SCOPE.md`), to
mirror to the Project.

## CI floors

API **2900** (was 2790), real-database **1525** (was 1415): 2.8 % and
2.7 % under the counts above (rule 17e).

## On the server, beyond `git pull`, `build --no-cache`, `up -d`

Nothing extra. Migrations **0022_party_crew_flag** and **0023_devices** run
when the API starts; **no data is migrated** (every party starts with the
crew tick off; there is no gear yet).

## CI on `origin/main`

Filled in by the commit after the closing commit, once CI on `origin/main`
has run.
