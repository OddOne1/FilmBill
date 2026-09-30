# FilmBill P0b-0 — §199–§206 ported, and the test suite that was lying

> Recorded 2026-09-26 from Claude Code's report. `main` at **`3f36aa8`**, CI green on all four jobs, nothing pushed to any registry (rule 18, gate R1). Commits: `9d4cd5b` (the §0 harness fix), `7cf9069` (the port), `3f36aa8` (head).
> Roughly a third of the source report arrived truncated. Everything below is what was legible and unambiguous; cut-off passages are marked **[paste truncated]** rather than reconstructed.

---

## §0 — the API suite was lying, and by how much

| | Redis reachable | Redis unreachable |
|---|---|---|
| Before | **30 failed / 297 passed** | 327 passed |
| After the §0 fix | 331 passed | 327 passed / 4 skipped |
| After the port | **537 passed** | 533 passed / 4 skipped |

The fix is entirely in the harness, nothing in the limiter: the suite uses Redis **database 15** of whatever `REDIS_URL` names, and an autouse fixture clears the `rl:` and `grl:` counters between tests. Counters are *deleted*, not the limiter disabled — so what runs in a test is the real `check_rate_limit` and the real middleware.

`test_rate_limiter_is_live.py` guards the fixture against quietly becoming an off switch, which nobody would notice because everything would pass. It asserts the cap still bites **inside** one test and that the next test is nevertheless clean, and it **skips** where Redis is unreachable rather than passing for the old wrong reason.

## The port

**Method.** Three-way merge per file — base = the P0a export point, theirs = FreeFrame `d5d5b2e`, ours = FilmBill — via `git merge-file` on plain files, so no remote and no history contact between the repos. 34 files new, 23 merged clean, 20 conflicted (46 hunks) resolved by hand.

**Cut on the way in**, and the part worth remembering is that several of these arrived through **clean, conflict-free merge regions**:
- `@freeframe/design-tokens`
- `GuestUser` / `guest_users` — a share-link model, caught by `alembic check` as a table with no migration
- transcription / LUT types, `PlatformStorage` **[paste truncated]**
- an invite role **[paste truncated]**, and a `/share/xyz` case that directly contradicted FilmBill's own `middleware.test.ts`
- 18 `ff_*` → `fb_*` renames; `/projects` in two ported tests

**Migrations are FilmBill's own.** FreeFrame's revision ids and a `down_revision` of `add_two_factor_method` (a revision FilmBill does not have) were replaced by a renumbered `0002` / `0003` / `0004` chain on FilmBill's baseline. `upgrade head` → `check` → `downgrade base` clean on a scratch database.

**Three defects found during the port, none of them by a test:**
1. `admin.py` called `datetime.now()` where the module had no such name in scope — the call, not the import. A 500 from clear-account-gate.
2. `auth_service.py` lost `decode_2fa_pending_token` to a **duplicate definition** — upstream had reordered, and the second `def` silently wins. A duplicate-def scan found it; nothing else would have.
3. Claude Code's own rename tooling: it renamed the product via `tokenize` specifically to avoid rewriting provenance comments — but **docstrings are strings**, so sentences describing FreeFrame became claims about FilmBill. Restored against HEAD. (Worth noting beyond the bug: FilmBill is AGPL-3.0 over MIT-licensed FreeFrame code, so provenance text is a licence artefact, not just a comment.)

**SMTP security modes: ported**, with its own INDEX line, as asked. The reason it belongs: FilmBill's P0a fix inferred implicit TLS from `port == 465`, which is wrong for implicit TLS on a non-standard port and wrong for plaintext on 465. The explicit setting replaces that heuristic and stops `email_service.py` being a permanent divergence that conflicts on every future merge. Existing `use_tls` still works, so nothing configured changes on upgrade.

## Verification

**Run & observed** — API 537 passed inside `filmbill-api` with Redis reachable; web 338 passed / 37 files; `pnpm lint`, `tsc --noEmit`, `pnpm build` clean. `alembic upgrade head` → `check` → `downgrade base` clean on a scratch database (created and dropped, dev data untouched). All six required mutations produced real FAIL lines:

| Mutation | Caught by |
|---|---|
| Skip the `tv` check | `test_token_version.py` — 5 failures |
| Gate computed from a client flag | `…claim_can_turn_the_gate_off[extra0]` |
| Reset mail falls back to the login address | `…means_no_reset_mail_at_all` + 1 |
| Enrolment renders `magic_code.html` | 5 failures |
| Re-auth code into the enrolment pool | `…the_CHALLENGE_pool` + 2 |
| Digits-only strip on the backup-code field | `backup-code-entry.test.tsx` — 6 failures |

On the live dev stack: migrations auto-applied, the new endpoints answer (403 unauthenticated; `/auth/password-policy` 200 by design), and `/setup`, `/login`, `/settings/profile` compile and serve 200 with no `@zxcvbn-ts` module error. A module-resolution failure did appear in the web logs and turned out to be **stale output from before the container reinstalled** — named in the report because that is exactly the shape rule 17b warns about.

`password-policy-loading.test.ts` imports `@zxcvbn-ts/language-common/dist/index.esm.js` by explicit path, and that file was confirmed to genuinely have no default export — so the guard is testing the real condition, not a stand-in.

**Statically checked only** — the three new email templates were not opened in a mail client; `scripts/clear_account_gate.py` was ported but never executed.

**Not checked** — everything in the browser walkthrough (§4). `docker compose down -v` is refused by the permission classifier, so it needs a human at the Mac.

**CI on `3f36aa8`:** web lint/typecheck/test/build · migrations match the models · API tests · Docker build (no push) — all four green. The migrations job runs `alembic upgrade head` then `alembic check` against a real Postgres; it is the job that would have caught `guest_users`.

## Decisions taken here

- **The 378 `§` citations stay, qualified.** They are the only link from a piece of security code back to the incident that shaped it, and FilmBill will drift from FreeFrame over time. An unqualified bare `§204` inside FilmBill would falsely imply FilmBill has a §204; the qualified in-prose form ("ported from FreeFrame §204") is provenance and rationale in one line. Removing them saves nothing and costs the reason.

## Still open

The browser walkthrough. Empty database, `down -v`, rebuild, `up -d`, then at `http://localhost:3100` with Mailpit at `http://localhost:8125`:

1. Setup → create admin. `Sommer2026!` must be **rejected with a stated reason**, and the submit button must never be dead-and-silent (rule 17c). Then a real password.
2. The account gate: password first, then a backup address; the code arrives in Mailpit; a same-domain address must say so readably; the gate clears only once the code is entered.
3. Enrol in email 2FA. Subject **"Confirm two-factor authentication on FilmBill"**, **no code in the subject**, body says the code cannot be used to sign in.
4. Log out, log in. That mail says "finish signing in" and the code **is** in the subject.
5. Redeem a backup code at the login challenge — lower case, no dash.
6. Disable 2FA with a backup code. Then with the instance-wide requirement on, "Turn off" must be disabled **with the reason shown**.
