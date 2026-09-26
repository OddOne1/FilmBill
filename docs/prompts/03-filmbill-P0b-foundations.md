# Claude Code prompt — FilmBill P0b: business foundations

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**, after P0a is merged and its acceptance passed.
> Save as `docs/prompts/P0b-foundations.md`, add INDEX line.
> Scope reference: SCOPE.md §0 (principles P3–P7), §11.1 (roles), §5.5 (audit), §9.3b (tax advisor 2FA), §13 (P0), §16 D3/D23.
> P0a changed many files; do not trust line numbers from any earlier document.

---

## Port, do not rebuild (2026-09-21)
FreeFrame now carries §199 (`token_version`, four backported fixes), §200 (mandatory password, backup address, onboarding gate, password policy), §201 (gate UI), **§202 (the fix for §200's zxcvbn loader bug plus the "a disabled control must say why" rule)**, §203 (the emailed code's copy split into `login` / `two_factor_challenge` / `two_factor_setup`, HTML and text from one source) and §204 (the enrolment code's own Redis pool, so a setup code cannot satisfy a login challenge). Port that code as it stands at FreeFrame's current `main` — **never §200 without §202**, or FilmBill inherits a dead submit button on four forms, and **never §203 without §204**, or it inherits a security mail promising something the system does not keep. P0a already copied the email templates and tasks, so check what is in the FilmBill tree before porting and replace rather than duplicate.

## Fix this before you trust any test result (2026-09-26)
The API suite's green is environmental. Run where `redis:6379` is unreachable, the rate limiter fails open and 327 tests pass; run **inside** `filmbill_api`, where Redis is reachable, **36 return 429** — `test_setup_superadmin.py` alone makes 7 POSTs to `/setup/create-superadmin` against a cap of 3 per 600s. Those tests are not asserting what they look like they assert (CLAUDE.md 17b). **First task of P0b:** give the limiter a per-test reset fixture or a test-scoped key namespace, re-run inside the container, and report the real pass count before building anything else. Every test below is worthless until that number is honest.

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
- **2FA requirement per role — exactly one condition changes** (refined 2026-09-19 after the FreeFrame session traced it; [Certain] from the code at §198):
  FreeFrame has two independent levers, and only the first one is ours to change:
  | Lever | Where | What it does | FilmBill |
  |---|---|---|---|
  | forced enrolment | `_login_outcome()` in `routers/auth.py`, branch `if require_2fa_enabled(db):` (runs **after** `if user.two_factor_enabled:`) | user not enrolled → pending token with `setup_required=True` instead of real tokens | **replace the condition** with `two_factor_required_for(db, user)` |
  | code issuance | `send_magic_code()`, `if body.purpose != "password_reset" and require_2fa_enabled(db)` (§195) | refuses to issue magic codes instance-wide, 403 | **leave reading the instance-wide flag only** — it is a global policy switch, not a per-person rule |
  - `user.two_factor_enabled` (the user's own enrolment) stays untouched and is checked first, so an enrolled user is always challenged.
  - The **third state** that does not exist in FreeFrame today — *not enrolled, instance-wide switch off, but their role requires it* — is created entirely by the first row. No other file needs to change for it.
  - `two_factor_required_for(db, user) -> bool`: `require_2fa_enabled(db)` **or** the user holds an active, unrevoked `tax_advisor` membership in any company **or** holds a role listed in any of their companies' `require_2fa_roles`. Derived from `CompanyMembership` + company settings — no new column on `User` (a denormalised flag would drift the moment a membership is revoked). One query, called once per login; add an index on `(user_id, revoked_at)`.
  - Sequence for a role-required, not yet enrolled user: magic code or password correct → `_login_outcome` → `setup_required=True` → they must enrol before any token exists. Magic-code issuance is untouched, so they can still receive the code.
- **Session invalidation — check first whether P0a already copied it:** if FreeFrame shipped `token_version` before the P0a export (§199), it is already in the repo and this section shrinks to *bump it on membership grant/revoke and role change*. Otherwise build it here.
   [Certain] FreeFrame's tokens carry only `sub`, `type`, `exp` — there is no way to end a session. Granting a `tax_advisor` membership to someone who is already logged in therefore does **not** force 2FA until their token expires, and revoking a membership leaves their session alive. Add a `token_version` integer on `User`, stamp it into access and refresh tokens as `tv`, reject a mismatch in `get_current_user` and `/auth/refresh` (a token **without** `tv` counts as `tv = 0`, so existing sessions survive the migration), and bump it on: membership granted/revoked, role changed, 2FA enabled/disabled/reset, password changed. Precision (verified by the FreeFrame session 2026-09-19): **deactivation is already handled** — `get_current_user` and `/auth/refresh` both re-read `user.status` from the DB on every call, so a deactivated account dies immediately. The gap is only about changes that alter what an *active* user is entitled to: 2FA enabled/disabled/reset, password change, and FilmBill's membership/role changes. Also note `/auth/refresh` never re-runs `_login_outcome`, so without `tv` a session keeps renewing itself for the whole multi-day refresh window; with `tv`, a bump ends it at the next request and the user comes back through the login gate — no need to re-run the 2FA gate inside refresh.
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
6. **2FA policy**, all with the instance-wide `require_2fa` **off**:
   a. grant `tax_advisor` membership to a user without 2FA → password login and magic-code login each return `requires_2fa: true, setup_required: true`, and no access/refresh token;
   b. `/auth/send-magic-code` for that user still returns 200 (issuance is not blocked — §195 is a separate lever);
   c. an unaffected user logs in with a magic code normally;
   d. an enrolled user is challenged regardless of role or switch;
   e. instance-wide switch **on** → `/auth/send-magic-code` returns 403 for everyone (unchanged FreeFrame behaviour);
   f. revoke the membership → that user logs in without 2FA again (unless enrolled themselves).
7. **Session invalidation:** user is logged in; grant `tax_advisor` → their existing access and refresh tokens are rejected (`tv` mismatch) and the next login runs into forced enrolment; a legacy token without `tv` works while `token_version = 0`.
8. **Codegen drift:** CI step fails if a schema field is added without regenerating (demonstrate once, then revert).

Mutation checks with real FAIL lines for: removing the role branch from `two_factor_required_for` (test 6a must fail), skipping the `tv` check (test 7), removing the company filter from one scoped query (test 1), removing `FOR UPDATE` (test 3 must fail — if it doesn't fail reliably, increase contention and say so), dropping the trigger (test 4).

## Acceptance in the browser (dev compose)
1. Superadmin creates company "YON Studio OG" (AT, EUR, de) and a second test company.
2. Switcher appears; data and settings differ per company.
3. Invite `advisor@example.com` as tax advisor to YON only, expiry in 30 days → invite in Mailpit → on accept they are forced into 2FA setup → after setup they see an (empty) archive placeholder and nothing else; second company invisible.
4. Number series preview shows `AR-2026-0001`; change pattern → preview updates; audit log shows the change with old/new values.

Report in three tiers; list every new permission key; push when green.
