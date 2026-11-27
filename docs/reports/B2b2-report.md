# B2b-2 report — gear completion

Prompt: `docs/prompts/34-filmbill-B2b2-gear-completion.md`. Built 2026-11-26/27.

**In plain words.** A device no longer has "Rented out as". Its **Rental
prices** sit on the Details tab and *are* the catalog item's price: change
them on the device and the catalog shows the same number, and a new budget
line picks it up. **Set up rental price** makes the catalog item; if one of
exactly that name is already in the catalog, the button says **Use the
existing 'X'** instead. The device page has tabs **Details · Units ·
Dependencies · Alternatives**. Units are a list, and **Add new item(s)**
opens a popup with one box per serial number and the purchase details typed
once. **Dependencies** (2 batteries per camera, always, included) and
**Alternatives** (Alexa 35 → Mini LF → Venice 2) are recorded and shown;
booking with them is B2c. Weight, size and one picture are on Details.
On the server: pull, build, up — three new migrations run by themselves;
the bucket needs nothing.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `0fc9ab9` | the prompt file and its INDEX line |
| 1 | `79d3db3` | rental prices on the device = the catalog item's rates; no "Rented out as" |
| 2 | `caf6092` | Units tab, Add new item(s) popup, `device_unit.created_bulk` |
| 3 | `c3905e3` | dependencies: migration 0026, loop refusal, setup hint, tab |
| 4 | `894c905` | alternatives: migration 0027, order, reverse on request, tab |
| 5 | `58fc36e` | weight, size, one cover picture: migration 0028, Pillow (now declared), thumbnails |
| 6 | `7a62d31` | SCOPE D124–D129, D107/D108 amended, §4.4 note, §13 row |
| close | (closing commit) | INDEX, this report, CI floors |

## Step 0 answers

1. **State.** Worktree branch `Claude_MS/filmbill-b2b-gear-completion-f642f1`
   at `cff6ea0` = `origin/main`, clean but for the copied prompt. Prompts 32
   (P1b-4) and 33 (UI-1) on `origin/main`; CI run 45893373995 on `cff6ea0`
   green on all five jobs; `docs/reports/UI1-report.md` present. Baselines
   (python:3.11 API image, dev-stack Postgres + Redis, `CI=1`, from a `git
   archive` export): API **3097 passed, 30 skipped**; `-m realdb` **1647**
   (UI-1's figure); web **933 / 80 files** (UI-1's figure). Floors **3010** /
   **1600**.
2. **Gear as built (B2b).** `apps/web/app/(dashboard)/gear/page.tsx` (list,
   add form, import) and `gear/[id]/page.tsx` (one page: the device form with
   the **"Rented out as (catalog item)"** `<select>` over
   `/catalog/items?kind=equipment_rental`, a serial-number `<textarea>` with
   shared purchase fields, the units table and its remove / reactivate /
   purchase panels). `devices.catalog_item_id` was a free FK set by
   `POST/PATCH /devices` (`check_catalog_item`: this company's, kind
   `equipment_rental`), shown only as a name, used by nothing. Equipment items
   are `items` rows of kind `equipment_rental` with `unit_id`, `item_rates`
   (`bill`/`cost` × period unit, unique per item) and an optional
   `rental_factor_group_id`; `seed_catalog` makes one default top-level
   category per kind ("Equipment-Miete") and the system unit `day`.
   **There is no unique-name rule for items** — only the SKU is unique (partial
   index `uq_items_company_sku`); two items may share a name.
3. **The logo's upload.** `companies.logo_s3_key` has **no upload endpoint**
   (it is only listed as an audited field). The live pattern is the site
   logo / favicon (`routers/site_settings.py: upload_site_logo`) and the
   avatar (`routers/users.py: upload_avatar`): `UploadFile` read in the API,
   `s3_service.put_object(key, body, content_type, cache_control)` under a
   fresh `uuid` key, the column set to the key, and the browser reads it
   through `routers/files.py`'s object proxy via `proxy_url_for(key)` — a
   JWT scoped to the key's folder (`/files/object/<file>?token=…`), resolved
   on the web by `lib/utils.resolveApiMediaUrl`. **Neither checks type or
   size** (everything is stored as `.webp`), and **replacing never deletes the
   old object** — there is no cleanup path; `BRAND_IMAGE_FIELDS` additionally
   refuses clearing a brand image. The device picture reuses put_object +
   proxy_url_for, and adds the checks (D129).
4. **Pillow.** Installed in the API image: **Pillow 13.0.0**, WebP support
   built in, pulled in by `qrcode[pil]==7.4.2` — not declared directly, so it
   is now, in the Part 5 commit.
5. **Permissions.** `devices.view` = owner, admin, producer, accountant,
   staff; `devices.edit` = owner, admin, producer; purchase prices behind
   `budgets.view`; `catalog.edit` = owner, admin, producer (the same set as
   `devices.edit` today). Staff walk: `test_devices_api.
   test_staff_see_devices_and_counts_but_no_price_and_can_change_nothing`,
   which sends every `/devices` operation as staff and is counted against
   the OpenAPI document (`test_the_gear_surface_in_the_walk_is_the_whole_
   surface_and_has_no_delete`). Both now cover the 22 new operations.

## Migrations

`0026_device_associations`, `0027_device_alternatives`,
`0028_device_size_weight_image`. All additive; no data migration. `alembic
upgrade head` → `alembic check` ("No new upgrade operations detected") →
`downgrade base` run locally on a fresh database after each of parts 3, 4, 5.

## Audit actions (new)

`device.catalog_linked`; `device_unit.created_bulk`;
`device_association.created|updated|removed`;
`device_alternative.created|updated|removed|reordered`;
`device.image_changed`, `device.image_removed`. Rate changes from the device
page are the catalog's own `item.created`, `item.rates_changed`,
`item.updated` (not duplicated). `device.updated` now also records
weight/length/width/height.

## New routes (all under `/devices`, all in the staff walk and the isolation table)

`POST …/rental/setup`, `PUT …/rental`; `GET …/dependencies`,
`GET …/dependencies/hint`, `POST …/dependencies`,
`PATCH …/dependencies/{association_id}`, `POST …/dependencies/{id}/remove`;
`GET …/alternatives`, `POST …/alternatives`, `PATCH …/alternatives/{id}`,
`POST …/alternatives/{id}/move`, `POST …/alternatives/{id}/remove`;
`POST …/image`, `POST …/image/remove`. No DELETE route. `POST/PATCH
/devices` no longer accept `catalog_item_id` (422).

## Mutations — each a real FAIL line, each reverted (run on a copy of the tree)

| Mutation | Caught by |
|---|---|
| the rate write without `catalog.edit` (guard call removed) | `FAILED test_device_rental.py::test_rates_need_catalog_edit_as_well_as_devices_edit - assert 200 == 404` |
| set up creates a second item when the name exists | `FAILED test_device_rental.py::test_an_unlinked_item_of_the_same_name_is_used_instead_of_a_duplicate` (another item id) |
| the shared price on the first row only | `FAILED test_device_units_bulk.py::test_shared_purchase_fields_land_on_every_created_unit_in_one_event - AssertionError: AX-3` |
| a loop accepted (graph walk skipped) | `FAILED test_device_dependencies.py::test_a_two_device_loop_is_refused - assert 201 == 409` and `…::test_a_three_device_loop_is_refused_and_names_the_chain - assert 201 == 409` |
| the pre-multiplication forgets the parent quantity | `FAILED test_device_dependencies.py::test_the_setup_hint_pre_multiplies_and_creates_links_with_origins_in_one_go - assert (1, 1) == (1, 2)` |
| "also add the reverse" unticked still creates it | `FAILED test_device_alternatives.py::test_one_direction_by_default` (extra reverse pair) |
| a non-image accepted because of its extension | `FAILED test_device_picture.py::test_a_text_file_renamed_jpg_is_refused - assert 200 == 422` (the mutation also broke six other picture tests, as a bypass of the whole processing step must) |
| a thumbnail readable across companies (token scoped to `devices/`) | `FAILED test_device_picture.py::test_a_thumbnail_is_served_only_to_its_own_company - assert 200 == 404` |

## Numbers

Declared environment: `filmbill-api:latest` (python 3.11), the dev stack's
Postgres 15 and Redis 7, `CI=1`, run on a `git archive` export of the commit.

| | before | after |
|---|---|---|
| API, full suite | 3097 passed, 30 skipped | **3191 passed, 30 skipped** |
| API, `-m realdb` | 1647 | **1740**, 0 skipped |
| web (`pnpm ci:web`, node 20, Linux) | 933 / 80 files | **957 / 80 files** |

New API test files: `test_device_rental.py` (13), `test_device_units_bulk.py`
(3), `test_device_dependencies.py` (15), `test_device_alternatives.py` (10),
`test_device_picture.py` (11) — every one real-DB — plus the new routes'
cases in `test_company_isolation.py` and the staff walk in
`test_devices_api.py`. Web: 24 more cases in `gear/__tests__/gear.test.tsx`.

**How each part was checked.** Each part was committed with its own API test
files, the device, isolation and OpenAPI-surface files, the gear web tests,
typecheck and lint green on the working tree, and `alembic check` for the
migration parts. The **full** suites ran once, on the final tree. That run
first caught one failure — `test_declared_dependencies`: Part 5 imported
Pillow without declaring it — which was fixed by adding the pin to the Part 5
commit itself before anything was pushed, so Part 5 stands green on its own.

**CI floors (rule 17e):** API **3010 → 3100** (2.9 % under 3191), real-DB
**1600 → 1690** (2.9 % under 1740).

## Three tiers

**Run and observed.**
- The API suite and `-m realdb` on a `git archive` export of the final
  commit (environment above), counts in "Numbers". The new real-DB files —
  `test_device_rental.py`, `test_device_units_bulk.py`,
  `test_device_dependencies.py`, `test_device_alternatives.py`,
  `test_device_picture.py` — use `pg_db`/`pg_client` and so run in CI's
  `-m realdb` step, where a skip fails the job.
- The worked numbers: set up, 1200.00/300.00 per day → a new budget line
  1200.00/300.00; changed on the device page to 1450.00/350.00 → the next new
  line 1450.00/350.00, the old one stays 1200.00 and reports the catalog
  difference 1450.00.
- The eight mutations above.
- `pnpm ci:web` from HEAD (Linux, node 20): lint, typecheck, tests, build.
- `alembic upgrade/check/downgrade` per migration part.
- Pillow on real JPEG, PNG and WebP bytes, a GIF and a renamed text file; the
  object proxy and its token on the real route.

**Statically checked only.**
- The generated API types (`pnpm gen:api` after every API part; no drift at
  the end).

**Not checked.**
- **The popups, the tabs and the upload in a real browser** — Escape and
  focus are tested in jsdom with Radix's own handlers, not in Safari or
  Chrome; the file picker and image display were not opened.
- A real S3/MinIO bucket: the tests replace the client with an in-memory
  one (both call sites, with a positive marker that it was used). The dev
  bucket was not written to.
- A full Compose rebuild (`build --no-cache` + `up -d`) — CI's Docker build
  job builds the images.

## Defaults I chose

- **Day price and cost are both behind `budgets.view`** on the device page,
  following your acceptance step 6 ("as Staff … no prices"). The prompt's
  Part 1 only required this for the cost.
- The rate write without `catalog.edit` answers **404** (the "missing
  permission" rule); the page never offers it and says why.
- "Use the existing" matches an **active** equipment item by `name_de`, case
  and spacing ignored, that **no other device** (archived ones included)
  links to. An inactive item of that name is not offered; set up then
  creates a new one.
- A new item gets **no tax class** (the catalog form sets it) and no SKU.
- The popup sends every non-empty row, flagged ones included; the API skips
  them and the page lists what was skipped. One serial is a "bulk" of one.
- A hint row that the parent already has, that would loop, or whose device
  is archived is shown **disabled with its reason**; sending one anyway is
  a 409 that writes nothing (one transaction).
- Hinted links copy the origin's booking mode, pricing and documents flag;
  the note is not copied.
- Alternatives: append to the end; Up/Down (no drag); "also add the
  reverse" unticked, and skipped silently when the reverse exists (the
  response says `reverse_added: false` and the page says so).
- Picture: at most 5 MB and 50 megapixels, WebP at most 1600 px and a
  160 px thumbnail, quality 85, EXIF rotation applied, metadata dropped.
  Keys `devices/<company>/<device>/<uuid>.webp` and `…-thumb.webp`.
  Replace/remove keep the objects (as the logo does), the old key in the
  audit event. Proxy tokens last 24 h, as the logo's do.
- Weight in kg to 3 decimals, sizes in cm to 1; more decimals is refused,
  not rounded. Shown in the field with a decimal comma, sent with a dot.

## Flagged

- **Staff can read cost rates in the catalog itself** (`GET
  /catalog/items` serves every rate to `catalog.view`, which staff hold).
  The device page hides them; the catalog does not. Not changed — a
  decision for you (recorded in D124).
- **Pillow was used through `qrcode[pil]` without being declared**, which
  `test_declared_dependencies` caught on the full run. Declared at the same
  version (13.0.0); the built image is unchanged.
- **Stored pictures are never deleted** (nor are logos). A storage sweep
  can find superseded keys in the audit log; none exists yet.
- **Open** (D126): the "dependencies changed, review?" notice.
- `/devices?limit=2000` loads the whole device list into each picker; fine
  at today's sizes, a search endpoint later.

## On the server, beyond `git pull`, `build --no-cache`, `up -d`

Nothing. The `api` container runs `alembic upgrade head` on start, which
applies **0026, 0027, 0028**. The S3 bucket needs no change: pictures go into
the existing bucket under `devices/…`, through the API.
