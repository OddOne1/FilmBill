# Claude Code prompt — FilmBill P0b-1: companies, memberships, scoping, permissions

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**, after P0b-0 (`3f36aa8`, accepted in the browser 2026-09-30).
> Save as `docs/prompts/09-P0b-1-companies.md`, add an INDEX line.
> Scope reference: `docs/SCOPE.md` §0 (P3–P7), §11.1 (roles), §9.3b (tax advisor), §13 (P0), §16 D3/D23.
> Second of three. P0b-2 (company settings UI, number series, audit events, money, OpenAPI codegen) comes after. **Do not start on it here** — in particular, do not add `money.py` or a number series "while you're in there".

---

## Why this is its own prompt
This is the layer every later phase sits on: if company scoping leaks, every invoice, party and document built on top of it leaks too, and the leak will be found by a customer rather than a test. It gets its own report and its own browser check for that reason alone.

## 1. Companies

- `Company`: legal name, trading name, legal form, register number + court, VAT ID, tax number, address (street, zip, city, country ISO-3166), email, phone, website, default currency (ISO-4217), default language, fiscal year start month, timezone, logo (S3 key), `created_at`, `archived_at`.
- `CompanyBankAccount`: company, label, IBAN, BIC, bank name, `is_default`.
- `CompanyMembership`: user, company, role, `granted_by`, `granted_at`, `expires_at` NULL, `revoked_at` NULL. Index `(user_id, revoked_at)`.
- **Install-level vs company-level.** `User.role == superadmin` runs the *installation* (users, mail, site settings) — it is not automatically a member of any company, but may create companies and add itself. Company roles are separate and live only in `CompanyMembership`. `services/permissions.py` says in its own docstring that it is rewritten here; do that rather than adding a second module beside it.
- Setup (from P0a) additionally creates the first company and makes the superadmin its **Owner**. An existing install with no company must still be able to create one — do not assume setup ran after this migration.

## 2. Scoping — one mechanism, no second way

- Requests carry the active company in header **`X-Company-Id`**.
- Dependency `current_membership()` resolves user + company + role and returns **404** when there is no active, unrevoked, unexpired membership — **not 403**. A 403 confirms the company exists; 404 says nothing (CLAUDE.md rule 6).
- Base mixin `CompanyScoped` (`company_id` FK, indexed) and a helper `scoped(query, membership)` that every business router uses. No router filters by `company_id` by hand.
- Web: a company switcher in the top bar, hidden when the user has exactly one company; the active company persists per user in preferences; **`lib/api` attaches the header centrally** — no call site sets it.

## 3. Roles and permissions — data, not scattered `if`s

Roles: `owner`, `admin`, `accountant`, `producer`, `staff`, `tax_advisor`.

- `apps/api/core/permissions.py` (or the rewritten `services/permissions.py` — pick one location and say which): a single `PERMISSIONS: dict[str, set[Role]]` keyed by dotted permission. Seed it with this prompt's keys **plus** placeholders later phases will need: `company.settings.edit`, `company.members.manage`, `number_series.manage`, `audit.view`, `archive.view`, `archive.download`, `documents.finalize`, `documents.revise`, `ledger.view`. Unused keys are fine and are the point.
- Dependency `require(permission)`; every endpoint added here uses it.
- `tax_advisor` holds only `archive.view` and `archive.download` (plus `reports.view` / `exports.download` if a company setting allows). Everything else 404.
- Navigation items render only when the user holds the permission. A placeholder **Archive** page gated by `archive.view` ("Archive — coming in P5") exists so the tax-advisor view can be accepted now.

## 4. Per-role 2FA — the two lines P0b-0 left ready

P0b-0 ported FreeFrame §199–§206, and the extension point is already in place and commented. Verified in this tree on 2026-09-30:

| Where | Today | Change |
|---|---|---|
| `services/site_settings_service.py::two_factor_required_for(db, user)` | `return require_2fa_enabled(db)` | **or** the user holds an active, unrevoked `tax_advisor` membership in any company, **or** holds a role listed in any of their companies' `require_2fa_roles` |
| `routers/auth.py:713`, `_login_outcome`'s forced-enrolment branch | `if require_2fa_enabled(db):` | `if two_factor_required_for(db, user):` |

**Do not touch** `routers/auth.py:231` — `send_magic_code`'s `require_2fa_enabled(db)` gate is an instance-wide policy switch, not a per-person rule, and making it per-person would stop a role-required user from receiving the very code they need to enrol.

Derive the answer from `CompanyMembership` plus company settings. **No denormalised flag on `User`** — it would drift the moment a membership is revoked. One query, once per login.

`two_factor_required` on `/auth/me` (§206) already calls this helper, so the web's disabled "Turn off" button inherits the per-role rule with no frontend change. Confirm that in the report rather than assuming it.

## 5. Sessions end when entitlements change
`token_version` exists from P0b-0. Bump it on: membership granted, membership revoked, role changed. (2FA and password changes already bump it.) Deactivation is already handled by the status re-read in `get_current_user` — do not add a second mechanism for it.

## 6. Membership management UI
Settings → Company → Members: invite an existing or new user by email with a role, set an expiry date (for tax advisors), revoke. Mail goes through the existing email worker and the existing invite flow — do not write a second invite path.

## Out of scope here, deliberately
Per-company **branding** (logo, colours, fonts for documents). It is coupled to companies and it is tempting, but it touches the branding router and the settings shell, and nothing in P0b needs it. It lands with the layout work in P4. Note it in INDEX as deferred rather than doing it.

## Tests — behavioural, against the real thing (CLAUDE.md 11, 17b)
1. **Cross-company isolation, parametrised.** For every endpoint added here, a user with membership only in company A gets 404 for company B's resources and when sending `X-Company-Id: B`. Build the case list from the OpenAPI path list filtered by tag, so a new endpoint under a company tag with no case **fails the test** rather than silently escaping it.
2. **Permissions matrix:** each role × each endpoint → expected status. `tax_advisor` reaches only the archive permissions.
3. **2FA policy**, all with the instance-wide `require_2fa` **off**:
   a. grant `tax_advisor` to a user without 2FA → password login and magic-code login each return `requires_2fa: true, setup_required: true`, no tokens;
   b. `/auth/send-magic-code` for that user still returns 200 — issuance is a separate lever;
   c. an unaffected user logs in with a magic code normally;
   d. an enrolled user is challenged regardless of role or switch;
   e. instance-wide **on** → `/auth/send-magic-code` 403 for everyone (unchanged);
   f. revoke the membership → that user logs in without 2FA again (unless enrolled);
   g. `/auth/me` reports `two_factor_required: true` for the role-required user.
4. **Session invalidation:** a logged-in user is granted `tax_advisor` → their existing access **and** refresh tokens are rejected on `tv` mismatch, and the next login hits forced enrolment.
5. **Expiry:** a membership past `expires_at` behaves exactly like no membership — 404, and no 2FA requirement derived from it.

**Mutation checks with a real FAIL line each:** remove the role branch from `two_factor_required_for` (3a); revert `auth.py:713` to `require_2fa_enabled` (3a); change `two_factor_required_for` to also gate `send_magic_code` (3b must fail); drop the company filter from one scoped query (1); skip the `tv` bump on grant (4); ignore `expires_at` (5). Revert, show green.

Report the honest API and web counts the way P0b-0 did, with Redis reachable.

## Acceptance in the browser — you write the steps, Mathias clicks them
Dev stack, empty database.
1. Superadmin creates **YON Studio OG** (AT, EUR, de) and a second test company.
2. The switcher appears; settings and data differ per company; it is hidden for a user with one company.
3. Invite `advisor@example.com` as `tax_advisor` to YON only, expiry 30 days → invite in Mailpit → on accept they are forced into 2FA setup → after enrolling they see the empty Archive placeholder **and nothing else**, and the second company is invisible.
4. While that advisor is signed in on a second browser, revoke the membership → their next request lands them back at login.

## Report
Three tiers, every new permission key listed, the two 2FA lines shown as a diff, and the deploy shape (dev stack only; no image pushed to any registry before R1 — rule 18).
