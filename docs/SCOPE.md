# FilmBill v2 — Scope & Architecture

> Status: **DRAFT v0.11 — 2026-09-20** (decisions rounds 1–8 applied; inventory, associated items, alternatives, kits) · Owner: Mathias (YON Studio OG, formerly 257 Studio OG / yon.studio)
> Target audience: production companies in film, photo and media — Rentman-grade gear/crew handling is a differentiator, not an add-on.
> Role split: **Cowork** = scoping, acceptance tests, troubleshooting between builds. **Claude Code** = all implementation, one phase prompt at a time.
> Confidence tags: **[Certain]** verified source · **[Likely]** strong reference · **[Guessing]** needs confirmation (usually by your Steuerberater).

---

## 0. Why v1 failed and what v2 does differently

v1 grew feature-first: invoices, templates and PDF came before the data model was stable, so every schema change meant a DB reset and every rule (totals, VAT, partial billing) lived in several places. v2 inverts that.

| # | Principle | Concretely |
|---|---|---|
| P1 | **One calculation engine** | A single pure Python module (`billing/calc`) computes every line, group, adjustment, tax and total. The frontend never computes money; it asks the API for a preview. Covered by golden fixtures (§12). |
| P2 | **Finalized documents are versioned, never overwritten** | Finalize locks the document. Corrections via Storno **or** a guarded *Revise* that creates revision r2, r3… under the same number; every sent revision's PDF + data stays retrievable, with who/when/why and a visible banner (§5.5). [Likely] BAO §131 requires that records are not altered such that the original content becomes unrecognisable — revisions satisfy that, in-place overwrites don't. |
| P3 | **Snapshots, not live references** | On finalize, customer address, VAT ID, own company data, item names, tax codes and layout version are copied into the document. Editing a company later never changes an issued invoice. |
| P4 | **Gap-free numbering at finalize** | Number series per document type (and optionally per company/year), assigned inside the finalize transaction with a row lock. Drafts have no number. |
| P5 | **Money is Decimal, never float** | `NUMERIC(18,4)` for rates/quantities, `NUMERIC(18,2)` for amounts, explicit rounding rules (§5.6). |
| P6 | **Migrations from day one** | Alembic baseline in P0. "rm -rf postgres" is never a valid step again. |
| P7 | **Types generated, not hand-copied** | TypeScript API types generated from FastAPI's OpenAPI schema in CI (fixes FreeFrame's `types/index.ts` drift class of bug). |
| P8 | **Everything posts to a journal** | Finalizing an invoice/bill/payment writes journal entries from the first release, even before accounting reports exist. Retro-fitting a ledger onto existing invoices is the expensive mistake ERP projects make. |
| P9 | **Rules are data** | Tax codes, tax rules, charts of accounts, units, item kinds and legal texts are seed data per region ("region packs"), not `if country == "AT"` in code. |
| P10 | **Short CLAUDE.md, per-phase prompt files** | CLAUDE.md holds only the durable contract. Each phase = its own `docs/prompts/Pn-*.md` + an index line. |

---

## 1. Reuse from FreeFrame

[Certain] FreeFrame stack (repo read 2026-09-16): FastAPI 0.115 · SQLAlchemy 2 · Alembic · Celery + Redis · PostgreSQL 15 · S3 (MinIO/AIStor) · Next.js 14 · Tailwind · Radix UI · SWR · Zustand · Traefik · Docker Compose. Upstream is MIT (Techiebutler/freeframe).

**Copy (new repo, not a fork):**
- Auth (magic code + password, invites, first-user superadmin, setup flow)
- Users, admin dashboard, activity log, SSE events
- Notification system + notification prefs
- Email system (SMTP/SES, encrypted secrets via `secrets_service`, Jinja templates, Celery `email_worker`) — **transport and settings UI copied as they are; P2 extends it** (§5.5b)
- 2FA (§191–§197: TOTP, backup codes, email second factor, magic-code gate, web screens)
- Site settings, settings shell / sidebar layout, dashboard layout, theme
- **Branding page 1:1** → plus new **Design** page directly underneath (§7)
- S3 service incl. the `_get_presign_client()` / `S3_PUBLIC_ENDPOINT` rule
- Share-link token mechanism (reused later for online quote acceptance)

**Drop:** assets, HLS, transcription/whisper, LUTs, sidecars, approvals/votes, desktop app.

**Add services:** `gotenberg` (HTML→PDF/A-3 + Factur-X embedding [Certain], replaces in-process Puppeteer), `ocr_worker` (P5), optional `ollama` (P5, opt-in).

Why a fresh repo instead of forking: FreeFrame carries media-specific models, a 90 kB CLAUDE.md and §-numbered history that would mislead Claude Code. Accepted cost: fixes to shared modules (auth, email) must be ported manually between the two repos.

---

## 2. Domain map

```
Company (own legal entity, multi-company-ready)
 ├─ Parties ── Organisation ─┬─ Department / Branch ── Contact person
 │             Person (no org)└─ Contact person
 ├─ Catalog ── Item (kind) ── Price list · Rental factor table · Asset (serial)
 ├─ Projects ── Quote(s) → Order baseline → Billing plan → Invoices / Credit notes
 │             └─ Bills (costs) · Time entries · Tasks · Crew assignments
 ├─ Archive ── Outgoing docs · Incoming bills/receipts · Folder view Y/Q/M
 ├─ Ledger ── Chart of accounts · Journal · Payments · Bank statement imports
 └─ Settings ── Users/Roles · Email · Notifications · Branding · Design · Tax/Region · Number series
```

---

## 3. Parties (customers, suppliers, partners)

Model follows Odoo's unified partner approach (one table, hierarchy, role flags) rather than ERPNext's separate Customer/Supplier doctypes — one organisation is often client *and* supplier in film. [Likely]

- **Party**: `type = organisation | department | person`, `parent_id` (person → department → organisation), role flags `is_customer / is_supplier / is_partner` (multi-select, extensible tags).
- **Organisation fields**: legal name, trading name, legal form, register no. (Firmenbuch/HRB), VAT ID (with **VIES check** + date/result stored), tax number, tax country, default currency, language, payment terms, default tax profile, addresses (billing, delivery/shooting, postal), bank accounts, e-invoice routing (Peppol ID, DE Leitweg-ID, AT e-Rechnung.gv.at), customer/supplier number, notes, tags.
- **Department**: own name, own billing address, own cost-centre/PO reference, own invoice email (e.g. "ORF – Abteilung Unterhaltung").
- **Person**: name, title, role/position, email(s), phone(s), language; may have no parent (private client / freelancer).
- **Inheritance rule** (resolved at document creation, then snapshotted): each field on a document recipient = first non-empty of `person → department → organisation`. Billing address and VAT ID always come from the nearest level that *has* them; the contact name stays the person. UI shows inherited values greyed with their source.
- **Validation before finalize**: invoice cannot be finalized if the resolved recipient lacks what the selected tax rule needs (e.g. reverse charge without verified VAT ID → blocking error, overridable only with reason).
- Dedup on VAT ID / email; merge tool (later).

---

## 4. Catalog / inventory

### 4.1 Item kinds (system behaviour) vs categories (user labels)

`kind` is a fixed system enum because it drives behaviour; users create unlimited **categories** mapped onto a kind.

| Kind | Examples | Qty dims | Default unit | Behaviour |
|---|---|---|---|---|
| `labor` | DoP, gaffer, editor | persons × period | day / hour | Links to a user or freelancer; has **cost rate** vs **bill rate**; feeds "owed per user per project" (P7) |
| `equipment_rental` | Alexa 35, lens set | units × period | day | Optional rental factor; optional serialized assets + availability (P7) |
| `space_rental` | Studio A, office | units × period | day / half-day / month | Supports per-item tax override (§6.3) |
| `travel` | taxi, Uber, train, km allowance | units | flat / km | Flag `rebilled_cost` vs `service`; can be created from an uploaded receipt |
| `per_diem` | Diäten | persons × days | day | Separate reporting |
| `storage` | drive archive, gear storage | units × period | month | Recurring-billing candidate |
| `product` | hardware sold | units | piece | Stock movements, purchase price |
| `software_license` | plugin resale | units × period | piece / month | |
| `fee` | production fee, insurance | — | flat / % | Usually used as adjustment (§5.4) |
| `other` | anything | configurable | configurable | |

Users can add categories (e.g. "Drones", "Sound") and custom units; they cannot add kinds without code — that keeps tax/reporting logic sound.

### 4.2 Item fields
Name (multi-language), SKU, kind, category, description, unit, **bill rate(s)** (per unit: day / week / hour), cost rate, default tax code (overridable by tax rule), revenue & expense account, rental factor table, active flag, image, internal notes. Price lists (standard, customer-specific, union/KV rates) override rates.

### 4.3 Rental factors (optional per item)
Rentman-style multi-day factor [Certain]: 5 rental days × factor table → billed as e.g. 2.8 days. Stored as a table `{days → factor}` per factor group; shown on the line; user can override.

### 4.4 Equipment inventory: Device → Unit (decided 2026-09-17)

**Terms (fixed, used everywhere in code and UI):**
- **Item** — anything that can appear on a quote (crew, taxi, studio, fee … and equipment prices). Unchanged from §4.1.
- **Device** — the "mother", e.g. *ARRI Alexa 35*. Company-scoped.
- **Unit** — one physical piece of a Device: serial number, inventory number, purchase data, condition.
- **Associated item** (easyjob term "associated items"; code: `associated_link`) — a Device that travels with another Device (charger, batteries).
- **Alternative device** (code: `alternative_link`) — a Device the user has set up as replacement when another Device is overbooked (easyjob calls these "reference items"; FilmBill does not use that term).
- **Kit** — a named template of Devices across categories (*Podcast kit*: cameras + lights + audio). Becomes a group when booked, see §4.4.6.

#### 4.4.1 Device
System ID `D-0042` (sequence per company, **immutable**) · name · manufacturer · model · category (user-defined, hierarchical, e.g. Camera › Cinema) · images (multiple, first = cover; S3) · description (internal / customer-facing) · tracking mode `bulk` (count only) or `serialized` (units) · rental prices (day / week / custom period, per price list) · rental factor table (§4.3) · replacement value · weight/dimensions (packing, later) · optional link to an **Item** for tax class, revenue account and default text · active flag · internal notes.

#### 4.4.2 Unit
System ID `D-0042-03` (Device ID + running suffix, **immutable**, printed on the QR label) · **inventory number** (user-defined, editable, unique per company, e.g. `YON-CAM-007`) · **serial number** (editable, not required unique across manufacturers but warned on duplicates within a Device) · purchase date · purchase price · supplier (party) + purchase invoice link (archive, P5) · condition `ok / needs check / in repair / lost / retired` · repair & maintenance log (date, cost, note, attachments) · own images (optional, e.g. damage) · notes.
- Search finds a unit by system ID, inventory number or serial number from one field.
- Bulk devices have no units; their stock count lives on the Device (with purchase batches for cost tracking).

#### 4.4.3 Associated items ("always with" / "ask when booking") — decided 2026-09-17, renamed from "reference items"
- Rule on a Device: `{associated device, quantity per parent (e.g. 2 batteries per camera), booking mode: always | ask (checkbox popup when booking, pre-ticked or not), pricing: included | charged, visibility on customer documents: hidden (default) | shown}`.
- **Booking:** adding the parent adds all `always` associated items automatically; `ask` associated items appear in one popup with checkboxes and quantities before anything is added. All added associated items are reserved, availability-checked, and appear on packing lists and check-out. They can be removed per job afterwards (with a note in the job history).
- **Only one level is followed at booking time** — an associated item's own associated items are never pulled in automatically (no chains, no loops, no surprise bookings). Saving a link that would create a cycle is rejected.
- **Made visible at setup instead (Mathias' proposal):** when a user adds a device as associated item that has associated items of its own, the dialog says: *"Battery V-Mount 150 Wh has its own associated items: Charger (1 per battery). They will not be booked through Alexa 35 unless you add them here."* with checkboxes to add them directly to the parent, **quantities pre-multiplied** (2 batteries per camera × 1 charger per battery → 2 chargers per camera), editable.
- **Keeping it in sync:** links added that way remember their origin ("added via Battery V-Mount 150 Wh"). When the battery's own associated items change later, every parent that copied them shows a notice: *"Associated items of Battery V-Mount 150 Wh changed — 3 devices use it. Review?"* with a per-parent apply/ignore. Nothing changes silently.
- The device page shows two lists: *Associated items of this device* and *Used as associated item by* (reverse lookup).
- Included associated items carry € 0 on documents and receive **no revenue allocation**; charged ones behave like normal devices.

#### 4.4.4 Revenue & ROI per Device and Unit (decided 2026-09-17)
- Allocation happens **when payments are recorded**, not at invoicing. A payment of x % of an invoice allocates x % of each equipment line's net amount (partial payments allocate progressively).
- Net amount per equipment line = line net after line, group and document discounts/surcharges (pro rata). When a group is shown collapsed on the document, the group's net is split across its booked devices by their list price × quantity × days.
- Target: the **Unit** if specific units were assigned (at booking or check-out), otherwise the **Device**. Assigning units later moves the already-allocated amounts from Device to Unit.
- Storno/refund of a paid invoice creates negative allocations. Rebilled costs, crew and fees are never allocated to equipment.
- Shown per Device and Unit: revenue paid (lifetime, per year), days rented, utilisation %, purchase price, repair costs, **ROI** = revenue − purchase price − repair costs, break-even date. Allocation rows are append-only and traceable to invoice + payment.

#### 4.4.5 Alternative devices (decided 2026-09-17)
- Per Device an ordered list of alternatives, e.g. *Alexa 35 → Alexa Mini LF → Sony Venice 2*, each with optional note ("needs different mount") and price hint.
- Links are **one-directional** (A lists B does not mean B lists A); the UI offers "also add the reverse" on save.
- Used only in the overbooking popup (§4.4.7). Never booked automatically.

#### 4.4.6 Kits (decided 2026-09-17)
- **Kit template:** name, description, images, category, list of Devices with quantities (associated items of those devices follow their own rules at booking). **No kits inside kits.**
- **Price:** the kit shows the calculated sum of its devices' day prices. The user either keeps it (default) or sets a **custom kit day price**; the difference is shown as "kit discount/surcharge x %". Days and rental factors apply to the kit day price like to any line.
- **Duplicate kit:** one click copies contents, quantities and price setting into a new kit ("Podcast kit – 2 cam"). Also "Save group as kit" from a job's equipment list.
- **Booking a kit = inserting a group.** The kit becomes a normal group (level 2 inside a user group, or top level) in the job's equipment list and on documents, always shown as **kit + contents** in full view. After insertion it is fully editable like any group. It keeps a reference to its template and template version for statistics ("Podcast kit booked 14×"), but later template changes never alter booked jobs.
- **Swapping a device inside a booked kit** (during booking or prepping/check-out): popup shows old device, new device, price difference per day and in total, and asks: *keep kit price* or *adjust kit price by the difference*.
  - If a finalized document already contains that kit price (order confirmation, invoice): *adjust* does not change it silently — it offers a change line in Section B of the next/final invoice (§8.1) or a revision of the unsent document (§5.5).
- **Availability:** no special kit logic. Every device inside the kit goes through the normal overbooking check (§4.4.7).
- **Revenue:** always allocated to the devices (or units) inside, never to the kit. With a custom kit price, each device's share is scaled by kit price ÷ calculated sum.

#### 4.4.7 Availability & overbooking popup (decided 2026-09-17)
- Wherever a device is added (equipment list, kit insertion, swap, associated items), the picker shows for the job's booking period: **total owned · available · booked confirmed · on option** — computed per day; the lowest day counts, and a hover shows the day-by-day breakdown.
- Adding more than available opens the **overbooking popup**: shortage count and which jobs hold the device → alternatives with their availability for the same period (one click to swap, price difference shown as in §4.4.6) → or *book anyway* (marked overbooked, shows in the planner) → or *sub-rent* (P7).
- Serialized devices: shortage is hard (a specific unit cannot be in two places); bulk devices: soft with count.
- Calendar view per device and per unit (P7 UI; reservation data from P1).

### 4.5 Resource booking & planning (Rentman reference — P7, data model from P1)
The long-term differentiator for production companies. Reference: Rentman (equipment, crew & vehicle scheduling, availability, packing/warehouse flow).

- **Resources**: equipment items (bulk qty or serialized assets: serial no., purchase date/price, condition, maintenance/repair status), **crew** (internal users + freelancers with skills/roles), spaces (studios, offices), vehicles. Sub-rentals from other rental houses as temporary stock linked to a supplier + purchase order.
- **Bookings**: every quote/order line with a resource + period creates a **reservation** with status `option (tentative) → confirmed → checked-out → returned` (`cancelled`). Quote = option, order confirmation = confirmed. Internal bookings (own productions, maintenance, private use) without a customer document.
- **Planner views**: timeline/Gantt per resource, per project and per crew member; conflict & overbooking warnings (hard for serialized, soft for bulk with shortage count); "suggest sub-rental" when short.
- **Warehouse flow**: pick list → check-out scan (QR/barcode labels) → check-in with condition report → damage/missing → surcharge lines into Section B of the final invoice.
- **Crew**: availability, assignment per project day, deal memos, cost vs bill rate, feeds §11.3 owed report.
- **Why the model lands in P1 not P7:** lines already carry quantity + period; if reservations don't exist from the start, every quote written before P7 has no booking history and the planner starts empty. P1/P2 therefore write reservation rows silently; P7 adds the UI and conflict logic.
- Feeds fixed-asset register (AfA) later.

---

## 5. Documents & line model

### 5.1 Document types
All share one table + type-specific rules. **Financial** types are numbered gap-free, immutable after finalize and post to the ledger; **non-financial** types are numbered and versioned but never post.

| Type (DE / EN) | Class | Notes |
|---|---|---|
| Angebot / Quote | non-financial | versions v1, v2…; expiry; online acceptance (P8) |
| Auftragsbestätigung / Order confirmation | non-financial | created from accepted quote; freezes the **order baseline** (§8) |
| Lieferschein / Delivery note | non-financial | goods or gear handed over; qty without prices optional |
| Rechnung / Invoice | financial | variants: standard · **Anzahlungsrechnung** (advance) · **Teilrechnung** (partial) · **Schlussrechnung** (final) |
| Mahnung / Dunning letter | non-financial* | levels 1–n, fees + statutory interest; *fees post to ledger |
| Brief / Letter | non-financial | free text on letterhead layout, optional party + project link, no line items |
| **Rechnungskorrektur / Storno** — Credit note | financial | corrects or cancels an own invoice; always references it |
| **Gutschrift (§11 UStG) / Self-billing invoice** | financial | issued *by us as recipient* on behalf of a supplier — e.g. freelance crew payouts; requires a self-billing agreement flag on the party |
| Incoming bill / Eingangsrechnung | financial | from archive (§9) |

[Likely] In Austrian VAT law "Gutschrift" means a self-billing invoice issued by the recipient; a correction should be titled "Korrektur/Stornierung zu Rechnung XY" (Gaedke & Partner). The dropdown therefore offers both, clearly labelled — users who still want the word "Gutschrift" on a credit note can rename the display title in Design, but the internal type stays correct.

**Film-specific types (recommended, P7 with the booking module):** Bestellung / Purchase order (to rental houses, suppliers) · Mietvertrag / Rental agreement · Übergabe- & Rücknahmeprotokoll / Check-out & check-in report (condition, damages, missing items → auto-surcharge lines) · Packliste / Pick list · Crew-Buchungsbestätigung / Crew deal memo · Proforma-Rechnung / Proforma invoice (customs, ATA Carnet, prepayment request — explicitly *not* an invoice, never posts).

### 5.2 Header
Company, number series, recipient (resolved + snapshot), contact, project, language, currency (+ rate & source), document date, **service period `start`–`end`** (either may be in the future; single-day = start = end), due date/payment terms, customer reference / PO, intro & closing text (templates), tax treatment (§6), layout.
[Likely] §11 UStG requires date of supply *or* the period of supply; EN 16931 carries it as BG-14 (invoice period) and BG-26 (line period).

### 5.3 Line (the core requirement)
```
line_net = round( quantity × period_value × factor × unit_price × (1 − line_discount%) , 2 )
```
| Field | Example | Notes |
|---|---|---|
| `quantity` | 2 (cameras) | decimal |
| `period_value` | 3 | decimal; hidden when kind has no period |
| `period_unit` | day | hour / half-day / day / week / month / night |
| `factor` | 1.0 (or 2.8 from rental table) | shown only if ≠ 1 |
| `unit_price` | 180.00 per camera per day | |
| `line_discount` | % or amount | optional |
| `line_period` | 12.–14.10.2026 | optional per-line service period |
| `tax_code` | proposed by rule, overridable | |
| `item_id` + snapshot | | free-text lines allowed |

PDF renders e.g. **"2 × 3 Tage à € 180,00 = € 1.080,00"** (format configurable in Design).
E-invoice export: EN 16931 has a single invoiced quantity (BT-129) [Likely], so export `quantity × period × factor` with the period unit code and put the breakdown into the line note (BT-127).

### 5.4 Groups and adjustments
- **Group** (section) = ordered container of lines with title, optional description, subtotal. Max nesting depth **2**.
- **Equipment list vs document lines (decided 2026-09-17):** a job's equipment list (devices, units, associated items, kits) is the internal source; quote/invoice groups are built from it. A **job toggle** controls customer documents: **Full view** — group, then device/kit rows with amount/days/price/discount/VAT/sum, then unit rows with serial (no price) · **Groups only** — one row per group with amount, days, price per day and sum. Per-document override allowed; packing lists and internal views always show everything.
- **Uniform-days rule:** a collapsed group shows "price/day × days" only if every line in it has the same days. Otherwise it shows amount 1 and the group total, and the editor warns: *"Items in this group are charged for different numbers of days. To show days on the document, move the items with a different number of days into their own group."*
- **Adjustment** = `{label, type: discount|surcharge, mode: percent|amount, base}`; attachable to a **group** or the **document**. Document adjustments are an ordered list; each declares its base: `subtotal` (default) or `subtotal incl. previous adjustments` (compound), or a **kind filter** ("production fee 8 % on labor + equipment only").
- **Tax on adjustments:** percent adjustments are allocated to the tax categories of their base proportionally; amount adjustments too, pro rata by net. Per EN 16931 these become document-level allowances/charges with their own VAT category (BG-20/BG-21) [Likely]. Mixed-rate documents therefore stay correct.
- A surcharge like "production fee" can alternatively be a normal `fee` line — user's choice; the engine treats both correctly.

### 5.5b Sending documents by email (P2 — what FreeFrame's mail system does not do yet)
[Certain, repo read 2026-09-19] FreeFrame's `email_service` sends HTML+text only, from one site-wide sender, with no attachments and no delivery state. Document sending needs, on top of the copied transport:
- **Attachments** (PDF, and the e-invoice XML in P5) — SMTP and SES paths both.
- **Per-company sender identity**: from name/address, reply-to, BCC-to-self, signature; a company that sends as its own domain needs its own SMTP credentials (encrypted per company).
- **Document email templates** per document type × language, with variables (number, amounts, due date, payment link), editable in Settings → Company, previewed before sending.
- **Send state per dispatch**: queued → sent → bounced/failed, with the provider message id, bounce reason, retry, and a notification + task when a document email bounces (a silently failed invoice email is a real risk: FreeFrame swallowed mail errors for weeks once).
- Size guard (large PDFs), and a "send me a copy" option.
- P5 adds **inbound** mail (IMAP polling of a receipts mailbox) — new work, FreeFrame has nothing inbound.

### 5.5 Lifecycle, output actions & revisions

**States:** `draft → finalized → sent → (partially) paid → paid` · `→ overdue → dunning level n` · `→ cancelled (via Storno)` · `→ in revision (reopened) → finalized r2`. Quotes: `draft → sent → accepted / declined / expired → converted`. Every transition in the audit log. Quote versions (v1, v2 …) kept.

**Output actions (decided 2026-09-16).** One "Finalize & …" button group:

| Action | What happens |
|---|---|
| **Finalize** only | Number assigned, document locked, journal posted. No dispatch recorded. |
| **Download PDF** | Finalizes if draft → PDF (+ e-invoice XML if enabled) → dispatch entry `downloaded` |
| **Send via email** | Finalizes if draft → email composer (template, recipients resolved from party, PDF attached) → dispatch entry `emailed` with recipients + delivery status |
| **Print** | Finalizes if draft → PDF opened in browser print dialog → dispatch entry `printed` (later: direct IPP/CUPS network printer) |
| **Mark as sent** | For documents sent outside the system (portal upload, handed over) → dispatch entry `sent_manually` + note |

- Every dispatch entry stores: user, method, timestamp, revision, recipients (email), SHA-256 of the exact PDF. **All of this is internal only** — shown in the app's document header (**"Sent by Mathias · email · 16.09.2026 14:32 · r1"**) and dispatch history, never printed on the PDF.
- **Signatory ≠ sender:** the PDF shows a configurable *signatory / contact on document* (e.g. the managing partner) chosen per company, document type or document; the system separately records which user actually finalized, revised and sent it.
- **Draft preview/download** is always possible without finalizing: watermark "ENTWURF / DRAFT", no number, no dispatch entry.

**Revise (reopen a finalized/sent invoice) — soft path, not a hard block:**
- Any user with permission can click **Revise** → mandatory reason → document goes `in revision` → fully editable → re-finalize creates **r2** under the **same number**. r1 PDF, data and dispatch history stay viewable ("Show revision r1").
- **Visibility for other users:** orange banner on the document ("Revised by XY on … — reason: …"), badge "r2" in all lists, activity-feed entry, notification to the user who finalized/sent the previous revision and to all Accountant/Owner roles; while `in revision`, a banner "Currently being revised by XY" and no one else can dispatch it.
- After re-finalizing, the send dialog pre-fills a "corrected invoice replaces version dated …" text.
- **Hard block — the only one (decided 2026-09-16): partial-invoice chains.** Advance/partial invoices of a billing plan (e.g. 1st of 2 × 50 %, 1st or 2nd of 3 × 33 %) lock **permanently** as soon as the next invoice of the same plan exists. Details §8.1.
- **Month approval (optional, per company):** a user with Accountant/Owner role can mark a month as **approved for the tax advisor** ("Monat freigegeben") — matching workflows where the advisor only starts after internal approval. Revising a document in an approved month requires reopening that month first; reopening notifies Owners/Accountants and the tax-advisor user. Companies that don't use approval never see this.
- **Warnings (non-blocking):** payments already recorded · amount/tax changed beyond rounding tolerance · recipient changed · document was delivered as a structured e-invoice (Peppol / e-Rechnung.gv.at, P8) — [Guessing] some receiving portals reject a second invoice with the same number, so the warning suggests Storno + new invoice there.

### 5.6 Rounding
Line nets rounded to 2 dp → group/document adjustments rounded to 2 dp → **VAT computed per tax category on the sum of nets** (not per line) → gross. Cash rounding (e.g. CHF 0.05) as optional per-currency setting. Golden fixtures lock this in.

---

## 6. Tax engine

### 6.1 Tax codes (data)
`code, rate, EN16931 category (S, Z, E, AE, K, G, O, L, M), legal note text per language, reporting mapping (e.g. AT UVA field, DE UStVA line), account`. Region packs ship AT, DE, generic-EU; others add YAML packs.

### 6.2 Tax treatments (document-level selector)
Domestic · EU reverse charge (B2B services) · Intra-community supply of goods · Export (non-EU) · Not taxable in country (place of supply abroad) · Small business exemption (Kleinunternehmer) · Internal recharge (intra-group, no VAT / own code) · Custom.

### 6.3 Automatic determination + override (decided 2026-09-16: no built-in legal special cases)

**Principle:** FilmBill proposes, the user decides. The software ships only the generic, uncontroversial rules; anything that depends on a country's interpretation or an advisor's opinion is **user configuration**, never hard-coded.

**Shipped rules (region packs, editable):**
- Seller and buyer in same country → domestic rate of the item's tax class
- EU seller → EU business buyer with verified VAT ID, services → reverse charge (AE) + legal note
- EU seller → EU business buyer, goods → intra-community supply (K)
- EU seller → non-EU buyer → export / not taxable (G / O)
- EU seller → private buyer → domestic rate (distance-selling/OSS rules out of scope for v1)
- Kleinunternehmer company → exempt note on everything

**User-configurable exceptions (no presets):**
- **Per item or item category:** optional *tax rule override* — "always use tax code X regardless of customer location" or "use domestic rate for foreign business buyers". This is how a user whose advisor says "studio rental = Austrian VAT" configures Studio A once; a user in another country configures it differently or not at all.
- **Per party:** default tax treatment override (e.g. a client with special status).
- **Per document / line:** manual change → yellow warning showing the rule that would have applied, mandatory reason, audit log.
- Help texts point out *that* such cases exist ("some services, e.g. property rental or passenger transport, may follow different place-of-supply rules — check with your tax advisor") without deciding them.

**Rebilled expenses & per diems** — per-company/per-project policies (D12):
- **Rebill policy** for `travel`, `per_diem` and "rebilled cost" lines: `base` receipt net | gross · `markup` none | % | flat · `output tax` follow main service | specific code | pass-through (no VAT, flag "in fremdem Namen und für fremde Rechnung") · `input VAT` deductible | not deductible. YON default: gross base, no markup, main-service tax (taxi € 27.50 incl. 10 % → line € 27.50 net + 20 %). [Likely] legally just your price, fine as long as the line isn't labelled pass-through or "incl. VAT".
- **Per-diem policy per project:** `statutory` (user-maintained rates table per country; region packs may ship example values marked "verify") · `client cap` · `actual cost` (rebill receipts, e.g. crew lunch) · `none`. Mixable per crew member/day.

### 6.4 Related
VIES validation (stored proof) · EC Sales List (ZM) data for reverse-charge lines · multi-currency with ECB daily rates · VAT shown in company currency where required.

---

## 7. Layout designer (Settings → Branding → **Design**)

### 7.1 Page model
- Page presets: A4 / US Letter; margins; first-page vs following-page zones.
- **Grid**: 12 columns × row units in mm, snap-to-grid; blocks have `x, y, w, h, page_scope (first|following|all|last)`.
- **Locked zones via envelope presets**: DIN 5008 Form A / Form B address window, C5/C6 DL windows, Swiss left/right, US #10. The address block can only move inside the selected window zone; fold & punch marks optional. (Detailed print guidelines = later step, as you said — but presets exist from day one so v1 layouts don't break when rules arrive.)
- **Flow region**: the items table + totals is *flowing content* spanning pages. It gets a rectangle on page 1 and on following pages; it cannot be freely scattered. Everything else is free.

### 7.1b Reference layout (Mathias, 2026-09-17)
`docs/reference/invoice-layout-mathias-v1.png` is the target for the default layout and for P4 acceptance:
- logo top right · receiver address block left, sender address + document data (number, date, service period, customer no.) right · centred title "invoice number + subject / job name"
- columns: NAME · DESCRIPTION / SERIAL · AMOUNT · DAYS · PRICE · DISCOUNT · VAT · SUM
- body in full view: **Group** (bold, underlined) with group sum → device/item row indented → unit row further indented, italic, serial in description column; kit row bold italic with its devices and units below
- totals: sum positions · discount (currency or %) · net after discount · VAT · gross total ("Gesamtbetrag Brutto")
- payment terms block · footer in three columns: company information · legal information · bank information
- Required additions over the sketch (legal/functional, not optional): VAT lines **per rate/category** when a document has more than one (e.g. 20 % + reverse charge); the **deduction table** of earlier invoices on final invoices (§8.1); legal notes (reverse charge text, service period); page numbers on multi-page documents. The red colour in the sketch only marks calculated fields.

### 7.2 Blocks
Logo · sender line (Rücksendezeile) · recipient address · document meta (number, date, service period, customer no., contact, PO) · title · intro text · **items table** (column config: pos, description, qty, period, factor, unit price, discount, tax, total; group rendering style) · totals & tax breakdown · advance-payment deduction table · legal/tax notes · payment info + **EPC/GiroCode QR** · closing text · signature · footer columns (bank, VAT ID, register no., court, managing partners) · page x/y · custom text (with variables) · custom image · line/box shapes.

### 7.3 Rendering
Layout JSON (versioned) → **backend Jinja HTML** → Gotenberg → PDF/A-3 (+ embedded Factur-X/ZUGFeRD XML for invoices). The editor canvas is React (drag/resize, e.g. react-grid-layout [Likely]) but the **preview is the backend HTML in an iframe** — the exact same HTML that becomes the PDF. There is never a second renderer.
Layouts assignable per document type × language × company; fonts from Branding; theme colours from Branding.

---

## 8. Projects & partial billing

### 8.1 Flow
`Quote (versions) → accepted → Order baseline (locked copy) → Billing plan → Advance/Partial invoices → Final invoice`

- **Scope of these rules:** they apply **only when a billing plan with more than one invoice exists**. Converting a quote straight into a single invoice ("Quote → Invoice") produces a normal, fully editable draft — everything prefilled from the quote, nothing locked, lines can be added, changed, removed. (Decided 2026-09-16.)
- **Billing plan**: list of milestones `{label, percent|amount, due trigger (date / on acceptance / on delivery), service period}`; default template "50 % on acceptance / 50 % on delivery".
- **Advance invoice** shows **every baseline line** with its full amount and a column "billed now: 50 %", then totals for this invoice. Never a single line "50 % according to quote XY". Optional: user can additionally print the reference text.
- **Final invoice** layout:
  1. **Section A — Fixed block**: all baseline lines, 100 %, locked (edits require an explicit "change" line in Section B, not silent mutation).

- **Fixed-block lock rule (decided 2026-09-16):**
  - The fixed block is copied from the order baseline into the **first** invoice of a billing plan and stays **editable while that first invoice is a draft** (e.g. client asked to drop a line before the 50 % invoice goes out).
  - Edits made there create **baseline version n+1** (the sent quote and order confirmation stay untouched and remain viewable; diff shown).
  - **Finalizing the first invoice locks the baseline.** Every later invoice in the same plan (further partials, final) renders the fixed block read-only from that locked version.
  - Anything that changes afterwards — reductions included — goes into Section B.
  - Escape hatch: cancel the first invoice via Rechnungskorrektur/Storno → baseline unlocks again → new first invoice. Audit-logged.
- **Chain lock (decided 2026-09-16):** as soon as invoice *n+1* of a billing plan is **created**, invoices *1…n* are permanently locked — no Revise. Their numbers, amounts and VAT are what later invoices deduct, so changing them would break every subsequent deduction.
  - Recommendation adopted for the edge case: while invoice *n+1* is still a **draft**, deleting that draft releases the lock (an accidental "create next invoice" click shouldn't freeze the chain forever). Once *n+1* is finalized, the lock is permanent.
  - A locked invoice that turns out wrong can only be fixed by Storno, which cascades: every later invoice of the chain must be cancelled too, then the chain is re-issued. The UI walks the user through this and shows the affected documents first.
  2. **Section B — Changes & additions**: overtime, extra days, extra items, negative corrections; each can carry its own discount/surcharge %. Document adjustments declare whether they apply to A, B or both.
  3. **Total of service** (net / VAT per rate / gross).
  4. **Less previous invoices**: one row per advance/partial invoice with **number, date, net, VAT per rate, gross**.
  5. **Balance due** (net / VAT / gross).
- [Likely, source: facturo.at, Austrian UStR Rz 1525] If the final invoice does not deduct the VAT of earlier advance invoices, that VAT is owed twice (Steuerschuld kraft Rechnungslegung). The engine therefore makes step 4 mandatory whenever linked advance invoices exist.
- Cancelled advance invoice (credit note) is excluded from deductions automatically.

### 8.2 Project object
Client, period, status, budget, quotes, invoices, bills (costs), crew assignments, time entries, tasks, files, profitability (revenue − bills − labour cost).

---

## 9. Archive, uploads & extraction

### 9.1 Intake
Upload (drag & drop, multi) · mobile photo · **email inbox** (IMAP polling of e.g. `belege@…`) · later sync folders. SHA-256 dedup.

### 9.2 Extraction pipeline (Celery `ocr_worker`), cheapest-exact first
1. **Structured e-invoice present?** Factur-X/ZUGFeRD XML inside PDF, XRechnung, UBL, ebInterface → parse exactly, no guessing.
2. **PDF with text layer** → rule/template extraction (invoice2data-style YAML templates per supplier, learned from confirmations).
3. **Scan/photo** → OCR (Tesseract or docTR/PaddleOCR) → field extraction; optional LLM step, **off by default**, either local (Ollama) or user-supplied API key — never a hidden external call, because these are financial documents.
Extracted: supplier (matched to party), invoice no., invoice date, service period, due date, net/VAT/gross per rate, currency, IBAN, VAT ID, category suggestion.
4. **Review queue**: nothing is booked until a human confirms; confidence per field highlighted.

### 9.3 Folder view
Virtual tree generated from metadata (files stored once in S3). **The structure is a user-editable template per company**, built from tokens `{year} {quarter} {month} {section}` with custom labels. Default template = the structure YON's tax advisor already uses (screenshot 2026-09-16):

```
<Company> – Steuerberatung/
└─ 2026/
   ├─ Allgemeine Ordner/          contracts, registrations, anything uploaded as "general"
   ├─ Ausgaben/                   incoming bills, receipts
   │  └─ 1. Quartal … 4. Quartal/ (optional month level below)
   ├─ Bank/                       bank statements (PDF / CAMT / CSV imports)
   │  └─ 1. Quartal … 4. Quartal/
   ├─ Einnahmen/                  outgoing invoices, credit notes, Storno
   │  └─ 1. Quartal … 4. Quartal/
   └─ Revisionen/                 superseded revisions (r1, r2 … of revised documents)
      └─ 1. Quartal … 4. Quartal/
```
- **Revision handling (decided 2026-09-16):** when a document is revised, only the **current** revision stays in Einnahmen/Ausgaben. Every superseded revision moves to `Revisionen/<quarter>` of the original document date, filename `AR-2026-014_r1_superseded-2026-09-20.pdf`, with a CSV index (number, revision, superseded by, user, reason). Section label is configurable ("Revisioned", "Revisionen", …).
- Sections map to document classes; users can add sections (e.g. "Löhne", "Anlagen") and assign upload categories to them.
- **Date basis is a setting**: invoice date *or* payment date. [Likely] EAR (Einnahmen-Ausgaben-Rechnung) works on cash basis, so an EAR user's advisor wants the payment date; a double-entry/Soll user wants invoice date. Default follows the company's accounting mode.
- **Works with zero external services (decided 2026-09-16).** The archive is fully usable inside FilmBill on its own; sync (§9.4) is an optional later add-on.
- **Export / handover packages:** download the internal structure as ZIP at any granularity — **entire archive · year · quarter · month · custom date range · single section** (e.g. only Ausgaben Q3) — each with a CSV index (number, date, party, net/VAT/gross, revision, section, file name) and optionally the booking export file (§10). Filenames and folder layout inside the ZIP follow the company's folder template exactly.
- **Handover log:** every package records who downloaded what scope and when, with the list of included files + hashes. The archive then shows per month "handed over on … by …" and flags documents added or revised *after* the last handover ("3 new, 1 revised since handover") — so nothing silently goes missing. Pairs with month approval (§5.5).

### 9.3b Tax advisor access (P5, decided 2026-09-16)
- Dedicated **Tax advisor** user type: sees **only** the archive folder tree (all sections incl. Revisionen), previews, downloads/handover packages, and — if enabled — reports and booking exports. No parties, projects, drafts, settings, other users, or editing.
- **Scoped per company:** in a multi-company install the advisor account is granted company by company; an advisor for YON Studio OG never sees another company.
- **Optional scope limits:** from-date (e.g. only 2025+), sections, only approved months.
- **Security, because this account is used by an outside party over the internet:** mandatory **TOTP two-factor** for this role (and optional for everyone else), access expiry date, one-click revoke, every view and download in the audit log, notification to Owners on first login and on each handover download. [Certain] FreeFrame's auth has no 2FA today (repo checked 2026-09-16) — TOTP is new work, planned in P0 so it exists before any external role does.
- Several advisor users per company (e.g. partner + clerk at the firm).

### 9.4 Sync targets (P8 — later stage, decided 2026-09-16)
Push the archive tree (§9.3) into a folder the tax advisor already uses, so their workflow doesn't change.

- **Backends:** rclone under the hood → Dropbox, OneDrive/SharePoint, Google Drive, Nextcloud/WebDAV, S3, SMB/NAS share, SFTP. Credentials encrypted via `secrets_service`. [Likely] OAuth backends (Dropbox, OneDrive, Google) need each self-hosted install to register its own OAuth app or complete rclone's authorize flow — the setup wizard must explain this; WebDAV/SMB/SFTP need no app registration.
- **Sync modes (per target, user's choice):**
  1. **Continuous** — every finalized/confirmed document is pushed within minutes.
  2. **On approval** — pushes a month automatically when it is marked approved (§5.5 month approval).
  3. **Manual job** — user picks scope (month / quarter / year / sections), sees a **preview diff** (new, updated, moved to Revisionen), then starts the job. Only what they checked goes through.
- **Never touch foreign files:** FilmBill keeps a manifest of files *it* uploaded (path + hash). It only creates, updates or moves those; files the advisor or anyone else put into the folder are never modified or deleted.
- **Revisions on the remote:** a superseded revision is moved on the remote into `Revisionen/<quarter>` exactly like in the app (not deleted, not overwritten).
- **Never delete remotely** — a Storno adds the cancellation document; nothing disappears. Drafts are never synced.
- **Job log:** per run: user or trigger, mode, files pushed/moved/skipped, errors, retry; failures create a task + notification.
- **One-way only (app → folder)** in v1. Pulling files the advisor drops into the folder (e.g. their reports) back into the archive = later.
- Retention lock: finalized/booked docs undeletable for the retention period ([Certain] AT: 7 years BAO §132 per USP/WKO; DE 8–10 years).

---

## 10. Accounting core

- **Modes per company** (settable per company and per fiscal year, switch with opening-balance wizard): EAR (Einnahmen-Ausgaben-Rechnung, cash basis) or double-entry (Bilanzierung, accrual). Same journal underneath; reports differ.
- **VAT timing per company**: Soll (on invoicing) or Ist (on payment) — independent of the bookkeeping mode.
- **[Likely] AT rules the setup wizard encodes** (USP.gv.at, BMF 16.04.2026): double-entry is mandatory *by legal form* for GmbH, FlexKapG, AG and GmbH & Co KG. OG/KG with natural persons and sole traders may use EAR until revenue thresholds are exceeded (€700 k in two consecutive years / €1 M in one year; the government announced raising this to €1 M / €1.5 M in 2026 — check if in force). Freiberufler never by UGB. The wizard shows the rule and the user confirms; the system warns when year revenue approaches a threshold.
- **YON Studio OG default**: EAR + regular VAT + **Sollbesteuerung**. Double-entry mode ships for other users (GmbH etc.) but is not needed for YON.
- **Everything the tax advisor decides is a user selector** (decided 2026-09-16), in Settings → Company → Accounting, each with a short explanation and "ask your Steuerberater" hint: bookkeeping mode · Soll/Ist · Kleinunternehmer · chart of accounts template · export format · folder date basis · rebill & per-diem policies · self-billing allowed.
- **Chart of accounts** templates: AT Einheitskontenrahmen (EKR), DE SKR03/SKR04, generic.
- **Auto-posting**: finalize invoice/bill, record payment, credit note, bank fee, write-off.
- **Payments**: manual record, partial payments, overpayment/credit, cash, **bank statement file import (CAMT.053 / MT940 / CSV)** with match suggestions by amount/reference/IBAN. This replaces live bank connections entirely — no bank credentials ever stored.
- **Reports**: open items & aging (AR/AP), EAR report, P&L, balance sheet (double-entry mode), VAT return preparation (UVA/UStVA), EC Sales List (ZM), revenue by customer / item kind / project, cash-flow forecast from due dates.
- **Exports (selector)**: generic CSV + document ZIP first, then DATEV booking batch (DE), BMD NTCS and RZL (AT). [Likely] each format is its own implementation with its own field mapping and must be validated against a real import by an advisor, so the selector only lists formats that passed that test; contributors can add more as plugins.
- Later: fixed assets & depreciation (camera gear!), year-end closing, budgets.

---

## 11. Team: users, tasks, time

### 11.1 Roles (global) + project membership
Owner · Admin · Accountant (all finance, no system settings) · Producer/Sales (parties, catalog, quotes, invoices, projects; no ledger/settings) · Staff (own tasks, own time, own expenses, assigned projects) · Tax advisor (external; archive only, per company, mandatory 2FA — §9.3b). Roles are granted **per company**. Permission matrix as data; FreeFrame's `is_superadmin` maps to Owner.

### 11.2 Tasks
Title, assignee(s), due date, linked object (project / document / party), status, comments; notifications through FreeFrame's system. System-generated tasks: "quote expires in 3 days", "invoice overdue", "receipt needs review".

### 11.3 Production days (data model from P3)
`ProjectDay {date, type: prep|shoot|travel|wrap|post|office|off, location, call time, wrap time, notes}` is a first-class entity. Bookings, crew assignments, time entries, overtime, per diems and — later — call sheets all hang off it. Adding it now is cheap; retro-fitting it once call sheets exist is not.

### 11.4 Overtime profiles (P7, engine in P3)
Country- and union-specific overtime rules as **data**, same principles as the tax engine (pure calc module + golden fixtures).

- **Profile** = `{name, region, normal_hours (8/10/12/custom), hourly_base: day_rate ÷ normal_hours | fixed OT rate, tiers: [{after_hour, surcharge %}], rounding: 15 min | 30 min | per started hour, break rules (unpaid/paid, auto-deduct after X h), surcharges: night (from–to, %), Saturday / Sunday / public holiday (%, holiday calendar per country/region), turnaround / rest-time violation (min. rest hours, penalty %), travel-day handling, max hours warning}`.
- **Assignment chain:** company default → **project (per job setting, as requested)** → crew member deal memo → single day override. Shown on the time entry: which profile and which tier applied.
- **Two sides, separately configurable:** billed to client (revenue, flows into Section B of the final invoice) vs paid to crew (cost, "owed per user per project"). They often differ.
- Region packs ship examples (e.g. AT film collective agreement, DE TV-FFS) marked **[Guessing] until reviewed** — users can copy and edit any profile.
- Golden fixture S11 (§12).

### 11.5 Time & crew payouts (P7)
- Time entry: user, project, date, start/end/break or duration, labour item, billable flag, note → approval.
- Cost rate per user (or per user × item) → **"owed per user per project"** report; export for payroll / freelancer invoices (Honorarnoten).
- Approved billable time → overtime engine (§11.4) → one click into "Changes & additions" of a final invoice.
- **Geofence mobile app (future)**: weekly work location, auto clock-in on arrival. [Likely] background geofencing needs a native app (e.g. React Native/Expo), not a PWA. [Likely] in Austria, GPS-based tracking of employees needs a works-council agreement or individual consent (ArbVG §96/§96a) plus GDPR documentation — design as opt-in per user, location stored only as "arrived/left", not tracks.

---

## 12. Acceptance scenarios (golden fixtures)

Claude Code implements these as automated tests in P2/P3; Cowork re-checks them on each build. Numbers verified by script.

**S1 — Domestic AT B2B, 20 % VAT, advance + final**
Quote:
- Group *Crew*: DoP 1 × 3 d × 850 = 2 550.00 · Gaffer 1 × 3 d × 550 = 1 650.00 · group discount −5 % = −210.00 → **3 990.00**
- Group *Equipment*: Alexa 35 1 × 3 d × 1 200 = 3 600.00 · Lens set 2 × 3 d × 180 = 1 080.00 → **4 680.00**
- Group *Studio*: Studio A 1 × 2 d × 1 400 = **2 800.00**
- Group *Travel*: Taxi flat **120.00**
- Subtotal 11 590.00 · Production fee +8 % = 927.20 · **Net 12 517.20 · VAT 2 503.44 · Gross 15 020.64**

Advance invoice (50 %), all lines listed: **Net 6 258.60 · VAT 1 251.72 · Gross 7 510.32**

Final invoice:
- A (as quoted) net 12 517.20
- B additions: Overtime DoP 1 × 4 h × 120 = 480.00 · Extra lens day 2 × 1 d × 180 = 360.00 · fee 8 % = 67.20 → 907.20
- Total net 13 424.40 · VAT 2 684.88 · gross 16 109.28
- Less advance invoice: net 6 258.60 · VAT 1 251.72 · gross 7 510.32
- **Balance: net 7 165.80 · VAT 1 433.16 · gross 8 598.96**

**S2 — German B2B client with verified USt-IdNr.** Same quote. Crew & equipment → reverse charge (AE, 0 %, legal note); all lines reverse charge by default; **variant S2b:** item *Studio A* has a user-configured override "domestic rate for foreign business buyers" → studio line AT 20 %, rest reverse charge; production fee allocated pro rata across both categories; lines appear in ZM data.

**S3 — US B2B client.** Services → not taxable in AT (O) with note; with the S2b override the studio line → AT 20 %.

**S4 — Lock, revise, cancel.** Finalized invoice: plain PATCH rejected (409). *Revise* with reason → r2 under same number; r1 PDF + dispatch history retrievable; r1 moves to `2026/Revisionen/3. Quartal`, r2 stays in `Einnahmen`; banner + notification to previous sender; PDF contains no sender/hash data. Advance invoice 1/2 after invoice 2/2 was created as draft → Revise blocked; delete draft 2/2 → Revise allowed again; finalize 2/2 → permanently blocked, Storno cascade offered. Storno cancels; next invoice gets the next number; no gaps; audit log shows everything.

**S4b — Output actions.** "Send via email" on a draft → finalized + numbered + dispatch entry (user, method, recipients, PDF hash). "Download" of a draft without finalizing → watermark, no number, no entry.

**S5 — Inheritance & snapshot.** Contact without address under department without address → organisation address used. Change organisation address after finalize → PDF and stored snapshot unchanged.

**S6 — Rounding.** 3 × 2.5 d × 333.33 at 20 %, plus 7 lines at 0.333 → VAT per category on the sum of line nets; matches fixture to the cent.

**S7 — Intake.** Supplier PDF with embedded ZUGFeRD → exact fields, no OCR run. Photo of taxi receipt → OCR → review queue → confirmed → appears under `2026/Q3/09/Receipts`, booked with correct VAT.

**S8 — Tax override.** User switches S2's crew lines to domestic 20 % → warning shown, reason required, audit entry written.

**S11 — Overtime.** Profile: normal 10 h, hourly base = day rate ÷ 10, +50 % for hours 11–12, +100 % from hour 13, rounding per started 30 min. Gaffer day rate € 850, worked 13 h net of breaks → base € 85.00/h → 2 h × € 127.50 = € 255.00 + 1 h × € 170.00 = € 170.00 → **overtime € 425.00, day total € 1 275.00**. Same day with crew-side profile "normal 12 h, base = day rate ÷ 12, +50 % from hour 13" → 1 h × € 106.25 = **€ 106.25 paid to crew**, € 425.00 billed to client.

**S12 — Rebill policy.** Taxi receipt € 27.50 gross (€ 25.00 + 10 % VAT). YON policy (gross base, no markup, main-service tax 20 %) → line net € 27.50, VAT € 5.50, gross € 33.00; input VAT € 2.50 deductible. Policy "net base" → € 25.00 / € 5.00 / € 30.00. Policy "pass-through" → € 27.50, no VAT, flagged.

**S13 — Revenue allocation on payment.** Invoice net € 3 000: group *Camera* collapsed, containing Alexa (list € 1 200/day) and lens set (€ 300/day), 2 days, group discount 10 % → group net € 2 700 split 80/20 → Alexa € 2 160, lenses € 540. Payment of 50 % recorded → Alexa device +€ 1 080, lenses +€ 270. Unit `D-0042-03` assigned at check-out → € 1 080 moves from the Alexa device to the unit. Remaining 50 % paid → unit +€ 1 080. Storno → −€ 2 160 on the unit, −€ 540 on lenses.

**S14 — Associated items.** Alexa has associated items: battery 2× (charged, mode *ask*, pre-ticked), monitor cage 1× (included, *always*, hidden). Battery has its own associated item: charger 1×.
- Setup: adding battery to Alexa shows the "has its own associated items" dialog; ticking charger adds *charger 2× per Alexa (added via battery)*.
- Booking 2 Alexas → popup shows batteries (4, ticked) → confirm → 4 batteries + 2 cages + 4 chargers reserved (chargers because they were copied at setup, not because the chain was followed). Customer document in full view shows batteries and chargers only if set to shown; cages hidden, € 0, no revenue.
- Without the setup copy: booking 2 Alexas reserves no chargers.
- Changing battery's associated items to 2 chargers → notice on Alexa "associated items of battery changed — review?"; nothing updated until applied.
- Linking charger → battery → Alexa → charger as a cycle → rejected on save.

**S15 — Display toggle.** Same job, "Groups only": camera group row shows `1 × 2 days × € 1 350 = € 2 700`. Add a drone (€ 300/day) for 1 day to the group → group net € 2 700 + € 270 = € 2 970 → row shows `1 × € 2 970` without days, editor warning with the move-to-own-group hint. Full view → device and unit rows with serials, units without prices.

**S16 — Kit.** Kit *Podcast kit*: 2× FX6 (€ 250/day) + 3× light (€ 60/day) + 1× audio set (€ 120/day) → calculated € 800/day; custom kit price € 700/day (−12.5 %). Book for 2 days → group "Podcast kit" with contents; group net € 1 400. Revenue on full payment: FX6 each € 250×2×0.875 = € 437.50, lights each € 105.00, audio € 210.00 (sum € 1 400). Swap one FX6 for an FX3 (€ 150/day) → popup "−€ 100/day, −€ 200 total: keep kit price / adjust" → adjust → € 600/day. Same swap after the order confirmation was finalized → *adjust* offers a Section-B change line instead of editing the confirmation. Template changed afterwards (add a 4th light) → booked job unchanged. Duplicate kit → new kit with identical contents and custom price.

**S17 — Overbooking.** Company owns 2 Alexa; job X holds 2 confirmed for 12.–14.10. Job Y adds 1 Alexa for 14.–15.10. → picker shows "2 owned · 0 available (14.10) · 2 confirmed"; popup lists alternatives Alexa Mini LF (1 available) and Venice 2 (0) → swap to Mini LF shows price difference → alternatively *book anyway* → job Y line flagged overbooked.

**S9 — Booking conflict.** Quote A reserves the one serialized Alexa 35 for 12.–14.10. (option). Quote B for 13.–15.10. → soft warning on B. Quote A confirmed → B shows hard conflict + "sub-rent" suggestion. Quote A declined → reservation released, B clean. (Tables from P1, checks in P7.)

**S10 — Fixed-block lock.** From S1's order: edit Gaffer to 2 days on the draft advance invoice → baseline v2, quote v1 unchanged, advance recalculates on v2. Finalize advance → final invoice's Section A is read-only on v2; attempting to change it via API → 409. Storno the advance → baseline unlocked.

---

## 13. Phases (each = one or more Claude Code prompts, each ends with Cowork acceptance)

| Phase | Content | Exit criterion |
|---|---|---|
| **P0 Foundation** | AGPL-3.0 licence (FreeFrame-derived files keep their MIT notice); multi-company from the start (company switcher in UI, `company_id` on all business tables, per-company roles, number series, layouts, branding); new repo from FreeFrame core; TOTP 2FA (optional per user, enforceable per role) (auth, users, notifications, email, settings, branding), media code removed; Gotenberg in compose; Alembic baseline; OpenAPI→TS codegen; Decimal money util; audit log; company settings; number series; roles; CI (lint, tests, multi-arch image); CLAUDE.md + prompt index | Fresh `docker compose up` → setup → login → email test → branding saved; zero media references |
| **P1 Master data** | Parties (hierarchy, inheritance, VIES, self-billing flag), catalog (kinds, categories, units, price lists, rental factors), resource & reservation tables (no UI), tax codes/treatments/rules + AT/DE/EU packs | S5 inheritance part; tax proposal unit tests |
| **P2 Documents** | Calc engine; document email (attachments, per-company sender, templates, bounce state — §5.5b); output actions (finalize / download / email / print / mark sent) with dispatch log; Revise with revisions r2…; quotes, invoices, credit notes; groups & adjustments; service periods; finalize/numbering/immutability; journal posting (tables only); default fixed layout PDF via Gotenberg + EPC QR; email sending; manual payments; all §5.1 core types incl. Brief, Mahnung (basic), Rechnungskorrektur, Gutschrift (self-billing) | S1 (without partial), S4, S6, S8 green |
| **P3 Project billing** | Projects, **ProjectDays**, overtime calc engine (no UI), rebill & per-diem policies, order baseline, billing plans, advance/partial/final invoices with deduction table | S1 full, S2, S3 green |
| **🚦 Release gate R1 (first official deploy, after P3)** | **FilmBill v1 import mandatory**: parties, items, issued v1 invoices as archived PDFs + metadata, number-series continuation, dry-run report + rollback. Not needed for test deploys. YON uses FilmBill for real quotes/invoices from here while P4–P7 continue; first weeks run in parallel with v1. | Dry-run on a copy of the v1 DB reconciles counts & totals |
| **P4 Design** | Grid designer, envelope presets, flow region, per-type/language layouts, Design settings page | Yon layout rebuilt in designer; pixel-diff vs P2 default ≤ tolerance; multi-page items table |
| **P5 Archive & e-invoice** | Folder template + Revisionen section, **ZIP handover packages at any granularity + handover log (§9.3)**, **tax-advisor login (§9.3b)**, uploads, inbox, extraction pipeline, review queue, folder tree, tax-advisor role, ZIP export; Factur-X/ZUGFeRD + XRechnung + ebInterface output | S7 green; outgoing XML passes an EN 16931 validator |
| **P6 Accounting** | CoA templates, EAR & double-entry reports, bank statement import & matching, UVA/ZM prep, BMD/RZL/DATEV export, dunning, recurring invoices | Advisor-checked export of one real quarter |
| **P7 Team & bookings** | Tasks, web time tracking, **overtime profiles UI** (§11.4), cost rates & owed report; Rentman-style planner (§4.4): resources, reservations UI, conflicts, sub-rentals, pick list, check-out/in with QR labels, crew planning; film-specific document types | Conflict detection on S9 |
| **P9 Production workflow (future)** | Upgrade of project tracking into a production workspace: call sheets (from ProjectDays, crew assignments, locations, weather/sunrise, emergency info), crew lists, shooting schedules / Drehpläne, workflow sheets & checklists, location database. Scoped separately when P7 is done. | — |
| **P8 Future** | **Sync targets with continuous / on-approval / manual modes (§9.4)**, geofence mobile app, client portal with online quote acceptance, Peppol sending, fixed assets | — |

---

## 14. Features you didn't list that comparable systems have

**Approved by Mathias 2026-09-16 — all in scope.** Credit notes/Storno · dunning with late-interest calc (kalkül, sevDesk) · recurring invoices (storage!) · order confirmations & delivery/packing lists · quote versions & expiry · **EPC/GiroCode QR on invoices** · **structured e-invoice in/out (EN 16931)** · VIES checks · bank statement *file* import (instead of bank connection) · UVA/ZM preparation · BMD/RZL/DATEV exports · tax-advisor login · price lists / customer-specific rates · multi-currency · fixed assets & depreciation · project profitability · online quote acceptance via share link · **import from FilmBill v1 incl. number-series continuation** · REST API + webhooks · backup/restore job (pg_dump + S3) · GDPR tools (export/anonymise contacts after retention) · Kleinunternehmer mode · document language per customer.

Deliberately out: live bank connections (your call, agreed) · POS/cash register (RKSV) · payroll · CRM pipelines · e-commerce.

---

## 15. References

| Reference | Take from it | License / note |
|---|---|---|
| [ERPNext](https://github.com/frappe/erpnext) | Tax rules & templates by region, payment schedules, CoA templates, accounting dimensions, project/timesheet billing | GPL-3.0 — ideas only |
| [kalkül](https://kalkuel.at/buchhaltungssoftware-oesterreich/) | AT-specific UX: EAR auto-generated, dunning with statutory interest, Kleinunternehmer, quote→invoice one click | proprietary |
| [Odoo](https://www.odoo.com) | Unified partner/contact hierarchy, sections & notes in quotes, down-payment invoices, l10n modules | LGPL community / proprietary enterprise |
| [Dolibarr](https://www.dolibarr.org) | Modular enable/disable, situation invoices (progress billing) | GPL-3.0 |
| [Akaunting](https://akaunting.com) | Clean SMB UX, app modules | **BSL since 3.1** [Certain] — not OSI open source |
| [Aureus ERP](https://github.com/aureuserp/aureuserp) | Laravel/Filament modular ERP, plugin structure | MIT |
| [sevDesk](https://sevdesk.de) | Receipt capture UX, DATEV export, advisor access | proprietary |
| [Bigcapital](https://github.com/bigcapitalhq/bigcapital) | Modern double-entry + inventory data model, headless API | AGPL-3.0 |
| [Invoice Ninja](https://invoiceninja.com) | Client portal, recurring, expenses, time tracking | Elastic License (source-available) |
| [Rentman](https://support.rentman.io/hc/en-us/articles/360013944139-Multiple-Days-Discount-Factor) | Multi-day rental factor, gear availability, packing lists | proprietary |
| [Gotenberg](https://gotenberg.dev/docs/manipulate-pdfs/attachments) | HTML→PDF, PDF/A-3, native Factur-X embedding | MIT |
| [factur-x (Akretion)](https://github.com/akretion/factur-x) · [drafthorse](https://pypi.org/project/drafthorse/) | Python Factur-X/ZUGFeRD XML generate/parse | BSD / Apache |
| [invoice2data](https://github.com/invoice-x/invoice2data) | Template-based PDF invoice extraction | MIT |

Compliance sources: [Brandauer – E-Rechnung AT 2026](https://brandauer-rechtsanwaelte.at/2026/06/06/e-rechnung-e-invoicing-pflicht-oesterreich-2026/) (no AT domestic B2B mandate yet; ViDA cross-border 1 July 2030; B2G since 2014 via ebInterface/UBL) · [facturo – Schlussrechnung AT](https://facturo.at/blog/teilrechnung-schlussrechnung-oesterreich/) · [USP – Aufbewahrungspflicht](https://www.usp.gv.at/themen/steuern-finanzen/steuerliche-gewinnermittlung/weitere-informationen-zur-steuerlichen-gewinnermittlung/betriebliches-rechnungswesen/aufbewahrungspflicht.html)

**Rule for Claude Code:** no code copied from GPL/BSL/Elastic/proprietary references into this repo. Data models and behaviour may be studied; implementations are original.

---

## 16. Decisions log

| # | Decision | Date |
|---|---|---|
| D1 | Name stays **FilmBill** — positioned for film, photo & media production companies | 2026-09-16 |
| D2 | Licence **AGPL-3.0** (FreeFrame-derived files keep MIT notice) | 2026-09-16 |
| D3 | **Multi-company UI** in v1 | 2026-09-16 |
| D4 | Fixed block editable only on the draft of the first invoice of a plan; locked on finalize (§8.1) | 2026-09-16 |
| D5 | YON Studio OG: regular VAT, EAR today; system supports EAR + double-entry, Soll + Ist, Kleinunternehmer (§10) | 2026-09-16 |
| D6 | v1 import not needed for test builds; **mandatory at release gate R1** | 2026-09-16 |
| D7 | All §14 features in scope; Rentman-style booking/planning module added (§4.4, P7) | 2026-09-16 |
| D8 | Document types per screenshot + split Gutschrift into Rechnungskorrektur/Storno and self-billing Gutschrift (§5.1) | 2026-09-16 |

| D9 | Quote → single invoice = normal editable invoice; fixed-block rules only with multi-invoice billing plans | 2026-09-16 |
| D10 | Sent invoices can be **revised** (same number, r2…) with reason, banner and notifications; hard block only in the three guard-rail cases; Storno always available | 2026-09-16 |
| D11 | Output actions finalize / download / email / print / mark sent, each logged with user + method | 2026-09-16 |
| D12 | Rebilled costs & per diems via configurable policies; YON: gross base, main-service VAT | 2026-09-16 |
| D13 | YON: Sollbesteuerung, EAR; all advisor-level choices are user selectors | 2026-09-16 |
| D14 | Overtime profiles per country/union, assignable per job; ProjectDay entity; call sheets & workflow sheets as future P9 | 2026-09-16 |
| D15 | Self-billing Gutschrift kept for other users; YON receives invoices | 2026-09-16 |
| D16 | Revisions exist mainly for internal traceability; superseded revisions move to a `Revisionen` section in the archive tree | 2026-09-16 |
| D17 | Only hard block on Revise: partial-invoice chains once the next invoice exists (draft deletion releases); e-invoice and approved-month cases are warnings / optional month approval | 2026-09-16 |
| D18 | Dispatch fingerprint & sending user internal only; PDF shows a configurable signatory | 2026-09-16 |
| D19 | No country-specific legal special cases in code; generic rules + user-configurable per-item/party overrides | 2026-09-16 |
| D21 | Sync modes continuous / on approval / manual job with preview; manifest-based — FilmBill only ever touches files it created. **Scheduled for P8**, not P5 | 2026-09-16 |
| D22 | Archive fully usable without any external service: ZIP handover packages (all / year / quarter / month / range / section) + handover log | 2026-09-16 |
| D23 | External tax-advisor login, archive-only, per company, mandatory TOTP 2FA (2FA built in P0) | 2026-09-16 |
| D24 | v1 repo renamed to `FilmBill_OLD` on GitHub; v2 takes `OddOne1/filmbill`, local path `~/Claude/Projects/FilmBill/repo` | 2026-09-16 |
| D26 | Equipment model Device → Unit; Item stays "anything billable". Immutable system IDs `D-0042` / `D-0042-03`, editable inventory and serial numbers | 2026-09-17 |
| D27 | Equipment revenue allocated on **recorded payments**, pro rata, to Unit if assigned else Device; ROI per Device/Unit | 2026-09-17 |
| D28 | Job toggle Full view / Groups only for customer documents; uniform-days rule with move-to-own-group warning | 2026-09-17 |
| D29 | Associated items (easyjob term) separate from Kits: modes always / ask-with-popup, included/charged, hidden/shown; only one level followed at booking; nested associated items surfaced at setup with pre-multiplied quantities and change notices | 2026-09-17 |
| D30 | Mathias' sketch is the reference default layout (§7.1b), plus per-rate VAT, deduction table, legal notes, page numbers | 2026-09-17 |
| D31 | Terms: **Associated items** = always/ask-with devices; **Alternative devices** = replacements offered when overbooked (easyjob's "reference items"; term not used) | 2026-09-17 |
| D32 | Kits: calculated price or custom kit day price; swap popup with price difference; no nested kits; duplicate kit + save group as kit; booked kit = normal group shown with contents; revenue to devices, scaled by custom price | 2026-09-17 |
| D33 | Availability shown in the device picker (owned / available / confirmed / option per booking period); overbooking popup with alternatives, book anyway, sub-rent | 2026-09-17 |
| D35 | **Email stays the main second factor, made safe by splitting the channels** (decided 2026-09-20): every user sets a **backup email address used only for password resets**, verified, must differ from the login address (warning when both are on the same domain); 2FA codes go to the login address, reset links only to the backup address. Conditions: magic-code sign-in is **off for users whose factor is email**; changing either address, changing the password, disabling 2FA or regenerating backup codes requires the **current second factor** and notifies **both** addresses. Cannot be enforced, only warned about: forwarding between the two mailboxes. Roles with mandatory 2FA (tax advisor) must use an authenticator app, never email | 2026-09-20 |
| D36 | **A password is mandatory for every account** (decided 2026-09-20). Password-less legacy users may still sign in with a magic code but land on a blocking set-password screen; after 30 days that route closes and an admin must help. | 2026-09-20 |
| D37 | **Onboarding gate, both steps blocking, server-driven** (decided 2026-09-20): after login the app is unusable until (1) a password is set and (2) a **backup email address is verified by a 6-digit code** sent to it. Order: password → backup address → (2FA). State comes from `/auth/me` (`must_set_password`, `backup_email_state: missing\|pending\|verified`) — never from a "seen" flag in the browser, so a prompt disappears exactly when the stored data is real and reappears if it is cleared. The screen allows changing the address and resending (rate-limited, code TTL 15 min). Escape hatches, because a blocking gate on an undeliverable address is a lockout: an admin can clear another user's requirement, and a documented CLI/psql command clears it for the only superadmin. Admin list shows who still lacks a password or a verified backup address. | 2026-09-20 |
| D38 | **Password policy** (decided 2026-09-20): minimum **12 characters** plus upper case, lower case, digit and special character (Mathias' rule) **and** a server-side strength check — blocklist of common passwords, no user name/email/company fragments, and a zxcvbn-style score that rejects obvious patterns even when the four classes are met (`Sommer2026!`). The signup/change form shows a live **weak / medium / strong** meter with the reason. The meter is advisory; the binding check runs on the server. No forced rotation. | 2026-09-20 |
| D39 | **Two superadmins recommended** (decided 2026-09-20): the app warns while an installation has only one superadmin, the docs recommend a second one, and the documented CLI command to clear an onboarding requirement exists for the case where that advice was ignored. | 2026-09-20 |
| D34 | Release gate R1 (v1 import + first real use by YON) moved from after P7 to **after P3** | 2026-09-17 |
| D25 | TOTP 2FA built in FreeFrame first, then copied into FilmBill P0a (one implementation); P0 split into P0a (repo from FreeFrame core) and P0b (companies, roles, number series, audit, money, codegen) | 2026-09-16 |
| D20 | Archive folder structure is a per-company template; default = YON advisor structure (Year / Allgemeine Ordner · Ausgaben · Bank · Einnahmen · Revisionen / Quartal) | 2026-09-16 |

## 17. Questions for the Steuerberater

**Status 2026-09-16:** answered by Mathias for YON — Q2/Q3 → configurable policies (D12), Q4 Soll (D13), Q5 OK provided every item is listed (already §8.1), Q6 not used by YON (D15), Q7 → selectors (D13). Q1 (studio rental) → **not built into the software** (D19): YON configures a per-item tax override for its studio once the advisor answers. Optional check: whether the YON rebill practice (gross base + 20 %) matches how the advisor books the input VAT. Original questions kept below for other users' onboarding docs.

Send as-is. Answers go into the AT region pack seed data; the engine code does not change.

**English**
1. **Studio rental to foreign business clients:** when a German (EU) or US company rents our Vienna studio for a shoot, is that Austrian VAT (service connected to immovable property) or reverse charge / not taxable in Austria? Does the answer change if the studio is booked as a package with crew and equipment?
2. **Taxi/Uber and travel costs we re-bill:** part of our service (same VAT treatment as the main service) or pass-through costs (durchlaufende Posten, outside VAT)?
3. **Per diems (Diäten) billed to clients:** same question as 2.
4. **VAT timing:** is YON Studio OG on Sollbesteuerung (VAT due on invoicing) or Istbesteuerung (VAT due on payment)?
5. **Advance/final invoices:** is this final-invoice format fine: full service total, then each advance invoice deducted with number, date, net, VAT and gross, then balance due?
6. **Freelance crew:** may we pay freelancers via self-billing (Gutschrift per §11 UStG) instead of them sending invoices? What agreement do you need?
7. **Handover to you:** which software and export format do you use (BMD NTCS, RZL, DATEV, other) and which chart of accounts? Should the monthly document folders be sorted by invoice date or payment date?

**Deutsch**
1. **Studiovermietung an ausländische Firmenkunden:** Wenn ein deutsches (EU) oder US-Unternehmen unser Studio in Wien für einen Dreh mietet — österreichische USt (Leistung im Zusammenhang mit einem Grundstück) oder Reverse Charge bzw. in Österreich nicht steuerbar? Ändert sich das, wenn das Studio als Paket mit Crew und Equipment gebucht wird?
2. **Weiterverrechnete Taxi-/Uber- und Reisekosten:** Teil unserer Leistung (gleiche USt-Behandlung wie die Hauptleistung) oder durchlaufende Posten (nicht umsatzsteuerbar)?
3. **Diäten, die wir Kunden verrechnen:** gleiche Frage wie 2.
4. **Besteuerungsart:** Ist die YON Studio OG soll- oder istversteuert?
5. **Anzahlungs-/Schlussrechnungen:** Passt dieses Format für die Schlussrechnung: Gesamtleistung, dann jede Anzahlungsrechnung einzeln abgezogen mit Nummer, Datum, Netto, USt und Brutto, dann Restbetrag?
6. **Freie Crew:** Dürfen wir Freelancer per Gutschrift (§11 UStG) abrechnen, statt dass sie uns Rechnungen stellen? Welche Vereinbarung braucht es dafür?
7. **Übergabe an die Kanzlei:** Welche Software und welches Exportformat nutzt ihr (BMD NTCS, RZL, DATEV, anderes) und welchen Kontenrahmen? Sollen die Monatsordner nach Rechnungsdatum oder Zahlungsdatum sortiert sein?

Additional compliance sources (v0.2): [USP – Buchführungspflicht](https://www.usp.gv.at/themen/steuern-finanzen/steuerliche-gewinnermittlung/weitere-informationen-zur-steuerlichen-gewinnermittlung/betriebliches-rechnungswesen/buchfuehrungspflicht-und-buchfuehrung.html) · [BMF – Anhebung Buchführungsgrenzen (16.04.2026)](https://www.bmf.gv.at/presse/pressemeldungen/2026/april-2026/buchfuehrungsgrenzen.html) · [Gaedke & Partner – Gutschrift oder Rechnungskorrektur](https://gaedke.co.at/kanzleinews/gutschrift-oder-rechnungskorrektur/)
