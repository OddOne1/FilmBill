# Claude Code prompt — FilmBill P0b-0: port FreeFrame §199–§206 (the whole account-security layer)

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**, after P0a-fix (`03773d5`) — done and accepted in the browser 2026-09-26.
> Save as `docs/prompts/07-P0b-0-port-auth.md`, add an INDEX line.
> This is the **first of three** prompts that replace the old single P0b. The other two (companies/scoping/permissions, then settings/number-series/audit/money/codegen) come after this one lands. Do not start on them here.

---

## What this is

P0a's `git archive` export was taken before FreeFrame's §199, so FilmBill has §191–§198 (2FA core, `require_2fa`, `totp_service`, the web auth components) and **nothing after it**. Verified in this tree on 2026-09-26: no `token_version`, no account gate, no `backup_email`, no `MAIL_CODE_COPY`, no enrolment code pool, no `two_factor_required`, no `resolveModuleExports`.

FreeFrame's current `main` is `d5d5b2e`. **Port §199–§206 as they stand there** — this is a port, not a reimplementation. Read FreeFrame's code at `~/Claude/Projects/FreeFrame/Freeframe/repo` and bring it across, adapting only what FilmBill's own naming and schema require. Every one of those eight sections was found and fixed by a human clicking through the deployed app; rebuilding from the prompts would reintroduce what the fixes were for.

**Two orderings that are not negotiable, both learned the hard way:**
- **Never §200 without §202.** §200's password meter read `common.default.dictionary`; `@zxcvbn-ts/language-common` has no default export in its ESM build, so the submit button was permanently disabled on four forms while every test passed. §202 is the fix.
- **Never §203 without §204.** §203's enrolment mail says "This code cannot be used to sign in"; §204 is what makes that true by giving enrolment codes their own Redis pool.

## 0. Before anything else: the API test suite is lying

Run where `redis:6379` is unreachable, the rate limiter fails open and 327 tests pass. Run **inside** `filmbill_api`, where Redis answers, and **36 return 429** — `test_setup_superadmin.py` alone fires 7 POSTs at `/setup/create-superadmin` against a cap of 3 per 600s.

Fix this first: a per-test reset fixture or a test-scoped key namespace for the limiter, so the suite exercises the real limiter and each test starts clean. Re-run inside the container and **report the honest pass count before writing any other code**. Everything below inherits this suite's verdict; until the number is true, a green run means nothing (CLAUDE.md 17b).

## 1. What to port

FreeFrame's `30afb86..d5d5b2e` on `apps/api` and `apps/web` is the source range. The substantive pieces:

**API**
- `middleware/account_gate.py` (new) and its wiring in `main.py`. Keep the middleware-not-dependency decision and its reasoning comment — the point is that it covers routers nobody remembered.
- `models/user.py` — `backup_email`, `backup_email_verified_at`, `token_version`, `account_setup_required`.
- `middleware/auth.py` — the `tv` check in `get_current_user` **and** `get_optional_user` (where a mismatch returns `None`, not an error).
- `routers/auth.py` — the large one: `via` claim, `_login_outcome`, the three code purposes plus `two_factor_reauth`, the enrolment pool, `/auth/2fa/send-reauth-code`, the policy check on `disable_two_factor`, `two_factor_required` on `/auth/me`.
- `services/password_policy.py`, `data/common_passwords.txt`, `services/redis_service.py`'s new pools (`TWOFA_ENROL_*`, backup-email codes), `services/site_settings_service.py`, `tasks/email_tasks.py`'s `MAIL_CODE_COPY` and `SECURITY_NOTICE_BODIES`, the three new email templates.
- `routers/admin.py` — the admin 2FA reset and gate-clear; `scripts/clear_account_gate.py`.
- Three Alembic migrations: `add_user_token_version`, `add_account_security_gate`. **Write FilmBill's own migrations against FilmBill's baseline** — do not copy revision ids or `down_revision` chains.

**Web**
- `lib/password-policy.ts` including `resolveModuleExports` and `types/zxcvbn-esm.d.ts`, plus the `@zxcvbn-ts/*` dependencies.
- `components/auth/`: `account-setup-gate.tsx`, `backup-email-form.tsx`, `password-field.tsx`, `password-submit-note.tsx`, `code-or-backup-input.tsx`, and the changes to `code-input.tsx`, `code-confirm-dialog.tsx`, `invite-accept.tsx`, `login-form.tsx`, `two-factor-settings.tsx`.
- `middleware.ts`, `layout/dashboard-shell.tsx`, `settings/profile/page.tsx`, `settings/admin/page.tsx`, `types/index.ts`.

**Decide and say which:** the same commit range also carries **SMTP security modes** (`add_email_smtp_security`, `services/email_config.py`, `test_smtp_security_modes.py`). That is an email-settings feature, not part of the account-security layer. FilmBill inherits FreeFrame's email system, so it probably belongs — but decide deliberately, state the decision in the report, and if you port it, say so as a separate line in INDEX rather than folding it into this one.

## 2. What must NOT come across
- FreeFrame's Alembic revision ids and `down_revision` chain (FilmBill has its own baseline from P0a).
- Any `ff_` prefix or FreeFrame-named table, setting key or Redis prefix. Redis **key strings** may keep their FreeFrame spelling where changing them buys nothing — but say so explicitly where you do.
- Media, project, share or transcription concepts. If a ported file references them, that reference does not belong in FilmBill; cut it and note the cut.

## 3. Tests
Port FreeFrame's tests for these sections too — they are most of the value, and several of them exist because a green suite once lied:
`test_token_version.py`, `test_password_policy.py`, `test_magic_code_second_factor_channel.py`, the `test_two_factor*` additions, and on the web `account-setup-gate`, `backup-code-entry`, `code-input`, `invite-accept-submit`, `password-policy-loading`, `password-submit-block`, `two-factor-settings-token-reissue`, `middleware-setup-gate`.

Keep `password-policy-loading.test.ts` intact in particular: it imports `@zxcvbn-ts/language-common/dist/index.esm.js` **by explicit path**, to read the ESM build the browser gets rather than the CJS one vitest would hand back. That test is the §202 lesson in executable form.

**Mutation checks with a real FAIL line each:** skip the `tv` check; compute the gate from a client flag; let the reset mail fall back to the login address; point the enrolment branch at `magic_code.html`; route a re-auth code into the enrolment pool; restore the digits-only strip on the backup-code field. Revert, show green.

## 4. Acceptance in the browser — you set it up, Mathias clicks it
Dev stack, **empty** database (`down -v`, rebuild, `up -d`). Write the steps out for him in the report, in the order he should click, covering:
1. Setup → create admin → password policy rejects `Sommer2026!` with a reason, and the submit button is never dead and silent.
2. The account gate appears: password first, then backup address; a code arrives in **Mailpit** (http://localhost:8125); the same-domain warning is readable; the gate disappears only when the data is real.
3. Enrolment in email 2FA: the mail says "Confirm two-factor by email" with **no code in the subject** and states the code cannot be used to sign in.
4. Log out, log in: the challenge mail says "finish signing in" and the code **is** in the subject.
5. Backup code redeemed at the login challenge, typed lower-case without the dash.
6. Disable 2FA using a backup code; with the instance-wide requirement on, "Turn off" is disabled **with the reason shown**.

## 5. Report
Three tiers, the honest API pass count from §0 both before and after the fix, the web counts, every mutation's FAIL line, the SMTP decision, and the deploy shape (expected: dev stack only — FilmBill is not deployed anywhere yet, and **no image is pushed to any registry before release gate R1**, CLAUDE.md rule 18).
