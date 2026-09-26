# Build log

One line per Claude Code prompt: number · title · prompt file · commit · status.
The prompt files are the specification; this is the record of what was built
from them. `docs/SCOPE.md` is where the product is decided.

| # | Title | Prompt | Commit | Status |
|---|---|---|---|---|
| — | Scope, durable contract and phase prompts | — | `df4b5f6` | done |
| P0a | New repo from the FreeFrame platform core | [`02-filmbill-P0a-repo-from-freeframe.md`](02-filmbill-P0a-repo-from-freeframe.md) | `3349695` | done — see notes |
| P0a-fix | Post-login 404 and leftover FreeFrame branding | [`05-filmbill-P0a-fix-login-redirect-and-branding.md`](05-filmbill-P0a-fix-login-redirect-and-branding.md) | `90173fe` | done — see notes |
| P0b | Foundations: companies, roles, number series, audit, money, codegen | [`03-filmbill-P0b-foundations.md`](03-filmbill-P0b-foundations.md) | — | open |

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

**Found while running acceptance, not fixed (out of scope):** the API suite
passes only because the rate limiter fails open when Redis is unreachable.
Run inside `filmbill_api`, where `redis:6379` *is* reachable, 36 tests 429.
`test_setup_superadmin.py` alone makes 7 POSTs to `/setup/create-superadmin`,
which is limited to 3 per 600s, and no fixture clears `rl:*` between tests.
Nothing here caused it — this change touches `apps/web` only — but the suite
is not asserting what it looks like it asserts on those 36.
