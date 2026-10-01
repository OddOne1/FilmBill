# Claude Code prompt — FilmBill: the CI web flake, found and fixed properly

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**. Save as `docs/prompts/14-ci-web-flake.md`, add an INDEX line.
> Diagnosed by Cowork on 2026-10-01 by reproducing CI's environment in Docker (`linux/amd64`, `node:20`, pnpm 9.12.3, cold install from `git archive HEAD`). **Do not re-diagnose from scratch** — the cause is below and is confirmed by a reproduction.

---

## The failure

```
FAIL app/(dashboard)/settings/branding/__tests__/logo-never-clears.test.tsx
  > "reset" no longer reaches the logos > resets the name and colours and leaves the logo alone
TestingLibraryElementError: Unable to find an accessible element with the role "button"
and name `/reset name and colors/i`
```

1 failed / 390 passed. Green on macOS every time; red in a Linux container; red on GitHub's runners since at least CI #13.

## The cause [Certain — read from the source and reproduced]

`app/(dashboard)/settings/branding/page.tsx:664` renders the button conditionally:

```tsx
{hasResettableBranding && (
  <section …><Button onClick={stageResetAll}>Reset name and colors</Button>…</section>
)}
```

`hasResettableBranding` is derived from the **fetched** settings. But the test's readiness gate is:

```ts
async function renderPage() {
  const r = render(<SWRConfig …><BrandingPage /></SWRConfig>)
  await screen.findByText('Workspace name')   // a STATIC heading
  return r
}
```

`"Workspace name"` is an `<h2>` that paints on first render, before any fetch resolves. The test then does `screen.getByRole('button', …)` — **synchronous, no retry**. So the gate waits for something that does not depend on the data, and the assertion needs something that does.

On a fast machine the SWR fetch settles within the same flush and the button is there. Under emulation, or on a loaded CI runner, it is not. Nothing is wrong with the component: a conditional that flips when data arrives is ordinary SWR behaviour.

This is why CI has been intermittently red since #13 and why #16 ("…and fix CI") appeared to fix it — the probability shifts as the suite grows (338 → 361 → 391 tests), it was never fixed.

## Fix

1. **`renderPage()` must wait for the fetched data, not for static chrome.** Gate on something that only exists once settings have arrived — the org name in its input, or the Reset button itself. State in a comment why, so the next person does not "simplify" it back to a heading.
2. **In that file, every query for data-dependent UI becomes `findBy*`/`waitFor`**, not `getBy*`. `getBy*` stays only for things that render unconditionally.
3. **Sweep the rest of `apps/web` for the same shape**: a `render` + a gate on static text, followed by `getByRole`/`getByText` for an element behind a data-dependent condition. Report what you find; fix the ones that are genuinely racy, and leave the ones where the query target renders unconditionally. Do not convert everything to `findBy` blindly — a `getBy` on unconditional UI is correct and catches real regressions.

Do **not** fix this by adding a timeout, a sleep, or `retry` in the vitest config. That hides the class of bug rather than removing it.

## Guard against the recurrence
Add a CI-shaped local check so this is catchable without GitHub. A script (`scripts/ci-web-local.sh` or a `package.json` entry) that runs the web pipeline in `node:20` on Linux from a clean `git archive` export, exactly as Cowork did:

```
rm -rf /tmp/fb-ci && mkdir -p /tmp/fb-ci && git archive HEAD | tar -x -C /tmp/fb-ci
docker run --rm -t -v /tmp/fb-ci:/w -w /w node:20 bash -c \
  "corepack enable && corepack prepare pnpm@9.12.3 --activate && pnpm install --frozen-lockfile \
   && pnpm --filter web lint && pnpm --filter web typecheck && pnpm --filter web test && pnpm --filter web build"
```

Document it in `CLAUDE.md` under local development as the thing to run before pushing when CI disagrees with the laptop. (`--platform linux/amd64` is optional; it is slower under emulation and only matters for genuinely architecture-specific bugs.)

## Acceptance
- The container run above goes green, end to end, from a clean export.
- Run the web suite **ten times in a row** locally (`pnpm --filter web test` in a loop) and report the count — if anything else is order- or timing-dependent, that surfaces it.
- Push, and report whether CI #22 is green on all four jobs.

## Also worth noting, not in scope
The dependabot PR runs that are red (#2, #3, #4, #9, #11, #21 — `setup-python` 5→7, `setup-buildx` 3→4, `lucide-react`, `pydantic`) are a separate matter on PR branches and some have been red since Sep 20. Do not touch them here; list them in INDEX as an open item so they are not mistaken for this bug later.
