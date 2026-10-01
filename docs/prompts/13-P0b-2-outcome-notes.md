# FilmBill P0b-2 — money, generated types, company screens

> Recorded 2026-10-01 from Claude Code's report. Clean tree, two commits on `main`, **not pushed** at time of writing. Nothing pushed to any registry (rule 18).
> Part of the source report arrived truncated; cut-off values are marked **[paste truncated]**. The full tables live in the repo's own outcome doc.

---

## What landed

Money is a `Decimal` that travels as a string — a JSON number like `12.3` is refused rather than silently rounded — and all rounding happens in one function nothing outside it can reach. The web app's API types are generated from the API's own schema with a CI drift guard. A company now has screens: create, General, bank accounts with a real IBAN check, the accounting selectors, and Security.

### §1 Money
A JSON number is a 422; an integer is accepted (it survives JSON intact). `"12.30"` stays `"12.30"`. **NaN and Infinity are refused** — all three are valid `Decimal()` input and none is an amount, which is a catch the prompt did not ask for. `quantize_amount` / `quantize_qty` pass an explicit local `Context` to `quantize`, never `getcontext()`; half-UP, not half-EVEN.

**The ESLint rule, and a deliberate departure.** The prompt asked for arithmetic *on non-string operands*. ESLint selectors carry no type information, so "is this `+` a concatenation?" cannot be decided without guessing — wrong in both directions. So **every** operator is banned in `lib/money.ts` and the file uses template literals: eight selectors covering `+ - * / % **`, compound assignment, `++`/`--`, unary `+`/`-`, `Number`/`parseFloat`/`parseInt`/`BigInt`, any `Math.*`, and `.toFixed()`. Strictly stronger than what was asked, and free, because formatting needs no arithmetic. `.eslintrc.json` became `.eslintrc.js` so the reasoning sits beside the rule. **Endorsed.**

### §2 Codegen — it found four real schema bugs on day one
`pnpm gen:api` imports the app to dump the schema (verified with `--network none` and no env vars). New CI job `api-types-are-generated`.

| Found | Was |
|---|---|
| `User.deleted_at` | claimed by the hand-written type; `/auth/me` has never sent it |
| `preferences`, `theme_colors`, `payload` | `Record<string, never>` — a map that can hold nothing. `dict[str, Any]` was not enough; needed explicit `additionalProperties` |
| `EmailSettingsUpdate` `*_clear` flags | required; the admin page now sends them as `false` |

Kept hand-written, with reasons: `LoginResponse` (a union FastAPI cannot declare — both arms are generated), `SmtpSecurity` (deliberately narrower than the API's own type so the client gives a readable 400 instead of pydantic's 422), `PasswordStrength` (browser-computed), **[paste truncated]**.

### §3 Screens
Five under Settings → Company, plus `/settings/company/new` reached from "New company…" in the switcher — which now appears for a superadmin with one company. IBAN validation is **server-side only**; a second mod-97 in TypeScript would be a second thing to keep in step.

**Bank-account reads are gated on `company.settings.edit`, not `company.view`** — the builder's reasoning: an IBAN is what a convincing invoice fraud needs. Endorsed for now. **Revisit in P5/P6**: when the tax-advisor archive and any reconciliation feature land, an advisor may legitimately need to see bank details, and that is the moment to decide deliberately rather than inherit this.

Accounting carries a banner saying it changes nothing yet.

## Verification

**Run & observed.** API **705 passed** (615 before — see the finding below for why that number was not what it looked like). Web **[paste truncated]** / 41 files. `tsc`, lint, build clean with all five routes. `alembic upgrade` → `check` → `downgrade` clean on a scratch database. All four required mutations gave real FAIL lines. The ESLint rule and the codegen drift guard were each demonstrated failing, then reverted. Live on dev: migration applied, a valid IBAN stored, a wrong check digit refused with its sentence, an invalid selector 422'd.

**§5 confirmed, and it mattered.** The bank-account endpoints joined P0b-1's cross-company isolation test automatically — all four were caught as "no isolation case" on the first run, exactly as that test was built to do. The OpenAPI-derived case list needed no change; the **row substitution** did (`{membership_id}` was hard-coded), which was the one silent failure mode left in it.

**Statically checked only.** None of the five screens was opened in a browser. `pnpm gen:api` in its Python-direct form was not run on the host (no API Python environment there); `gen:api:docker` was used throughout.

**Not checked.** The browser walkthrough (needs `down -v`). Money against a real `NUMERIC` column — nothing stores an amount until P1.

## The finding: the suite was reading its answer off its surroundings — the fourth time

`SetupGuardMiddleware` opens its **own** `SessionLocal`, so the ambient `DATABASE_URL` decided the outcome:

| Ambient DB | Result |
|---|---|
| No tables (CI, no dev stack) | guard fails open, **all pass** |
| Tables, no superadmin | 503 on **[paste truncated]** |
| One superadmin row | all pass |

All three from the same commit inside an hour, confirmed by inserting and deleting a single row between otherwise identical runs.

**The green configuration is the one where the middleware never runs — and that is the configuration P0b-1's reported 615 came from.** That number was therefore partly fiction, and it was recorded in `16-filmbill-P0b-1-outcome.md` as fact. 705 is the honest figure, with and without a superadmin present.

Fixed in `conftest.client`; `SessionLocal` deliberately left alone rather than patched. A new `test_setup_guard.py` drives the real middleware — **nothing tested it before**.

### Why this keeps happening
Six instances now, one family: §202 (jsdom synthesised a default export webpack does not), §203 (a source-text assertion proved a branch existed, never what it rendered), §204 (in-memory doubles meant real key prefixes never came into play), P0b-0 §0 (the rate limiter failed open where Redis was unreachable — 30 tests were 429s), P0b-1 (a patched *alias* of a policy that had moved), and now P0b-2 (a middleware with its own session reading the ambient database).

The shared mechanism: **a component that opens its own connection, session or resolver is invisible to the harness's configuration, so the harness silently selects the configuration in which that component does not run.** The consequence is not a flaky test — it is a confident green number that nobody can act on.

The rule that follows is in `CLAUDE.md` 17b: a test that does not declare its environment inherits one, and every component that opens its own session or connection needs at least one test asserting it actually ran.

## Next
`git push origin main`, then the browser walkthrough (P0b-1's four steps plus P0b-2's five — and step 1 no longer touches Swagger), then **P0b-3**: number series and audit events.
