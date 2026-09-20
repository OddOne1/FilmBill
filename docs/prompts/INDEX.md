# Build log

One line per Claude Code prompt: number · title · prompt file · commit · status.
The prompt files are the specification; this is the record of what was built
from them. `docs/SCOPE.md` is where the product is decided.

| # | Title | Prompt | Commit | Status |
|---|---|---|---|---|
| — | Scope, durable contract and phase prompts | — | `df4b5f6` | done |
| P0a | New repo from the FreeFrame platform core | [`02-filmbill-P0a-repo-from-freeframe.md`](02-filmbill-P0a-repo-from-freeframe.md) | `PENDING` | done — see notes |
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

**Acceptance.** A–D ran green. E ran green against the live stack over HTTP;
the two browser-rendered halves of E were not observed in a browser, because
the Chrome extension was not connected — see the P0a report.
