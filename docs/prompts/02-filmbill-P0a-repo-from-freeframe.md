# Claude Code prompt — FilmBill P0a: new repo from FreeFrame platform core

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**
> Prerequisites (see `00-BEFORE-YOU-START.md`): FreeFrame 2FA complete and verified (§191–§197; §198 desktop is out of scope) · old FilmBill repo renamed to `FilmBill_OLD` · empty GitHub repo `OddOne1/filmbill` exists · Docker Desktop running.
> Inputs to place in the new repo before starting: `docs/SCOPE.md`, `CLAUDE.md`, `docs/prompts/` — already placed by Cowork.
> Scope reference: SCOPE.md §0, §1, §13 (P0). This prompt covers **P0a only** — P0b (companies, roles, number series, audit, money, codegen) is a separate prompt.

---

## Goal
A clean FilmBill repository that boots with FreeFrame's platform features (login incl. 2FA, users, invites, notifications, email, settings shell, branding) and **nothing media-related**, plus Gotenberg and Mailpit in the stack. No business features yet.

## 1. Create the repo
- `git init` in `~/Claude/Projects/FilmBill/repo` (if not already done), default branch `main`, first commit = only `CLAUDE.md` + `docs/`, remote `origin` = `git@github.com:OddOne1/filmbill.git`.
- Source: the local FreeFrame clone at `~/Claude/Projects/FreeFrame/Freeframe/repo`, at the commit that contains the finished 2FA work (backend + web). Record that commit hash in `NOTICE` and in the P0a INDEX line.
- **Copy files, not git history, and only tracked files:** export with `git -C ~/Claude/Projects/FreeFrame/Freeframe/repo archive <hash> | tar -x -C /tmp/freeframe-export` and copy from there. The working folder contains `node_modules`, `.pytest_cache`, `.claude/`, a 94 kB `CLAUDE.md`, an 800 kB `CLAUDE-ARCHIVE.md`, `claude-md/` and `INDEX.md` — none of these may end up in FilmBill.
- Do not add FreeFrame as a remote. **Never write to the FreeFrame folder.**

## 2. Licence & attribution
- `LICENSE` = full AGPL-3.0 text.
- `LICENSES/MIT-FreeFrame.txt` = FreeFrame's MIT licence text including all copyright lines found in FreeFrame's `LICENSE` (upstream Techiebutler + any added).
- `NOTICE`: "Portions of this software are derived from FreeFrame (MIT), commit `<hash>`" + list of top-level directories/modules that were copied.

## 3. Keep (copy, then rename)
API (`apps/api`):
- `config.py`, `database.py`, `main.py` (trimmed), `middleware/*` (auth, rate limits)
- models: `user` (incl. 2FA fields), `activity`, `branding`, `email_settings`, `site_settings` (incl. `require_2fa`), + whatever notification models the kept code depends on
- routers: `auth` (incl. all `/auth/2fa/*` endpoints), `admin` (user management + 2FA admin disable), `me`, `setup`, `users` (invites), `notifications`, `events` (SSE), `branding`, `email_settings`, `site_settings`
- 2FA: everything touched by FreeFrame **§191 → §197** (`7ff47a6`, `4b0c48b`, `e15ac90`, `db66e45`, `c9f7fd4`, `a1f78c6`, `929f5f5` — get the file list with `git show --stat`). **§198 is the desktop app → skip.** Includes: TOTP + backup codes, email as second factor, the §193 magic-code gate, §195 (magic-code sign-in and self-registration close when 2FA is required), the web login challenge and the settings UI.
- services: `auth_service`, `totp_service`, `site_settings_service`, `activity_service`, `email_service`, `email_config`, `event_service`, `notification_prefs`, `redis_service`, `s3_service`, `secrets_service`, `crypto_service` (only if still referenced), `permissions` (keep file, it will be rewritten in P0b)
- `tasks/` email tasks only · `templates/` email templates only · tests for kept modules only
Web (`apps/web`): `(auth)` routes, dashboard layout shell, notifications page, settings: `admin`, `appearance`, `branding`, `notifications`, `profile`; shared UI components, `lib/`, `stores/`, hooks used by kept pages.

## 4. Drop entirely
assets, uploads, HLS proxy, transcription + `transcribe_worker` + whisper cache, LUTs, sidecars, metadata, approvals, votes, comments, folders, FreeFrame projects, share links, zip export, purge service, storage prefix, contact form, poster/thumbnail code, `apps/desktop`, `faster-whisper`/`huggingface-hub`/`requests`/Pillow AVIF pins (unless something kept still imports them — then say which), media-specific scheduled tasks.
If a kept module imports a dropped one, **remove the dependency in the kept module** and list every such edit in the report. Do not keep dropped modules "just to make imports work".

## 5. Rename
- Product name FreeFrame → FilmBill in UI strings, email templates, page titles, OpenAPI title, README.
- Cookie `ff_access_token` → `fb_access_token`; localStorage keys `ff_*` → `fb_*`; Compose project name `filmbill`; container names `filmbill_*`.
- Env var names unchanged unless they contain `FREEFRAME`/`FF_` → `FILMBILL_`/`FB_`.

## 6. Settings navigation
Order: Profile · Appearance · Notifications · **Branding** · **Design** · Admin. "Design" = placeholder page ("Document layout designer — coming in P4") so the navigation is final from day one (SCOPE §7).

## 7. Database
- **Fresh Alembic history**: delete FreeFrame's migrations; generate one baseline migration `0001_platform_baseline` from the kept models. Review it by hand for leftovers of dropped tables and media columns on kept models (e.g. `User.storage_limit_bytes` at `3bdcca5` — remove it and its code paths).
- First-run setup flow creates the first superadmin as before.

## 8. Docker
`docker-compose.dev.yml` and `docker-compose.prod.yml` services: `postgres`, `redis`, `minio` (dev) / external S3 (prod, as FreeFrame), `api`, `worker`, `email_worker`, `beat`, `web`, **`gotenberg`** (pin an exact 8.x tag, internal network only, no published port), **`mailpit`** (dev only, catches all outgoing mail).
Ports (dev) offset so FreeFrame can run in parallel: web 3100, api 8100, mailpit UI 8125, minio console 9101. Update `.env.example` accordingly; dev defaults send mail to mailpit.
Traefik config from FreeFrame prod compose may stay, renamed.
FreeFrame's `.dockerignore` and `.gitignore` are copied, then checked: patterns for build dirs root-anchored (CLAUDE.md rule 17).

## 9. PDF smoke endpoint
`GET /health/pdf` (superadmin only): renders a hard-coded HTML page ("FilmBill PDF OK" + current timestamp, A4) via Gotenberg's Chromium HTML route and returns the PDF. Service wrapper in `services/pdf_service.py` with a configurable Gotenberg URL and timeout. This is the only PDF code in P0a.

## 10. CI (`.github/workflows/ci.yml`)
Jobs: API tests (Postgres + Redis service containers), web lint + typecheck + build, Docker build of all images **without push**. Add a comment at the top: pushing images is forbidden until release gate R1 (CLAUDE.md rule 18).

## 11. Docs
`README.md` (what FilmBill is, quick start, licence), `docs/SCOPE.md` (as provided), `CLAUDE.md` (as provided — don't rewrite), `docs/prompts/INDEX.md` with the P0a line, this prompt saved as `docs/prompts/P0a-repo-from-freeframe.md`.

## Acceptance — you run these, report results
**A. Route allowlist test (behavioural, replaces any "grep for media words" idea):** pytest that loads `app.openapi()` and asserts (1) the set of operation **tags** is exactly the kept set — at FreeFrame `3bdcca5` these are `auth`, `admin`, `me`, `setup`, `users`, `notifications`, `events`, `branding`, `email-settings`, `site-settings`, plus `health` (2FA endpoints live under the `auth` tag); and (2) a snapshot of the full sorted path list, committed as `tests/snapshots/openapi_paths.txt`, matches. Note: at `3bdcca5` `notifications`, `branding`, `email_settings` and `site_settings` routers have **no prefix** (paths declared per route), so a prefix check alone would miss things — that's why tags + snapshot. Also trim `admin.py` to user/site management: storage-limit, purge and other media admin endpoints go. Mutation: re-register one dropped router → show the FAIL line.
**B. Import check:** inside the built api image, `python -c "import apps.api.main"` (or the repo's actual module path) succeeds; `pip check` clean.
**C. Kept test suites green** (auth, two_factor, two_factor_disable, rate limit, email case-insensitivity, notification prefs, setup superadmin, …).
**D. Web:** `pnpm lint`, `pnpm tsc --noEmit` (or equivalent), `pnpm build` green.
**E. End-to-end on `docker compose -f docker-compose.dev.yml up --build`, observed in a browser:**
  1. Fresh DB → `/setup` → create superadmin
  2. Log out → log in with password → log in with magic code (code arrives in Mailpit)
  3. Enable 2FA → log out → password login requires code → backup code works once → magic-code login also requires the authenticator code (emailed fallback refused on that path)
  4. Invite a second user → invite email in Mailpit → accept → login
  5. Branding: upload logo + change colour → visible after reload
  6. `/health/pdf` returns a valid PDF (open it)
  7. Settings nav shows Design placeholder under Branding
If Docker Desktop is not running or a step cannot be executed, stop and say so — do not replace E with static checks.

**Report** in three tiers (run & observed / statically checked / not checked), list all edits made to kept modules because of removed dependencies, the FreeFrame commit hash used, and the final `git log --oneline | head`. Push `main` to `origin` when A–E pass.
