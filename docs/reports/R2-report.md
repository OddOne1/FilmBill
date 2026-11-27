# R2 report — team members (people with a login) can also be crew

Prompt: `docs/prompts/37-filmbill-R2-team-members-as-crew.md`. Built 2026-11-27.

**In plain words.** A login can now be connected to a person contact. In
**Settings → Team** (the old Members page, renamed) every member has a
**Crew** column: an admin clicks **Works as crew**, and if a contact already
has that person's email the dialog offers it ("Link to the existing contact
Anna Berger?"), otherwise it creates a crew contact from the login; then
roles, Save. Nothing is ever merged. A member can do the same for themselves
under the new **user menu (top right) → My crew profile**, choosing roles
but never a rate; those roles show as **self-reported** until an admin
clicks **Confirm**. If their email already belongs to a contact, nothing is
created and the admins get a notification. Team members are ordinary crew
with a small **Team** chip (contacts, Crew tab with a Team / Freelance
filter, the Add crew picker). **My schedule** shows a member their own
bookings only. On the server: pull, build, up — migration **0032** runs by
itself.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `a4707d1` | the prompt file and its INDEX line |
| 1 | `f185bbe` | migration 0032, the link service, `/team…` and `/me/…` routes, the Team marker on contacts / crew list / availability, tests |
| 2 | `f5c2825` | Settings → Team crew column and dialogs, Team chips, Team / Freelance filter |
| 3 | `5e2afac` | user menu, My crew profile, My schedule (+ the schedule API test) |
| 4 | `1d1053f` | SCOPE D142–D147, §13 row |
| close | (closing commit) | INDEX, this report, CI floors |

## Step 0 answers

1. **State.** Worktree `Claude_MS/filmbill-r2-team-crew-590c73` at
   `1909684` = `origin/main`, clean but for the copied prompt. Prompt 35
   (`docs/reports/R1-B2c1-report.md`) and 36 (`B2c2-report.md`) on
   `origin/main`; CI run 46044980240 on `1909684` green on all five jobs.
   Baselines (filmbill-api image, dev-stack Postgres 15 + Redis 7, `CI=1`,
   `git archive` export): API **3371 passed, 30 skipped**; `-m realdb`
   **1902** (B2c-2's closing count, same commit); web **1002 / 83 files**.
   Floors **3270** / **1845**.
2. **Logins today.** Settings → Company → Members
   (`/settings/company/members`, `GET/POST /company/members`,
   `PATCH/DELETE /company/members/{id}`, all `company.members.manage` =
   owner + admin): invite by email with role and optional expiry, change
   role, revoke (soft, `revoked_at`). Roles: owner, admin, accountant,
   producer, staff, tax_advisor. Name and email come from the `User`
   (`first_name last_name`, email) on each row.
3. **Crew after R1.** A crew person is a `Party` of type person with
   `is_crew`; roles are `party_crew_roles` (a labor item, an optional
   `own_day_rate`, retired by `is_active`); the Crew tab is Contacts →
   Crew (`GET /crew`), the strip picker uses `GET /crew/availability`;
   `crew_assignments` are `scheduled | booked | declined | cancelled`. An
   email lives in `party_contact_points` (kind `email`, `is_primary` per
   kind and purpose). There was no case-insensitive party-email helper; R2
   adds `team_crew_service.email_matches`.
4. **Notifications for admins: yes.** A `Notification` row per user
   (title, body, link) is read by the UI-1 popover through
   `GET /me/notifications`; nothing created one for business events yet.
   R2 adds the type `team` (enum value in migration 0032).
5. **The user menu.** There was none: the top bar held the company
   switcher, Search and the bell; the account was a plain link at the
   bottom of the sidebar to Settings → Profile. R2 adds a small user menu
   (avatar, top right) with Profile, My crew profile, My schedule.
6. **Who sees crew.** `parties.view` = owner, admin, accountant, producer
   (staff and the tax advisor do not, D111/D134). Crew rates: visible with
   `budgets.view` (same four), edited with `parties.edit` (owner, admin,
   producer).

## What was built

* **Migration 0032** — `parties.user_id` + `uq_parties_company_user`;
  `party_crew_roles.confirmed` (default true), `set_by` (`admin | self`,
  default admin); `notificationtype` + `team` (the downgrade rebuilds the
  enum and turns `team` notifications into `account` ones).
* **Routes** (`routers/team.py`, tag `team`):
  `GET /team`, `POST /team/{user_id}/crew` (`mode = link` with the confirmed
  `party_id`, or `create`; optional `role_item_ids`),
  `POST /team/{user_id}/crew/stop`, `POST /team/{user_id}/unlink`,
  `POST /team/{user_id}/crew/roles/{role_id}/confirm` — all
  `company.members.manage`; `GET /me/crew`, `PUT /me/crew`,
  `PUT /me/crew/roles`, `GET /me/schedule` — any active member but the tax
  advisor, the contact always the caller's own. Added fields:
  `is_team_member` (+ `team_user_id`, `team_login_email`) on parties,
  `is_team_member` on the crew list and availability, `confirmed` and
  `set_by` on crew roles; `GET /crew?kind=team|freelance`.
* **Audit actions:** `team.crew_linked`, `team.crew_created`,
  `team.crew_stopped`, `team.unlinked`, `crew_role.self_set`,
  `crew_role.confirmed`; self-service entries carry `self_service: true`
  and the member as actor. A contact created from a login also writes the
  ordinary `party.created` (source `team`).
* **Screens:** see the summary and D143–D146.

## Defaults chosen (say if any is wrong)

1. **Admin notification: yes** — one per owner and admin (type `team`,
   link to Settings → Team), not repeated while an earlier one is unread.
2. **Role confirmation:** every existing role and every role written by an
   admin path is confirmed; a member's own new roles are unconfirmed; saving
   the Team dialog's roles confirms the ones listed and makes the list
   exact (unticked ones are retired); a member saving again never undoes a
   confirmation.
3. **No name or email sync** between a login and its contact.
4. **"Needs link" is derived, not stored:** no linked contact and at least
   one active, unlinked **person** contact whose **primary** email matches
   (case-insensitive). Organisations and archived contacts never match.
5. **The tax advisor has no crew profile** (`/me/crew…` answers 404): the
   role is external and its access is the archive (D23/D43).
6. **"Stop working as crew" is `POST /team/{user_id}/crew/stop`, not the
   prompt's `DELETE`.** The existing guard
   `test_there_is_no_delete_route_for_crew_on_the_calendar` forbids a
   DELETE on crew paths, and crew's other soft actions are POSTs
   (`…/remove`). Same behaviour: `is_crew` false, link, contact and
   bookings kept.
7. **The members page is renamed "Team"** in the settings menu and its
   heading; the URL stays `/settings/company/members`.
8. **The stop sentence comes after the action** (a confirm dialog first
   says bookings stay and nothing is deleted); it names the count and the
   projects.
9. **My schedule lists earlier bookings too**, below the upcoming ones.
10. **The login's email on the person page** is visible to whoever may read
    contacts (`parties.view`); Unlink only for owner and admin.
11. **The user menu asks for the crew state when it opens**, not on every
    page load.

## Verification

### Run and observed

- API suite on `5e2afac` (the last commit touching `apps/api`; Part 4 and
  the close change none): **3427 passed, 30 skipped** (3371 / 30 before).
  `-m realdb`: **1957 passed, none skipped** (1902 before). Environment:
  `filmbill-api:latest`, the dev stack's Postgres 15 and Redis 7 on
  `filmbill_default` (own database and Redis db per run), `CI=1`, from a
  `git archive` export of the commit. Part 1 alone (`f185bbe`): 3425
  passed, 30 skipped.
- New tests, all real-DB (run as `realdb`, none skipped):
  `test_team_crew_api.py` (**29**, incl. the 6-row permission matrix and the
  0031 ⇄ 0032 migration round trip) and **9** new isolation cases × 3
  checks in `test_company_isolation.py` (`{user_id}` = the other company's
  member). The OpenAPI surface snapshot gained the 9 operations and the
  `team` tag.
- `alembic upgrade head` → `alembic check` ("No new upgrade operations
  detected") → `downgrade base` → `upgrade head` → `check`, on an empty
  database.
- Web: **1025 tests / 86 files** (1002 / 83 before) with lint, typecheck
  and build via `pnpm ci:web` (Linux, node 20, clean export of HEAD). New:
  `settings/company/__tests__/team-crew.test.tsx` (8),
  `my/__tests__/my-crew.test.tsx` (7),
  `components/layout/__tests__/user-menu.test.tsx` (3), plus 2 in
  `crew-tab.test.tsx`, 1 each in `parties-list`, `strip-crew` and
  `settings-nav` tests.
- Mutations, each a real FAIL line, each reverted (files restored from a
  copy, `git status` clean after):
  1. a self-service route takes the party id from the body (victim: Max's
     linked contact) → `FAILED test_a_self_service_route_never_touches_another_members_contact - AssertionError: assert [(UUID('b64d0…'), False, True), (UUID('c520…'), True, False)] == [(UUID('b64d0…'), True, True)]`.
  2. the email match links without the admin → `FAILED test_switching_on_with_a_match_creates_nothing_and_tells_the_admins - assert 200 == 409`.
  3. a self-set role stored as confirmed → `FAILED test_a_member_writing_roles_gets_self_reported_ones_and_an_admin_confirms - AssertionError: assert [('DoP', True, 'self'), …] == [('DoP', False, 'self'), …]`.
  4. the rate in `GET /me/crew` → `FAILED test_no_rate_in_any_me_response - AssertionError: GET /me/crew carries a rate: [('.roles[0].own_day_rate', '650.00')]`.
  5. the dialog links the suggestion after "Create a new crew contact instead" → `FAIL team-crew.test.tsx > “Create a new crew contact instead” sends create, and never the suggested contact — AssertionError: expected { mode: 'link', …(2) } to deeply equal { mode: 'create', role_item_ids: [] }`.
  6. `/me/schedule` without the party filter (victim: Max's booking on the same strip) → `FAILED test_my_schedule_is_my_own_bookings_only - AssertionError: Left contains one more item: '8791cfb2-…'`.
  7. (extra) My schedule shown to non-crew in the menu → `× hides My schedule when I am not crew, and keeps My crew profile to switch it on`.
- CI floors (rule 17e): **3270 → 3325** (3427 local, 3.0 % under) and
  **1845 → 1899** (1957, 3.0 % under).

### Statically checked only

- `tsc --noEmit` and `next lint` on the web (no errors; the two existing
  warnings in catalog and contacts pages are unchanged).

### Not checked

- The screens in a real browser (Mathias's acceptance on the server).
- The notification as it appears in the bell popover with real data (the
  popover's icon map gained `team`; the row is asserted in the database).

## SCOPE diff (to mirror to the Project)

```diff
diff --git a/docs/SCOPE.md b/docs/SCOPE.md
index 57fade5..4b80f69 100644
--- a/docs/SCOPE.md
+++ b/docs/SCOPE.md
@@ -565,6 +565,7 @@ Final invoice:
 | **B2b-2** ✅ built 2026-11-27 (prompt 34) | **Gear completion** after using the Gear pages on the server: rental prices on the device as its catalog item's rates, no "Rented out as" picker (D124); the Units tab and the Add new item(s) popup (D125); dependencies as data and screens, one level, loops refused, the setup hint (D126); alternatives, one-directional with the reverse offered (D127); weight and size (D128); one cover picture (D129). Open: the "dependencies changed, review?" notice (D126); staff reading cost rates in the catalog (D124). | Mathias, 2026-11-26 |
 | **R1 + B2c-1** ✅ built 2026-11-27 (prompt 35) | **Crew roles and rates; crew on the calendar**: roles are the catalog's labor items with an own day rate per person and role, effective rate own else typical, rates as cost data [Guessing] (D130); tags offered as roles, never converted (D131); assignments to strips with days, scheduled then booked by hand, bulk booking (D132); clashes and unavailability warn, never block, and the availability grid (D133); crew hidden from staff until D111 (D134). Not built: D135. | Mathias, 2026-11-26 |
 | **B2c-2** ✅ built 2026-11-27 (prompt 36) | **Gear booking**: devices booked on strips with days, `scheduled` then `booked` by hand, the status words replacing "option / confirmed" in the UI (D136); availability per day, the lowest day governing, cancelled ignored, overbooking warns and never blocks (D137); `always` dependencies added automatically, `ask` ones by popup, one level only, followers recalculated, removing a parent asks (D138); alternatives in the overbooking popup with a swap (D139); total weight on Project → Gear (D140); the Gear planner and the device's Bookings tab. Not built: D141. | Mathias, 2026-11-26 |
+| **R2** ✅ built 2026-11-27 (prompt 37) | **Team members as crew**: a login linked to at most one person contact per company, never merging anything (D142); admins and the member themselves switch "works as crew" on, an email match is suggested and an admin confirms, the needs-link case notifies the admins (D143); members set roles, never rates, self-reported until confirmed (D144); team members are normal crew marked "Team" (D145); My schedule, own bookings only, read only (D146). Not built: D147. | Mathias, 2026-11-27 |
 | **B3** after B2 — **not yet scoped** | **Content tracking**: deliverables, review rounds, a footage log. | Mathias, 2026-11-24 |
 | **B** | Documents proper: quotes, invoices, credit notes, snapshots, finalize, numbering, revisions, PDF. | P2 as written |
 | **C** | The rest of §4: devices with serial numbers, kits, several price lists per recipient, resources and reservations. | P1c's remainder plus P7's planner — each of these changes what an item is (D69), so they come after B has settled |
@@ -754,6 +755,12 @@ Compliance sources: [Brandauer – E-Rechnung AT 2026](https://brandauer-rechtsa
 | D139 | **Alternatives in the overbooking popup, with a swap (B2c-2, §4.4.5, D127).** A save that comes back overbooked opens the popup: the shortage, **who holds the device** (project, strip, dates, status, quantity), then the device's **alternatives in their order**, each with **its own availability on the same days**, whether it has enough, its note and price hint; **Swap** replaces the device on that booking and keeps its quantity, days and status (`gear_booking.updated` with the device before and after; a device already on the strip by the same route is a 409); **Book anyway** keeps it, with an optional note; **Cancel booking** cancels it and the dependencies added with it. A swap keeps the booking's dependents as they are and adds none of the new device's (default). Alternatives are never booked automatically. Sub-renting is not offered (D141). | 2026-11-27 |
 | D140 | **Total weight on Project → Gear (B2c-2).** `GET /projects/{id}/gear` gives per strip and for the project the bookings with their warnings, counts **in pieces** (2 cameras and 4 batteries are 6) — what the strip badge "Gear: 6 booked · 2 scheduled" shows, with a marker when anything is overbooked — and **`Σ quantity × weight_kg`** (kg, three decimals) with **"n items without weight"** counting the pieces whose device has none. No prices anywhere. The tab has one card per strip with the panel's own Gear section and "Open on the calendar". The device page's **Bookings** tab lists its open bookings in date order by project and strip, with status. | 2026-11-27 |
 | D141 | **Deliberately not built in B2c-2:** **specific units** at booking or check-out (a booking is a quantity of a device; serial numbers stay records, §4.4.2) — with check-out; **check-out and return** and **packing lists** (the warehouse phase, §4.5); **kits** (§4.4.6, each device inside would go through this same check); **sub-renting** from the overbooking popup (§4.4.7, P7); **prices and documents** — no day rate, no quote or invoice line is made from a booking — and with them **revenue and ROI** (§4.4.4); the **"dependencies changed, review?"** notice (D126 open); **budget lines created from bookings**; the hard shortage for serialized devices (§4.4.7: every shortage is soft with a count). | 2026-11-27 |
+| D142 | **A login is linked to at most one contact per company, and the link never merges anything (R2, Mathias 2026-11-27).** Migration 0032: `parties.user_id` (nullable, `ON DELETE SET NULL`) with a partial unique index on `(company_id, user_id)` where set; a contact has one column, so at most one login. A link is allowed only to an active **person** contact and only for a user with an **active membership in the same company** (revoked or expired: refused with a sentence; a user of another company: 404). Linking sets `user_id` and `is_crew` and nothing else — roles, assignments, tags, contact points and tax data stay as they were; **unlinking** clears `user_id` and keeps the contact. A revoked or deactivated login keeps its link (history); the Team page lists it as "no active login". Audit `team.crew_linked\|crew_created\|crew_stopped\|unlinked`, self-service entries marked `self_service`. | 2026-11-27 |
+| D143 | **Who makes a team member crew: admins and the member themselves; an email match is only a suggestion (R2, Mathias 2026-11-27).** Settings → Team (the members page, renamed; owner and admin, `company.members.manage`) shows per member **Not crew / Crew / Needs link**. "Needs link" = no linked contact and at least one active, unlinked person contact whose **primary email** is the login's (case-insensitive). **Exactly one** match is offered ("Link to the existing contact …?") and linked **only when the admin confirms it** (the contact's id comes back from the dialog); **two or more**: no suggestion, the admin picks; "Create a new crew contact instead" makes a person contact from the login's first and last name with the login email as primary email, crew, tag "Team". **Stop working as crew** (`POST /team/{user_id}/crew/stop`, there is no DELETE) sets `is_crew` false, keeps the link, and names the person's future bookings, which stay as they are. **Self-service** (`/me/crew…`, any active member except the tax advisor, no `parties.view` needed, the contact always the caller's own): switching on with no link and no match creates the contact exactly as "create"; **with a match nothing is created or linked**, the member reads "A contact with your email already exists. An admin has to connect it to your login.", and each owner and admin gets one notification (type `team`) — not again while it is unread. | 2026-11-27 |
+| D144 | **Members set their roles, never a rate; self-reported until an admin confirms (R2, Claude's condition on D143, accepted).** `party_crew_roles.confirmed` / `set_by (admin \| self)`; existing roles are `confirmed, admin`. `PUT /me/crew/roles` takes the list of labor items and nothing else; new ones are stored `confirmed = false, set_by = self` (`crew_role.self_set`), a role an admin confirmed stays confirmed when the member saves again, leaving one out retires it. An admin confirms with **Confirm** on the Team page or by saving the Team dialog's roles (`crew_role.confirmed`). **No `/me` request or response carries a rate**; own day rates stay with whoever may edit crew rates today (`parties.edit` + `budgets.view`), on the contact. | 2026-11-27 |
+| D145 | **Team members are normal crew, marked "Team" (R2).** Day rates, roles, the crew list's filters, availability, the picker, booking and the budget are the same for team and freelance crew. A linked contact carries `is_team_member` and shows a **Team** chip in the contacts list, on the person page ("Team · has a login: …", Unlink for admins), in the Crew tab — which gains a **Team / Freelance** filter (`GET /crew?kind=team\|freelance`) — and in the strip's Add crew picker. | 2026-11-27 |
+| D146 | **My schedule: my own bookings, read only (R2, Mathias 2026-11-27).** A user menu in the top bar (Profile, My crew profile; **My schedule** only when linked and crew; not sidebar items). `GET /me/schedule` returns only the scheduled and booked assignments of the contact linked to the caller's login in the active company, upcoming first then earlier, grouped by project and strip: project, strip, dates, role, status, call and wrap (own, else the day's, else the strip's), location, the booking note. Nothing about other crew, rates, budgets or gear. Empty: "No bookings yet." | 2026-11-27 |
+| D147 | **Deliberately not built in R2:** members editing their own "not available" periods; syncing name or email changes between a login and its contact (they are separate records once linked); a member confirming a booking themselves; confirmation by link (C3); pay or timesheets for team members; unlinking automatically when a membership is revoked (the link is history). | 2026-11-27 |
 
 ## 17. Questions for the Steuerberater
```
