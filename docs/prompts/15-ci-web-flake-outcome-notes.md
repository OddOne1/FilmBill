# FilmBill — the CI web flake, fixed

> Recorded 2026-10-01 from Claude Code's report. Two commits (`e68d879` fix, `36552ac` index), clean tree, **not pushed** at the time of writing. Nothing under `apps/api` touched. Nothing pushed to any registry (rule 18).
> Part of the source report arrived truncated; cut-off passages are marked **[paste truncated]**.

---

## The fix

The readiness gate in `logo-never-clears.test.tsx` now waits for three things instead of a static heading:

1. the fetch has resolved at all;
2. the committed org name is in its own field;
3. the committed logo is in its slot, for fixtures that have one.

Clauses 2–3 already existed in `branding-draft.test.tsx` — P0a-fix got them right for this same reason, and this file never picked them up. **Clause 1 is new to both, and it is the important one.**

### The finding worth keeping: assertions that passed vacuously
On a fixture whose fetched values equal the pre-fetch defaults — a fresh install, `org_name: 'FilmBill'`, no logo — clauses 2 and 3 are *already true mid-flight*. So every `queryBy….toBeNull()` after the gate was passing **vacuously**: it would have held just as well against a page that never loaded at all. Clause 1 was added to `branding-draft.test.tsx` for that reason, not only to the file that was red.

This is a different shape from the six instances already in `CLAUDE.md` 17b. Those were *the harness is kinder than production*. This one is *the assertion is satisfied by the absence of the thing under test* — a negative check that cannot distinguish "correctly absent" from "never arrived". Both end in a confident green that means nothing.

`darkSlotSrc()` now uses `?.` rather than `!`: it is called from inside the gate's `waitFor`, where the slot legitimately has no `<img>` yet, and a `TypeError` there is the gate crashing rather than a retryable assertion.

Two queries became `findBy` as a second lock, and no sleep, timeout or vitest `retry` was used — as the prompt required.

## Method: reproduced before anything was touched
A **20 ms delay injected on the `get` mock** turned the intermittent failure into a certain one. That also surfaced a **second racy test in the same file** that CI had never shown — `darkSlotSrc()` reading a slot with no `<img>` and throwing on `.getAttribute` of null. Green afterwards at 20 ms, 50 ms and 120 ms.

## The sweep
Of 43 web test files, only **three** exercise a real SWR hook:

| File | Verdict |
|---|---|
| `logo-never-clears.test.tsx` | racy — fixed |
| `branding-draft.test.tsx` | correct gate since P0a-fix; hardened with clause 1 |
| `sidebar-logo-ssr.test.tsx` | deliberately leaves the fetch pending forever and asserts the first paint — the inverse case, correct as written |

Everything else either mocks its data hook with a synchronous object (`require-2fa-toggle`, `login-form-2fa`, `backup-code-entry`, `two-factor-settings`), seeds a store via `setState` (`company-switcher`, `permission-gated-nav`), or already awaits. Their `getBy` calls are correct and were left alone — which is right: converting every `getBy` to `findBy` would hide slow regressions behind a timeout.

## Verification

**Run & observed**

| | |
|---|---|
| `pnpm ci:web` on the **parent** commit | 1 failed / 390 passed — same test, same line, same error as CI |
| `pnpm ci:web` on the fix | green end to end: lint, typecheck, 43 files / 391 tests, build |
| Web suite, ten consecutive runs | 391 passed every time |
| Injected fetch delay 20 / 50 / 120 ms | green **[paste truncated]** |
| Scope | nothing under `apps/api` touched |

**Statically checked only** — the sweep's verdict on the ~40 non-racy files is a reasoned read of how each mocks its data source, not an injected delay per file. The property checked was the one that matters: real SWR hook vs. synchronous mock.

**Not checked** — CI #22. Nothing pushed from the build session.

## The guard
`scripts/ci-web-local.sh` / `pnpm ci:web` reproduces CI's shape locally: clean `git archive` export, `node:20` on Linux, pnpm 9.12.3, cold install, then lint → typecheck → test → build. It reproduced CI's failure character-for-character against the parent commit, which is the only acceptable proof that a CI-reproduction script works.

The script found a bug in itself on first run: macOS ships bash 3.2, where `"${arr[@]}"` on an empty array is an error under `set -u`. Guarded with the `+` expansion form, reason in a comment.

## Decisions
- **`CLAUDE.md` rule 17e stays.** It was added without being asked for; the prompt only requested a Local-development note. Keeping it: the flake class — gate on static chrome, assert on data — applies to every web test anyone writes next, which is exactly the bar that file sets ("change this file only when a rule that applies to *every* task changes").
- **Open item recorded in INDEX, untouched:** the red dependabot PR runs (#2, #3, #4, #9, #11, #21 — `setup-python` 5→7, `setup-buildx` 3→4, `lucide-react`, `pydantic`). Separate branches, separate cause, some red since Sep 20.

## Follow-up for the docs, not the code
`CLAUDE.md` 17b has now been extended three times and carries six worked examples in one paragraph, with 17d and 17e beside it. A rule nobody finishes reading is not a rule. These should be consolidated into one short numbered rule with the examples moved to a referenced file before the next phase.
