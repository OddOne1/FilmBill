Before anything else, run `git remote -v`. This prompt is for the FilmBill repo (OddOne1/FilmBill). If the remote is anything else, stop, say so, and do nothing.

# Claude Code prompt — FilmBill UI-1: sidebar, notifications popup, no Crew menu item, calendar always six weeks

> This file is already saved in `docs/prompts/` as `33-filmbill-UI1-nav-notifications-calendar-weeks.md`. Do not copy or rewrite it. Add its line to `docs/prompts/INDEX.md` and commit both as the first commit, then work. **Run this only after prompt 32 (P1b-4, Contacts rework) is merged and CI on `origin/main` is green on all five jobs** (`docs/reports/P1b4-report.md` on `origin/main`); otherwise stop and say so. The menu item names below assume "Contacts" exists.
> Read CLAUDE.md and obey it (behavioural tests with the real API shapes, real-FAIL mutations, three-tier report, rule 17e CI floors, no hard delete). No migration is expected; if you find you need one, stop and report why.
> **Why (Mathias, looking at the running server, 2026-11-26):** (1) The left sidebar should be **expanded by default**. (2) Notifications should be a **small popup from the top-right, next to Search**, not a full-height panel that covers the sidebar (see his screenshot: the panel slides over the menu and cuts its labels). (3) Crew are just people, so there is **no separate Crew menu item**; crew live in Contacts → the Crew tab, which already exists. (4) The project calendar must **always show six weeks**, and the "Booked days" strip should sit **directly under the calendar at the calendar's width**. (5) The **Archive** menu item is empty today and its purpose (the bookkeeping document archive, P5) is far off.
> **Working rules:** merge to `origin/main` and push before you report; report the CI run of the final commit, all five jobs. Each numbered part is its own commit, green on its own.

## Step 0 — report these, then continue
`git status -sb`, baselines (API, real-DB, web), the two CI floors; the sidebar component and how its collapsed state is stored and defaulted; the notification panel component, its API and unread count; the top bar component and where Search lives; the Crew page files and what it contains (list, quick add, CSV import) and what the Contacts → Crew tab already offers; the calendar's month-grid code from B2a and the Booked-days strip; the Archive page and where the nav item is defined.

## Part 1 — sidebar expanded by default
Expanded is the default for a user who has never chosen. The collapse button stays and the choice is remembered per browser in `localStorage` wrapped in try/catch (a per-viewer convenience only). **A previously stored "collapsed" value from before this change must not keep Mathias's browser collapsed**: store the new choice under a new key (version it) so everybody starts expanded once. Tests: no stored value → expanded; stored new key honoured; storage throwing → expanded; an old-key value is ignored.

## Part 2 — notifications as a small popup in the top bar
- The bell moves into the **top bar at the right, beside Search**, with an **unread count badge** (hidden at 0, "9+" above nine). The sidebar "Notifications" item is removed.
- Clicking it opens a **compact popover anchored under the bell** (about 380 px wide, at most about 480 px tall, scrolling inside), not a full-height drawer. Contents as the current panel has them: All / Unread tabs, the list, "Mark all as read", the settings gear linking to the notification settings, and the empty state ("No updates yet"). A "See all" link goes to the full page if one exists; do not build a new page.
- It closes on Escape, on an outside click and when navigating; focus moves into it on open and returns to the bell on close; it is reachable and operable by keyboard; it never covers the sidebar.
- Component tests with the real response shape: badge counts, popover opens and closes by click, Escape and outside click, tabs filter, mark all read updates the badge, the empty state, no sidebar item. **Mutation, a real FAIL line:** the badge shows a count when everything is read. Revert.

## Part 3 — no Crew menu item; crew work happens in Contacts
- Remove the **Crew** item from the sidebar. `/crew` **redirects** to the Contacts list with the Crew tab selected (keep any deep links working).
- Whatever the Crew page offered that Contacts → Crew does not (quick add, CSV import and export, role suggestions, search by role) moves into the **Crew tab's toolbar** of the Contacts list: **Quick add crew**, **Import CSV**, **Export CSV**. Nothing is lost; say in the report what moved.
- Tests: the sidebar has no Crew item; the redirect; the toolbar actions work from the tab; permission behaviour unchanged (`parties.view` to see, `parties.edit` for the actions).

## Part 4 — calendar: always six weeks, Booked days under it
- **The month grid always has six week rows (42 days), Monday first.** If the month needs six rows, nothing changes. If it needs five rows, one extra week is added on the side with **fewer filler days** (filler = days of the neighbouring month in the first row for the "before" side, in the last row for the "after" side); a tie adds it **after**. If it needs four rows (a February that starts on a Monday and ends on a Sunday), **one extra week is added before and one after**. Examples that a test must pin: **March 2027** (starts on a Monday, 0 leading days, 4 trailing days) shows the week 22–28 February first; **February 2027** shows 25 January–31 January first and 1–7 March last; **August 2026** needs six rows by itself.
- Implement as a **pure function** `monthGrid(year, month)` with a table test over every month of 2026 to 2028 asserting: always 42 days, starts on a Monday, contains every day of the month, and the extra-week rule above. Strips and day cells in the filler weeks are drawn like any other day (the data request covers the **grid's** date range, not the month's).
- **Booked days strip:** it sits directly under the calendar, **the same width as the calendar** (not the full page), and fits inside it: months wrap onto further lines or it scrolls horizontally inside its box, never beyond the calendar's width. Its click behaviour stays.
- Tests: the table test above; the layout widths via class or container assertions (no pixel measurements); the data request range equals the grid. **Mutation, a real FAIL line:** the extra week is added on the wrong side for March 2027. Revert.

## Part 5 — the Archive menu item stays hidden until it has content
Hide **Archive** from the sidebar behind a single named constant (`ARCHIVE_NAV_ENABLED = false`) with a comment saying it comes back with P5, the bookkeeping document archive (SCOPE §9: outgoing documents, incoming bills and receipts, year/quarter/month folders, handover packages for the tax advisor). The route stays; if someone opens it by URL it shows one plain sentence that the document archive is not available yet. Do **not** change the per-list "Show archived" toggles: those show items that were archived inside their own list, which is a different thing. The permission key `archive.view` stays. Test: the item is absent while the constant is false and present when it is true (a test that flips it). Record this in SCOPE as a decision Mathias can reverse by flipping the constant.

## Part 6 — documentation (separate commit)
SCOPE next free D-numbers: (a) sidebar default expanded and the versioned storage key; (b) notifications as a top-bar popover; (c) crew are not a menu item, they live in Contacts → Crew; (d) the calendar always shows six weeks and the extra-week rule; (e) Archive hidden until P5 and why. Report the exact diff so it can be mirrored to the Project.

## Closing steps
1. Merge to `main`, push, CI for the final commit all five jobs; raise both CI floors to within 3 % of the current counts (rule 17e) and say what you set.
2. Say exactly what Mathias must do on the server beyond `git pull`, `build --no-cache`, `up -d` (expected: nothing).

## Acceptance for Mathias — on the SERVER, about 10 minutes
1. Reload: the sidebar is expanded. There is no Crew and no Archive item. The bell sits next to Search; clicking it opens a small popup that does not cover the menu.
2. Contacts → Crew tab: quick add, import and export are in its toolbar. An old `/crew` bookmark lands there.
3. Project 1000m, Days tab: March 2027 starts with the week of 22–28 February and always shows six rows; step through every month of the project: always six rows. February 2027 has a week before and a week after.
4. The Booked days strip sits right under the calendar and is not wider than it.

## Report format
A 1–3 sentence plain-language summary first, **under 600 words in total**; details in `docs/reports/UI1-report.md`. Then three tiers: run and observed, statically checked only, not checked (the popup position in a real browser is not checkable here; say so). List test counts with the environment declared, the SCOPE diff, the Step 0 answers, what moved off the Crew page, the floors you set, and every default you chose.
