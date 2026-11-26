# UI-1 report — sidebar, notifications popup, no Crew menu item, six-week calendar

Prompt: `docs/prompts/33-filmbill-UI1-nav-notifications-calendar-weeks.md`. Built 2026-11-26.

**In plain words.** The sidebar now starts **expanded**. The **bell sits next
to Search** in the top bar and opens a small popup instead of a panel over
the menu. There is **no Crew or Archive menu item**: crew are in Contacts →
Crew, which now has Quick add crew, Import CSV and Export CSV in its toolbar,
and an old `/crew` bookmark lands there. The project calendar **always shows
six weeks**, and Booked days sits right under it at its width. Nothing to do
on the server beyond the usual pull, build and up.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `031da04` | the prompt file and its INDEX line |
| 1 | `d98ff3e` | sidebar expanded by default, versioned storage key |
| 2 | `ebc8ae5` | notifications as a top-bar popover |
| 3 | `fb79a49` | no Crew menu item; `/crew` → `/contacts?tab=crew`; the Crew tab's toolbar |
| 4 | `46e6726` | `monthGrid`: always six weeks; Booked days under the grid |
| 5 | `3c4d131` | Archive hidden behind `ARCHIVE_NAV_ENABLED = false` |
| 6 | `8e5f9bd` | SCOPE D119–D123 and the §13 row |
| close | (closing commit) | INDEX, this report; floors unchanged (below) |

No migration, no API change, no generated-type change: every part is in
`apps/web` (plus SCOPE). Each web part was committed with typecheck, lint
and the full web suite green on its tree; the API suites were run at the
start and on the final tree (they cannot be affected by a web-only change,
and were not re-run per part).

## Step 0 answers

1. **State.** `git status -sb`: clean worktree branch
   `Claude_MS/filmbill-ui-nav-notifications-075091` at `cdeec6c` =
   `origin/main`, only the copied prompt untracked. P1b-4's CI on
   `origin/main` (run 45887318911) green on all five jobs;
   `docs/reports/P1b4-report.md` present. Baselines: API **3097 passed, 30
   skipped**; `-m realdb` **1647**; web **858 / 76 files**. Floors **3010** /
   **1600**.
2. **Sidebar** — `components/layout/sidebar.tsx`. The collapsed state was
   **not stored at all**: `useState(true)` in
   `components/layout/dashboard-shell.tsx`, so it started collapsed on every
   load. So there was no old stored value to override; the versioned key is
   there anyway, and a test proves plausible old names are ignored.
3. **Notifications** — `components/layout/notification-drawer.tsx`, a fixed
   full-height 380 px drawer at `left-[52px]` (the collapsed sidebar's
   width — which is why it covered the expanded sidebar's labels), opened by
   a sidebar button that also held the badge. Store
   `stores/notification-store.ts`: `GET /me/notifications` (list of
   `NotificationResponse`), `POST /me/notifications/{id}/read`,
   `POST /me/notifications/read-all`; the unread count is computed by the
   store from the list. A full page exists at `/notifications`.
4. **Top bar** — `components/layout/header.tsx`: breadcrumbs left; company
   switcher and the Search (⌘K) button right.
5. **Crew page** — `app/(dashboard)/crew/page.tsx` (+ `quick-add.tsx`,
   `crew-import.tsx`): the `GET /crew` list (roles, phone, email, notes,
   "archived" and "also a supplier" marks), search over name/role/email, a
   role filter from `GET /crew/roles`, five sorts, Show archived people,
   Add person (quick add with role suggestions), Import CSV (preview →
   tick duplicates → apply), Export CSV. **Contacts → Crew** offered only the
   general table filtered by `role=crew` (`GET /parties?role=crew`).
6. **Calendar** — `monthWeeks()` in `lib/calendar-dates.ts` gave 4–6 rows;
   `MonthGrid` in `projects/[id]/calendar/month-grid.tsx` draws them;
   `days-tab.tsx` loads `GET /projects/{id}/calendar` — **one request for
   the whole project, no date range**. `BookedDays` in `calendar-parts.tsx`
   sat below the grid *and* the side panel, at the full page width, each
   month's days in a non-wrapping row.
7. **Archive** — `app/(dashboard)/archive/page.tsx`, a "coming in P5"
   placeholder gated on `archive.view`; the nav item is a line in
   `navItems` in `sidebar.tsx`.

## What moved off the Crew page

All of it, into Contacts → Crew (`contacts/crew-tab.tsx`): **Quick add crew**
(with role suggestions), **Import CSV**, **Export CSV** in the tab's toolbar,
plus the **role filter** and **sort** at the toolbar's right. The tab's list
is the crew endpoint's again (roles column, phone, notes, marks). The page's
own search box searches name, role and email on this tab (placeholder says
so) and the page's "Show archived" replaces "Show archived people". The
read-only note is the Contacts page's; Export stays for readers. Nothing was
dropped.

## Defaults I chose

- Storage key `filmbill.sidebar.collapsed.v2`, values `'1'`/`'0'`.
- Popover: Radix Popover, `align="end"`, 380 px, max 480 px (or the window
  height less 4 rem), `collisionPadding` left 200 px so it never slides over
  an expanded sidebar; "See all" → `/notifications`. Unread tab with nothing
  unread says "Nothing unread"; otherwise "No updates yet".
- `/crew` → `/contacts?tab=crew` as a 307, like the `/parties` ones; `?tab=`
  is read once after mount and accepts any tab key.
- `monthGrid(year, month)` is 1-based in the month; `monthGridFor("2027-03")`
  is what the screen calls.
- Booked days: `w-full max-w-full overflow-x-auto`, months and a month's
  days both `flex-wrap`.
- `ARCHIVE_NAV_ENABLED` in `apps/web/lib/nav-flags.ts`, read at render time.
  The `/archive` page now says one sentence for everyone and no longer
  checks the permission (there is no data behind it).

## Mutations (real FAIL lines, reverted)

- Part 2, badge shown at 0 (`unread <= 0` → `unread < 0`):
  `FAIL … the bell in the top bar > shows no badge when everything is read`
  (and 2 more). Reverted, 16/16.
- Part 4, extra week on the wrong side (`before < after` → `before > after`):
  `FAIL lib/__tests__/month-grid.test.ts > the months the prompt pins >
  March 2027 starts on a Monday, so the extra week is 22–28 February, before
  it` — `expected [ '2027-03-01', … ] to deeply equal [ '2027-02-22', … ]`
  (26 failures in all). Reverted.
- Part 1 (not asked for): default back to collapsed → 3 FAIL lines.

## Flagged

- **Tax advisor sidebar.** `archive.view` is their only permission, so with
  Archive hidden they see only Dashboard until P5. Recorded in D123; flip the
  constant if that is not wanted.
- The B2b test "Crew entry right after Parties" asserted the opposite of
  this decision and was replaced, not kept.
- "Data request covers the grid's range": the calendar has no ranged
  request at all; it loads the whole project. The test pins that only
  `/projects/p1/calendar` is asked and that a strip in March 2027's filler
  week is drawn.

## Verification

**Run and observed**
- Web suite on macOS host: 933 passed / 80 files (858 / 76 before).
- `pnpm ci:web` (Linux, node:20, pnpm 9.12.3, clean `git archive HEAD` of
  `8e5f9bd`): lint, typecheck, 933 / 80, `next build` — green.
- API on the final tree (`git archive` export, `filmbill-api:latest` on the
  dev stack's Postgres and Redis, run one after the other): see Numbers.
- The two mutations above.
- CI of the final commit on `origin/main` is reported with the merge (it
  cannot be written into the commit it runs on).

**Statically checked only**
- `tsc --noEmit` and `next lint` (only the two pre-existing
  `exhaustive-deps` warnings in catalog and contacts).

**Not checked**
- The popup's position and that it does not cover the sidebar in a real
  browser (jsdom does no layout; asserted via classes and Radix props only).
- Booked days' real width beside an open side panel (asserted by
  containment and classes, no pixel measurements).
- The `/crew` redirect through a running Next server (asserted on the
  redirect table Next.js reads).
- Anything on the server.

## Numbers

Environment: macOS host for the web suite; Linux `node:20` for `ci:web`;
API in `filmbill-api:latest` against the dev stack's Postgres 16 and Redis 7.

| | before | after |
|---|---|---|
| API | 3097 passed, 30 skipped | 3097 passed, 30 skipped |
| `-m realdb` | 1647 | 1647 |
| web | 858 / 76 files | 933 / 80 files |

**CI floors.** Unchanged at **3010** and **1600** (rule 17e): the API
counts did not move, and both are already within 3 % (3010 is 2.8 % under
3097; 1600 is 2.9 % under 1647).

## On the server

Nothing beyond the usual pull, `build --no-cache` and `up -d`. No migration,
no environment variable, no new service.

## SCOPE diff (to mirror to the Project)

New rows after D118 in §16, and one row after P1b-4 in §13:

```diff
diff --git a/docs/SCOPE.md b/docs/SCOPE.md
index ac39de3..127b895 100644
--- a/docs/SCOPE.md
+++ b/docs/SCOPE.md
@@ -555,6 +555,7 @@ Final invoice:
 | **B2a** ✅ built 2026-11-25 (prompt 30) | **The project calendar**: strips over days with days as the source of truth for counts (D99), distinct-date counting and the strip-limited global (D100), the migration that turned existing days into strips (D101), travel/off as day overrides and every day edited on its own (D102), the calendar screen with the booked-days strip (D103). Not built: D104. | Mathias, 2026-11-25 |
 | **B2b** ✅ built 2026-11-26 (prompt 31) | **Crew and gear records**, before anything is booked: crew as parties with a third flag, no number when crew-only, roles as tags, quick add, CSV import/export (D105); devices with the planning quantity and units with serial numbers, independent by design (D106); removing and reactivating a unit with the quantity question, six unit statuses, removal reasons (D107); device and unit purchase prices, the unit's seller and invoice number (D108); the two permission keys (D109). Not built: D110. Open: who may see how much of the crew list (D111). | Mathias, 2026-11-26 |
 | **P1b-4** ✅ built 2026-11-26 (prompt 32) | **Contacts rework** before B2c, which shows people's names everywhere: names in parts and no automatic split (D112), organisations' second line (D113), sole proprietors as one record (D114), direct tax-ID entry and the note's wording (D115), Contacts as the UI word with redirects (D116), the full-width list and its internal columns [Guessing] (D117), people created inside an organisation (D118). | Mathias, 2026-11-26 |
+| **UI-1** ✅ built 2026-11-26 (prompt 33) | **Navigation and calendar polish** after looking at the running server: the sidebar starts expanded (D119), notifications as a top-bar popup (D120), no Crew menu item — crew live in Contacts → Crew (D121), the calendar always six weeks with Booked days under it (D122), Archive hidden until P5 (D123). | Mathias, 2026-11-26 |
 | **B2c** next — builds on B2a and B2b | **Bookings of gear and crew**: reservations against B2a's days and strips, availability, clashes (warn, never block — Mathias, 2026-11-26), the planner, crew availability. Scoped after B2b is reviewed. | Mathias, 2026-11-26 |
 | **B3** after B2 — **not yet scoped** | **Content tracking**: deliverables, review rounds, a footage log. | Mathias, 2026-11-24 |
 | **B** | Documents proper: quotes, invoices, credit notes, snapshots, finalize, numbering, revisions, PDF. | P2 as written |
@@ -722,6 +723,11 @@ Compliance sources: [Brandauer – E-Rechnung AT 2026](https://brandauer-rechtsa
 | D116 | **The screen says Contacts, the code says party (P1b-4).** Every user-facing Parties/Party is Contacts/Contact; the pages are `/contacts` and `/contacts/[id]`, with 307 redirects from `/parties` and `/parties/:id` so no bookmark breaks; the emails-and-phones tab is "Phone & email". API paths, permission keys, models, identifiers and test names keep party (CLAUDE.md 8b). API sentences a person reads say contact too. The guard reads rendered text and the aria-label/placeholder/title/alt of the list, every detail tab, crew and the sidebar — not the source (rule 11) — with a probe proving it can fail. | 2026-11-26 |
 | D117 | **The Contacts list is a full-width table, and its internal columns are for `parties.edit` [Guessing] (P1b-4).** Columns: number, name (second line beneath), type, role chips, country, city, primary email, phone, tags; for `parties.edit` also VAT status, payment terms, tax status and last check date. Chips are fixed-height and never wrap; past two they become "+N" with every role in the tooltip. Columns drop by breakpoint — tags and city first, then phone, then email; name, type and roles never. A column picker hides columns, remembered per browser in `localStorage` (a convenience, every access guarded). `GET /parties` serves the contact columns to `parties.view` and **withholds** the internal ones from a list served without `parties.edit` (`restricted_withheld: true`). Today that role is the accountant only — staff hold no `parties.view` at all (D58) — and the detail page still shows a reader the Tax tab and payment terms, as before; whether the detail should withhold them too is open. | 2026-11-26 |
 | D118 | **People and departments are created inside an organisation (P1b-4).** The Hierarchy tab has Add person (title, first, middle, last, position, email, phone, and B2b's Crew box and roles) and Add department (name, additional line), creating the child with `parent_id` set and inheriting through the resolver exactly as before; the child appears in the list in place, "Add, then add another" keeps the dialog open. `POST /parties` takes `email` and `phone` (primary contact points in the same transaction) and, for an organisation, `people` — repeatable rows created **in one transaction with it: one refused row and nothing is created**, and **one `party.created` event** with `people_created` and the names (the people have no event of their own). A person's page moves them under an organisation or department and back out. The tree's refusals name the contact and say what to do (17f). | 2026-11-26 |
+| D119 | **The sidebar starts expanded (UI-1, Mathias 2026-11-26).** Expanded is the default for anybody who has not chosen; the collapse button stays and the choice is remembered per browser in `localStorage` under the versioned key `filmbill.sidebar.collapsed.v2` (a per-viewer convenience, every access guarded — storage that throws answers "expanded"). Older names are never read, so every browser starts expanded once; until UI-1 the state was not stored at all and started collapsed. | 2026-11-26 |
+| D120 | **Notifications are a small popup from the top bar (UI-1).** The bell sits at the right of the top bar beside Search, with an unread badge (hidden at 0, "9+" above nine); the sidebar has no Notifications entry. A click opens a compact popover anchored under the bell — about 380 px wide, at most 480 px tall, the list scrolling inside — with All / Unread, Mark all as read, the settings gear and a "See all" link to `/notifications`; empty, it says "No updates yet". Escape, an outside click and navigating close it; focus moves in on open and back to the bell on close. It keeps a sidebar's width free on the left, so it never covers the menu. Replaces the full-height drawer that slid over the sidebar. | 2026-11-26 |
+| D121 | **Crew are not a menu item; they live in Contacts → Crew (UI-1).** Crew are people, so the sidebar has no Crew entry and there is no `/crew` page; `/crew` redirects (307) to `/contacts?tab=crew`, and `?tab=` opens that tab of the Contacts list. The Crew tab shows the crew list (roles, phone, email, notes) and carries what the Crew page had, in its own toolbar: Quick add crew, Import CSV, Export CSV, the role filter with the roles in use, and the sort; the page's search finds name, role or email there, and "Show archived" is the page's own toggle. `parties.view` to see, `parties.edit` for quick add and import, unchanged. D111's open question now applies to this tab. | 2026-11-26 |
+| D122 | **The project calendar always shows six weeks (UI-1).** The month grid has six week rows, 42 days, Monday first, so it never changes height. A month whose own weeks are five rows gets one week on the side with **fewer filler days** (the neighbouring month's days in the first row before, in the last row after); a tie adds it after. A four-row February (starts on a Monday, ends on a Sunday) gets one week before and one after. March 2027 therefore opens with 22–28 February; February 2027 runs 25 January – 7 March. Days and strips in the filler weeks are drawn like any other (the calendar is loaded for the whole project). **Booked days sits directly under the grid, in the calendar's own column and at its width** — never under the side panel too; its months, and a month's days, wrap inside it. Amends D103. | 2026-11-26 |
+| D123 | **Archive is hidden from the sidebar until P5 (UI-1, reversible).** The entry opened an empty page, and its purpose — the bookkeeping document archive of §9: outgoing documents, incoming bills and receipts, year/quarter/month folders, handover packages for the tax advisor — is far off. One constant, `ARCHIVE_NAV_ENABLED = false` in `apps/web/lib/nav-flags.ts`, hides it; **setting it to `true` brings it back** behind the same `archive.view` gate. The `/archive` route stays and says in one sentence that the document archive is not available yet; `archive.view` stays. Not the per-list "Show archived" toggles, which show items archived inside their own list. **Consequence:** a tax advisor, whose only permission is `archive.view`, now sees only the Dashboard in the sidebar until P5. | 2026-11-26 |
 
 ## 17. Questions for the Steuerberater
 
```
