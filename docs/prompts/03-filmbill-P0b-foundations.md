# Claude Code prompt — FilmBill P0b: business foundations

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**, after P0a is merged and its acceptance passed.
> Save as `docs/prompts/P0b-foundations.md`, add INDEX line.
> Scope reference: SCOPE.md §0 (principles P3–P7), §11.1 (roles), §5.5 (audit), §9.3b (tax advisor 2FA), §13 (P0), §16 D3/D23.
> P0a changed many files; do not trust line numbers from any earlier document.

---

## Goal
The cross-cutting building blocks every later phase depends on: companies (multi-company UI), per-company roles & permissions, company settings, number series, audit events, money type, generated API types. **No invoices, parties or items yet.**

## 1. Companies & multi-company
- Model `Company`: legal name, trading name, legal form, register number + court, VAT ID, tax number, address (street, zip, city, country ISO-3166), email, phone, website, default currency (ISO-4217), default language, fiscal year start month, timezone, logo (S3 key), `created_at`, `archived_at`.
- `CompanyBankAccount`: company, label, IBAN, BIC, bank name, is_default.
- `CompanyMembership`: user, company, role (enum below), `granted_by`, `granted_at`, `expires_at` NULL, `revoked_at` NULL.
- **Install-level vs company-level:** `User.role == superadmin` = runs the installation (users, email, site settings). Company roles are separate; a superadmin is not automatically a member of every company, but can create companies and add themselves.
- Setup flow (from P0a) additionally creates the first company and makes the superadmin its **Owner**.
- **Scoping mechanism (one way only):** requests carry the active company in header `X-Company-Id`; dependency `current_membership()` resolves user + company + role, returns **404** if no active membership (not 403 — don't leak existence). Base mixin `CompanyScoped` (`company_id` FK, index) and helper `scoped(query, membership)` used by every business router.
- Web: company switcher in the top bar (hidden when the user has one company), active company stored per user in `preferences`, all API calls send the header via `lib/api`.
- Branding becomes **per company** (logo, colours, fonts for documents); the existing site branding remains for login page / app chrome.

## 2. Roles & permissions (data, not scattered ifs)
Roles: `owner`, `admin`, `accountant`, `producer`, `staff`, `tax_advisor`.
- `apps/api/core/permissions.py`: a single matrix `PERMISSIONS: dict[str, set[Role]]` with dotted permission keys. Seed it now with keys for P0b features plus placeholders used later (e.g. `company.settings.edit`, `company.members.manage`, `number_series.manage`, `audit.view`, `archive.view`, `archive.download`, `documents.finalize`, `documents.revise`, `ledger.view` …). Unused keys are fine.
- Dependency `require(permission)`; every endpoint added in this prompt uses it.
- `tax_advisor`: only `archive.view`, `archive.download` (+ optionally `reports.view`, `exports.download` via company setting). Everything else 404.
- 2FA policy: FreeFrame's login flow asks `require_2fa_enabled(db)` (install-wide), and §195 additionally **closes magic-code sign-in and self-registration entirely** while that flag is on. Replace that call in the post-first-factor helper with **one** function `two_factor_required_for(db, user) -> bool`: True if the install-wide `require_2fa` is on, **or** the user holds an active `tax_advisor` membership in any company, **or** holds a role listed in any of their companies' `require_2fa_roles`. Every first-factor path (password, magic code, invite) must use it — add it to the parametrised first-factor tests.
  **Careful with §195:** FilmBill requires 2FA *per user/role* (tax advisors), not instance-wide, so magic-code sign-in must stay open for everyone else and, for a user who is required or enrolled, must chain into the 2FA challenge (§193) instead of being switched off. Keep FreeFrame's instance-wide switch as an additional company/site setting; only that switch may close magic-code sign-in globally. Test both: required user via magic code → 2FA challenge; unaffected user via magic code → normal login.
- Web: navigation items are shown only if the user holds the permission; a placeholder **Archive** page (gated by `archive.view`, text "Archive — coming in P5") exists so the tax-advisor view can be accepted now.
- Membership management UI: Settings → Company → Members (invite existing or new user by email with role, expiry date for tax advisors, revoke). Emails via existing email worker.

## 3. Company settings UI
Settings → **Company** (new group, per active company): General (legal data, bank accounts, logo) · Members · Accounting · Number series · Security (`require_2fa_roles`).
**Accounting** stores selectors only — no logic yet (SCOPE §10, D13): bookkeeping mode (EAR / double-entry) · VAT timing (Soll / Ist) · Kleinunternehmer flag · chart-of-accounts template (placeholder list) · export format (placeholder list) · archive date basis (invoice date / payment date) · month approval enabled. Each with a one-line help text and "decide with your tax advisor" hint.

## 4. Number series
- Model `NumberSeries`: company, `document_type` (string key), name, pattern, `reset` (never / yearly), `next_value`, `current_period` (e.g. year), `padding`, active flag. Unique active series per company + document type (allow several inactive).
- Pattern tokens: `{YYYY}`, `{YY}`, `{MM}`, `{SEQ}` (padded), literal text. Example default for invoices: `AR-{YYYY}-{SEQ}` padding 4 → `AR-2026-0001`.
- Service `allocate_number(db, company_id, document_type, document_date) -> str`: `SELECT … FOR UPDATE` on the series row, handles yearly reset based on `document_date`, increments, returns formatted number, **must be called inside the caller's transaction** (no own commit). Rollback of the caller → number not consumed.
- Admin UI to create/edit series with live preview of the next number; editing `next_value` downward is blocked if it would re-issue a number that was already allocated (keep an `allocations` table: series, value, number, allocated_at, entity ref nullable).
- Seed per new company: series for `quote`, `order_confirmation`, `delivery_note`, `invoice`, `credit_note`, `self_billing`, `dunning`, `letter` with sensible German prefixes (AN, AB, LS, AR, KR, GS, MA, BR) — user-editable.

## 5. Audit events
- Table `audit_events`: id, company_id NULL (install-level events), user_id NULL (system), `entity_type`, `entity_id`, `action`, `changes` JSONB (field → [old, new]), `reason` NULL, `ip`, `user_agent`, `created_at`.
- Append-only enforced **in the database**: migration adds a trigger that raises on UPDATE/DELETE of `audit_events`.
- Helper `audit.record(db, membership|user, entity, action, before=None, after=None, reason=None)` computing the diff.
- Wire it into everything in this prompt: company edits, membership grant/revoke, role changes, number series edits, security setting changes, 2FA enable/disable/reset (in addition to FreeFrame's activity log).
- Settings → Company → Audit log: filterable list (entity, user, date), requires `audit.view`.

## 6. Money
`apps/api/core/money.py`:
- `Money` / `Quantity` Pydantic-compatible types backed by `Decimal`; JSON serialisation as **string**; parsing accepts strings and ints, **rejects floats** (`12.3` as JSON number → 422).
- `quantize_amount(d)` → 2 dp, `quantize_qty(d)` → 4 dp, `ROUND_HALF_UP`, explicit local decimal context (never the global one).
- SQLAlchemy column helpers `AmountColumn()` = `Numeric(18,2)`, `QtyColumn()` = `Numeric(18,4)`.
- Web: `lib/money.ts` for **formatting only** (locale-aware display from strings); enforce "no arithmetic" with an ESLint `no-restricted-syntax` override for that file (BinaryExpression with `+ - * / %` on non-string operands, and `Number()`/`parseFloat` calls) — not a source-text test (CLAUDE.md rule 11).

## 7. Generated API types
- `openapi-typescript` dev dependency; script `pnpm gen:api` writes `apps/web/types/api.gen.ts` from the running API's or a dumped `openapi.json` (add `python -m apps.api.scripts.dump_openapi` so CI needs no running server).
- Migrate the copied FreeFrame hand-written interfaces in `types/index.ts` to re-exports of generated types where they mirror API responses; keep only UI-only types hand-written.
- CI step: regenerate, then `git diff --exit-code apps/web/types/api.gen.ts`.

## Tests (behavioural)
1. **Cross-company isolation:** for every endpoint added here, a user with membership only in company A gets 404 for company B resources and when sending `X-Company-Id: B`. Parametrise over the OpenAPI path list filtered by tag, so new endpoints are covered automatically; the test fails if an endpoint under a company tag has no case.
2. **Permissions matrix:** each role × each P0b endpoint → expected status; `tax_advisor` sees only archive permissions.
3. **Number series concurrency:** real Postgres, 20 threads allocate 50 numbers each for one series → 1 000 distinct numbers, no gaps, in order; a thread that rolls back does not consume a number; yearly reset on 1 Jan in company timezone.
4. **Audit append-only:** UPDATE and DELETE on `audit_events` raise at DB level.
5. **Money:** float JSON rejected; `"0.005"` quantizes to `"0.01"`; string round-trip preserves trailing zeros `"12.30"`.
6. **2FA policy:** granting a `tax_advisor` membership to a user without 2FA → next login (via password, magic code and invite each) returns `requires_2fa: true, setup_required: true`.
7. **Codegen drift:** CI step fails if a schema field is added without regenerating (demonstrate once, then revert).

Mutation checks with real FAIL lines for: removing the company filter from one scoped query (test 1), removing `FOR UPDATE` (test 3 must fail — if it doesn't fail reliably, increase contention and say so), dropping the trigger (test 4).

## Acceptance in the browser (dev compose)
1. Superadmin creates company "YON Studio OG" (AT, EUR, de) and a second test company.
2. Switcher appears; data and settings differ per company.
3. Invite `advisor@example.com` as tax advisor to YON only, expiry in 30 days → invite in Mailpit → on accept they are forced into 2FA setup → after setup they see an (empty) archive placeholder and nothing else; second company invisible.
4. Number series preview shows `AR-2026-0001`; change pattern → preview updates; audit log shows the change with old/new values.

Report in three tiers; list every new permission key; push when green.
