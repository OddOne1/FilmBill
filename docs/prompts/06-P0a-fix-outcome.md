# FilmBill P0a-fix — outcome, and two defects it uncovered

> Recorded 2026-09-26 from Claude Code's report on prompt 05. Commits `90173fe` (fix), `0c3783c` (index), `8b4dc7f` (favicon ink), `03773d5` (test timing), all on `main`.
> Parts of the source report arrived truncated; everything below is what was legible and unambiguous. Where a number or a command was cut off mid-line it is marked **[paste truncated]** rather than reconstructed.

---

## Done

**The post-login 404 is fixed.** Five call sites, not the four the prompt listed — it missed `hooks/use-auth.ts:71`.

| File | Was | Now |
|---|---|---|
| `components/auth/login-form.tsx:126, 224, 413` | `router.replace('/projects')` | `router.replace('/')` |
| `app/(auth)/login/page.tsx:33` | `from \|\| '/projects'` | `from \|\| '/'` |
| `hooks/use-auth.ts:71` | `router.push('/projects')` | `router.push('/')` |

Every other post-auth redirect already pointed at a live route (setup → `/login`, invite accept → `/`, `middleware.ts` → `/login` and `/setup`, `/settings` → `/settings/profile`).

The guard is `apps/web/__tests__/post-auth-redirects.test.tsx` (14 tests): it drives each flow through the UI and asserts on what reached `router.replace` / `router.push` / `NextResponse.redirect`, with the allowed set read off the App Router tree at run time (route groups and `[token]` handled). Three mutations each produced a real FAIL with `redirect target … is not a route this app has`.

**Branding.** Tagline and root `description` replaced; the five `public/logo*` files re-rendered from DM Sans SemiBold at the original pixel dimensions, transparent. A `FreeFrame`/`freeframe` sweep outside comments and test docstrings found nothing user-visible; what remains is provenance (`NOTICE`, `LICENSES/`, `README`, `CLAUDE.md`, `docs/`).

Two documented departures from the prompt, both accepted:
- The **square** logo files carry an **FB monogram**, not the wordmark. At 1311×1311 displayed at 28px (sidebar) and 32px (favicon), a 4.3:1 wordmark centres to roughly 6px of letter height. `logo-full.png` and `logo.svg` keep the full wordmark as specified.
- `logo-full.png` and `logo-icon.png` are drawn in the accent `#5b8def`, because neither has a light/dark paired filename and both sit on backgrounds that flip between `#ffffff` and `#0d0d10`. This caught a self-inflicted defect: the first attempt rendered `logo-icon.png` in near-white for its dark sidebar role, forgetting `app/layout.tsx` also uses it as the **default favicon** — invisible on a light tab strip. Fixed in `8b4dc7f`.

Green: `pnpm build` (14 routes, matching the set the test derives independently), `pnpm lint`, `tsc --noEmit`, 233 web tests (5/5 consecutive clean runs), 327 API tests. Over HTTP against the dev stack: `/` → 200 dashboard, `/projects` → genuine 404, `/` unauthenticated → 307 to `/login?from=%2F`, `/login` serving "Invoicing and production resources", served `logo-full.png` bytes matching the repo file.

## Not done

**The empty-database walkthrough.** `docker compose down -v` was refused by the permission classifier as irreversible local destruction, and Docker Desktop has since stopped. This is the last acceptance step of prompt 05 and it needs a human at the Mac. Nothing in a real browser has been checked either — the Chrome tooling reported not connected, the same gap P0a had.

## Two defects to carry forward

### 1. The API suite passes only because the rate limiter fails open — **P0b work item**
Run where `redis:6379` is unreachable, the limiter fails open and 327 tests pass. Run **inside** `filmbill_api`, where Redis is reachable, **36 tests return 429**. `test_setup_superadmin.py` alone makes 7 POSTs to `/setup/create-superadmin` against a cap of 3 per 600s.

Nothing in P0a-fix caused this — that change touched `apps/web` only — but those 36 tests are not asserting what they appear to assert, and the suite's green is environmental. This is CLAUDE.md rule 17b in its purest form: the harness is kinder than production. **P0b must fix it before adding tests that depend on the suite's verdict**: give the limiter a per-test reset fixture (or a test-scoped key namespace), then re-run and report the real number.

### 2. CI's web Test step is intermittently red
Also failed on `cc6f2b7` and `1a333f3`, with every other job green in those runs; the log needs a token Claude Code does not have, and the suite is 5/5 locally. The suite's slowest tests are real wall-clock waits — `lib/__tests__/auth-refresh.test.ts` spends ~1.2s of real time in each of five tests waiting out the retry backoff. One wait that P0a-fix had added was moved to fake timers (2079ms → 128ms, `03773d5`); the pre-existing ones were left alone. A CI-runner timeout is the leading hypothesis, **not confirmed** — the CI log has never been read. **[paste truncated]** on the final CI result.

## What a human still has to run

Docker Desktop must be started from Applications first, then, in Terminal at `~/Claude/Projects/FilmBill/repo`:

```
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml build --no-cache api worker email_worker beat web
docker compose -f docker-compose.dev.yml up -d
```

Wait ~2 minutes for the web container to install and compile, then:

```
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://localhost:3100/
```

Expected: `307 http://localhost:3100/setup`. Then in a browser at `http://localhost:3100`: complete setup, create the admin account, and confirm it lands on the **dashboard, not a 404**; that the login screen shows the FilmBill wordmark and "Invoicing and production resources"; then log out and log in again and confirm the same.
