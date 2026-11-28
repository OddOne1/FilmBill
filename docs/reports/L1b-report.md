# L1b report — locations inside the project, map links, hotel guests, hospitals

Prompt: `docs/prompts/39-filmbill-L1b-project-locations-hotel-stays-hospitals.md`
(Mathias's review of L1, decisions of 2026-11-28). Built 2026-11-28.

**In plain words.** Locations moved from the sidebar into each project: a
**Locations** tab next to Days, Crew and Gear, with sections for shooting
locations, hotels, hospitals, restaurants and other places. A place you used
before can be picked again in any project. A place can have several uses — a
hotel you also shoot in is one record with both ticks. Paste a **Google or
Apple Maps link** (also the short share links) and FilmBill fills in the
position, the name and the address and shows a **map**. Each hotel lists its
**guests** (crew of the production) with their own check-in and check-out,
and holds the **booking confirmations** (PDF, email, picture or pasted text).
The **nearest hospitals** are looked up on OpenStreetMap by themselves as soon
as a shooting location has a position — one set per area when the shooting
locations are far apart — and added with one click. On the server: pull,
build, up — migration **0035** runs by itself.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `19c921d` | the prompt and its INDEX line (pushed before the build) |
| 1 | `5c90c55` | migration 0035, uses, project locations, map-link reading and the short-link resolver, OSM map view, hotel stays, confirmations, OSM hospital search, the Locations tab, crew rows, tests (API and web in one commit: the regenerated types tie them) |
| 2 | `51c3235` | SCOPE D155–D161, D148/D149/D150/D152/D154 marked superseded in part, §13 row |
| close | (closing commit) | this report, INDEX notes, CI floors |

## What changed from L1

- `locations.kind` → `locations.uses` (shoot, hotel, hospital, **restaurant**,
  other); `source_link`, `osm_ref` added. A strip or day needs the
  **shooting use**.
- New: `project_locations`, `hotel_stays`, `location_attachments`.
- **Removed** after copying: `project_block_hotels` and
  `crew_assignments.hotel_location_id` (and their routes: the strip's Hotels
  section and the crew row's Hotel choice). The crew row now shows the
  person's stays read-only with a Route.
- The sidebar item and `/locations` page are gone (`/locations` redirects to
  `/projects`); the form and the picker moved to `components/locations/`.
- New routes: `POST /locations/resolve-link`; `GET|POST /projects/{id}/locations`,
  `…/locations/{location_id}/remove`, `…/locations/{location_id}/stays`,
  `PATCH …/stays/{stay_id}`, `…/stays/{stay_id}/remove`,
  `…/locations/{location_id}/attachments` (file) and `…/attachments/text`,
  `GET …/attachments/{id}/download`, `…/attachments/{id}/remove`,
  `GET …/hospital-suggestions`, `POST …/hospital-suggestions/add`.
- New settings, both with defaults: `OVERPASS_URL`
  (`https://overpass-api.de/api/interpreter`), `HOSPITAL_SEARCH_RADIUS_KM` (30).
- Audit: `project_location.added|removed`, `hotel_stay.added|updated|removed`,
  `location_attachment.added|removed`; `location.*` as in L1.

## Defaults chosen (say if any is wrong)

- **Hospital groups:** shooting locations within 30 km of each other (chains
  included) share hospitals; search radius 30 km; three per group;
  confirmed emergency department (`emergency=yes` in OpenStreetMap) first,
  then distance; unnamed entries left out; answers cached for an hour.
- **Guests** are people with a scheduled or booked assignment in the
  project; default dates = first assigned day to the day after the last.
- **Taking a place off a project** is refused while a strip, a day or a guest
  of the project uses it.
- **Confirmations:** 15 MB; PDF/PNG/JPEG/WebP recognised by content, `.eml`,
  `.msg`, `.txt` by name; download link valid 10 minutes.
- **A pasted link** fills the position always, the name and the address only
  into empty fields; the address text goes into Street (Google gives one
  line, not parts).
- **Removing the hotel use** is impossible for a user who cannot see hotels
  (it is kept).
- **Hospital search starts by itself** when the tab opens and a shooting
  location has coordinates; "Search again" repeats it.

## Verification

### Run and observed

- **API suite** on `5c90c55`: **3658 passed, 30 skipped, 0 failed** (3564 /
  30 before). `-m realdb`: **2116 passed, none skipped** (2056 before).
  Environment: `filmbill-api:latest`, the dev stack's Postgres 15 and Redis 7
  on `filmbill_default`, `CI=1`, from a `git archive` export. The first run
  (`da64956`, amended) had 9 setup errors and 1 failure: one test file builds
  the schema on SQLite, which has no array type and cannot parse the uses
  CHECK (fixed as `parties.tags` is: a JSON variant and a Postgres-only
  CHECK), and the dependency guard did not know `http` (`http.server`) is
  the standard library.
- **New / changed API tests:** `test_project_locations_api.py` **24** and
  `test_l1b_migration.py` **1** (0034 → 0035 → 0034 on L1-shaped rows),
  real-DB; `test_locations_api.py` 41 and `test_location_links_api.py` 24
  (rewritten for uses and stays), real-DB; `test_map_links.py` **51** (16
  pasted-URL and 2 map-view golden cases added), `test_geo.py` **9**,
  `test_map_link_resolver.py` **5**, pure or with a local HTTP server.
  **13 new isolation operations** (`{stay_id}`, `{attachment_id}` are the
  other company's rows); the 3 removed L1 hotel routes left the isolation
  list and the calendar walk.
- **The two outbound components ran for real:** the short-link resolver
  against a local HTTP server that redirects twice (both hops recorded, the
  final Google link parsed, never requested; a hop to another host
  refused), and the Overpass client against a local server answering like
  Overpass (2 queries recorded, `around:30000`), plus "nothing listens" →
  the sentence. Neither was pointed at the real Google or OpenStreetMap.
- **Migrations:** `alembic upgrade head`, `alembic check` (no new operations),
  0035 → 0034 → head, `downgrade base`.
- **Web:** `vitest` **1051 / 87 files** (1050 / 87 before; the L1 screen
  test was rewritten as `projects/__tests__/locations.test.tsx`, 25 tests),
  `tsc` clean, `next lint` no error; `pnpm ci:web` (Linux, node 20, from the
  commit) green.
- **Mutations (exported copies, worktree untouched), each a real FAIL line:**
  1. a staff user sees a stay →
     `FAILED test_project_locations_api.py::test_staff_see_the_places_and_no_guests_or_hotel_confirmations - AssertionError`
  2. the parser prefers `/@` over `!3d…!4d` →
     `FAILED test_map_links.py::test_map_url_golden[google-place-pin]` and
     `FAILED test_map_link_resolver.py::test_two_hops_then_the_map_link_is_read_not_requested ('48.220000' != '48.220954')`
  3. a hop to a host not on the list is followed →
     `FAILED test_map_link_resolver.py::test_a_hop_to_another_host_is_refused`
  4. (web) the tab builds the map view URL itself →
     `FAIL locations.test.tsx > Project → Locations > lists places by use — … with the API’s links`
  All reverted.

### Statically checked only

- The confirmation's "Open" opens the presigned URL in a new tab (tested with
  a mocked `window.open`; the presigned URL itself is generated by boto3 in
  the API test, not fetched).

### Not checked

- **The real Google and Apple short links and the real Overpass API** —
  only local stand-ins. Whether `maps.app.goo.gl` still answers with a
  redirect straight to a map link from the server's network (and not a
  consent page without a `continue` link) needs the acceptance step.
- **The screens and the OpenStreetMap map view in a real browser.**
- The acceptance on the server.

## CI floors

Raised at the close (rule 17e): passed **3458 → 3549** (3658 × 0.97 = 3548.3,
rounded up) and real-DB **1995 → 2053** (2116 × 0.97 = 2052.5, rounded up).

## On the server

Beyond `git pull`, `build --no-cache` and `up -d`: nothing. Migration
**0035_project_locations_stays** runs on start; it copies L1's strip hotels
and crew hotels into the project lists and stays before dropping them. The
API container needs outbound HTTPS to `maps.app.goo.gl` / `maps.apple` (short
links) and `overpass-api.de` (hospitals); without it those two features say
so in a sentence and everything else works.

## Acceptance for Mathias — on the SERVER, about 15 minutes

1. Sidebar: no Locations item; `/locations` lands on Projects.
2. Project 1000m → **Locations**. New location "Studio Wien": paste a Google
   Maps link of a place (also try a short "Share" link) — name, address and
   position fill in and a map shows.
3. A hotel: tick Hotel **and** Shooting location — it shows in both sections
   and can be picked for a strip.
4. Hotels: add two crew members as guests, change one check-out; nights update.
5. Attach a PDF confirmation and paste an email text; open both.
6. Hospitals: suggestions appear by themselves for Studio Wien; add one. Add a
   second shooting location 50+ km away — a second group appears.
7. Calendar: a crew row shows the hotel and **Route**.
8. Log in as Staff: shooting places visible, no hotel, no guests, no hotel
   confirmations.

## SCOPE diff (to mirror to the Project)

Commit `51c3235`: five L1 rows marked "Superseded in part by …", D155–D161
added after D154, and the L1b row in §13 after L1. Changed and added lines,
verbatim:

```
| **L1b** ✅ built 2026-11-28 (prompt 39) | **Mathias's review of L1**: locations in each project, shared across projects, no sidebar item (D155); a set of uses incl. restaurants, the shooting use required on strips and days (D156); pasted Google/Apple Maps links, short links opened once, an OpenStreetMap map view (D157); hotel guests with their own dates replacing L1's strip hotels (D158); booking confirmations as file or text (D159); hospitals from OpenStreetMap per group of shooting locations (D160). Not built: D161. | Mathias, 2026-11-28 |
| D148 | **Superseded in part by D155 and D156 — locations now live in each project's Locations tab (no sidebar item) and have a set of uses instead of one kind (L1b, Mathias 2026-11-28).** **Locations are records of four kinds in one table (L1, Mathias 2026-11-28).** `locations` (migration 0033), company-scoped, never deleted (`is_active`; an archived location keeps showing wherever it is used, marked "archived", and cannot be picked anew): `name` (unique among a company's active locations, case-insensitive), `kind` = `shoot \| hotel \| hospital \| other`, address (`street`, `street2`, `zip`, `city`, `country` alpha-2 through the B1.2 dropdown), `latitude`/`longitude` (NUMERIC(9,6), both or neither, -90..90 / -180..180), `phone`, `email`, `website` (lightly checked), `emergency_phone`, `access_notes`, `notes`, an optional on-site contact from Contacts (`contact_party_id`, name shown only with `parties.view`) plus `contact_text`. Sidebar **Locations** after Gear: search, kind chips, name/kind/city/phone/coordinates/in-use; one form for create and edit, also inline in the strip panel ("New location…"). `locations.view` = everybody who sees projects, staff included; `locations.edit` = owner, admin, producer. `GET /locations/{id}/usage` lists strips, days, strip hotel lists and crew rows with their projects; archiving shows them first. Audit `location.created\|updated\|archived\|restored\|verified\|verification_cleared`. | 2026-11-28 |
| D149 | **Superseded in part by D158 — the strip hotel list and the per-person hotel became hotel stays on the project; the day/strip location rule stays, with "shooting use" for "shoot or other" (L1b, Mathias 2026-11-28).** **A day happens at its own location, else its strip's; strips hold hotels; a person's hotel is picked from them (L1).** Migration 0034: `project_blocks.location_id` (the strip's default), `project_days.location_id` (a day that differs), `project_block_hotels` (unique per strip and hotel, removed by `is_active`, re-added by reactivation) and `crew_assignments.hotel_location_id`. A strip's or a day's location is of kind **shoot or other**; a hotel must be kind **hotel**; each refusal is a sentence. A person's hotel is one of the strip's hotels, or **any hotel while the strip lists none**; the one already set stays allowed. Strip and day responses carry the effective location record with its map link and its source (`day`/`strip`); a crew row carries the hotel and a **Route** link from the hotel to the effective location of the person's first day. "Set for selected days" sets or clears several days at once. Moving, resizing or duplicating a strip never changes a location (a duplicate copies the strip's and the days' locations, not its hotels). Taking a hotel off the strip's list leaves the people who already sleep there as they are. | 2026-11-28 |
| D150 | **Superseded in part by D156 — the rule now applies to a location used only as a hotel, and to stays and hotel confirmations (L1b, Mathias 2026-11-28).** **Hotels are visible only to roles that see crew (L1, D111).** A hotel says where a person sleeps, so a location of kind `hotel` needs `locations.view` **and** `parties.view` (what shows crew today). Without it a hotel is absent from every list, its id answers 404, a strip's hotel list is empty with `hotels_visible: false`, crew rows carry no hotel, and adding or setting one is a 404. Staff therefore see shoot, hospital and other locations and the strip's location, and no hotel. When D111 decides depths of crew access, this line moves with it. | 2026-11-28 |
| D152 | **Superseded in part by D157 and D160 — short share links are opened once, map views come from OpenStreetMap, and hospitals are looked up on OpenStreetMap; still no geocoding (L1b, Mathias 2026-11-28).** **Map and directions links are built without a key and without a network call; coordinates are typed or pasted (L1).** `billing/planning/map_links.py` (pure) builds `https://www.google.com/maps/search/?api=1&query=…` and `…/maps/dir/?api=1&origin=…&destination=…` from the coordinates when both exist, else from the URL-encoded address, else none; the web app only renders the API's links. `parse_coordinates` reads "48.2082, 16.3738", "48.2082 16.3738" and "48.2082;16.3738", decimal commas tolerated, kept to six decimals half-up, and refuses one number, an out-of-range value (saying when the longitude seems to come first) or anything else with a sentence; `POST /locations/coordinates` is the same reading without saving. **No geocoding until C2.** | 2026-11-28 |
| D154 | **Superseded in part by D160 — nearest-hospital suggestions are now built (L1b, Mathias 2026-11-28).** **Deliberately not built in L1:** weather, nearest-hospital suggestions, geocoding and static map images (C2 — each needs an outside service); several locations on one day (a day has one; a second place is a note until a call sheet needs more); the call sheet and its PDF (C1); importing locations from a CSV; copying a contact's address into a location; travel time or distance (the Route is a link only); gate codes and passwords — **never stored**, and the form says so. | 2026-11-28 |
| D155 | **Locations live in the project and are shared across projects (L1b, Mathias 2026-11-28).** The sidebar has no Locations item; `/locations` redirects to `/projects`. **Project → Locations** lists the places a project uses (`project_locations`, migration 0035), in sections by use; a place used on a strip or a day joins the list by that use, anything else is added by hand — from any location the company already has ("places from earlier projects are found here too"), or new, straight into the project. Taking a place off the list is refused while a strip, a day or a hotel guest of the project still uses it; the place itself stays for other projects. `projects.view` + `locations.view` to see, `projects.edit` to change, `locations.edit` to create. Audit `project_location.added\|removed`. | 2026-11-28 |
| D156 | **One place, several uses (L1b, Mathias 2026-11-28).** A location has one or more of **Shooting location, Hotel, Hospital, Restaurant, Other** (`locations.uses`, replacing L1's `kind`; existing rows got their kind, and a location already on a strip or day also got the shooting use). A strip's or a day's location must have the **shooting use**, ticked on purpose — a hotel or hospital you shoot in is the same record with both ticks. A location used **only** as a hotel is crew information (D150): absent and 404 without `parties.view`; one with other uses is shown as those, without its hotel use, guests or hotel confirmations. A user who cannot see hotels cannot add or remove the hotel use. | 2026-11-28 |
| D157 | **Pasted Google and Apple Maps links; an OpenStreetMap map view (L1b, Mathias 2026-11-28).** The form's **Map link** field sends the link to `POST /locations/resolve-link`, which reads a full link without any request — the place pin `!3d…!4d…` before the view centre `/@…`, then `q`/`query`/`ll`/`sll`/`coordinate`/`center`/`destination`/`daddr`, then a pair in the path; a name from `/place/<name>/` or Apple's `q`, an address from a text query or Apple's `address`; a consent page's `continue` link — and opens a **short share link** (`maps.app.goo.gl`, `goo.gl/maps`, `maps.apple/p/…`) **once**: GETs without reading any page, at most 5 redirects, 5 s each, every hop a Google/Apple map host, stopping as soon as a link carries a position. Position always, name and address only into empty fields; the link is kept (`source_link`). The **map view** is an OpenStreetMap embed (`embed_link`, built by the server, ±0.005° around the pin, no key) — the view the call sheet will use. Still **no geocoding**: an address without coordinates gets no pin. | 2026-11-28 |
| D158 | **Hotel stays: guests with their own dates (L1b, Mathias 2026-11-28).** A hotel in the project lists its **guests**: crew members of this production (a scheduled or booked assignment), each with **check-in and check-out** (`hotel_stays`, check-in before check-out; default: their first assigned day to the day after their last; changed per person). A person may have stays in several hotels. This replaces L1's strip hotel list and per-assignment hotel: migration 0035 turned each L1 crew hotel into a stay (first to the day after the last active day) and dropped both. The **crew row** (calendar and Project → Crew) shows the person's stays that overlap the strip's days, read-only, each with a **Route** from the hotel to where their first day on the strip inside the stay happens. Crew information: `parties.view` to see; `projects.edit` to change. Audit `hotel_stay.added\|updated\|removed`. | 2026-11-28 |
| D159 | **Booking confirmations on a place in the project (L1b, Mathias 2026-11-28).** A **file** — PDF, an email (`.eml`, `.msg`), a picture, a `.txt`, recognised by its first bytes where possible, at most 15 MB, stored in the company's S3 space, opened through a 10-minute presigned link (public endpoint, rule 16) — or **pasted text** (an email or a message), each with a title, who added it and when (`location_attachments`). On a hotel they are crew information. Removed softly (`is_active`), the stored object kept. Audit `location_attachment.added\|removed`. | 2026-11-28 |
| D160 | **Hospitals found on OpenStreetMap near the shooting locations (L1b, Mathias 2026-11-28).** The project's shooting locations with coordinates are grouped: two within **30 km** of each other are one group, chains included (`billing/planning/geo.clusters`); each group gets the hospitals within **30 km** (`HOSPITAL_SEARCH_RADIUS_KM`) of any member from the **Overpass API** (`OVERPASS_URL`, default `https://overpass-api.de/api/interpreter`; only the coordinate is sent, generic User-Agent, answers cached for an hour), **confirmed emergency departments first**, then by distance, three per group, unnamed entries left out. The tab asks by itself as soon as a shooting location has a position, names the shooting locations without one, and says so in a sentence when OpenStreetMap does not answer. **Add** creates the hospital (name, position, address, phone, website as shown) or reuses the one made from the same OpenStreetMap entry (`osm_ref`), and puts it on the project. Suggestions are never added by themselves; "verified" stays a person's check (D151). | 2026-11-28 |
| D161 | **Deliberately not built in L1b:** geocoding an address into coordinates (a shooting location without coordinates or a map link gets no hospital search); forwarding confirmation emails to FilmBill; reading dates or room numbers out of a confirmation; room types, prices or costs of stays; static map images for the PDF (C1/C2); travel times; weather (C2). | 2026-11-28 |
```
