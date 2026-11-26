# P1b-4 report — the contacts rework

Prompt: `docs/prompts/32-filmbill-P1b4-contacts-rework.md`. Built 2026-11-26.

**In plain words.** The screen is called **Contacts** now, and old `/parties`
bookmarks still open. People have first, middle and last names; the people
already on file are **not** split automatically and say so on their
page and under Needs attention until you enter the parts. A person can tick
**"Also works as a business"** and stays one record. On the Tax tab you type
the VAT number straight in; it is checked at once. The list uses the whole
window as a table. On an organisation you add people and departments right
on its Hierarchy tab, or with the organisation itself.

## Commits

| Part | Commit | What |
|---|---|---|
| prompt | `ce142a0` | the prompt file and its INDEX line |
| 1 | `123c24c` | migration **0024**: first/middle/last name, `name_addition`; derived `name`; sort, search, Needs attention; crew CSV both styles |
| 2 | `b9e83f3` | migration **0025**: `is_sole_proprietor`, `business_name`; resolver `invoice_name` / `owner_name`; `auto` → business |
| 3 | `2bd7918` | Tax tab direct entry; `POST /parties/{id}/tax-ids/detect`; `direct_entry` in `tax_id_formats.yaml`; the note relabelled |
| 4 | `97172c4` | Contacts everywhere, `/contacts` + redirects, "Phone & email", the table, column picker, restricted columns |
| 5 | `353c4a3` | Add person / Add department, "Add people now" in one transaction, moving a person in and out |
| 6 | `efab711` | SCOPE D112–D118, §17 Q11, §3, §13 |
| close | (closing commit) | CI floors 3010 / 1600, INDEX, this report |

Each part was green on its own: the full API suite on each part's tree
(part 1 on its commit: 3011; part 3: 3071; part 4: 3078; part 5: 3097, all 0
failed), the web suite, typecheck and lint before each web commit.

## Step 0 answers

1. **State.** Clean tree on `cc10ce2` = `origin/main`. B2b's CI on
   `origin/main` (run 45842578273) green on all five jobs. Baselines (clean
   export, environment below): API **2984 passed, 30 skipped**; `-m realdb`
   **1568**; web **811 / 73 files**. Floors were 2900 / 1525.
2. **The model as built.** `type ∈ organisation|department|person`; CHECKs
   `ck_parties_type`, `ck_parties_business_status`,
   `ck_parties_organisation_has_no_parent`, `ck_parties_not_its_own_parent`.
   Parents (`ALLOWED_PARENTS`, enforced in `party_service.validate_parent`):
   organisation none; department → organisation (required); person →
   organisation or department or nothing; three deep at most, no cycles.
   `name` NOT NULL; `trading_name`, `legal_form`, `title`, `position` free
   text. `business_status = auto`: the nearest level that is NOT `auto` wins
   (`private`/`business` set by hand); otherwise an organisation, a
   department, or anybody inside one is business; a person with a VAT-like
   ID is business (`person_with_vat_id`); otherwise private. `invoice_name`
   is the **outermost** level's name; `contact_name` is the party's **own**
   name and does not inherit.
3. **Where `name` is read.** Parties list, search and sort; every picker
   (project client, budget supplier, gear seller, tax settings "take buyer
   from"); crew list, search, quick-add duplicate check, import context and
   export; duplicate warnings; budget actuals CSV payee matching (with
   `trading_name`); device seller; audit snapshots and every sentence that
   names a party. **All keep the single `name` string** — which is why it
   stays the stored display name and is derived rather than replaced.
4. **D65 / D59.** `tax_status_note` is free text on the Tax tab, resolved and
   inherited like any field, read by nothing that computes tax
   (`tax_inputs` does not take it) and not snapshotted yet (P2 snapshots the
   resolved recipient). D59: kinds `vat`, `uid_ch`, `abn`, `ein`,
   `tax_number`, `company_register`, `other`; **the kind was chosen by the
   user in the Add dialog — there was no detection.**
5. **"Parties" on screen.** Sidebar, list title, tabs label, search label,
   empty state, read-only notes, New dialog, detail breadcrumb and errors,
   "Roles — a party can be several at once", crew page notes, project
   client note, tax settings ("Take buyer from a party", "Party override").
   The detail tab "Contacts" held emails and phones.
6. **Files.** `app/(dashboard)/parties/page.tsx` and `[id]/page.tsx`, both
   `max-w-5xl`; the list's chip was a local `Chip` with no fixed height and
   no `whitespace-nowrap` — which is why "VAT checked" broke onto two lines.

## Migration and audit

- **0024_party_name_parts**: `first_name`, `middle_name`, `last_name`,
  `name_addition` (nullable) + `ck_parties_name_parts_person_only`,
  `ck_parties_name_addition_not_person`. No data migration.
- **0025_party_sole_proprietor**: `is_sole_proprietor` (NOT NULL, default
  false), `business_name` + `ck_parties_sole_proprietor_person_only`.
- `party.created` / `party.updated` diffs now carry the name parts,
  `name_addition`, `is_sole_proprietor`, `business_name`; `party.created`
  carries `email`/`phone`, and with "Add people now" `people_created` and
  the names (one event for the organisation and its people).

## Verification

**Environment (declared):** API in `filmbill-api:latest` (Python 3.11) on
`filmbill_default` against the dev stack's Postgres 15 and Redis 7, `CI=1`,
CI's env vars, on `git archive` exports. Web: macOS, Node 24, vitest; and
`pnpm ci:web` (Linux, node 20, clean export of `efab711`).

**Run and observed**
- API full suite on `efab711`: **3097 passed, 30 skipped, 0 failed** (2984
  before). `-m realdb`: **1647 passed, 0 skipped** (1568 before).
  **79 of the 113 new API tests are real-database tests** and so run, not
  skip, in CI's real-database step (`test_party_names.py`,
  `test_party_sole_proprietor.py` endpoint half, `test_party_tax_entry.py`
  endpoint half, `test_party_list_columns.py`, `test_party_children.py`, the
  new isolation case); the rest are pure tables on the resolver and the
  detection.
- One full run alongside the real-DB run failed
  `test_rate_limiter_is_live` (422 instead of 429): both runs shared Redis
  DB 0 and that test counts requests there. Re-run alone on the same commit:
  3097 passed, 0 failed. Untouched code; my two runs collided.
- Web: **858 tests / 76 files** (811 / 73 before); typecheck clean; lint the
  two older warnings only. `pnpm ci:web`: lint, typecheck, 858 / 76, build
  (`/contacts`, `/contacts/[id]`) — green.
- `alembic upgrade head` → `alembic check` (no new operations) →
  `downgrade base` → `upgrade head`; migration tests run 0023→0024→0023→0024
  and 0024→0025→0024→0025 with rows, and the CHECKs fire in the migrated
  schema.
- **Mutations, each a real FAIL line, each on a scratch copy, reverted:**
  - `name` not rebuilt after a first-name edit: `FAILED test_editing_a_first_name_rebuilds_the_name - AssertionError: assert 'Anna Huber' == 'Johanna Huber'` (+3);
  - the old `name` column import into `last_name`: `FAILED test_the_old_name_column_is_stored_as_given_and_flagged_not_split - AssertionError: assert (None, None, ...p Maximilian') == (None, None, None)`;
  - `invoice_name` ignores the business name: `FAILED test_a_business_name_wins_and_the_person_becomes_the_owner - AssertionError: assert {...'Sam Licht'} == {...'Studio Licht'}` (+1);
  - `auto` ignores the flag: `FAILED test_a_sole_proprietor_without_a_business_name_is_invoiced_under_their_own_name - AssertionError: assert 'private' == 'business'` (+2);
  - the direct field saves without the check: `FAIL … a typed number is detected, added through the ordinary create, and checked at once — expected [ '/parties/p1/tax-ids/detect', …(1) ] to deeply equal [ …(2) ]`, missing `/parties/p1/tax-ids/t9/check`;
  - the restricted column rendered without `parties.edit`: `FAIL … the internal columns are absent, header, cells and picker, without parties.edit`; and the API side (list stops withholding): `FAILED test_the_internal_columns_reach_only_somebody_who_may_edit[accountant-True-False] - assert False is True`;
  - a failing child row leaves the organisation: `FAILED test_a_failure_in_one_row_creates_nothing_at_all[bad_row0-…] - AssertionError: assert [('Doomed GmbH',)] == []` (×3).
- Two real defects the new tests found on their first run: the wording
  guard found "Roles — a party can be several at once"; the role walk found
  a VAT number in a reader's row (a false alarm — the placeholder example for
  DE is the same digits; fixture changed).

**Statically checked only:** the generated API types (regenerated each part;
CI's drift job will confirm).

**Not checked:** the responsive layout and column drop in a real browser
(jsdom has no media queries; the tests assert the breakpoint classes); the
redirects in a running Next.js (the test reads the table Next.js reads);
migrations 0024/0025 on the server's real data; a live VIES answer for the
direct-entry check.

## Defaults I chose, and things you should know

- **There is no contact CSV**, only the crew CSV. The crew CSV now reads
  `first_name;middle_name;last_name` (plus German aliases vorname, nachname,
  …) and the old `name`; the export writes the three parts **and** the
  display name, so an unsplit person survives a round trip.
- **Staff cannot open Contacts at all** (D58: no `parties.view`). The role
  that sees the list *without* the internal columns is the **Accountant**
  — use an accountant for acceptance step 4. The detail page still shows a
  reader the Tax tab and payment terms, as before (open in D117).
- **One audit event for "Add people now"** (as asked): the people get no
  `party.created` of their own; the organisation's event lists their names.
- **First check on direct entry** = Check now right after the ordinary
  create, which also schedules the initial series as before. The beat may
  check once more shortly after; harmless, append-only.
- **Kind detection is data** (`direct_entry` in `tax_id_formats.yaml`): a
  `vat` only counts for a country with a VAT format; `abn` only for AU,
  `ein` only for US; anything else unmatched without an EU prefix asks
  "What kind of number is this?" with the free-text kinds.
- **The wording guard reads the rendered screen**, not the source — a
  source grep would break CLAUDE.md rule 11. It covers the list (all
  states), every detail tab, crew and the sidebar, with a probe.
- Title is not part of the derived name. A `name` sent beside the parts
  that differs is refused. A middle name alone is refused.
- A business name is refused unless the box is ticked; unticking clears it.
- API sentences people read now say contact ("Contact not found", "There
  is already a contact called …"); the tree's refusals name the contact and
  say what to do.
- Redirects are 307 (not permanent), so a browser never caches them for good.
- CI floors: **3010** and **1600**.

## On the server, beyond `git pull`, `build --no-cache`, `up -d`

Nothing extra. Migrations **0024_party_name_parts** and
**0025_party_sole_proprietor** run at API start with `alembic upgrade head`.
Rebuild all five services together, as always.

## SCOPE diff (to mirror to the Project)

```diff
diff --git a/docs/SCOPE.md b/docs/SCOPE.md
index 045d668..ac39de3 100644
--- a/docs/SCOPE.md
+++ b/docs/SCOPE.md
@@ -72,7 +72,7 @@ Model follows Odoo's unified partner approach (one table, hierarchy, role flags)
 - **Party**: `type = organisation | department | person`, `parent_id` (person → department → organisation), role flags `is_customer / is_supplier / is_partner` (multi-select, extensible tags), and since B2b `is_crew` — people who can be booked on a production, their roles as tags, no number when crew is their only hat (D105).
 - **Organisation fields**: legal name, trading name, legal form, register no. (Firmenbuch/HRB), VAT ID (with **VIES check** + date/result stored), tax number, tax country, default currency, language, payment terms, default tax profile, addresses (billing, delivery/shooting, postal), bank accounts, e-invoice routing (Peppol ID, DE Leitweg-ID, AT e-Rechnung.gv.at), customer/supplier number, notes, tags.
 - **Department**: own name, own billing address, own cost-centre/PO reference, own invoice email (e.g. "ORF – Abteilung Unterhaltung").
-- **Person**: name, title, role/position, email(s), phone(s), language; may have no parent (private client / freelancer).
+- **Person**: first, middle (optional) and last name with the display name derived from them (D112), title, role/position, email(s), phone(s), language; may have no parent (private client / freelancer). A person can also be a **sole proprietor** — one record, person and business (D114). Organisations and departments have a second line, `name_addition` (D113). The screen calls all of them **Contacts** (D116).
 - **Inheritance rule** (resolved at document creation, then snapshotted): each field on a document recipient = first non-empty of `person → department → organisation`. Billing address and VAT ID always come from the nearest level that *has* them; the contact name stays the person. UI shows inherited values greyed with their source.
 - **Validation before finalize**: invoice cannot be finalized if the resolved recipient lacks what the selected tax rule needs (e.g. reverse charge without verified VAT ID → blocking error, overridable only with reason).
 - Dedup on VAT ID / email; merge tool (later).
@@ -554,6 +554,7 @@ Final invoice:
 | **B1.2** ✅ built 2026-11-25 (prompt 29) | **Adjustments by section and kind, production fee preset, country dropdown, clearer messages**: adjustments on a section or sub-section and on certain kinds, Equipment / Crew discount quick-adds (D94); the production fee as one adjustment per budget with a company preset (D95) and a per-extra choice (D98); countries stored 2-letter and shown 3-letter from one list (D96); messages that name whose problem it is (D97). D91 and D92's open decisions recorded. | Mathias, 2026-11-24/25 |
 | **B2a** ✅ built 2026-11-25 (prompt 30) | **The project calendar**: strips over days with days as the source of truth for counts (D99), distinct-date counting and the strip-limited global (D100), the migration that turned existing days into strips (D101), travel/off as day overrides and every day edited on its own (D102), the calendar screen with the booked-days strip (D103). Not built: D104. | Mathias, 2026-11-25 |
 | **B2b** ✅ built 2026-11-26 (prompt 31) | **Crew and gear records**, before anything is booked: crew as parties with a third flag, no number when crew-only, roles as tags, quick add, CSV import/export (D105); devices with the planning quantity and units with serial numbers, independent by design (D106); removing and reactivating a unit with the quantity question, six unit statuses, removal reasons (D107); device and unit purchase prices, the unit's seller and invoice number (D108); the two permission keys (D109). Not built: D110. Open: who may see how much of the crew list (D111). | Mathias, 2026-11-26 |
+| **P1b-4** ✅ built 2026-11-26 (prompt 32) | **Contacts rework** before B2c, which shows people's names everywhere: names in parts and no automatic split (D112), organisations' second line (D113), sole proprietors as one record (D114), direct tax-ID entry and the note's wording (D115), Contacts as the UI word with redirects (D116), the full-width list and its internal columns [Guessing] (D117), people created inside an organisation (D118). | Mathias, 2026-11-26 |
 | **B2c** next — builds on B2a and B2b | **Bookings of gear and crew**: reservations against B2a's days and strips, availability, clashes (warn, never block — Mathias, 2026-11-26), the planner, crew availability. Scoped after B2b is reviewed. | Mathias, 2026-11-26 |
 | **B3** after B2 — **not yet scoped** | **Content tracking**: deliverables, review rounds, a footage log. | Mathias, 2026-11-24 |
 | **B** | Documents proper: quotes, invoices, credit notes, snapshots, finalize, numbering, revisions, PDF. | P2 as written |
@@ -714,10 +715,17 @@ Compliance sources: [Brandauer – E-Rechnung AT 2026](https://brandauer-rechtsa
 | D109 | **`devices.view` and `devices.edit`. [Guessing — the prompt's proposal, not yet confirmed.]** View: owner, admin, producer, accountant and **staff** (the crew needs to know what exists); edit: owner, admin, producer. **Purchase prices, sellers and invoice numbers are cost information** and are served (and accepted) only for a role that also holds `budgets.view` — staff get the gear, the counts and the serial numbers, and `price_visible: false` / `purchase_visible: false` with no price, seller or invoice number anywhere, the export included. Missing permission answers 404, as everywhere. | 2026-11-26 |
 | D110 | **Deliberately not built in B2b:** bookings, availability and clashes (B2c — they need these records first); rented-in gear; kits and associated items/dependencies (§4.4.3, §4.4.6); maintenance and service dates; barcodes or QR labels and system IDs / inventory numbers (§4.4.2); day rates for crew (the catalog's labor items carry rates); crew availability and unavailability (B2c); per-device photos or documents. | 2026-11-26 |
 | D111 | **Open: who may see how much of the crew list (Mathias, 2026-11-26).** Some team members must see the crew page and others not, and at different depths — some only names, some name, phone and email, some everything including billing address and birthday. Not decided: whether access is per role or per member, which levels exist, which fields each shows, and a birthday field (parties have none). Until it is decided the Crew page needs `parties.view`, which staff do not hold. To be scoped with B2c. | 2026-11-26 |
+| D112 | **A person's name is first, middle (optional) and last; `name` is derived and never split by guessing (P1b-4, Mathias 2026-11-26).** `parties.first_name/middle_name/last_name` (migration 0024). `parties.name` stays NOT NULL and stays the one display string every query, picker, budget, project and audit label reads; for a person with a first or last name it is **derived on write** as "First Middle Last" by a mapper event on `Party`, so no write path (router, crew quick add, CSV import, a script) can store a name that disagrees with the parts, and a `name` sent beside the parts that differs is refused. **Existing people are not split automatically** — "Reisinger Philipp Maximilian" is surname first and a split would be a guess — so they keep their stored name, show "Name not split into first and last yet" on their page and sit in the list's Needs attention view (reason `name_not_split`) until somebody enters the parts. Lists sort people by last name (by `name` when there is none), everybody else by `name`; search finds any part. The crew CSV reads `first_name;middle_name;last_name` and still the old single `name` (stored as given, no parts, flagged); the export writes the three parts plus the display name, so an unsplit person survives a round trip. The title is not part of the derived name. | 2026-11-26 |
+| D113 | **Organisations and departments get a second line, `name_addition` (P1b-4).** "Marketing department", "Studio Vienna", "c/o …". Shown in lists, on the detail page and in the Hierarchy list; searchable. A CHECK keeps it off people. **Open, P2:** whether it prints on a document's recipient block, and where — not decided here. | 2026-11-26 |
+| D114 | **A sole proprietor (Einzelunternehmen) is one record: a person with a flag (P1b-4).** `parties.is_sole_proprietor` + optional `business_name` (migration 0025), person-only by CHECK. The person can be crew, supplier and customer at once and carries typed tax IDs and checks exactly as an organisation does; nothing creates a second party. `business_status = auto` resolves a sole proprietor to **business** (resolver reason `sole_proprietor`), whether or not a VAT ID is on file; a hand-set `business` or `private` still wins. **[Guessing]** — the resolver's `invoice_name` is the **business name if one is set, otherwise the person's full name**, and when the business name wins the person is exposed as `owner_name` so a document can print both; the registry's name check compares against the business name when there is one. Which name must stand on the invoice is §17 Q11. | 2026-11-26 |
+| D115 | **The Tax tab takes the number directly (P1b-4).** "VAT / tax ID (optional)" is an open field at the top while the contact has no active VAT-like identifier, with a placeholder from examples the data already carries (`vat.py`'s per-country examples, the Swiss and Australian authorities' worked examples; none where none is known — no invented formats). Saving asks `POST /parties/{id}/tax-ids/detect`, which reads the kind from the country for tax and the format (`direct_entry` in `tax_id_formats.yaml`: `uid_ch` and `vat` everywhere, `abn` only for AU and `ein` only for US, and a `vat` counts only for a country with a VAT format) and writes nothing; the screen then adds it through the ordinary `POST /tax-ids` (primary, initial series scheduled) and runs its first check at once. An ambiguous number gets one question — "What kind of number is this?" with the free-text kinds — and a number that claims an EU prefix but does not fit is refused with the normaliser's sentence and nothing is saved. "Add another identifier" keeps the dialog flow. **The note** (`tax_status_note`, D65) is relabelled "Why there is no VAT ID (optional)", shown only while the contact is a business with no active identifier, and says what it is: a note for you and your accountant that changes no tax calculation — which is what the code does (it is resolved and inherited and nothing reads it for tax). | 2026-11-26 |
+| D116 | **The screen says Contacts, the code says party (P1b-4).** Every user-facing Parties/Party is Contacts/Contact; the pages are `/contacts` and `/contacts/[id]`, with 307 redirects from `/parties` and `/parties/:id` so no bookmark breaks; the emails-and-phones tab is "Phone & email". API paths, permission keys, models, identifiers and test names keep party (CLAUDE.md 8b). API sentences a person reads say contact too. The guard reads rendered text and the aria-label/placeholder/title/alt of the list, every detail tab, crew and the sidebar — not the source (rule 11) — with a probe proving it can fail. | 2026-11-26 |
+| D117 | **The Contacts list is a full-width table, and its internal columns are for `parties.edit` [Guessing] (P1b-4).** Columns: number, name (second line beneath), type, role chips, country, city, primary email, phone, tags; for `parties.edit` also VAT status, payment terms, tax status and last check date. Chips are fixed-height and never wrap; past two they become "+N" with every role in the tooltip. Columns drop by breakpoint — tags and city first, then phone, then email; name, type and roles never. A column picker hides columns, remembered per browser in `localStorage` (a convenience, every access guarded). `GET /parties` serves the contact columns to `parties.view` and **withholds** the internal ones from a list served without `parties.edit` (`restricted_withheld: true`). Today that role is the accountant only — staff hold no `parties.view` at all (D58) — and the detail page still shows a reader the Tax tab and payment terms, as before; whether the detail should withhold them too is open. | 2026-11-26 |
+| D118 | **People and departments are created inside an organisation (P1b-4).** The Hierarchy tab has Add person (title, first, middle, last, position, email, phone, and B2b's Crew box and roles) and Add department (name, additional line), creating the child with `parent_id` set and inheriting through the resolver exactly as before; the child appears in the list in place, "Add, then add another" keeps the dialog open. `POST /parties` takes `email` and `phone` (primary contact points in the same transaction) and, for an organisation, `people` — repeatable rows created **in one transaction with it: one refused row and nothing is created**, and **one `party.created` event** with `people_created` and the names (the people have no event of their own). A person's page moves them under an organisation or department and back out. The tree's refusals name the contact and say what to do (17f). | 2026-11-26 |
 
 ## 17. Questions for the Steuerberater
 
-**Status 2026-10-24 — three open questions, Q8, Q9 and Q10; Q10 extended after P1b-2 with the qualified-procedure, non-EU-proof, re-check-interval and Kleinunternehmer parts.**
+**Status 2026-11-26 — four open questions, Q8, Q9, Q10 and Q11; Q10 extended after P1b-2 with the qualified-procedure, non-EU-proof, re-check-interval and Kleinunternehmer parts; Q11 added by P1b-4 (sole proprietors' invoices).**
 
 Q8, from D45: out-of-order document dates. FilmBill now allows a document
 dated before the latest one already numbered in its series, with a warning,
@@ -934,6 +942,31 @@ the **issuing company itself** is a small business — no VAT shown, an
 exemption notice on the invoice? What wording, and from when does the switch
 apply once the threshold is exceeded?
 
+
+**11. Sole proprietors' invoices (from P1b-4) — [Guessing] until answered.**
+A sole proprietor (Einzelunternehmen) is one record in FilmBill: a person who
+also works as a business, with an optional business name. When we invoice
+one, FilmBill currently makes the invoice out to the **business name if one is
+set, otherwise to the person's full name**, and carries the person's name as
+the owner when the business name is used. (a) Which name must the invoice
+carry — the registered business name (Firmenwortlaut), the owner's name, or
+both? (b) Must "e.U." appear when the business is registered in the
+Firmenbuch, and must the owner's name appear even then? (c) For an
+unregistered sole proprietor trading under a made-up name, is the person's
+own name required on the invoice?
+
+**11. Rechnungen an Einzelunternehmer (aus P1b-4) — [Annahme] bis zur
+Antwort.** Ein Einzelunternehmen ist in FilmBill ein Datensatz: eine Person,
+die auch unternehmerisch tätig ist, mit optionalem Firmennamen. Derzeit stellt
+FilmBill die Rechnung **auf den Firmennamen aus, wenn einer erfasst ist,
+sonst auf den vollen Namen der Person**, und führt den Namen der Person als
+Inhaber mit, wenn der Firmenname verwendet wird. (a) Welcher Name muss auf der
+Rechnung stehen — der Firmenwortlaut, der Name des Inhabers oder beide? (b)
+Muss bei einer im Firmenbuch eingetragenen Firma "e.U." aufscheinen, und muss
+der Name des Inhabers dann trotzdem genannt werden? (c) Muss bei einem nicht
+eingetragenen Einzelunternehmer mit Fantasiebezeichnung der eigene Name auf
+der Rechnung stehen?
+
 ---
 
 **Status 2026-09-16:** answered by Mathias for YON — Q2/Q3 → configurable policies (D12), Q4 Soll (D13), Q5 OK provided every item is listed (already §8.1), Q6 not used by YON (D15), Q7 → selectors (D13). Q1 (studio rental) → **not built into the software** (D19): YON configures a per-item tax override for its studio once the advisor answers. Optional check: whether the YON rebill practice (gross base + 20 %) matches how the advisor books the input VAT. Original questions kept below for other users' onboarding docs.
```
