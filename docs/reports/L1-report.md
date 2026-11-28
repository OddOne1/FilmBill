# L1 report — locations, hotels and hospitals as records

Prompt: `docs/prompts/38-filmbill-L1-locations-and-hotels.md`. Built 2026-11-28.

**In plain words.** There is a new **Locations** item in the sidebar, after
Gear. A location is a studio or set (**Shoot**), a **Hotel**, a
**Hospital** or **Other**, with an address, a phone, and optional
coordinates you paste in ("48.2082, 16.3738"). **Open in maps** opens it in
Google Maps in a new tab — no account or key is involved, and FilmBill
fetches nothing. A hospital can be **marked verified**; changing its address
or phone removes that and says so. On the calendar, a strip now has a
**Location** picker (with **New location…**), each day can have a different
one, and a strip has a list of **Hotels**; each crew person on the strip gets
a **Hotel** and a **Route** link from the hotel to where their first day is.
Staff see shoot locations but never hotels. The old free-text location stays
visible as "Old text" with a **Create location from this text** button. On
the server: pull, build, up — migrations **0033** and **0034** run by
themselves.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `5aa1e88` | the prompt file and its INDEX line |
| 1 | `a85d7e2` | migration 0033 `locations`, `billing/planning/map_links.py`, `/locations` API, permissions, tests |
| 2 | `62675aa` | migration 0034 (strip/day location, strip hotels, crew hotel), effective location, calendar and crew responses, the attach endpoints, tests |
| 3 | `9fdb51c` | Locations page and form, strip Location/Hotels sections, crew Hotel and Route, sidebar, web tests |
| 4 | `0a00b48` | SCOPE D148–D154, §13 row |
| close | (closing commit) | INDEX notes, this report, CI floors |

## Step 0 answers

1. **State.** Worktree `Claude_MS/filmbill-l1-locations-hotels-7c5f68` at
   `7e16d0f` = `origin/main`, clean but for the copied prompt. Prompt 36
   (`docs/reports/B2c2-report.md`) and 37 (`R2-report.md`) on
   `origin/main`; CI run 46073613942 on `7e16d0f`: all five jobs green.
   Baselines (filmbill-api image, dev-stack Postgres 15 + Redis 7, `CI=1`,
   `git archive` export): API **3427 passed, 30 skipped**; `-m realdb`
   **1957**; web **1025 / 86 files**. Floors **3325** / **1899**.
2. **A place today.** Free text only: `project_blocks.location` and
   `project_days.location` (`String(255)`), both from A2/B2a. The B2a
   migration (0021) copied the first day's text onto the strip. The strip
   panel edited the strip's text and each day's text (the day showing the
   strip's as a placeholder); the calendar API returned
   `effective_location = day's text else strip's`; My schedule (R2) shows the
   same. On a typical install: one text per strip that was entered by hand,
   rarely one per day — the dev database here predates B2a, so I could not
   count real rows; the B2a migration put at most one text per strip.
3. **Country and address.** One picker, `components/ui/country-select.tsx`
   (B1.2), alpha-2 stored, shown as "Austria (AUT)"; the server normaliser is
   `core/countries.normalise_country`. Contacts' address form
   (`contacts/[id]/page.tsx`) is inline Street / Postcode / City / Country
   inputs, not a shared component; locations use the same picker and the
   same field names and labels. **No coordinate was stored anywhere.**
4. **Crew and the panel.** `crew_assignments` (B2c-1): one row per person
   and strip, with its own days; `routers/crew_assignments.responses()`
   builds every crew row; `StripCrew` (`calendar/strip-crew.tsx`) is the one
   component used both in the strip panel and in Project → Crew, so a hotel
   on it shows in both. The strip panel (`calendar/strip-panel.tsx`): fields
   grid, day chips with multi-select and bulk type buttons, one-day details,
   move/duplicate/remove, then Crew and Gear as children. The sidebar
   (`components/layout/sidebar.tsx`) is a data list: Dashboard, Contacts,
   Catalog, Projects, Gear, (Archive hidden) — Locations goes after Gear.
5. **Permissions.** `projects.view` (owner, admin, accountant, producer,
   staff), `projects.edit` (owner, admin, producer); `parties.view`
   (owner, admin, accountant, producer), `parties.edit` (owner, admin,
   producer); `devices.view` (with staff), `devices.edit`. **D111** is open:
   until it is decided, crew need `parties.view`, so staff see no crew
   (D134). The permission that "lets a role see crew today" is therefore
   `parties.view`.

## What was built

- **Part 1.** `locations` (0033) with the prompt's fields and four CHECKs
  (kind, coordinate pair, two ranges) and a partial unique index on the
  active name key. `billing/planning/map_links.py`: `map_link`,
  `directions_link`, `parse_coordinates`, `coordinates_text`,
  `address_text`. `/locations` routes: list (`q`, `kind`,
  `include_archived`), create, read, update, archive, restore, verify,
  usage, and `POST /locations/coordinates` (the reading without saving).
  `locations.view` / `locations.edit`; hotels also need `parties.view`
  (`location_service.visible_query` is the one lookup).
- **Part 2.** 0034: `project_blocks.location_id`, `project_days.location_id`,
  `project_block_hotels`, `crew_assignments.hotel_location_id`.
  `billing/planning/locations.effective_location`. `PATCH` of a strip and of
  a day take `location_id`; `POST …/blocks/{id}/days/location` (bulk);
  `POST …/blocks/{id}/hotels` and `…/hotels/{location_id}/remove`;
  `PUT /projects/{id}/crew/{assignment_id}/hotel`. Calendar responses carry
  `location_ref`, `effective_location_ref`, `effective_location_source`,
  `hotels`, `hotels_visible`; crew rows `hotel_visible`, `hotel`,
  `route_link`. Usage and the list's "in use" count are real.
- **Part 3.** `/locations` page; `LocationForm` (also inline in the
  panel); `LocationPicker`; `strip-locations.tsx` (Location section with old
  text, per-day effective locations and "Set for selected days", the day's
  own location, Hotels); `CrewHotel` on each crew row; sidebar entry.
- **Audit actions.** `location.created|updated|archived|restored|verified|
  verification_cleared`; `project_block.updated` (with `location_id`),
  `project_day.updated` (with `location_id`), `project_block.hotel_added|
  hotel_removed`, `crew_assignment.hotel_set`.

## Defaults chosen (say if any is wrong)

- **Which kinds fit where:** a strip's or a day's location is **shoot or
  other**; a hospital or hotel there is refused with a sentence. A person's
  hotel must be kind hotel. (Shooting inside a hotel = make a Shoot
  location for it.)
- **Archived** locations keep showing everywhere they are used, marked
  archived, and cannot be picked for anything new ("Restore it under
  Locations, or pick another location."); re-saving the one already set is
  allowed. Restoring is refused while another active location has the name.
- **Coordinates** are kept to six decimals, half-up (a right-click pair from
  Google Maps has 14). Sending both the pasted text and the two numbers is
  refused.
- **Phone** may hold digits, spaces and `+ - / ( ) .`; **email** must look
  like `a@b.c`; **website** may not contain a space. Nothing more.
- **Changing a verified hospital's kind** away from hospital also clears
  the verification. Email, website, notes, access notes and the contact do
  not.
- **The on-site contact's name** is shown only to roles with
  `parties.view`; staff see the contact text only.
- **The Route** goes from the hotel to the effective location of the
  person's **first ticked day**; no first day or no location → no link.
- **The strip hotel list** governs new choices only: removing a hotel from
  it leaves people already sleeping there unchanged; with an empty list
  any hotel is accepted (the refusal says both).
- **Duplicating a strip** copies the strip's location and every day's own
  location (they are day data, like notes), not its hotels or crew.
  **A day's own location counts as "its own data"**, so shrinking a strip
  over it asks first, like notes and times.
- **The old text is no longer editable** on the calendar (it is shown, with
  the conversion); My schedule still shows it (D146 unchanged).
- **"Create location from this text"** opens the form as kind Shoot with
  the whole text as the name; the person edits before saving.
- Locations have their own OpenAPI tag `locations`.

## Verification

### Run and observed

- **API suite** on `62675aa` (the last commit touching `apps/api`; parts 3
  and 4 change none): **3564 passed, 30 skipped, 0 failed** (3427 / 30
  before). `-m realdb`: **2056 passed, none skipped** (1957
  before). Environment: `filmbill-api:latest`, the dev stack's Postgres 15
  and Redis 7 on `filmbill_default` (own database and Redis db per run),
  `CI=1`, from a `git archive` export of the commit. Part 1 alone
  (`548a6bb`, before an amend): 3519 passed, 1 failed — an audit-order
  assertion that relied on two rows of one transaction having different
  `created_at` (trap noted in my memory file); fixed in `a85d7e2` by
  comparing sorted, then re-run with the Part 2 suite.
- **New API tests:** `test_locations_api.py` **35** and
  `test_location_links_api.py` **25**, all real-DB (60 of the new tests run
  under `-m realdb`, none skipped); `test_map_links.py` **31** (golden cases
  and both import probes) and `test_effective_location.py` **7**, pure;
  **13 new isolation operations** in `test_company_isolation.py`
  (`{location_id}` = the other company's hospital); the calendar walk in
  `test_project_calendar_api.py` gained the three new strip routes (so the
  staff walk and the other-company walk cover them).
- **Migrations:** `alembic upgrade head`, `alembic check` ("No new upgrade
  operations detected"), a round trip 0034 → 0033 → head and
  `downgrade base`, on an empty Postgres 15.
- **Web:** `vitest` **1050 passed / 87 files** (1025 / 86 before):
  `locations/__tests__/locations.test.tsx` **23** new, 2 new sidebar tests,
  one panel test rewritten (it pinned the old free-text day location, which
  L1 replaces). `tsc --noEmit` clean; `next lint`: no error, the two
  warnings are in untouched files (`catalog/page.tsx`, `contacts/page.tsx`).
  `pnpm ci:web` (Linux, node 20, from `HEAD`): green (lint, typecheck, test, build) — "Web pipeline green on Linux/node:20 from a clean export.".
- **Mutations, each on an exported copy, worktree untouched, each a real
  FAIL line:**
  1. phone not in the verification fields →
     `FAILED test_locations_api.py::test_verification_is_kept_after_a_notes_edit_and_cleared_by_a_phone_change - AssertionError: assert (True, '2026-...st loc-owner') == (False, None, None)`
  2. the kind guard removed from `visible_query` (the hotel row is active,
     of this company, matches the search — only the kind guard protects it) →
     `FAILED test_locations_api.py::test_staff_list_shoot_locations_and_no_hotel - AssertionError: assert ['Hotel Wien', 'Studio Wien'] == ['Studio Wien']`
  3. `map_link` prefers the address →
     `FAILED test_map_links.py::test_map_link_golden[coordinates-beat-the-address] - AssertionError: When both exist the coordinates are used, not the address …`
  4. the day override ignored →
     `FAILED test_effective_location.py::test_effective_location_table[day set: the day's own wins over the strip's]` and
     `FAILED test_location_links_api.py::test_a_day_that_differs_overrides_the_strip` (+3 more)
  5. an archived location disappears from the strip → first run failed
     with a `TypeError` in the assertion (not counted, rule 12); the
     assertion was made None-safe and the rerun gave
     `FAILED test_location_links_api.py::test_an_archived_location_still_shows_and_cannot_be_newly_picked - AssertionError: assert None == ('Studio Wien', False)`
  6. (web) the picker builds the maps link from the coordinates →
     `FAIL locations.test.tsx > the strip panel: Location and Hotels > shows the strip’s location with the API’s map link, and each day’s effective location`
  All reverted; the suites above ran on the unmutated commits.

### Statically checked only

- Duplicating a strip copies the strip's and the days' location ids
  (`calendar_service.duplicate_block` via `DAY_DATA_FIELDS`): read, not
  tested.
- The on-site contact picker's search request (`/parties?search=…`) and the
  form's Contacts picker: type-checked, not driven in a test.

### Not checked

- **The screens in a real browser** (layout, the inline form inside the
  narrow panel, the picker's dropdown closing behaviour).
- **The map and directions links in a real browser**: the URLs are pinned
  exactly by the golden cases, but whether Google Maps opens the right
  place for each is not checkable here.
- The acceptance steps on the server.

## Golden cases (`apps/api/tests/fixtures/map_links/cases.json`)

Parse: `48.2082, 16.3738` / `48.2082 16.3738` / `48.2082;16.3738` /
`48,2082; 16,3738` / `48,2082, 16,3738` → 48.208200, 16.373800;
`48.20817433281537, 16.37381946802135` → 48.208174, 16.373819;
`-33.8688, -151.2093` accepted; `-122.4194, 37.7749` refused ("It looks like
the longitude came first"); `99, 16` refused; `48.2, 200` refused (longitude);
`48.2082` refused (one number); `Spitalgasse 23, Wien` refused; `48.2 16.3 12`
refused; blank refused. Map link: coordinates → `query=48.2082%2C16.3738`;
coordinates beat the address; half a pair uses the address; `Währinger
Gürtel 18-20, Eingang Süd, 1090 Wien, AT` percent-encoded; only `Wien`;
negative coordinates; empty → none. Directions: hotel coordinates →
`Hauptstraße 1, 2340 Mödling, AT`; both coordinates; no origin / no
destination → none. Effective location table: day set; day set strip empty;
day empty strip set; both empty; strip's archived; day's archived.

## CI floors

Raised at the close (rule 17e): passed **3325 → 3458** (3564 × 0.97 =
3457.1, rounded up) and real-DB **1899 → 1995** (2056 × 0.97 = 1994.3, rounded up as at every earlier close).

## On the server

Beyond `git pull`, `build --no-cache` and `up -d`: nothing. Migrations
**0033_locations** and **0034_location_links** run on start like every
other; neither rewrites data. No new environment variable, no new service.

## SCOPE diff (to mirror to the Project)

Two hunks in `docs/SCOPE.md` (commit `0a00b48`), lines added, nothing removed:

1. §13, after the R2 row: the **L1** row.
2. §16, after D147: **D148–D154**.

The added lines, verbatim:

```
| **L1** ✅ built 2026-11-28 (prompt 38) | **Locations, hotels and hospitals as records**, before the call sheet: one table of four kinds with archive, search and usage (D148); a day's location is its own else its strip's, strips hold hotels and a person's hotel is picked from them, with a Route link (D149); hotels only for roles that see crew (D150); a hospital's verification cleared by address or phone changes (D151); map and directions links without a key or a network call, pasted coordinates (D152); the old free text kept with a manual conversion (D153). Not built: D154. | Mathias, 2026-11-28 |
| D148 | **Locations are records of four kinds in one table (L1, Mathias 2026-11-28).** `locations` (migration 0033), company-scoped, never deleted (`is_active`; an archived location keeps showing wherever it is used, marked "archived", and cannot be picked anew): `name` (unique among a company's active locations, case-insensitive), `kind` = `shoot \| hotel \| hospital \| other`, address (`street`, `street2`, `zip`, `city`, `country` alpha-2 through the B1.2 dropdown), `latitude`/`longitude` (NUMERIC(9,6), both or neither, -90..90 / -180..180), `phone`, `email`, `website` (lightly checked), `emergency_phone`, `access_notes`, `notes`, an optional on-site contact from Contacts (`contact_party_id`, name shown only with `parties.view`) plus `contact_text`. Sidebar **Locations** after Gear: search, kind chips, name/kind/city/phone/coordinates/in-use; one form for create and edit, also inline in the strip panel ("New location…"). `locations.view` = everybody who sees projects, staff included; `locations.edit` = owner, admin, producer. `GET /locations/{id}/usage` lists strips, days, strip hotel lists and crew rows with their projects; archiving shows them first. Audit `location.created\|updated\|archived\|restored\|verified\|verification_cleared`. | 2026-11-28 |
| D149 | **A day happens at its own location, else its strip's; strips hold hotels; a person's hotel is picked from them (L1).** Migration 0034: `project_blocks.location_id` (the strip's default), `project_days.location_id` (a day that differs), `project_block_hotels` (unique per strip and hotel, removed by `is_active`, re-added by reactivation) and `crew_assignments.hotel_location_id`. A strip's or a day's location is of kind **shoot or other**; a hotel must be kind **hotel**; each refusal is a sentence. A person's hotel is one of the strip's hotels, or **any hotel while the strip lists none**; the one already set stays allowed. Strip and day responses carry the effective location record with its map link and its source (`day`/`strip`); a crew row carries the hotel and a **Route** link from the hotel to the effective location of the person's first day. "Set for selected days" sets or clears several days at once. Moving, resizing or duplicating a strip never changes a location (a duplicate copies the strip's and the days' locations, not its hotels). Taking a hotel off the strip's list leaves the people who already sleep there as they are. | 2026-11-28 |
| D150 | **Hotels are visible only to roles that see crew (L1, D111).** A hotel says where a person sleeps, so a location of kind `hotel` needs `locations.view` **and** `parties.view` (what shows crew today). Without it a hotel is absent from every list, its id answers 404, a strip's hotel list is empty with `hotels_visible: false`, crew rows carry no hotel, and adding or setting one is a 404. Staff therefore see shoot, hospital and other locations and the strip's location, and no hotel. When D111 decides depths of crew access, this line moves with it. | 2026-11-28 |
| D151 | **A hospital's verification is cleared by any change of its address, coordinates or phone (L1).** **Mark as verified** (hospitals only, active ones) stores the user, today's date and an optional note; the form shows "Verified by <name> on <date>" or "Not verified" and says "Changing the address or phone clears this." A change to `street`, `street2`, `zip`, `city`, `country`, the coordinates, `phone` or `emergency_phone` (or a change of kind away from hospital) clears it; the answer says so ("This hospital is no longer marked verified, because its address or phone changed. …") and the audit has `location.updated` plus `location.verification_cleared`. Notes, access notes, website, email and the contact do not clear it. **"Verified" means a person checked this entry, not that it is the nearest hospital.** | 2026-11-28 |
| D152 | **Map and directions links are built without a key and without a network call; coordinates are typed or pasted (L1).** `billing/planning/map_links.py` (pure) builds `https://www.google.com/maps/search/?api=1&query=…` and `…/maps/dir/?api=1&origin=…&destination=…` from the coordinates when both exist, else from the URL-encoded address, else none; the web app only renders the API's links. `parse_coordinates` reads "48.2082, 16.3738", "48.2082 16.3738" and "48.2082;16.3738", decimal commas tolerated, kept to six decimals half-up, and refuses one number, an out-of-range value (saying when the longitude seems to come first) or anything else with a sentence; `POST /locations/coordinates` is the same reading without saving. **No geocoding until C2.** | 2026-11-28 |
| D153 | **The old free-text strip and day location stays, with a manual conversion (L1).** B2a's `location` text on `project_blocks` and `project_days` is unchanged and no longer edited on the calendar; where set it shows as "Old text: …" under the picker with **Create location from this text**, which opens the create form with the name filled in. Nothing is ever converted by itself. My schedule (D146) still shows the old text. | 2026-11-28 |
| D154 | **Deliberately not built in L1:** weather, nearest-hospital suggestions, geocoding and static map images (C2 — each needs an outside service); several locations on one day (a day has one; a second place is a note until a call sheet needs more); the call sheet and its PDF (C1); importing locations from a CSV; copying a contact's address into a location; travel time or distance (the Route is a link only); gate codes and passwords — **never stored**, and the form says so. | 2026-11-28 |
```
