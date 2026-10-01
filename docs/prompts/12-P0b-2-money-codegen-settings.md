# Claude Code prompt — FilmBill P0b-2: money, generated API types, company settings screens

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**, after P0b-1 (`eba7b5f`, `21814f6` on `main`).
> Save as `docs/prompts/12-P0b-2-money-codegen-settings.md`, add an INDEX line.
> Scope reference: `docs/SCOPE.md` §0 (P1, P2), §10 (D13), §16.
> Third of four. **P0b-3** (number series, audit events) follows and is deliberately separate — those two are only *correct* against a real Postgres and deserve their own report. Do not start them here.

---

## Order matters: §1 and §2 first
`money.py` and the generated types are what every later phase is written against. Land them before the screens, so the screens are the first consumer rather than a retrofit.

## 1. Money

`apps/api/core/money.py`:
- `Money` / `Quantity`, Pydantic-compatible, backed by `Decimal`. JSON serialisation as a **string**. Parsing accepts strings and ints and **rejects floats** — `12.3` as a JSON number is a 422, not a silent binary-float round trip.
- `quantize_amount(d)` → 2 dp, `quantize_qty(d)` → 4 dp, `ROUND_HALF_UP`, using an **explicit local decimal context**, never the global one (a library that mutates the global context would otherwise change your rounding from a distance).
- SQLAlchemy helpers `AmountColumn()` = `Numeric(18,2)`, `QtyColumn()` = `Numeric(18,4)`.
- Web: `lib/money.ts` for **formatting only** — locale-aware display from strings. Enforce "no arithmetic here" with an ESLint `no-restricted-syntax` override scoped to that file (a `BinaryExpression` using `+ - * / %` on non-string operands, and `Number()` / `parseFloat` calls). **Not** a source-text test — CLAUDE.md rule 11.

Nothing computes money on the web, ever. The calculation engine (P1) is the only place arithmetic happens, and this is the groundwork that makes that enforceable rather than aspirational.

## 2. Generated API types
- `openapi-typescript` as a dev dependency; `pnpm gen:api` writes `apps/web/types/api.gen.ts`.
- Add `python -m apps.api.scripts.dump_openapi` so CI needs no running server.
- Migrate the hand-written interfaces in `types/index.ts` to re-exports of generated types wherever they mirror an API response. Keep genuinely UI-only types hand-written, and say in the report which ones you kept and why.
- CI step: regenerate, then `git diff --exit-code apps/web/types/api.gen.ts`. Demonstrate the drift failure once by adding a schema field without regenerating, then revert.

This is the guard against the class of bug FreeFrame's `CLAUDE.md` records: response interfaces silently drifting from the Pydantic schemas.

## 3. The company screens — and the hole they close

P0b-1 shipped `POST /companies`, `GET`/`PATCH /company` and the Members screen, but **no screen creates or edits a company**, so its own acceptance step 1 had to go through Swagger. That was a contradiction in that prompt, not a defect in the build. Close it here.

**Settings → Company** (per active company), a new group beside the existing `members`:

- **General** — legal name, trading name, legal form, register number + court, VAT ID, tax number, address (street, zip, city, country ISO-3166), email, phone, website, default currency, default language, fiscal year start month, timezone, logo. Requires `company.settings.edit`.
- **Create a company** — reachable from the company switcher ("New company…") and from Settings → Company for anyone who may create one. After creation the new company becomes active and the user is its Owner.
- **Bank accounts** — `CompanyBankAccount` is modelled and migrated but has no endpoint. Add CRUD under `/company/bank-accounts`, scoped like everything else, plus the UI: label, IBAN, BIC, bank name, default flag. Validate the IBAN's structure and check digits (mod-97); do not call out to any service. Exactly one default per company, enforced server-side.
- **Accounting** — selectors only, **no logic yet** (SCOPE §10, D13): bookkeeping mode (EAR / double-entry) · VAT timing (Soll / Ist) · Kleinunternehmer flag · chart-of-accounts template (placeholder list) · export format (placeholder list) · archive date basis (invoice date / payment date) · month approval enabled. Each with one line of help text and a "decide this with your tax advisor" hint. Storing a selector must not make anything behave differently yet — say so in the report.
- **Security** — `require_2fa_roles` and `tax_advisor_reports` are already columns and `PATCH /company` already writes them; what is missing is the screen. A multi-select of roles that must use 2FA, and the tax-advisor reports toggle.
  **State the consequence in the UI, at the moment of the change:** adding a role here forces every member holding it into 2FA enrolment at their next login, and (P0b-1) `two_factor_required_for` then prevents them turning it off. That is a real lockout risk for someone whose mail is broken — the screen must say so before saving, not after.

## 4. Tests — behavioural
1. **Money:** a float in JSON is rejected (422); `"0.005"` quantizes to `"0.01"`; a string round-trips preserving trailing zeros (`"12.30"` stays `"12.30"`, not `"12.3"`); `quantize_qty` keeps 4 dp.
2. **Decimal context:** code that mutates the global decimal context does not change `quantize_amount`'s result. Set a hostile global context in the test and assert the answer is unchanged.
3. **ESLint rule:** arithmetic added to `lib/money.ts` fails lint. Demonstrate, then revert.
4. **Codegen drift:** the CI step fails when a schema field is added without regenerating. Demonstrate, then revert.
5. **Bank accounts:** cross-company access 404s (this endpoint joins the parametrised isolation test from P0b-1 automatically — confirm it does, and if it does not, the test's OpenAPI-derived case list is broken and that is the bug to fix); a bad IBAN checksum is rejected; setting a second default demotes the first, in one transaction.
6. **Security screen:** adding a role to `require_2fa_roles` makes `/auth/me` report `two_factor_required: true` for a member holding it, and makes their next login force enrolment. This is P0b-1's helper seen from the UI side — assert it end to end rather than trusting the unit test underneath.
7. **Accounting selectors** persist per company and change no behaviour.

**Mutation checks with a real FAIL line each:** accept a float in `Money` (1); use the global decimal context (2); allow two default bank accounts (5); drop the `require_2fa_roles` read from the membership helper (6). Revert, show green.

Run the API suite against real Postgres with Redis reachable, and report the honest counts the way P0b-0 and P0b-1 did.

## 5. Acceptance in the browser — write the steps out for Mathias
Dev stack. **This time step 1 must not touch Swagger** — that is the point of §3.
1. Create a second company entirely through the UI; it becomes active and you are its Owner.
2. Fill in General for YON Studio OG; add two bank accounts and switch which is default; a wrong IBAN is refused with a readable reason.
3. Set Accounting selectors; reload; they persist.
4. On Security, add `producer` to the roles requiring 2FA. The screen must state the consequence **before** you save. Then sign in as a producer without 2FA and confirm forced enrolment.
5. Switch companies; General, bank accounts and Accounting all differ.

## 6. Report
Three tiers, honest test counts, which `types/index.ts` interfaces stayed hand-written and why, the ESLint rule as written, and the deploy shape (dev stack only; no image pushed to any registry before R1 — rule 18).
