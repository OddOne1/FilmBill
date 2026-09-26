# Build log

One line per Claude Code prompt: number · title · prompt file · commit · status.
The prompt files are the specification; this is the record of what was built
from them. `docs/SCOPE.md` is where the product is decided.

| # | Title | Prompt | Commit | Status |
|---|---|---|---|---|
| — | Scope, durable contract and phase prompts | — | `df4b5f6` | done |
| P0a | New repo from the FreeFrame platform core | [`02-filmbill-P0a-repo-from-freeframe.md`](02-filmbill-P0a-repo-from-freeframe.md) | `3349695` | done — see notes |
| P0a-fix | Post-login 404 and leftover FreeFrame branding | [`05-filmbill-P0a-fix-login-redirect-and-branding.md`](05-filmbill-P0a-fix-login-redirect-and-branding.md) | `90173fe` | done — see notes |
| P0b-0 | Port FreeFrame §199–§206: the account-security layer | [`07-P0b-0-port-auth.md`](07-P0b-0-port-auth.md) | `7cf9069` | done — see notes |
| P0b-0b | SMTP security modes (ported with P0b-0, decided separately) | [`07-P0b-0-port-auth.md`](07-P0b-0-port-auth.md) | `7cf9069` | done — see notes |
| P0b | Foundations: companies, roles, number series, audit, money, codegen | [`03-filmbill-P0b-foundations.md`](03-filmbill-P0b-foundations.md) | — | superseded — split into P0b-0, P0b-1, P0b-2 |

---

## P0a — notes

**Source.** FreeFrame commit `929f5f5306b54958c9c4f11bd1a283c8255eff76` ("2FA
part 2 of 2: settings UI, and two backend gaps found tracing it", 2026-09-19) —
the last commit of the 2FA work before the desktop app, which is out of scope.
Files only, no git history, exported with `git archive` so only tracked files
could come across. Recorded in `NOTICE` with the list of what was copied.

**What P0a delivers.** Accounts (password + magic code), TOTP/email two-factor
with backup codes, invites, first-run setup, users and admin, notifications,
email through Mailpit in dev, site settings and branding, a Design placeholder,
the object proxy, and `GET /health/pdf` rendering through Gotenberg. One
Alembic baseline, five tables. No business features.

**Decisions that differ from the prompt, and why.**

- **The tag allowlist is `auth, admin, setup, users, notifications, events,
  email-settings, site-settings, files, health`.** The prompt anticipated
  `branding` and `me`; neither survived, and `files` is new. FreeFrame's
  `branding` router was project branding and watermarking — both media; the
  Branding *page* is powered by `/site-settings`, which is kept. Its `me`
  router served only `/me/assets` and `/me/folders`; `/me/notifications` lives
  on under the `notifications` tag. `files` is the object proxy that replaces
  `hls_proxy`, minus everything HLS, because avatars and brand images still
  have to reach a browser without making the bucket browser-facing.
- **Notification categories are empty on purpose.** FreeFrame's six were about
  comments, uploads and asset status. Inventing billing-flavoured names for
  switches that gate nothing would recreate the bug that module exists to
  prevent. The email-frequency control stays live; categories arrive with the
  events they describe.
- **`pnpm` workspace at the repo root**, replacing FreeFrame's npm-workspaces
  field plus a per-app pnpm lockfile. `packages/design-tokens` is inlined into
  `app/globals.css`; it existed to share tokens with the desktop app.

**Fixes made to kept modules** (each has a test):

- SMTP could not reach a plaintext server at all — `smtp_use_tls=False` opened
  an implicit-TLS connection. Three transports now: STARTTLS, implicit TLS on
  port 465, plain otherwise. Found by pointing dev mail at Mailpit.
- An emailed second factor was mailed to the same inbox that had just supplied
  a magic code as the *first* factor. The pending token now records which
  primary credential produced it, and the emailed factor is refused — for the
  automatic send, the explicit fallback, and mid-login enrolment — when that
  was a magic code.
- `GET /auth/invite/{token}` never set `org_name`, so the invite-accept screen
  rendered its headline as an empty line.
- The dev web container had no `API_INTERNAL_URL`, so every server-side
  settings fetch silently fell back to an address that is nothing inside that
  container: the login page rendered the bundled logo and learned `require_2fa`
  only after first paint.
- The baseline migration declared eight columns nullable that the models make
  NOT NULL. Caught by `alembic check`, which now runs in CI.
- `PATCH /admin/users/{id}/disable-2fa` had no button on either side of the
  port, so the documented recovery for a user who lost every factor was a curl
  command. Added to the admin user table, confirmed before it fires.
- The route middleware's setup check answered `next()` and returned, so the
  token check below it never ran on any request without the `fb_setup_done`
  cookie — every first request after a deploy. Nothing leaked (the API still
  refused), but the gate did not gate.
- `app/page.tsx` redirected `/` to `/projects`, a route that no longer exists,
  and shadowed the real dashboard at `/`. Removed; `(dashboard)/page.tsx` owns
  `/` now.
- The `ff_*` → `fb_*` rename (cookies, localStorage) had been missed in the
  first pass: `middleware.ts`, `lib/auth.ts`, the theme store and the
  collapse-state keys still used FreeFrame's names.

**Acceptance.** A–D ran green. E ran green against the live stack over HTTP;
the two browser-rendered halves of E were not observed in a browser, because
the Chrome extension was not connected — see the P0a report.

---

## P0a-fix — notes

**The 404.** The dashboard home is `app/(dashboard)/page.tsx`, i.e. `/`, but
five call sites still sent people to FreeFrame's `/projects`: three in
`components/auth/login-form.tsx` (finished password login, first-password set,
post-2FA-enrolment), `app/(auth)/login/page.tsx`'s `from` fallback, and
`hooks/use-auth.ts`. All now `/`; the `from` parameter still wins when it is
set. Every other post-auth redirect was walked and already pointed at a live
route: setup completion → `/login`, invite acceptance → `/`, middleware's
gate → `/login` and `/setup`, `/settings` → `/settings/profile`.

**The test** is `apps/web/__tests__/post-auth-redirects.test.tsx`. It drives
each flow through the UI and asserts against what actually reached
`router.replace` / `router.push` / `NextResponse.redirect` — not against the
source text (CLAUDE.md rule 11). The allowed set is read off the App Router
tree at run time, handling route groups and `[token]`, so a deleted page fails
the test rather than the user. Two mutations were run, each producing a real
FAIL line: pointing `login-form.tsx:126` back at `/projects`, and deleting
`app/(dashboard)/settings/branding/page.tsx`.

**Branding.** Auth tagline → "Invoicing and production resources"; root
`description` → "Self-hosted invoicing, accounting and production resources".
The five `public/logo*` files are re-rendered from DM Sans SemiBold, the app's
own font, with transparent backgrounds at the pixel dimensions the FreeFrame
files had.

**Two judgement calls on the artwork, both departures from the prompt:**

- **The three square files carry an `FB` monogram, not the wordmark.**
  `logo-icon.png`, `logo-icon-dark.png` and `logo.png` are 1311×1311 and are
  displayed at 28px (sidebar) and 32px (favicon). "FilmBill" is a 4.3:1
  wordmark; centred in a square at 28px its letters are ~6px tall, which is a
  smudge rather than a mark. Same typeface, same weight. The wide file
  (`logo-full.png`, the auth screen) and `logo.svg` carry the full wordmark as
  specified.
- **`logo-full.png` is drawn in the accent blue `#5b8def`.** The prompt's
  fallback for a raster `<img>` is "the dark-on-light and light-on-dark pair
  the existing names imply" — but `logo-full.png` has no paired name, and the
  auth screen it sits on is `#ffffff` in light theme and `#0d0d10` in dark.
  The accent clears 3:1 against both at that size, and it needs no change to
  `app/(auth)/layout.tsx`'s `<Image>`, keeping "the file names so nothing else
  needs touching" true. `logo-icon.png` is the accent for the same reason and
  one more: `app/layout.tsx` uses it as the DEFAULT FAVICON when no custom one
  is set, so it has to read on a light browser tab strip as well as in the
  dark-theme sidebar. Rendered in the light ink its role in the sidebar would
  suggest, it was invisible in a light tab bar. `logo-icon-dark.png` keeps the
  dark ink `[data-theme="light"]` wants; the components' light/dark switching
  is untouched.

**A FreeFrame sweep outside comments and test docstrings** found no remaining
user-visible string, alt text, page title, email template or manifest entry.
What is left is provenance, and deliberate: `NOTICE`, `LICENSES/`, `README`,
`CLAUDE.md`, `docs/`, and code comments recording where a module came from.

**CI's web `Test` step was intermittently red, and was before this change** —
it failed on `cc6f2b7` and `1a333f3` too, with every other job green in those
runs. The cause is `branding-draft.test.tsx`, not anything in this prompt, and
it is fixed here because a red `main` cannot tell Mathias whether the next
change broke something.

Its `renderPage()` helper awaited the "Workspace name" HEADING. That heading is
static chrome: it is on screen before the settings fetch resolves, and the page
has no loading state — `useSiteSettings` simply returns `orgName: 'FilmBill'`
until data arrives. So every test that set `org_name: 'Acme'` and then reached
for a control that only exists once the fetched name differs from the default
was racing the fetch with a SYNCHRONOUS `getByRole`, and lost whenever React's
commit came late. It surfaced as `Unable to find ... /reset name and colors/i`,
which points nowhere near the cause — the same shape of misdirection the
helper's existing comment already warns about for the SWR cache.

Reproduced by giving the mocked `get` a 20ms delay: 4 of 14 fail, with CI's
exact message. The helper now waits for the settings themselves — the name
field, plus the dark-logo slot for the tests that change only a logo. With the
fix, the file is green at 20ms, 50ms and 120ms of injected delay.

The one wall-clock wait this prompt added (the setup wizard's 1.8s success
panel) is jumped with fake timers rather than slept through: 2079ms to 128ms.
`lib/__tests__/auth-refresh.test.ts` still spends ~1.2s apiece in five tests
waiting out the retry backoff; that is real but was not the failure, and is
left alone.

**Found while running acceptance, not fixed (out of scope):** the API suite
passes only because the rate limiter fails open when Redis is unreachable.
Run inside `filmbill_api`, where `redis:6379` *is* reachable, 36 tests 429.
`test_setup_superadmin.py` alone makes 7 POSTs to `/setup/create-superadmin`,
which is limited to 3 per 600s, and no fixture clears `rl:*` between tests.
Nothing here caused it — this change touches `apps/web` only — but the suite
is not asserting what it looks like it asserts on those 36.

---

## P0b-0 — notes

**Source.** FreeFrame `30afb86..d5d5b2e` (§199–§206), read at
`~/Claude/Projects/FreeFrame/Freeframe/repo`. `30afb86` is the commit P0a's
`git archive` was taken from, so it is the true merge base.

**Method: three-way merge, not retyping.** For each of the 77 files, base =
FreeFrame@30afb86, theirs = FreeFrame@d5d5b2e, ours = FilmBill. Run through
`git merge-file`, which works on plain files — no remote, no fetch, no
history contact, so CLAUDE.md's "never write to the FreeFrame folder, do not
add it as a remote" holds. 34 files were new, 23 merged clean, 20 conflicted
(46 hunks) and were resolved by hand.

**§ citations are kept, qualified.** 378 references to FreeFrame's
§-numbered history came across. P0a stripped all of them; FilmBill's own 11
`§` references are to `SCOPE.md`. Deleting them outright would have meant
rewording ~160 comments inside security code for no functional gain, so the
mechanical markers (`# §199 — text`) were dropped and every surviving
in-prose citation was qualified to `FreeFrame §NNN`. That is a findable
pointer — `NOTICE` says where FreeFrame is — rather than a dead number.
Reversible with one substitution if you would rather they were gone.

**What was cut on the way in** (prompt §2). Some of these arrived through
*clean* merge regions rather than conflicts, which is the part worth
remembering — a conflict-free merge is not a reviewed one:

- `@freeframe/design-tokens`, re-added to `package.json` because upstream
  moved the line and the merge read that as an addition.
- `GuestUser` / `guest_users`, a share-link model. Caught by `alembic check`
  as a table with no migration, not by reading.
- Transcription and LUT types in `types/index.ts`; `PlatformStorageSection`
  on the admin page; the per-project `role` on invite acceptance; the
  `/share/xyz` case in the ported middleware test, which directly
  contradicted FilmBill's own `middleware.test.ts`.
- `ff_*` → `fb_*` across cookies and localStorage (18 occurrences), and
  `-p freeframe` in a runbook comment.
- `/projects` as the stand-in protected route in two ported tests — the same
  dead route P0a-fix removed from the login flow.

**Migrations are FilmBill's own.** FreeFrame's three arrived with its
revision ids and a `down_revision` of `add_two_factor_method`, a revision
this repo has never had. Renumbered onto the baseline as
`0002_user_token_version`, `0003_email_smtp_security`,
`0004_account_security_gate`. `alembic upgrade head`, `alembic check` and
`alembic downgrade base` all run clean on an empty database.

**Two defects the port itself introduced**, both found by tests rather than
by reading:

- `routers/admin.py` called `datetime.now(timezone.utc)` with no import —
  the merge took the new call and not the new import line. Surfaced as a 500
  from `clear-account-gate`.
- `services/auth_service.py` came out with `decode_2fa_pending_claims`
  defined twice and `decode_2fa_pending_token` gone, because upstream
  reordered the two functions. A duplicate-definition check over the staged
  tree is what found it; nothing else would have, since the second def
  silently wins.

**One defect this prompt's own tooling introduced.** The product rename was
applied to Python *string literals* via `tokenize`, to avoid rewriting the
comments that record provenance. Docstrings are string literals too, so 17
sentences describing FreeFrame became claims about FilmBill
("FilmBill enriched each row with a per-project role summary"). Restored
against HEAD. Only user-facing copy — email subjects, `MAIL_CODE_COPY`, the
authenticator issuer default — keeps the rename.

**Pre-existing, fixed in passing:** `login-form.tsx` carried
`// ─── Two-factor state (FreeFrame-FreeFrame) ───`, a garbled P0a rename.

**Counts.** API 537 passed (331 before, and see §0 below); web 338 passed
(233 before); lint, `tsc --noEmit` and `pnpm build` clean.

### §0 — the API suite was lying, and now is not

Run where `redis:6379` is unreachable both limiters fail open and the suite
reported 327 passed. Run inside `filmbill_api`, **30 of those 327 returned
429** instead of what they asserted. Fixed before any other code, in
`9d4cd5b`: the suite gets Redis database 15, and an autouse fixture clears
the `rl:` and `grl:` counters between tests. The limiter itself is untouched,
so a 429 from here on is real, and `test_rate_limiter_is_live.py` is the
guard against that fixture quietly becoming an off switch.

Honest counts: **30 failed / 297 passed → 537 passed** with Redis reachable,
533 passed / 4 skipped without.

### Mutations, each with a real FAIL line

| Mutation | Caught by |
|---|---|
| Skip the `tv` check | `test_token_version.py` — 5 failures incl. `test_a_protected_route_rejects_it` |
| Compute the gate from a client flag | `test_no_client_supplied_claim_can_turn_the_gate_off[extra0]` |
| Reset mail falls back to the login address | `test_no_backup_address_means_no_reset_mail_at_all`, `test_an_unverified_backup_address_receives_nothing` |
| Enrolment branch renders `magic_code.html` | `test_code_email_copy.py` — 5 failures incl. `test_enrolment_says_the_code_cannot_sign_you_in` |
| Re-auth code into the enrolment pool | `test_the_code_lands_in_the_CHALLENGE_pool` + 2 |
| Digits-only strip on the backup-code field | `backup-code-entry.test.tsx` — 6 failures incl. `does NOT strip letters or the dash — the whole bug` |

The first attempt at the reset-mail mutation (`user.backup_email or
body.email`) was **not** caught, and correctly so: the guard above it returns
early, so the line is unreachable and the edit was a no-op. Recorded because
an uncaught no-op reads exactly like a gap in the tests (CLAUDE.md rule 12).

## P0b-0b — notes

**SMTP security modes: ported.** The prompt asked for a deliberate decision.

FilmBill's P0a already fixed the underlying bug — FreeFrame read
`smtp_use_tls=False` as implicit TLS, so a plaintext relay was unreachable —
but fixed it by *inferring* the mode from the port number
(`implicit_tls = smtp_port == 465`). That heuristic is wrong for implicit TLS
on a non-standard port and wrong for a plaintext server on 465.

Ported because: it replaces a heuristic with an explicit stored setting; it
arrives with its own 323-line test file; FilmBill's email system *is*
FreeFrame's, so leaving it out would strand `email_service.py` in permanent
divergence and make every future port conflict there; and it is a settings
feature on a module P0a already keeps 1:1.

Carries `0003_email_smtp_security`, `services/email_config.py`,
`smtp_security` on `email_settings`, and `test_smtp_security_modes.py`.
`smtp_use_tls` is kept and still read, so nothing already configured changes
behaviour on upgrade.
