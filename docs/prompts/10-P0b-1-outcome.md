# FilmBill P0b-1 — companies, memberships, scoping, permissions

> Implemented 2026-09-30 from [`09-P0b-1-companies.md`](09-P0b-1-companies.md).
> Nothing pushed to any registry (CLAUDE.md rule 18, gate R1). Dev stack only.

---

## In one paragraph

Companies exist now, with per-company roles, and every company-scoped request
carries the company in an `X-Company-Id` header that the web app attaches in
exactly one place. A user who is not a member of a company gets a 404 for it —
including an installation superadmin, who is not automatically a member of
anything. A tax advisor reaches the archive and nothing else, and is forced
into two-factor setup on their next login whether or not the instance requires
2FA of everyone. Granting, re-roling or revoking a membership ends that
person's open sessions immediately.

---

## Decisions taken here

**The permission table lives in `apps/api/services/permissions.py`**, the file
rewritten as its own docstring promised — not a new `core/permissions.py`. The
prompt allowed either and asked which; this one, because the module already
existed, already said it would become this, and a second module beside it would
have left two files with "permissions" in the name.

The FastAPI *dependencies* (`current_membership`, `require`) are in
`apps/api/middleware/company.py`, next to `middleware/auth.py`'s
`get_current_user`, which is the existing convention for request-scoped
dependencies in this codebase. The permission DATA — `PERMISSIONS`,
`has_permission`, `scoped` — is all in the one service module.

**Path shape: `/companies` plural, `/company` singular.** The prompt says one
scoping mechanism and no second way, and the honest reading of that is that a
company id must never appear in both a path and a header, because the
interesting bug is the request where they disagree. So:

| | |
|---|---|
| `GET /companies`, `POST /companies` | no company context — the switcher's list, and creating one |
| `GET /company`, `PATCH /company` | the company named by `X-Company-Id` |
| `GET·POST /company/members`, `PATCH·DELETE /company/members/{id}` | same |

Eight new operations, all under the `companies` tag.

**No `/company/archive` endpoint.** The prompt asks for a placeholder Archive
*page* gated by `archive.view` so the tax-advisor view can be accepted now. It
is a web page; inventing an API endpoint for P5 to replace would have widened
the surface for nothing. What that means for the permission matrix is stated
plainly in `test_company_permissions.py`: a tax advisor's every company
endpoint answers 404, and `GET /companies` still lists the company — because
an advisor who cannot see whose archive they are in cannot do the job.

**`CompanyBankAccount` is modelled with no endpoint.** §1 lists it as part of
what a company is; the company-settings UI that would edit it is P0b-2. It
ships in the same migration as its parent rather than needing a second one.

**Per-company branding: deferred to P4, as instructed.** Not started.

**A guard the prompt did not ask for:** a company can never be left with no
active owner, and only an owner can create another owner. That state has no
way out through the API — `company.members.manage` belongs to owners and
admins, and an admin cannot promote anyone — so someone would need SQL.

---

## The permission keys

Every key in `PERMISSIONS`, with the roles that hold it. The unused ones are
the point (§3): later phases import the spelling from here instead of each
inventing their own.

| Key | owner | admin | accountant | producer | staff | tax_advisor |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| `company.view` | ● | ● | ● | ● | ● | ● |
| `company.settings.edit` | ● | ● | | | | |
| `company.members.manage` | ● | ● | | | | |
| `number_series.manage` | ● | ● | | | | |
| `audit.view` | ● | ● | ● | | | |
| `archive.view` | ● | ● | ● | | | ● |
| `archive.download` | ● | ● | ● | | | ● |
| `documents.finalize` | ● | ● | ● | ● | | |
| `documents.revise` | ● | ● | ● | ● | | |
| `ledger.view` | ● | ● | ● | | | |
| `reports.view` | ● | ● | ● | | | ○ |
| `exports.download` | ● | ● | ● | | | ○ |

○ = only where the company sets `tax_advisor_reports`. Held in
`TAX_ADVISOR_OPTIONAL` rather than as a condition inside the table, so the
table above stays readable as an unconditional statement and the conditional
grant is one branch in one function.

`company.view` is the one key the prompt did not name. It exists because
`GET /company` needs something, and giving it to every role including the tax
advisor is what lets an advisor see whose archive they are looking at.

An unknown key is held by **nobody** — `PERMISSIONS.get(key, set())`. A typo in
a `require()` call locks an endpoint down rather than opening it.

---

## The two 2FA lines

`apps/api/services/site_settings_service.py`:

```diff
 def two_factor_required_for(db: Session, user) -> bool:
-    """Whether THIS user may not turn their own two-factor off.
+    """Whether THIS user must have two-factor authentication.
     ...
-    return require_2fa_enabled(db)
+    if require_2fa_enabled(db):
+        return True
+    return two_factor_required_by_membership(db, user)
```

`apps/api/routers/auth.py:713`, in `_login_outcome`'s forced-enrolment branch:

```diff
-    if require_2fa_enabled(db):
+    if two_factor_required_for(db, user):
```

`routers/auth.py:231` — `send_magic_code`'s gate — is **untouched**, as
instructed. Mutation 3 below proves a test fails if that changes.

The derivation is `company_memberships` joined to the companies' own
`require_2fa_roles`, computed on every call. No flag on `User`: it would be
wrong the moment a membership was revoked, in the direction that keeps
demanding a second factor from someone whose reason for needing one is gone.
`test_revoking_the_membership_restores_a_normal_login` is that assertion.

**`/auth/me` inherits it with no frontend change — confirmed, not assumed.**
`test_auth_me_reports_the_requirement_for_a_role_required_user` issues the
request and reads `two_factor_required: true` off the response.

---

## Verification, in three tiers

### Run & observed

| | |
|---|---|
| **API suite** | **615 passed, 0 failed**, Redis reachable, real Postgres. (537 at P0b-0 + 78 new.) |
| **Web suite** | **361 passed / 41 files** (338 / 37 at P0b-0). |
| `tsc --noEmit` | clean |
| `next lint` | clean — no warnings or errors |
| `next build` | clean; `/archive` and `/settings/company/members` both in the route table |
| `alembic upgrade head` → `check` → `downgrade base` → `upgrade head` | clean, on a scratch database created and dropped for it. Dev data untouched. |
| Live dev stack | migration `0005` auto-applied on API start; all four new paths answer 403 unauthenticated; `/login` and `/setup` serve 200 |

**The new tests run against real Postgres, not the mock session.** This is the
part worth arguing for. `conftest.py`'s `mock_db` is a MagicMock: it returns
what it was told to return for every filter and for none, so a cross-company
test written against it passes against a router with the scoping *deleted*.
That is CLAUDE.md 17b exactly — the harness, not the system. So P0b-1 adds
`pg_engine` / `pg_db` / `pg_client` fixtures that create a scratch database
(`filmbill_pytest_scratch`), create the tables, and point the app's own
`SessionLocal` at it as well as `get_db` — because `AccountGateMiddleware` and
`SetupGuardMiddleware` open their own sessions and a test whose middleware
talks to a different database than its handler is not testing what ships.
Where there is no Postgres they **skip**, not pass. CI has one.

**All six required mutations produced real FAIL lines:**

| Mutation | Caught by |
|---|---|
| Remove the role branch from `two_factor_required_for` | `test_company_role_2fa.py` — 6 failures incl. `…forced_into_setup_on_password_login` |
| Revert `auth.py:713` to `require_2fa_enabled` | 4 failures across `test_company_role_2fa.py` and `…session_invalidation.py` |
| Make `send_magic_code` consult the per-person rule | `test_send_magic_code_still_works_for_a_role_required_user` — exactly test 3b, alone |
| Drop the company filter from `scoped()` | `test_naming_another_companys_row_while_correctly_scoped_is_404[PATCH …]` and `[DELETE …]` |
| Skip the `tv` bump on grant | 3 failures incl. `test_granting_a_role_rejects_the_refresh_token_too` |
| Ignore `expires_at` | 6 failures across the expiry and 2FA files |

Reverted; green again (615) after.

**The isolation case list is derived, not written.** `test_company_isolation.py`
reads the app's own OpenAPI document, filters to the `companies` tag, and
asserts every operation appears in `CASES`. An endpoint added under that tag
with no case fails that file rather than escaping the suite. The two operations
with no company context are marked `NO_COMPANY_CONTEXT` *in the same table*, so
adding one is visible rather than an omission.

### Statically checked only

- The **Members screen, the company switcher and the Archive placeholder were
  not opened in a browser.** They typecheck, lint, build, and are covered by
  component tests against jsdom (switcher visibility and switching, nav gating
  for a tax advisor vs. staff). What a jsdom test cannot tell you is whether
  the dropdown is legible in the dark theme or whether the date input behaves
  the same in Safari.
- The **invite email was not opened in a mail client.** The test asserts the
  task is queued with the right address and that the invite token is in the
  URL, and that it is queued *after* the commit (checked by reading the
  database from a second session inside the queuing call — the ordering bug
  that would otherwise send someone to a token the database does not have
  yet). The rendered mail is `test_code_email_copy.py`'s subject, unchanged.

### Not checked

- **Everything in the browser walkthrough below.** It needs an empty database
  (`docker compose down -v`), which the permission classifier refuses, so it
  needs a human at the Mac — same as P0b-0.
- **`CompanyBankAccount` has no endpoint and no test beyond the migration.**
  Deliberate: P0b-2 builds the settings UI that writes it.
- **Multi-company behaviour under concurrent switching** — two tabs on
  different companies. The header is a module-level value in `lib/api`, which
  is per-tab (each tab has its own JS context), so this should be fine; it was
  not exercised.

---

## Two things found on the way that were not in the prompt

**1. Three test files were patching a stand-in for the policy, not the policy.**
`test_two_factor.py`, `test_magic_code_2fa_gate.py` and
`test_password_reset_code_pool.py` drove the instance-wide switch by patching
`apps.api.routers.auth.require_2fa_enabled` — the router's own import. Once
`_login_outcome` started asking `two_factor_required_for` instead, that patch
stopped reaching the login branch, which then read the MagicMock session, where
every attribute is truthy, and every plain login came back as forced 2FA
enrolment. Six tests failed and said so.

Fixed with `conftest.require_2fa(value)`, which sets it at **both** readers —
the service function (which `_login_outcome` reaches through) and the router's
import (which `send_magic_code` still uses, deliberately). The real
`two_factor_required_for` now runs in those tests, role branch and all, rather
than being patched out of the path.

**2. `test_setup_superadmin.py` read the user as "the last thing `add` was
called with".** Setup now adds three rows, so that was the membership. Changed
to pick the `User` out of `call_args_list` by type, which is order-independent,
and a test was added asserting setup creates the company.

Both are the tests doing their job. Recorded because "I changed some tests" is
the sentence that should always come with a reason.

---

## What Mathias clicks — browser acceptance

Empty database. **In Terminal, at the Mac:**

> **Mail did not actually reach Mailpit until `PENDING`.** The dev compose resolved SMTP security to `implicit_tls` against Mailpit's plaintext port, so every step below that waits for a code or an invite link would have waited forever — see `docs/prompts/16-dev-mail-smtp-none.md`.


```
cd ~/Claude/Projects/FilmBill/repo
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml build --no-cache api worker email_worker beat web
docker compose -f docker-compose.dev.yml up -d
```

(Two separate commands for build and up, never `up --build --no-cache` —
CLAUDE.md rule 14.)

Then **in the browser**: app at `http://localhost:3100`, mail at
`http://localhost:8125`.

**1 — Setup and the first company.**
Go to `http://localhost:3100`. Complete setup with your own address and a real
password, then the account gate (password, backup address, the code from
Mailpit).
*Expected:* you land on the dashboard. **Setup has already created one company
named "FilmBill"** — that is deliberate (§1: an install with no company has no
id to send, so nothing works). There is no switcher yet, because you are in
exactly one company.

**2 — Make the two real companies.**
Settings → Members is visible (you are the Owner). To create the two companies
the acceptance asks for, use the API directly for now — the "New company" form
is P0b-2's company-settings UI, and this prompt built the endpoint, not the
form. **In Terminal:**

```
open http://localhost:8100/docs
```

In the Swagger page: `POST /companies`, Try it out, with
`{"legal_name": "YON Studio OG", "address_country": "AT", "default_currency": "EUR", "default_language": "de"}`
— you will need to Authorize first with the access token from your browser's
devtools (Application → Local Storage → `fb_access_token`). Repeat with
`{"legal_name": "Test GmbH"}`.

*This is the one place the walkthrough departs from the prompt's step 1, and
it is a real gap: creating a company has an endpoint but no screen. Flagged
for P0b-2 rather than built here, because the prompt is explicit that the
company settings UI is P0b-2 and says not to start on it.*

**3 — The switcher.**
Reload `http://localhost:3100`.
*Expected:* a company chip appears in the top bar, left of Search, naming one
of the three companies with your role under it. Switching changes which
company Settings → Members lists. Sign in as a user who is in only one company
and the chip is **not there at all**.

**4 — Invite a tax advisor.**
Switch to **YON Studio OG**. Settings → Members. Add `advisor@example.com`,
role **Tax advisor**, Access ends = 30 days from today. Submit.
*Expected:* a row appears, "Invitation not accepted yet", with the expiry
shown. An invite mail is in Mailpit at `http://localhost:8125`.

**5 — The advisor's first login is 2FA enrolment.**
Open the invite link from Mailpit **in a private window**. Set a password,
complete the account gate.
*Expected:* you are **forced into two-factor setup** — even though Settings →
Admin has the instance-wide 2FA requirement OFF. That is the per-role rule.
Enrol with an authenticator.

**6 — What the advisor sees.**
*Expected, and this is the acceptance:* the sidebar has **Dashboard and
Archive and nothing else**. Archive says "Archive — coming in P5". Settings has
Profile, Appearance, Notifications — **no Members, no Branding, no Admin**.
There is **no switcher**, and **Test GmbH is nowhere**. Under Settings →
Profile, "Turn off two-factor" is **disabled with a reason shown**.

**7 — Revoking ends the session.**
Keep the advisor signed in in the private window. In your main window (YON
Studio OG → Settings → Members) click **Revoke** on the advisor.
Now go back to the private window and click anything.
*Expected:* they land back at the login screen.

**Paste back:** what you saw at steps 3, 6 and 7, and anything that did not
match.

---

## Deploy shape

Dev stack only. **No image pushed to any registry** — CLAUDE.md rule 18 holds
until gate R1, because a v1-named image would be pulled by Watchtower and
replace the production FilmBill that is issuing real invoices.

Migration `0005_companies_and_memberships` applies on API container start (the
dev compose command runs `alembic upgrade head`). It creates three tables and
one enum type and touches no existing row, so an install that upgrades into it
keeps working with zero companies until someone creates one. It deliberately
creates **no** company: an existing install has a superadmin already and will
never run setup again, and a migration inventing a legal name, a country and a
currency would be guessing at values that end up printed on an invoice.
`POST /companies` is that path.
