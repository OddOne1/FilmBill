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
| P0b-1 | Companies, memberships, `X-Company-Id` scoping, permissions, per-role 2FA | [`09-P0b-1-companies.md`](09-P0b-1-companies.md) | `eba7b5f` | done — see notes |
| P0b-2 | Money, generated API types, company settings screens | [`12-P0b-2-money-codegen-settings.md`](12-P0b-2-money-codegen-settings.md) | `d767fbf` | done — see notes |
| CI-fix | The CI web flake: a readiness gate that waited for static chrome | [`14-ci-web-flake.md`](14-ci-web-flake.md) | `e68d879` | done — see notes |
| dev-mail | Dev mail was never delivered: SMTP mode `none` for Mailpit | [`16-dev-mail-smtp-none.md`](16-dev-mail-smtp-none.md) | `fe6730e` | done — see notes |
| server-test | A LAN-only test instance on the TrueNAS server | [`17-server-test-instance.md`](17-server-test-instance.md) | `PENDING` | done — see notes |
| P0b | Foundations: companies, roles, number series, audit, money, codegen | [`03-filmbill-P0b-foundations.md`](03-filmbill-P0b-foundations.md) | — | superseded — split into P0b-0, P0b-1, P0b-2 |

---

## server-test — a LAN-only test instance on the TrueNAS server

New files only: `docker-compose.server-test.yml`, `.env.server-test.example`,
`scripts/gen-server-test-env.sh`, `scripts/server-test-preflight.sh`,
`scripts/server-test-backup.sh`, `docs/deploy/server-test.md`, and two test
modules. The dev and prod compose files are untouched.

**Isolation (rule 18).** Fixed `name: filmbill-v2`; every built image tagged
`filmbill-v2-<service>:test`, a name no registry serves; the Watchtower-disable
label on every service; its own network; no `external:` network or volume, no
Traefik or Cloudflare labels. Every published port binds to `${LAN_IP}` and
never `0.0.0.0`. Only `web`, `api`, `minio` and `mailpit` publish at all —
Postgres, Redis, Gotenberg and the workers stay internal.

**Why four ports and not the three the prompt listed.** `NEXT_PUBLIC_API_URL`
is baked into the web bundle at build time and production gets `/api` from
Traefik, which this stack does not have. Publishing the API (`API_PORT`, 8100)
and building the bundle against `http://${LAN_IP}:${API_PORT}` is what dev
already does and needs no reverse proxy; auth is a bearer token and no cookie
is marked `Secure`, so the cross-origin pair costs nothing. Recorded because
the prompt named three ports and this is a deliberate departure.

**Three bugs the smoke run found, all in this new file, all already solved in
`docker-compose.prod.yml` — found only because the smoke test ran the PROD
images rather than dev's:**
  1. `beat` crash-looped on `[Errno 13] Permission denied:
     'celerybeat-schedule'`. The prod image runs as `appuser` and cannot write
     `/workspace`; dev gets away with it as root over a bind mount. Needs
     `-s /tmp/celerybeat-schedule`, which prod has always carried.
  2. `worker` and `email_worker` sat `(unhealthy)` forever. The prod image's
     own `HEALTHCHECK` is the API's `curl .../health`; a Celery process serves
     no HTTP. Prod overrides it per service with `celery inspect ping`, and
     `beat` with a broker-connection probe. So does this file now.
  3. The pre-flight reported every port "free" and PASSED on a host with
     neither `ss` nor `netstat` — the worst available answer for the one step
     whose job is not colliding with FreeFrame. It now refuses and says which
     tool to install.

**Step 0 findings on plain `http://<LAN IP>`.** Cookies carry `SameSite=Lax`
and no `Secure`, so sessions work; no HSTS, no forced https, no service
worker, no WebAuthn, no `crypto.subtle`/`randomUUID`. One real break, fixed:
`handleCopyInviteLink` called `navigator.clipboard.writeText` unguarded, and
`navigator.clipboard` is `undefined` outside a secure context — the button
threw before setting its own "Copied" state, so it did nothing and said
nothing (rule 17c). It now shows the link to copy by hand.

**Reported, not fixed.** `apps/api/Dockerfile.prod`'s `HEALTHCHECK` probes
`http://localhost:8000/health` — the same `localhost`/IPv6 trap that left the
dev web service at FailingStreak 213. Every compose file overrides it, so
nothing is broken today, but an image whose own healthcheck is wrong is a trap
for the next service that forgets to override. And
`test_declared_dependencies.py`'s stdlib list is missing `stat`, so importing
it fails that test; worked around here by not importing it.

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

## P0b-1 — notes

Full report: [`10-P0b-1-outcome.md`](10-P0b-1-outcome.md).

**What landed.** Three tables (`companies`, `company_bank_accounts`,
`company_memberships`) in migration `0005`, six company roles, a
twelve-key permission map, and eight endpoints under one new `companies` tag.
The active company travels in `X-Company-Id` and nowhere else; `apps/web`
attaches it in `lib/api` centrally, so no call site sets it.

**Two ladders, kept apart.** `User.role` runs the installation; company
authority lives only in `CompanyMembership`. A superadmin who is not a member
gets the same 404 a stranger does — asserted in
`test_a_superadmin_is_not_a_member_of_every_company`. The one bridge is that a
superadmin may CREATE a company, which makes them its Owner.

**Path shape.** `/companies` (plural) is the two operations with no company
context — list yours, create one. `/company/...` (singular) is everything that
acts inside the company the header names. No company id ever appears in both a
path and a header, so there is no request in which the two can disagree.

**Permissions module: the rewritten `services/permissions.py`**, not a new
`core/permissions.py` — the prompt allowed either and asked which. The FastAPI
dependencies (`current_membership`, `require`) are in
`middleware/company.py`, beside `middleware/auth.py`, which is where this
codebase keeps request-scoped dependencies.

**The two 2FA lines went in as specified**, and `routers/auth.py:231`
(`send_magic_code`) was left alone — issuing a credential and accepting one
are separate levers, and gating issuance per person would refuse a
role-required user the very code they need in order to enrol. Mutation 3
proves a test fails if that changes.

**The new tests run against real Postgres.** A MagicMock session cannot prove
that a query filters by `company_id` — it returns what it was told for every
filter and for none, so a cross-company test written against `mock_db` passes
against a router with the scoping deleted (CLAUDE.md 17b). New `pg_*` fixtures
build a scratch database and point the app's own `SessionLocal` at it, because
the account gate and setup guard open their own sessions. They skip where
there is no Postgres; CI has one.

**The isolation case list is derived from the OpenAPI document**, filtered to
the `companies` tag, and every operation must appear in the case table — so an
endpoint added under that tag without a case fails the file rather than
escaping the suite.

**Two test files were fixed, both for the right reason.** Three 2FA files were
patching `apps.api.routers.auth.require_2fa_enabled` — the router's import,
not the policy — so once `_login_outcome` started asking
`two_factor_required_for`, the patch stopped reaching the login branch and six
tests failed loudly. Replaced with `conftest.require_2fa()`, which sets both
readers and keeps the real `two_factor_required_for` in the path. And
`test_setup_superadmin.py` read the new user as "the last thing `add` was
called with", which is now the membership; it picks by type instead.

**Deferred, as instructed: per-company branding** (logo, colours, fonts for
documents). It is coupled to companies and tempting, but it touches the
branding router and the settings shell and nothing in P0b needs it. It lands
with the layout work in **P4**.

**Known gap, flagged for P0b-2:** creating a company has an endpoint
(`POST /companies`) but no screen. The company-settings UI is P0b-2 and the
prompt says not to start on it, so the browser walkthrough creates the two
acceptance companies through `/docs`. `CompanyBankAccount` is likewise
modelled and migrated but has no endpoint yet, for the same reason.

**Counts.** API **615 passed** (537 at P0b-0), web **361 passed / 41 files**
(338 / 37). `tsc`, `next lint` and `next build` clean. `alembic upgrade head`
→ `check` → `downgrade base` → `upgrade head` clean on a scratch database. All
six required mutations produced real FAIL lines.

**Not checked:** the whole browser walkthrough — it needs
`docker compose down -v`, which the permission classifier refuses, so it needs
a human at the Mac. Nothing pushed to any registry (rule 18).

## P0b-2 — notes

Full report: [`13-P0b-2-outcome.md`](13-P0b-2-outcome.md).

**Money.** `apps/api/core/money.py` — `Money` / `Quantity` as `Decimal`,
serialised as strings, **JSON numbers refused with a 422** (a double has
already lost the value; an integer is accepted because it survives intact).
Trailing zeros preserved, non-finite values refused, and `quantize_amount` /
`quantize_qty` pass an explicit local `Context` to `quantize` so no library can
change FilmBill's rounding through the global one. Half-UP, not Python's
default half-EVEN. `AmountColumn()` / `QtyColumn()` are functions, not shared
type instances.

`lib/money.ts` formats only, everything a string end to end via
`Intl.NumberFormat`'s string argument. The ESLint override on that one file
bans **all** arithmetic rather than the brief's "arithmetic on non-string
operands": ESLint selectors have no type information, so that distinction
cannot be expressed without guessing in both directions. Strictly stronger, and
free — formatting needs no arithmetic. `.eslintrc.json` became `.eslintrc.js`
so the reason could sit next to the rule.

**Codegen.** `pnpm gen:api` dumps the schema by IMPORTING the app (no server, no
database, no network — verified with `--network none` and no env) and runs
`openapi-typescript` into `apps/web/types/api.gen.ts`. New CI job
`api-types-are-generated` regenerates and `git diff --exit-code`s it;
demonstrated failing on an unregenerated schema field. `gen:api:docker` exists
for a machine with no API Python environment, which is every Mac here.

`types/index.ts` is now a naming layer over the generated types. Four things
stayed hand-written and each says why in the file: `LoginResponse` (a union
FastAPI cannot declare), `SmtpSecurity` (deliberately narrower than the API,
which types it loosely so the router can answer a readable 400 instead of
pydantic's 422), `PasswordStrength` (computed in the browser), and the
`ACCOUNT_SETUP_REQUIRED` const.

**Four schema bugs the generated types found**, every one a case of the
generator being right:
- `User.deleted_at` never existed — the hand-written interface claimed a field
  `/auth/me` has never sent. Exactly the drift rule 10 exists for.
- `preferences`, `theme_colors` and `ActivityLogResponse.payload` generated as
  `Record<string, never>` — a map that can hold nothing. A bare `dict` emits an
  OpenAPI object with no value type, and `dict[str, Any]` is **not** enough
  either (Pydantic sees `Any` as no constraint). Fixed with a shared
  `JsonObject` alias.
- `EmailSettingsUpdate` requires the two `*_clear` flags; the admin page now
  sends them explicitly as `false`, which is what Save means.

**The company screens.** Settings → Company is a group of five: General,
Bank accounts, Accounting, Security, Members. Creating a company is
`/settings/company/new`, reached from **"New company…"** in the switcher — which
now shows for a superadmin with one company, and with none, because otherwise
the second company can only be created from a URL you have to know. **That
closes P0b-1's reported hole**: its acceptance had to go through Swagger.

IBAN validation is server-side only (`apps/api/core/iban.py`: structure,
per-country length, ISO 7064 MOD-97-10, no network). No TypeScript copy — a
second implementation of mod-97 would be a second thing to keep in step, and
the one that matters is the one the database sits behind. An unknown country
code passes on checksum alone, so a self-hosted install somewhere the registry
has not heard of can still enter its own account.

Bank accounts are gated on `company.settings.edit` for **reads** as well as
writes — stricter than `company.view`, because an IBAN is the detail a
convincing invoice fraud needs. Exactly one default, enforced server-side in
one transaction; demoting the only default is refused with the action that does
work named.

The Accounting selectors are **stored and read by nothing** (SCOPE §10, D13),
and the screen says so in a banner. A control that secretly does nothing is a
false statement to the user — the same bug as a notification category that
gates nothing.

The Security screen states the lockout consequence **before** the save: it
names the roles being added, **names the members** who hold them without a
second factor, says plainly when nobody is at risk, and relabels the button
"Save and require two-factor". Ten jsdom tests on that warning alone.

**The suite was reading its answer off its surroundings — fixed.**
`SetupGuardMiddleware` opens its own `SessionLocal`, so the ambient
`DATABASE_URL` decided the result: no tables → the guard fails open and
everything passes; tables and no superadmin → **503 on everything, 258
failures**; one superadmin row → passes again. All three observed from the same
commit within an hour. Same shape as the Redis problem P0b-0 §0 fixed, and
worse: the passing configuration is the one where the middleware never runs, so
**P0b-1's reported 615 was obtained in it**. `conftest.client` now sets
`setup_guard._setup_complete` explicitly and restores it; `SessionLocal` is
deliberately left alone (patching it broke a gate test, because the shared mock
handed the gate the wrong user). New **`test_setup_guard.py`** drives the real
middleware — nothing tested it before. Deterministic after: 705 with and
without a superadmin in the ambient database.

**Confirmed for §5:** the bank-account endpoints joined the parametrised
isolation test automatically — all four were caught as "no isolation case" the
first time it ran. The OpenAPI-derived case list needed no change; the row
*substitution* did, since `{membership_id}` was hard-coded. It is now a table,
with a test for the one silent failure mode left (an unmapped placeholder
leaves `{bank_account_id}` in the URL and the endpoint 404s for the wrong
reason).

**Counts.** API **705 passed** (615 at P0b-1), web **391 passed / 43 files**
(361 / 41). `tsc`, `next lint`, `next build` clean, all five company routes in
the build. `alembic upgrade head` → `check` → `downgrade base` → `upgrade head`
→ `check` clean on a scratch database. All four required mutations produced real
FAIL lines; the ESLint rule and the codegen drift check were each demonstrated
failing and then reverted.

Live on the dev stack: migration `0006` applied, setup created the first
company with the accounting defaults and an owner membership, a valid IBAN
stored normalised and auto-defaulted, a wrong check digit refused with its
readable sentence, an invalid selector 422'd.

**Not checked:** the browser walkthrough (needs `down -v`, which the permission
classifier refuses — steps in the report). None of the five screens was opened
in a browser. No `NUMERIC` column ships yet — nothing stores an amount until
P1, so the column helpers are asserted on precision and scale, not
round-tripped through Postgres. Nothing pushed to any registry (rule 18).

## CI-fix — the web flake — notes

Prompt: [`14-ci-web-flake.md`](14-ci-web-flake.md). Diagnosed by Cowork on
2026-10-01; the cause in that prompt was correct and is confirmed here by a
reproduction.

**The bug was the test's readiness gate, not the component.**
`logo-never-clears.test.tsx` gated on `findByText('Workspace name')` — an `<h2>`
that paints on the first render — then reached synchronously, with `getByRole`
and no retry, for a button that only exists once `/site-settings` has resolved
(`hasResettableBranding` is derived from the fetched org name, which is
`'FilmBill'` until then). The gate waited for something that does not depend on
the data; the assertion needed something that does. macOS won that race every
time; Linux and GitHub's runners did not. Growing the suite (338 → 361 → 391)
only moved the odds, which is why #16 looked like a fix.

**Reproduced before being touched**, with a 20ms delay on the `get` mock — which
also surfaced a **second** racy test in the same file that CI had never shown
(`darkSlotSrc()` read a slot with no `<img>` yet and threw).

The gate now has three clauses: the fetch has resolved at all; the committed
name is in its field; and the committed logo is in its slot. The first is the
one `branding-draft.test.tsx` was missing and is why it was added there too —
on a fixture whose values equal the pre-fetch defaults (a fresh install) the
other two are already true mid-flight, so every `queryBy…().toBeNull()` after
the gate passed **vacuously**, exactly as it would against a page that never
loaded.

**Sweep:** of 43 web test files, only three exercise a real SWR hook —
this one, `branding-draft` (correct gate since P0a-fix, now hardened), and
`sidebar-logo-ssr`, which deliberately leaves the fetch pending forever and
asserts the first paint. Everything else mocks its data hook with a synchronous
object or already awaits, so its `getBy` calls are correct and were left alone.
Two queries in the fixed file became `findBy` as defence in depth; the suite was
**not** converted wholesale, because `findBy` on something that should already
be present hides a slow regression. No sleep, no timeout, no `retry:`.

**`pnpm ci:web`** (`scripts/ci-web-local.sh`) runs lint, typecheck, test and
build on Linux in `node:20` with pnpm 9.12.3, from a clean `git archive HEAD`
export. Run against the parent commit it reproduced CI's failure to the
character — `1 failed | 390 passed (391)`, same test, same line — and against
the fix it is green end to end. It warns when `apps/web` has uncommitted
changes, since it tests HEAD and so would CI. Its empty-array guard exists
because macOS ships bash 3.2, where `"${arr[@]}"` on an empty array is an error
under `set -u`; found by running the script on the Mac it is for.

CLAUDE.md gains **rule 17e** for the class and a Local development note.

**Counts.** Web 391 passed / 43 files, ten consecutive local runs all 391.
Green at injected fetch delays of 20, 50 and 120ms. Container pipeline green.

**Open: CI #22 was not observed** — nothing has been pushed from here. Run
`git push origin main` and check the four jobs.

---

## Open items, unrelated to the above

**Red dependabot PR runs: #2, #3, #4, #9, #11, #21.** `setup-python` 5→7,
`setup-buildx` 3→4, `lucide-react`, `pydantic`. On PR branches, some red since
2026-09-20, and **not** the flake fixed above — recorded here so a future
reader does not mistake one for the other. Nothing in this change touches them.

## dev-mail — notes

Prompt: [`16-dev-mail-smtp-none.md`](16-dev-mail-smtp-none.md).

**No dev mail had ever been delivered.** The dev compose set
`SMTP_USE_TLS: "false"` and nothing else, and `smtp_security_from` maps a false
boolean to **implicit_tls** — deliberately, because that is what the boolean has
always DONE and P0b-0 would not silently switch off anybody's production
encryption. So the client opened an SMTP_SSL socket to Mailpit's plaintext port
1025 and every send died with `[SSL: WRONG_VERSION_NUMBER] wrong version
number`, which reads like a certificate problem. Reproduced before the prompt
existed; the fix is `SMTP_SECURITY: "none"` in the `&api_env` block, which
`worker`, `email_worker` and `beat` inherit.

**P0b-0 fixed the mechanism and its acceptance never exercised the dev compose
against Mailpit.** That is the whole reason the gap survived it: the three-mode
setting, the transport selection and a 323-line test file all landed and were
correct, while the one file that decides what dev actually connects with was
never part of the check. The mapping itself is untouched here, and no
production default changed.

**It invalidated an acceptance step in three walkthroughs.** P0b-0, P0b-1 and
P0b-2 all say a code or an invite link "arrives in Mailpit". None would have.
Each outcome doc now carries a one-line note saying so.

**`SMTP_USE_TLS` stays in the block**, and the comment says why. It is still
read — `apps/api/config.py` declares it, `email_config.resolve_mail_config`
reads it into `MailConfig.smtp_use_tls` (both the row and no-row branches),
`routers/email_settings.py` reports it, and the admin screen writes it back in
sync with the mode. Dropping it from dev would make dev inherit the library
default `True` and *report* "TLS on" for a plaintext connection — the same class
of quiet mismatch as the bug.

**`.env.example` gained a documented, commented `SMTP_SECURITY`.** There was no
"set `SMTP_USE_TLS=false` for a plaintext relay" line to correct; the trap was
the **omission** — the boolean was the only encryption knob documented, so a
self-hoster with a plaintext relay reaches for `false` and hits the same wall.
`SMTP_USE_TLS=true` is unchanged. `docker-compose.prod.yml` carries no SMTP
settings at all (they come from the operator's `.env`), so nothing there was
touched; with no `SMTP_SECURITY` in `.env` production resolves to `starttls`,
which is right for Microsoft 365.

**Tests** — `apps/api/tests/test_dev_mail_config.py`, three layers:
1. the compose file is parsed as YAML (the parser resolves the anchor) and its
   `api` values pushed through the real `Settings` → `resolve_mail_config` path,
   asserting mode `none`, host `mailpit`, port 1025. Runs anywhere, needs no
   Docker. A control test asserts the same values resolve to `implicit_tls`
   without the explicit mode, so the line is shown to be load-bearing rather
   than merely present;
2. `worker`, `email_worker` and `beat` resolve to the same mail config as
   `api` — the anchor is a promise, and a service given its own
   `environment:` block silently stops inheriting;
3. a real delivery, both through `EmailService` and through the real
   `send_invite_email` Celery task, read back out of **Mailpit's own API** by
   recipient. **Skips loudly** with a named reason where Mailpit is
   unreachable, which is what CI will do.

Layer 3 configures its send **from the compose file**, not from the test
runner's environment. First written the other way it failed with "Name or
service not known" in a harness that set no `SMTP_HOST` — a failure that says
nothing about the bug.

**`PyYAML` is now declared** in `apps/api/requirements.txt`. The repo's own
`test_declared_dependencies.py` caught the new import as transitive-only, which
is exactly its job.

**Mutations, real FAIL line each:** removing `SMTP_SECURITY` from the block
(3 failures, naming the mode and the fix); giving `email_worker` its own env
block without it (1 failure, naming the service and both resolved values).
The new file also fails against the **parent commit's** compose file (rule 12),
checked by putting `git show HEAD:docker-compose.dev.yml` in place.

**Counts.** API **714 passed** (705 at P0b-2) with the dev stack reachable;
**10 passed / 2 skipped** for the two new files where Mailpit is absent, which
is the CI shape. Both probe messages were confirmed present in Mailpit by
querying its API directly.

**One more instance for the P0b-3 consolidation, not added to CLAUDE.md 17b as
instructed:** a component's *effective configuration* was never asserted
anywhere, so the one file that decides it drifted from the mechanism that reads
it. Same family as the ambient-`DATABASE_URL` finding — the thing that was true
in the tests was not the thing that was true in the stack.

### Report-only: `filmbill_web` shows (unhealthy) while serving fine

Diagnosed, **not fixed** (out of scope here).

The healthcheck is `wget -q --spider http://localhost:3000`. Inside the
container `/etc/hosts` maps `localhost` to both `127.0.0.1` **and** `::1`;
BusyBox wget tries the IPv6 address first, and Next's dev server binds
`0.0.0.0:3000` — IPv4 only (`netstat` confirms, and `http://[::1]:3000` is
refused). So the probe has never succeeded: `FailingStreak: 213`.

`http://127.0.0.1:3000` answers 307 → `/login` → 200, and BusyBox wget follows
the redirect, so the path is fine once the address is literal.

**Proposed fix:** change the probe to `http://127.0.0.1:3000`. Optionally probe
`/login`, which returns 200 directly and does not depend on redirect-following.
Nothing else in the stack `depends_on` web's health, so the only cost so far
has been a misleading `(unhealthy)` in `docker compose ps` — which is its own
problem, since it trains people to ignore the column.
