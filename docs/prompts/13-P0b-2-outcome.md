# FilmBill P0b-2 — money, generated API types, company settings screens

> Implemented 2026-10-01 from [`12-P0b-2-money-codegen-settings.md`](12-P0b-2-money-codegen-settings.md), on `21814f6`.
> Nothing pushed to any registry (CLAUDE.md rule 18, gate R1). Dev stack only.

---

## In one paragraph

Money is a `Decimal` with a string on the wire, a JSON number is a 422, and the
rounding happens in one function nothing outside it can reach. The web app's
API types are generated from the API's own schema and CI fails if they drift —
which, the first time the generated types were compared against the
hand-written ones, found a field the hand-written `User` claimed and `/auth/me`
has never sent. And there are now screens for every part of a company:
creating one, General, bank accounts with a real IBAN check, the accounting
selectors, and Security — so P0b-1's acceptance no longer has to go through
Swagger.

---

## §1 — Money

`apps/api/core/money.py`. Three decisions worth stating:

**A JSON number is refused.** `{"amount": 12.3}` is a 422, with a message that
says to send a string. The value is already lost by the time FastAPI sees it —
`12.3` as a double is 12.3000000000000007105… — so coercing it would be
pretending. An **integer** is accepted, because an integer survives JSON
intact. `bool` is checked before `int`, since `True` is an `int` in Python and
an unguarded check turns a JSON `true` into an amount of 1.

**Trailing zeros survive.** `"12.30"` round-trips as `"12.30"`, not `"12.3"`.
Serialisation is `format(value, "f")` rather than `str(value)`, because `str`
emits scientific notation for values `Decimal` chooses to hold that way
(`Decimal("1E+2")`) and no client parses that as money.

**The rounding context is explicit and local.** `quantize_amount` /
`quantize_qty` pass an explicit `Context(prec=34, rounding=ROUND_HALF_UP,
traps=[InvalidOperation, DivisionByZero, Overflow])` to `quantize`. Never
`getcontext()`: that is global, mutable and thread-local, so any library in the
process can change FilmBill's rounding from a distance and the call site looks
identical either way. Half-UP, not Python's default half-EVEN — banker's
rounding is right for long statistical sums and wrong for an invoice.

Non-finite values are refused as well. `"NaN"`, `"Infinity"` and `"sNaN"` are
all valid input to `Decimal()` and none is an amount; left through, a `NaN`
poisons every total it touches without ever raising and surfaces as a database
constraint violation three functions away.

`AmountColumn()` → `Numeric(18,2)`, `QtyColumn()` → `Numeric(18,4)`, each a
function so no two tables share a mutable SQLAlchemy type instance.

### `lib/money.ts` and the ESLint rule

Formatting only. Everything is a string end to end:
`Intl.NumberFormat.prototype.format` accepts a **string** (Intl.NumberFormat
v3 — Chrome 106+, Safari 15.4+, Firefox 116+, Node 18.14+; CI runs Node 20)
and formats it exactly without constructing a number.

One cast exists and is named: TypeScript's lib types `format` as taking
`number | bigint | StringNumericLiteral`, and `StringNumericLiteral` is a
template-literal type that matches string *literals* and rejects a `string`
whose value is only known at run time. `asIntlValue()` holds that cast once,
with the reason, rather than three times at the call sites.

**The rule, as written** (`apps/web/.eslintrc.js`, scoped to `lib/money.ts`):

| Selector | Catches |
|---|---|
| `BinaryExpression[operator=/^[+\-*\/%]$/]` | `a + b`, `a * 100`, … |
| `BinaryExpression[operator='**']` | exponentiation |
| `AssignmentExpression[operator=/^([+\-*\/%]\|\*\*)=$/]` | `x += 1` |
| `UpdateExpression` | `x++`, `--x` |
| `UnaryExpression[operator=/^[+\-]$/]` | `+value` numeric coercion |
| `CallExpression[callee.name=/^(Number\|parseFloat\|parseInt\|BigInt)$/]` | explicit conversion |
| `MemberExpression[object.name='Math']` | any `Math.*` |
| `CallExpression[callee.property.name='toFixed']` | rounding a double |

**One deliberate departure from the brief.** It asked for arithmetic on
*non-string* operands. ESLint selectors are syntactic and have no type
information, so "is this `+` a string concatenation" cannot be decided without
guessing — and a guessing selector is wrong in both directions: it lets real
arithmetic through when a variable looks stringy and blocks honest
concatenation when it does not. So every arithmetic operator is banned
outright and the file uses template literals where it joins strings. Strictly
stronger than asked, and it costs the file nothing: formatting needs no
arithmetic, which is the point. `.eslintrc.json` became `.eslintrc.js` so that
reasoning could live next to the rule — JSON has no comments.

`.eslintrc.js` was converted rather than extended because the explanation is
load-bearing; a rule this specific is useless without it.

---

## §2 — Generated API types

`pnpm gen:api` = dump the schema with `python -m apps.api.scripts.dump_openapi`,
then `openapi-typescript` → `apps/web/types/api.gen.ts`.

The dump builds the document by **importing the app**, not by fetching
`/openapi.json`: those can differ (`DISABLE_DOCS` turns the served document off
in production), and the thing the web app must be typed against is the routes
the code declares. It supplies its own placeholder environment and patches the
S3 client, so it needs no server, no database, no Redis and no network —
verified by running it in a container with `--network none` and no env vars
set, which produced byte-identical output. `sort_keys` and a fixed indent, so
the bytes depend only on the routes and CI's diff check cannot fail at random.

`pnpm gen:api:docker` does the same through the running API container, for a
machine with no Python environment for the API — which is every developer Mac
here, since the API only ever runs in Docker.

`apps/api/openapi.json` is the intermediate and is **gitignored**: one
generated artefact under version control means one drift check rather than two
that can disagree.

**CI job `api-types-are-generated`** regenerates and runs
`git diff --exit-code apps/web/types/api.gen.ts`. Its own job rather than a
step in `web`, because it needs both toolchains. Demonstrated: adding
`drift_probe` to `CompanyResponse` without regenerating produces

```
> /** Drift Probe */
> drift_probe?: string | null;
```

which the check fails on. Reverted; byte-identical again.

### What stayed hand-written, and why

`types/index.ts` is now a naming layer over `api.gen.ts` — friendly names, so a
server-side schema rename is one file to fix rather than forty call sites.
Three things are not re-exports:

| Kept | Why |
|---|---|
| `LoginResponse` | A union of two generated arms. FastAPI declares one `response_model` per operation, so the generated type is whichever arm was declared. Both arms **are** generated; only the fact that they are alternatives is hand-written. |
| `SmtpSecurity` | Narrower than the API on purpose. The API types the field as `str` so `routers/email_settings.py` can answer **400 "smtp_security must be one of: starttls, implicit_tls, none"**. A Pydantic `Literal` would replace that with pydantic's 422 and a nested `loc`/`msg` blob — worse for an administrator who mistyped a mode. Kept in step by hand against `SMTP_SECURITY_*`. |
| `PasswordStrength` | Computed in the browser by zxcvbn. No endpoint sends it. |
| `ACCOUNT_SETUP_REQUIRED` | A `const` value, not a type. |

Everything else — `User`, `AdminUser`, `SetupStatus`, every `TwoFactor*`,
`SiteSettingsResponse`, `EmailSettings*`, `Notification`, every `Company*` — is
a generated re-export. `TwoFactorMethod` and `BackupEmailState` are *derived*
from the fields that carry them (`NonNullable<User['two_factor_method']>`),
because Pydantic inlines those two-value Literals rather than naming a schema —
so the day a third method is added, every `switch` over it stops compiling.

### Four things the generated types found

Each was a schema that was vaguer or wronger than the code assumed. In every
case the generator was right.

1. **`User.deleted_at` does not exist.** The hand-written interface declared
   it and `/auth/me` has never sent it. Nothing read it — it was simply wrong,
   for as long as the file was maintained by hand. Exactly the drift rule 10
   exists for, found on the first comparison.
2. **`preferences` generated as `Record<string, never>`** — a map that can hold
   nothing, so no preference key could be read. Cause: a bare `dict` in
   Pydantic emits `{"type": "object"}` with no `additionalProperties`. Fixed
   with a `JsonObject` alias (`Annotated[dict[str, Any], WithJsonSchema(...)]`),
   because `dict[str, Any]` alone is *not* enough — Pydantic sees that `Any`
   constrains nothing and emits the same bare object.
3. **`theme_colors` had the same problem**, and `ActivityLogResponse.payload`.
   Same fix.
4. **`EmailSettingsUpdate` requires `smtp_password_clear` and
   `aws_mail_secret_access_key_clear`** (openapi-typescript treats a field with
   a default as always present). The admin page now sends them explicitly as
   `false`, which is what the form means — clearing a stored secret is its own
   deliberate action, not a side effect of pressing Save with an empty box.

---

## §3 — The company screens

**Settings → Company** is now a group of five, in the order someone fills it
in. General carries `exact: true` in the nav, because every other entry is a
path *under* `/settings/company` and without it General would highlight on all
of them.

- **General** — every field from the brief, plus the fiscal-year month. An
  empty box is sent as `null`, not `""` (a company with `trading_name: ""`
  renders an empty line where a name should be); the four NOT NULL columns are
  omitted instead when blank, so a cleared box is refused rather than sent as a
  null the database cannot store. Saving reloads the switcher, because the
  switcher shows the trading or legal name.
- **Create a company** — `/settings/company/new`, reached from **"New
  company…"** in the switcher. This is the hole P0b-1 reported: it shipped
  `POST /companies` with nothing that calls it. The switcher now appears for a
  superadmin even with one company (otherwise the only way to the second one is
  a URL you have to know) and even with **none**, which is the state a fresh
  install is in. It does not use the shared `CompanySection` frame, because
  that frame checks a permission in the *active* company and this page is
  reachable when there is none.
- **Bank accounts** — CRUD under `/company/bank-accounts`. The IBAN is checked
  **server-side only**: `apps/api/core/iban.py` does structure, per-country
  length and ISO 7064 MOD-97-10, with no network call. A second copy of mod-97
  in TypeScript would be a second implementation to keep in step, and the one
  that matters is the one the database sits behind. An unknown country code
  passes on structure and check digits alone rather than being refused — a
  self-hosted install in a country the registry has not heard of must still be
  able to enter its own account.
- **Accounting** — the seven selectors, **stored and read by nothing**, and the
  screen says so in a banner at the top. A settings page whose controls
  secretly do nothing is the same bug as a notification category that gates
  nothing: a false statement to the user. The fix is to say what is true, not
  to hide the control. Three selectors are `Literal`s server-side so the
  generated TypeScript carries the options; the two placeholder lists (charts
  of accounts, export formats) are free strings, because their real options
  come from region packs and pinning today's stand-ins into the schema would
  make a region pack a migration.
- **Security** — `require_2fa_roles` and `tax_advisor_reports`, with the
  consequence stated **before** the save.

### The Security warning

Adding a role forces every member holding it into enrolment at their next
login, and `two_factor_required_for` then refuses to let them turn it off. For
someone whose mail is broken and who has no authenticator that is a lockout
only a superadmin can undo.

So the warning appears the moment a box is ticked, and it:

- names the roles being added;
- **names the people** who hold them and have no second factor yet — a count
  would not do, because the person saving has to recognise them to judge
  whether they can receive mail;
- says plainly when nobody would be locked out, rather than claiming a risk
  that is not there (a warning that cries wolf stops being read);
- changes the button to **"Save and require two-factor"**.

`tax_advisor` is shown ticked and disabled: that role requires 2FA on every
install and is not a per-company decision.

### Choices worth flagging

**Bank accounts are gated on `company.settings.edit` for READS as well as
writes** — stricter than `company.view`. An IBAN is the detail a convincing
invoice fraud needs, and there is no reason every member of a production
company can read one out of a settings screen. No new permission key was
invented; `company.settings.edit` already means "may work on this company's own
configuration". `test_an_accountant_cannot_read_the_bank_accounts` pins it so
the stricter choice cannot be loosened by accident.

**Exactly one default bank account, enforced server-side in one transaction.**
`is_default: false` on the current default is *refused* with the action that
does work named in the message, and the default cannot be deleted while
another account exists. A company with accounts and no default is a company
whose next invoice has no account number on it, and the client cannot maintain
the invariant — demote-then-promote is two requests and the gap is the bug.

---

## Verification, in three tiers

### Run & observed

| | |
|---|---|
| **API suite** | **705 passed, 0 failed** — real Postgres, Redis reachable (615 at P0b-1) |
| **Web suite** | **391 passed / 43 files** (361 / 41 at P0b-1) |
| `tsc --noEmit` | clean, against the generated types |
| `next lint` | clean |
| `next build` | clean; all five `/settings/company*` routes in the table |
| `alembic upgrade head` → `check` → `downgrade base` → `upgrade head` → `check` | clean on a scratch database, created and dropped. Dev data untouched. |
| Codegen drift | demonstrated failing on an unregenerated schema field, then byte-identical after revert |
| ESLint money rule | demonstrated failing on `Number(amount) * 100` (two distinct errors), then clean after revert |
| Live dev stack | migration `0006` auto-applied; setup created the first company with the accounting defaults (`ear` / `soll` / `invoice_date`) and an owner membership; all four bank-account paths answer 403 authenticated-less |

**All four required mutations produced real FAIL lines:**

| Mutation | Caught by |
|---|---|
| `Money` accepts a float | `test_a_json_number_is_refused` + the quantity twin |
| Use the global decimal context | 6 failures, incl. `test_a_hostile_global_context_does_not_change_the_answer` and the control that proves the hostile context is genuinely hostile |
| Allow two default bank accounts | 6 failures, incl. `test_promoting_a_second_account_demotes_the_first` |
| Drop `require_2fa_roles` from the membership helper | 3 failures, incl. `test_saving_the_security_screen_makes_a_producer_need_2fa` — the end-to-end one |

**§5's question answered: yes, the bank-account endpoints joined the
parametrised isolation test automatically.** All four were caught the first
time it ran:

```
company endpoints with no isolation case:
  ['DELETE /company/bank-accounts/{bank_account_id}', 'GET /company/bank-accounts',
   'PATCH /company/bank-accounts/{bank_account_id}', 'POST /company/bank-accounts']
```

The case list is still derived from the OpenAPI document filtered by tag, so
that held with no change. What *did* need extending is the row substitution:
`{membership_id}` was hard-coded, so a second path parameter had nowhere to get
an other-company row from. It is now a table (`OTHER_COMPANY_ROWS`) plus a new
test — `test_every_placeholder_has_a_row_in_the_other_company` — because that
was the one silent failure mode left: an unmapped placeholder leaves a literal
`{bank_account_id}` in the URL, FastAPI 404s it for being an unparseable UUID
rather than for being another company's row, and the isolation test passes
while proving nothing.

### Statically checked only

- **None of the five screens was opened in a browser.** They typecheck, lint,
  build, and the Security screen's warning has ten jsdom tests. What jsdom
  cannot tell you is whether the warning banner reads in dark theme, whether
  the IBAN field behaves when pasted into on Safari, or whether the selects
  look right at narrow widths.
- `pnpm gen:api` (the Python-direct variant) was **not** run on this machine —
  there is no API Python environment on the host. `gen:api:docker` was used
  throughout, and the `--network none` check above proves the script itself
  needs nothing. CI will be the first place the direct form runs.

### Not checked

- **The whole browser walkthrough below.** It needs `docker compose down -v`,
  which the permission classifier refuses, so it needs a human at the Mac.
- **`Money`/`Quantity` on a real database column.** Nothing stores an amount
  yet — the first table with one is P1's. `AmountColumn()`/`QtyColumn()` are
  asserted on their precision and scale, not round-tripped through Postgres.
- **The placeholder chart-of-accounts and export-format lists** are stand-ins
  and were not checked against what BMD, RZL or DATEV actually read.

---

## One thing found on the way: the suite was reading its answer off its surroundings

Not in the brief, and it had to be fixed before any count here could be
honest.

`SetupGuardMiddleware` runs before any route, so it has no dependency to take —
it opens its own session through `apps.api.database.SessionLocal`, which is the
**ambient** database named by `DATABASE_URL`. That made the suite's result
depend on what happened to be in it:

| Ambient database | Result |
|---|---|
| No tables at all (CI; any machine with no dev stack up) | the guard's query raises, it **fails open**, everything passes |
| Dev database, tables, no superadmin | **503 to every request — 258 tests fail** |
| Same database with one superadmin row | everything passes again |

All three were observed from the same commit inside an hour, and confirmed by
inserting and deleting a single `users` row between two otherwise identical
runs. It is the same shape as the Redis problem P0b-0 §0 fixed, and worse in
one respect: **the configuration that passes is the one where the middleware
never runs.** P0b-1's reported 615 was obtained in such a configuration.

The fix is three lines in `conftest.client`: set `setup_guard._setup_complete`
explicitly and restore it after, because it is a process global and a suite
that inherits it is a suite whose result depends on test order. `SessionLocal`
is deliberately **not** patched — `AccountGateMiddleware` also opens its own
session, and `test_account_setup_gate.py` already points that lookup wherever
it means to by patching `get_user_by_id`, which a blanket mock here would
quietly override. (That was tried first, and it broke
`test_an_admin_can_clear_another_users_gate_and_it_is_logged` — the shared
mock handed the gate the target user for the admin's own lookup. Narrowing the
fix was the right answer.)

This is the one place the fixture is kinder than production, so it carries
rule 17b's obligation: the thing switched off is made real elsewhere.
**`test_setup_guard.py`** is new and drives the actual middleware against a
database that says "no superadmin" — the 503 and its message, the exempt paths,
the cache, and the fail-open-on-unreachable-database behaviour including that a
failed check is not cached as a pass. Nothing tested that middleware before.

Verified deterministic afterwards: **705 passed** with and without a superadmin
in the ambient database.

---

## What Mathias clicks — browser acceptance

Empty database. **In Terminal, at the Mac:**

> **Mail did not actually reach Mailpit until `fe6730e`.** The dev compose resolved SMTP security to `implicit_tls` against Mailpit's plaintext port, so every step below that waits for a code or an invite link would have waited forever — see `docs/prompts/16-dev-mail-smtp-none.md`.


```
cd ~/Claude/Projects/FilmBill/repo
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml build --no-cache api worker email_worker beat web
docker compose -f docker-compose.dev.yml up -d
```

(Build and up are always two separate commands — CLAUDE.md rule 14.)

Then **in the browser**: app at `http://localhost:3100`, mail at
`http://localhost:8125`. **No Swagger anywhere in this walkthrough** — that is
the point of §3.

**1 — Setup, then create the second company entirely through the UI.**
Go to `http://localhost:3100`, complete setup with your own address and a real
password, then the account gate (password, backup address, the code from
Mailpit).
*Expected:* you land on the dashboard. A company named **"FilmBill"** already
exists — setup creates the first one, so an install is never companyless. In
the top bar, left of Search, there is a **company chip** (it shows for you
because you are an installation administrator, even with one company).
Click it → **"New company…"** → legal name `YON Studio OG`, country `AT`,
currency `EUR`, language `de` → **Create company**.
*Expected:* you land on Settings → Company, the chip now says **YON Studio
OG**, and you are its Owner.

**2 — General, and two bank accounts.**
You are on Settings → Company (General). Fill in the trading name, legal form
`OG`, a register number, VAT ID `ATU12345678`, the address, `Europe/Vienna` →
**Save changes**.
*Expected:* "Saved." and the chip in the top bar updates if you set a trading
name.

Now Settings → Company → **Bank accounts**. Add `AT61 1904 3002 3457 3201`,
label `Production`.
*Expected:* it appears and is marked **Default** automatically.
Add `DE89 3704 0044 0532 0130 00`, label `Payroll` → appears, not default.
Click **Make default** on Payroll.
*Expected:* the star moves; Payroll sorts first; Production is no longer
default.
Now add `AT61 1904 3002 3457 3202` (last digit changed).
*Expected:* **refused**, with "The check digits do not match. One character is
probably wrong — compare it against your bank statement." The button is never
dead-and-silent — it says "Checking…" while the request is in flight and then
shows the reason.

**3 — Accounting selectors persist.**
Settings → Company → **Accounting**. Read the banner at the top — it should say
these are recorded, not applied. Set **Doppelte Buchführung**,
**Ist-Versteuerung**, tick Kleinunternehmer, pick a chart and an export format,
set archive basis to **Payment date** → Save. Then **reload the page**.
*Expected:* every selection is still there.

**4 — Security, and the consequence before the save.**
First create a producer to test with: Settings → Company → **Members** → add
`producer@example.com`, role **Producer**. Open the invite from Mailpit in a
**private window**, set a password, complete the account gate — and **do not
enrol in 2FA**. Note that they get in without it.

Back in your main window: Settings → Company → **Security**. Tick
**Producer**.
*Expected, and this is the acceptance:* a warning appears **immediately,
before you save**, saying that saving will force two-factor setup at the next
login, that they will not be able to turn it off, and **naming the producer
account** with a line about making sure they can receive email. The save button
now reads **"Save and require two-factor"**. Untick the box → the warning
disappears. Tick it again and **Save**.

Now in the private window, sign out and sign in as the producer.
*Expected:* they are **forced into two-factor setup** — even though Settings →
Admin has the instance-wide 2FA requirement OFF. Enrol, then go to Settings →
Profile: **"Turn off two-factor" is disabled with a reason shown**.

**5 — Everything is per company.**
Switch the chip back to **FilmBill**.
*Expected:* General shows "FilmBill" with empty address fields, Bank accounts
is **empty**, Accounting is back to **Einnahmen-Ausgaben-Rechnung /
Soll-Versteuerung / Invoice date**, and Security has no roles ticked. Switch
back to YON Studio OG and all of it returns.

**Paste back:** what you saw at steps 2 (the refused IBAN), 4 (the warning
text, and whether the producer was forced to enrol) and 5, plus anything that
did not match.

---

## Deploy shape

Dev stack only. **No image pushed to any registry** — rule 18 holds until gate
R1, because a v1-named image would be pulled by Watchtower and replace the
production FilmBill that is issuing real invoices.

Migration `0006_company_accounting_settings` adds seven columns to `companies`
and touches nothing else. Defaults are the Austrian ordinary case (`ear`,
`soll`, `invoice_date`, not a Kleinunternehmer), which every existing row gets;
the two columns with no sensible default are NULL, so "nobody has chosen" stays
distinguishable from "chose the first one". It applies on API container start
in dev. Nothing stores money yet, so no `NUMERIC` column ships here — the
column helpers do.

CI gains one job, `api-types-are-generated`, which needs both Python and Node
and pushes nothing.
