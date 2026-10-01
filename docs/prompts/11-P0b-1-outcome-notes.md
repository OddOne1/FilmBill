# FilmBill P0b-1 — companies, memberships, scoping, permissions

> Recorded 2026-09-30 from Claude Code's report. Two commits on `main`, **committed but not pushed** at time of writing; nothing pushed to any registry (rule 18).
> Part of the source report arrived truncated — the API suite's new pass count among them. Cut-off values are marked **[paste truncated]** rather than reconstructed. The full permission table and mutation table live in the repo at `docs/prompts/10-P0b-1-outcome.md`.

---

## What landed
Three tables (migration `0005`), six company roles, a twelve-key permission map, eight endpoints under a new `companies` tag, plus the company switcher, permission-gated navigation, the Archive placeholder and the Members screen.

In plain terms: every business request now says which company it is for, and a user who is not a member of that company gets **404** — including an installation superadmin, who is not automatically a member of anything. A tax advisor sees the Archive and nothing else, is forced into 2FA setup even when the instance does not require 2FA of everyone, and loses their open session the moment access is revoked.

## Decisions taken by the builder, both endorsed

**1. The permission table is the rewritten `services/permissions.py`**, not a new `core/permissions.py` — that file already existed and its docstring already said it would become this; two files named "permissions" would have been worse than either. The FastAPI dependencies (`current_membership`, `require`) sit in `middleware/company.py`, beside `middleware/auth.py`, which is this codebase's existing home for request-scoped dependencies.

**2. Path shape: `/companies` plural, `/company` singular.** "One mechanism, no second way" was read as: *a company id must never appear in both a path and a header, because the interesting bug is the request where the two disagree.* So `/companies` carries only the two operations that have no company context (list yours, create one) and everything else is `/company/…` resolved from `X-Company-Id`. This is a better reading of the rule than the prompt's own wording and should be kept.

**Permission keys** — twelve, tabulated in the repo doc. The one the prompt did not name is `company.view`, held by **every** role including `tax_advisor`, so an advisor can see whose archive they are in. `reports.view` / `exports.download` reach a tax advisor only through `TAX_ADVISOR_OPTIONAL`, when the company sets `tax_advisor_reports`.

## The two 2FA lines, as shipped

```diff
 # services/site_settings_service.py
-    return require_2fa_enabled(db)
+    if require_2fa_enabled(db):
+        return True
+    return two_factor_required_by_membership(db, user)

 # routers/auth.py:713
-    if require_2fa_enabled(db):
+    if two_factor_required_for(db, user):
```

`routers/auth.py:231` — `send_magic_code`'s instance-wide gate — untouched, as instructed.

## Verification

**Run & observed.** API suite green against real Postgres with Redis reachable (**[paste truncated]** — 537 was the P0b-0 baseline). Web 361 passed / 41 files (was 338 / 37). `tsc --noEmit`, `next lint`, `next build` clean, with `/archive` and `/settings/company/members` in the route list. `alembic upgrade head` → `check` → `downgrade base` → `upgrade head` clean on a scratch database, created and dropped, dev data untouched. On the live dev stack the new paths answer 403 unauthenticated and `/setup` serves 200. All six required mutations produced real FAIL lines, reverted green.

The new tests run against **real Postgres**, because SQLite returns what it was told for every filter and for none — so a cross-company test on SQLite passes against a router with the scoping deleted. That is rule 17b, stated by the builder unprompted. New `pg_*` fixtures build a scratch database and repoint the app's own `SessionLocal`, because the account gate and the setup guard open their own sessions; they skip where there is no Postgres.

**Statically checked only.** The Members screen, switcher and Archive page were not opened in a browser — they typecheck, lint, build and have jsdom component tests, which cannot tell you whether the dropdown reads in dark theme or how the date input behaves in Safari. The invite email was not opened in a mail client; what is asserted is that the task is queued with the right address, that the token is in the URL, and that it is queued **after** the commit.

**Not checked.** The browser walkthrough — `docker compose down -v` is refused by the permission classifier, so it needs a human at the Mac. `CompanyBankAccount` is modelled and migrated per §1 but has no endpoint; that is P0b-2's settings UI.

## Three findings

### 1. A hole in the acceptance walkthrough — the prompt's fault
Creating a company has an endpoint but **no screen**: the company settings UI is explicitly P0b-2, and the prompt forbade starting it — while also writing an acceptance step that begins "superadmin creates YON Studio OG". Those two instructions contradict each other, and the contradiction is in the prompt, not the build. Step 1 therefore goes through `http://localhost:8100/docs` for now. Setup does create a first company named after the instance, so an install is never companyless.

**Consequence for P0b-2:** it must lead with the company create/settings screen, so acceptance stops depending on Swagger.

### 2. Three existing 2FA test files were patching a *name*, not the thing
They patched `routers.auth.require_2fa_enabled` — an alias of the policy. Once `_login_outcome` started asking `two_factor_required_for`, the patch stopped reaching the login branch and **six tests failed loudly**. Replaced with a `conftest.require_2fa()` helper that sets both readers and keeps the real function in the path.

Worth separating from the usual 17b story: this double failed **loudly** when the real thing moved, which is the good outcome. A double that keeps passing after the code under it moves is the dangerous kind. When a double is unavoidable, patch the thing itself rather than a name that aliases it — then a move breaks the test instead of orphaning it.

`test_setup_superadmin.py` also read the new **[paste truncated]** — now the membership.

### 3. `CLAUDE.md` is still uncommitted in the working tree
Rules 17b and 17d, expanded. It predates this build and was deliberately not swept into a P0b-1 commit. It should get its own commit.

## Next
`git push origin main` to let CI run, then the browser walkthrough (steps in the repo's `docs/prompts/10-P0b-1-outcome.md`), then P0b-2 — leading with the company create/settings screen.
