Before anything else, run `git remote -v`. This prompt is for the FilmBill repo (OddOne1/FilmBill). If the remote is anything else, stop, say so, and do nothing.

# Claude Code prompt — FilmBill P1a: tax data, region packs and the tax proposal engine

> Pasted inline. Save this entire message as the next free slot in `docs/prompts/` (check `docs/prompts/INDEX.md`; likely `21-`), add the INDEX line, then work.
> Scope reference: `docs/SCOPE.md` §6 (tax engine), §13 P1, §12 S2/S2b/S3/S8 (the proposal part), D19. Read CLAUDE.md first and obey it (money is Decimal, one calculation engine, company scoping and isolation tests, region packs are data, migrations only, generated API types, behavioural tests, real-FAIL mutation checks, three-tier report).
> **Decision by Mathias, 2026-10-20:** P1 is delivered in full as scoped, but cut into four prompts, each ending in something he can click: **P1a tax** (this prompt), **P1b parties**, **P1c catalog**, **P1d resource and reservation tables (no UI)**. Tax comes first because parties (default tax treatment) and items (tax class, override) both point at it.
> **Commit order matters.** Each numbered part is its own commit, green on its own, in order. Do not start a part while the previous one is red.
> The first visible invoice is P2. Nothing in this prompt creates a document.

---

## Step 0, before changing anything — report these, then continue
1. `git status -sb`, `git log origin/main..HEAD --oneline`, and the declared test environment with the full API suite baseline (17b). The expected baseline from the last task is 956 passed / 1 skipped (registry lookup, needs a Docker daemon) with `-m realdb` 228 passed / 0 skipped; if you get something else, say why before building.
2. Where Settings → Company navigation is defined (the new "Tax" entry goes there), how the number-series seeding runs at company creation and in its migration backfill (reuse that pattern), and which permission keys already exist for company settings. List any new key you add.
3. Which field holds a company's home country (`address_country`?) and what happens today when it is empty.
4. Is there already a `billing/` package (SCOPE principle P1 reserves `billing/calc`)? Put the new code under `apps/api/billing/tax/` as **pure Python with no database or FastAPI imports**, and add an import-boundary test that fails if it ever imports `sqlalchemy`, `fastapi` or `apps.api.models`.

## Part 1 — data model (migration, rule 9)
All tables carry `company_id` and join the cross-company isolation test.
- `tax_codes`: `code` (e.g. `AT-20`, immutable after creation), `name`, `rate` NUMERIC(18,4) as percent (20.0000), `category` (EN 16931: S, Z, E, AE, K, G, O, L, M; immutable), `legal_notes` JSONB `{lang: text}`, `notes_reviewed` bool (false for every shipped text), `reporting_mapping` JSONB (empty in P1a), `revenue_account` nullable string (accounts arrive in P6), `active`, `origin_pack` (which pack seeded it, nullable for user-created). Unique `(company_id, code)`. Categories Z, E, AE, K, G, O must have rate 0; refuse otherwise.
- `tax_classes`: `key` (`standard`, `reduced`, `reduced_2`, `zero` — per pack), `label`, `code_id` → the **domestic** code for the company's home country. This is "the item's tax class" of SCOPE §6.3. Foreign domestic rates and OSS are out of scope.
- `tax_rules`: `position` (ordered), `name`, `conditions` JSONB, `outcome` JSONB, `origin_pack`, `active`. **Condition vocabulary is closed and validated**: `seller_is_kleinunternehmer` (bool), `buyer_region` (`same_country` | `eu_other` | `non_eu`), `buyer_type` (`business` | `private`), `buyer_vat_verified` (bool), `supply_kind` (`goods` | `services`). **Outcome** = `{treatment, code: {class: <key>} | {code: <code>}}`. Reject unknown keys or values with a readable error.
- **Treatments are a system enum in code, not data** (they drive behaviour): `domestic`, `eu_reverse_charge`, `intra_community_supply`, `export`, `not_taxable_in_country`, `small_business_exemption`, `internal_recharge`, `custom`. Labels in de and en.
- Seeding: at company creation **and backfill existing companies in the migration**, from the pack matching the home country. If a company has no country yet, seed when the country is first set. **Never re-seed over a company's edits.** Report the migration result on the dev database and on the server-test data shape (one company, no country-specific data, created by first-run setup).

## Part 2 — region packs (rule: rules are data, P9)
`apps/api/region_packs/at.yaml`, `de.yaml`, `eu_generic.yaml`, loaded by a small loader with schema validation.
- **AT:** standard 20, reduced 13, reduced_2 10, zero 0; DE-pack: standard 19, reduced 7, zero 0 [Likely, to be confirmed by the Steuerberater; say so in the pack header and in the report]. **eu_generic:** the reverse-charge, intra-community, export, not-taxable, exempt and zero codes, plus a standard code with **rate 0 and `active=false`**, which the user must set before it can be proposed.
- Legal note texts, de and en, **generic wording only** (for example "Reverse charge: VAT to be accounted for by the recipient", "Steuerschuld geht auf den Leistungsempfänger über"). Do **not** cite statute numbers you cannot verify. Every note ships `notes_reviewed: false`, and the UI shows "not reviewed" until a user confirms.
- Shipped rules in this order (SCOPE §6.3): (1) seller is Kleinunternehmer → `small_business_exemption`; (2) buyer same country → `domestic`, class from the item; (3) buyer business, EU other country, VAT ID verified, services → `eu_reverse_charge` (AE); (4) same, goods → `intra_community_supply` (K); (5) buyer business, EU other country, VAT ID **not** verified → `domestic` with a warning (reverse charge cannot be proposed) **[Guessing, flagged for confirmation]**; (6) buyer private, EU or non-EU → `domestic` (OSS out of scope); (7) non-EU business, services → `not_taxable_in_country` (O); (8) non-EU business, goods → `export` (G).
- Tests: every pack loads; codes unique; every rule outcome points at an existing class or code; every condition key and value is in the vocabulary; rate/category constraints hold; loading twice changes nothing.

## Part 3 — the proposal engine (pure function, golden fixtures)
`propose_tax(inputs, codes, classes, rules, lang) -> Proposal`, where inputs = `seller_country`, `buyer_country`, `buyer_type`, `buyer_vat_verified`, `supply_kind`, `item_class`, optional `item_override`, optional `party_override`, `seller_is_kleinunternehmer`. The caller passes the tables in, so the function is testable without a database.
- **Precedence: item override > party override > rules [Guessing, record in SCOPE §16 and §17 for Mathias to confirm].** Manual document/line overrides happen in P2 outside this function; the function must return `would_have_applied` (the code and rule the rules alone would have produced) whenever an override changes the result, so P2 can show SCOPE S8's yellow warning.
- Output: `treatment`, `code`, `rate`, `legal_note` (in `lang`, or none), `applied_rule` (name and position, or `override:item` / `override:party`), `reasons` (plain sentences, one per decision step), `warnings` (list of codes plus text: unverified VAT ID, legal note not reviewed, standard rate inactive, home country missing), `blocking` (bool). A missing home country or an inactive resolved code gives `blocking: true` with a reason; it never guesses a rate.
- **Golden fixtures** (data-driven table test, expected values written out, not computed): AT→AT business, class standard → `AT-20`; class reduced → `AT-10`; AT→DE business verified services → AE; same goods → K; AT→DE business **unverified** → `AT-20` + warning; AT→DE private → `AT-20`; AT→US business services → O with note; goods → G; Kleinunternehmer seller → exempt for every case; S2b (item override "domestic rate for foreign business buyers" on a studio item) → studio `AT-20`, other items AE; party override; item + party override together → item wins; seller without country → blocking. Add the same set for the DE pack with DE codes.
**Mutations, a real FAIL line each:** swap rule order; drop the `buyer_vat_verified` check; ignore the item override; let precedence prefer the party override; return a rate from an inactive code. Revert each.

## Part 4 — API and audit
- `GET /company/tax/codes`, `PATCH /company/tax/codes/{id}` (name, rate, legal notes, `notes_reviewed`, active; `code` and `category` are immutable), `GET /company/tax/classes`, `GET /company/tax/rules` (read-only in P1a), `POST /company/tax/propose` (preview; calls the pure function; **the web never computes tax**).
- Write endpoints use the existing company-settings edit permission; read uses the matching view permission; list every key in the report. Another company's data is 404 (the layer's invariant).
- Audit (P0b-3): `tax_code.updated` with old/new values, written in the caller's transaction; wire it, test it, and add it to the "wired actions" list in the report.
- Regenerate `api.gen.ts` and let the drift job agree.
Tests (real Postgres, declared): permission and isolation matrix; edit constraints (rate on a zero category refused, immutable fields); audit row written and rolled back with the change; seeding idempotent and not overwriting edits; the propose endpoint returns exactly the pure function's result for the fixtures.

## Part 5 — screens (Settings → Company → Tax)
Two tabs, using the existing settings layout and permission-driven menu:
- **Codes:** table (code, name, rate, category, active, "legal note: not reviewed / reviewed"); edit drawer for name, rate, legal notes per language, reviewed flag, active. Inactive standard codes of the generic pack show "set your rate".
- **Test a case:** seller country read-only from the company; buyer country, buyer type, "VAT ID verified" toggle, goods/services, item class, optional "item override" and "party override" selectors (a code or "domestic for foreign business buyers"), and the result panel: treatment, code and rate, legal note, which rule or override applied, the reasons, warnings, and `would_have_applied` when an override is in play. It calls `POST /company/tax/propose`.
- Component tests with the real API shapes (rule 11: behaviour, not source text); settings-nav test updated for the new entry and its permission.

## Part 6 — documentation (separate commit)
SCOPE §16: next free D-numbers for (a) P1 split into P1a–d and the order; (b) override precedence item > party [Guessing]; (c) unverified EU B2B VAT ID → domestic with warning, never silent reverse charge [Guessing]; (d) legal notes and rates ship unreviewed. SCOPE §17: add a question for the Steuerberater (English and German) covering the AT/DE rates, the legal note wording, and decisions (b) and (c). Report the exact diff so it can be mirrored to the Project.

## Acceptance for Mathias — dev compose (≈10 minutes)
Settings → Company → Tax, tab **Test a case**, seller = your company (AT):
1. AT business, services, class standard → `AT-20`, domestic.
2. DE business, VAT ID verified, services → reverse charge (AE) with a note marked "not reviewed".
3. Same, goods → intra-community supply (K).
4. DE business, VAT ID **not** verified → `AT-20` and a warning explaining why.
5. US business, services → not taxable (O); goods → export (G).
6. S2b: add the item override "domestic for foreign business buyers" → AT-20, with `would_have_applied` showing the reverse-charge result.
7. Tab **Codes**: change a rate to 21, save, check the Audit log row; set it back.
8. Unset-country check: tell me what a company without a country shows (blocking message, no guessed rate).

## Report format
A 1–3 sentence plain-language summary first. Then three tiers: run and observed, statically checked only, not checked. List every migration, new permission key and audit action, the full API and web test counts **with the environment declared** and which new tests ran in CI as real-DB (not skipped), and the SCOPE diff. State what only the server can verify (the migration against the server-test data). Push when every part is green and CI shows the real-DB tests running.
