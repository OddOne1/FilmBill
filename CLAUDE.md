# CLAUDE.md — FilmBill

Self-hosted, open-source (AGPL-3.0) billing, accounting and production-resource tool for film, photo and media production companies. Built by YON Studio OG (Vienna).

**This file is the durable contract only.** Do not append task narratives here. Per-task work lives in `docs/prompts/Pn-*.md`; one line per task in `docs/prompts/INDEX.md` (number · title · prompt file · commit · status). Change this file only when a rule that applies to *every* task changes.

## Source of truth
- `docs/SCOPE.md` — product scope, data model, decisions log (§16). If a prompt contradicts SCOPE.md, stop and ask.
- `docs/prompts/INDEX.md` — what has been built, what is open.
- Scoping, acceptance checks and troubleshooting are done in a separate Cowork session; it cannot run code, push, or reach the LAN. You (Claude Code) implement, run, commit, push.

## Stack
FastAPI · SQLAlchemy 2 · Alembic · Celery + Redis · PostgreSQL · S3-compatible storage · Gotenberg (PDF) · Next.js · Tailwind · Radix UI · SWR · Zustand · Docker Compose.
Platform modules (auth incl. TOTP 2FA, users, notifications, email, settings shell, branding) are derived from FreeFrame (MIT) — see `NOTICE`.

## Hard rules — domain
1. **Money is `Decimal`**, never float. DB `NUMERIC(18,2)` amounts, `NUMERIC(18,4)` quantities/rates. API serialises money as strings. Rounding only via `apps/api/core/money.py`.
2. **One calculation engine.** All line/group/adjustment/tax/total math lives in the calc module. The web app never computes money; it requests previews from the API.
3. **Finalized documents are never overwritten.** Changes create revisions (r2, r3 …) or a Storno. Every state change writes an audit event.
4. **Snapshots on finalize** (recipient, own company data, item texts, tax codes, layout version).
5. **Gap-free numbering** assigned only inside the finalize transaction via the number-series service.
6. **Everything business-related is company-scoped** (`company_id` + scoped query helpers). Cross-company access must return 404, and every new router needs a test proving it.
7. **Rules are data.** No `if country == "AT"` in code; tax codes, rules, units, charts of accounts live in region packs.
8. **No code copied** from ERPNext, Odoo, Dolibarr (GPL), Akaunting (BSL), Invoice Ninja (Elastic) or proprietary products. Studying behaviour is fine.

## Hard rules — engineering (learned from FreeFrame incidents)
9. **Migrations only.** Every schema change is an Alembic migration. Never instruct a DB reset.
10. **API types are generated** from OpenAPI (`pnpm gen:api`). Never hand-edit generated types; CI fails on drift.
11. **Tests assert behaviour, not source text.** No grep/`includes()` over source files as a test — comments and docstrings have caused false passes four times.
12. **Mutation checks must show a real FAIL line.** A crash in test setup or a bare non-zero exit is not a caught mutation.
13. **Report verification in three tiers:** run & observed · statically checked only · not checked. Never present `py_compile`/`tsc` as "tested".
14. **Rebuild every buildable service together** (`api worker email_worker beat web`) — Compose tags one image per service even from the same Dockerfile; stale workers have broken email before. `build --no-cache` and `up -d` are always **two separate commands**, never `up --build --no-cache`.
15. **Runtime env vars for Next.js server code** must be declared in the Dockerfile *runner* stage or compose `environment:`, not only as build args.
16. Browser-facing presigned S3 URLs only via the presign client that uses `S3_PUBLIC_ENDPOINT` (Safari blocks mixed content).
17. `.gitignore` patterns for build dirs must be root-anchored (`/lib/`, not `lib/`).
17b. **A test cannot catch a bug in the thing it replaces with a double.** Whenever a test substitutes for the subject it is named after — a module resolver, a source file read with `inspect.getsource`, an in-memory stand-in for Redis or S3 — it proves the harness, not the system, and it passes for exactly as long as the harness is kinder than production. FreeFrame hit this three times in one day (2026-09-21): §202, where vitest/jsdom resolved a package's CJS build and synthesised `default = module.exports` so `common.default.dictionary` worked in tests and threw `TypeError` in the browser (`@zxcvbn-ts/language-common` has no default export in ESM); §203, where `assert 'purpose == "two_factor"' in src` proved a branch existed while that branch rendered the wrong template for months; §204, where a "the two code pools don't share an attempts bucket" test used in-memory doubles with their own counters, so the real key prefixes never came into play. **The rule:** name the thing the test is really about, and make that thing real in at least one test — the package's ESM entry or built chunk, the rendered output, the actual key names against a fake that keys on them. A double is fine everywhere else — and when one is unavoidable, patch the **thing**, not a name that aliases it. P0b-1 (2026-09-30) moved the 2FA policy call and six tests that had patched `routers.auth.require_2fa_enabled` failed **loudly**, which is the good outcome; had they patched a stand-in further out they would have kept passing while testing nothing. Also: SQLite returns what it was told for every filter and for none, so a cross-company isolation test on SQLite passes against a router with the scoping deleted — scoping tests run against real Postgres or they are decoration.
17c. **A disabled control must always say why.** No state where a button is dead and the screen is silent. A value that is merely unknown (a score still loading, a lookup that failed) never blocks submission — the server enforces the rule anyway. That one condition, copy-pasted into four forms, took out FreeFrame's invite acceptance, onboarding gate, password reset and profile password change at once.
17d. **A conflict-free merge is not a reviewed one.** Porting §199–§206 from FreeFrame (2026-09-26), several things arrived through *clean* merge regions with no conflict markers at all: `guest_users` (a share-link model), `@freeframe/design-tokens`, transcription/LUT types, and a `/share/xyz` case that contradicted FilmBill's own `middleware.test.ts`. Git was silent and wrong; `alembic check` caught the one that mattered. Two consequences: **read every hunk a merge accepted, not only the ones it refused**, and keep a check that compares the models to the migrations in CI, because a table with no migration is the cheapest possible signal that foreign concepts came across. Python adds its own version of this — a duplicate `def` after an upstream reorder loses the first one **silently**, which is how `decode_2fa_pending_token` disappeared; scan for duplicate definitions after any merge.
18. **Do not push images to any registry** until release gate R1. A running FilmBill v1 on the server is auto-updated by Watchtower from Docker Hub; an image with a v1 name would replace production.

## Working with Mathias
- He is technical but not a full-time developer: every instruction he has to run gives **where** (which app/terminal/server), the **full literal commands**, one per line, and **what to paste back**. Repeat commands in full each time, even if given earlier.
- Every status report: a 1–3 sentence plain-language summary first, technical detail after.
- Diagnoses without a reproduction are hypotheses — label them `[Guessing]`/`[Likely]`, ask for repro steps.
- Split work into prompts by file/function overlap; merge only when two changes touch the same function.

## Local development
```
docker compose -f docker-compose.dev.yml up --build
```
Web http://localhost:3100 · API http://localhost:8100/docs · Mailpit http://localhost:8125 · MinIO console http://localhost:9101 · Gotenberg internal only.
(Ports offset from FreeFrame's so both stacks can run side by side.)
