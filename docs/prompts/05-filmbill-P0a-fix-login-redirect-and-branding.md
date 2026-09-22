# Claude Code prompt — FilmBill P0a-fix: post-login 404 and leftover FreeFrame branding

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**
> Save as `docs/prompts/P0a-fix-login-redirect-and-branding.md`, add an INDEX line.
> Found by Mathias' browser click-through on 2026-09-20 — the half of acceptance E that could not be run headlessly. Causes verified by Cowork in the working copy; fix them, don't re-diagnose from scratch.

---

## 1. Post-login 404 (blocker)
After creating the admin account and logging in, the app lands on a 404.

**Cause** [Certain]: the dashboard home is `app/(dashboard)/page.tsx`, i.e. the route `/`, but the login flow still sends users to FreeFrame's `/projects`, which no longer exists:
- `components/auth/login-form.tsx` lines ~126, ~224, ~413 (`router.replace('/projects')`)
- `app/(auth)/login/page.tsx` line ~33 (`router.replace(from || '/projects')`)

**Fix:** send them to `/`. Keep the `from` parameter behaviour (`from || '/'`). Check every other post-auth redirect too — setup completion, invite acceptance, the 2FA challenge screen, `middleware.ts`'s login redirect target — and make sure each one points at a route that exists.

**Test (behavioural, not a string check):** for each of those flows, assert the redirect target resolves to a route the app actually has. Build the allowed set from the filesystem route list (the same way the route-allowlist test builds its set from OpenAPI) so a future deleted page fails the test instead of the user. Mutation: point one redirect at `/projects` again → must FAIL.
Also update `app/(dashboard)/__tests__/sidebar-logo-ssr.test.tsx`, which mocks `usePathname` as `/projects`.

## 2. Leftover FreeFrame branding on the auth screen
The login and setup screens still show FreeFrame's logo and the line "Collaborative media review & approval".

- `app/(auth)/layout.tsx` ~L65: replace the tagline with **"Invoicing and production resources"**.
- `app/layout.tsx` ~L21: `description` is still "Collaborative media review and approval platform" → **"Self-hosted invoicing, accounting and production resources"**.
- `apps/web/public/` still holds FreeFrame's `logo.png`, `logo.svg`, `logo-full.png`, `logo-icon.png`, `logo-icon-dark.png`. Replace them with a plain FilmBill wordmark; the real logo comes later from Branding settings. Keep the file names so nothing else needs touching, and keep whatever light/dark switching the components already do.

Wordmark to ship as `logo.svg` (and render the PNGs from it at the sizes the old files had — same pixel dimensions, transparent background):

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 56" role="img" aria-label="FilmBill">
  <text x="0" y="39" font-family="DM Sans, system-ui, -apple-system, sans-serif"
        font-size="34" font-weight="600" letter-spacing="-0.5" fill="currentColor">FilmBill</text>
</svg>
```
Where a component cannot use `currentColor` (a raster `<img>`), ship the dark-on-light and light-on-dark PNG pair the existing names imply.

**Check:** grep the repo for `FreeFrame` / `freeframe` **outside** code comments and test docstrings — those may keep referring to where the code came from, that is deliberate — and fix any remaining user-visible string, alt text, page title, email template or manifest entry.

## 3. Acceptance (you run it, then report)
- `pnpm build`, `pnpm lint`, `tsc --noEmit`, web tests, API tests — all green.
- On the dev stack with an **empty** database (`down -v`, then `up -d`): setup → create admin → **land on the dashboard, not a 404** → log out → log in again → same. Screenshot-free is fine; state what you observed over HTTP.
- Auth screen shows the FilmBill wordmark and the new tagline.

Report in three tiers as usual, then push (this repo's `main` is already on GitHub).
