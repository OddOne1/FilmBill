# Test-harness traps

Every instance behind CLAUDE.md rule 17b, one paragraph each, in the order
they were found. They are kept because the rule is short and the rule alone
has never been enough: each of these was written by somebody who had read the
previous ones.

**The shared mechanism.** A check reported green because it had not run, or
had run against something kinder than production. There are four ways in:

1. **a double stood in for its own subject** — the thing named in the test
   was the thing replaced;
2. **the assertion was satisfied by the absence of the thing under test** —
   a negative assertion, a source-text match, a spy on a path nothing takes;
3. **the component picked its own configuration** — it opened its own
   connection, session or resolver, so the harness could not configure it,
   and silently selected the arrangement in which it does nothing;
4. **the result was compared across environments** — a skip read as a pass,
   or a count from one machine read against a count from another.

---

## 1. §202 — jsdom resolved the CJS build and synthesised a default export

FreeFrame, 2026-09-21. The password-strength meter imports
`@zxcvbn-ts/language-common`. Under vitest with jsdom, the module resolver
picked the package's **CommonJS** build and synthesised
`default = module.exports`, so `common.default.dictionary` was an object and
every test passed. In the browser the ESM entry is used, that package has no
default export, and the expression threw `TypeError`. The test and the
browser were loading two different files.

**What it should have done:** import the package's ESM entry, or the built
chunk, in at least one test.

## 2. §203 — a source-text assertion for a branch that rendered the wrong thing

FreeFrame, 2026-09-21. `assert 'purpose == "two_factor"' in src` proved that
a branch existed in the source. It existed, and it rendered the wrong
template for months. A test that reads source text can only ever report that
somebody wrote something.

**What it should have done:** assert on the rendered output.

## 3. §204 — in-memory doubles with their own counters

FreeFrame, 2026-09-21. "The two code pools do not share an attempts bucket"
was tested with in-memory stand-ins for Redis, each with its own counter
dictionary. The property under test was *which key prefixes the real code
uses*, and the key names never came into play at all.

**What it should have done:** run against a fake that keys on the real key
names, or against Redis.

## 4. P0b-0 — the rate limiter failed open where Redis was unreachable

FilmBill. The suite was green because it ran on a machine where Redis did not
answer, and both limiters fail open by design. Run where Redis answered, 30
of 327 tests returned 429 instead of what they asserted —
`test_setup_superadmin.py` alone fires 7 POSTs at a cap of 3 per 600 s, so
four of them had been testing the limiter rather than the endpoint, and
saying so in a way nobody saw.

**The fix:** `tests/conftest.py` clears the real counters around every test
rather than disabling the limiter, so the limiter that runs is the real one
and a 429 from now on is a real one.

## 5. P0b-1 — six tests patched an alias of a policy that had moved

FilmBill. Six tests patched `routers.auth.require_2fa_enabled`. P0b-1 moved
the 2FA policy call, and all six failed **loudly** — which is the good
outcome, and the reason it is in this list: had they patched a stand-in
further out, they would have kept passing while testing nothing.

**The rule it illustrates:** patch the thing, not a name that aliases it.

## 6. P0b-2 — `SetupGuardMiddleware` and the ambient `DATABASE_URL`

FilmBill, 2026-10-01. The middleware runs before any route, so it has no
dependency to take and opens its own session through
`apps.api.database.SessionLocal` — the **ambient** database named by
`DATABASE_URL`. Three different outcomes came from one commit inside an hour:
no tables at all (CI, any machine with no dev stack) → the guard's query
raises, it fails open, everything passes; a dev database with tables and no
superadmin → 503 to every request, 258 failures; the same database with one
superadmin → everything passes again. **The all-green configuration was the
one in which the middleware never ran**, and that is where P0b-1's reported
615 came from.

**The fix:** the `client` fixture sets the guard's flag explicitly and says
so, and `test_setup_guard.py` drives the real middleware against a database
that says "no superadmin" — the state the fixture stands in for.

## 7. P0b-2 — SQLite cannot fail a scoping test

FilmBill. SQLite returns what it was told for every filter and for none, and
so does a `MagicMock` session. A cross-company isolation test written against
either passes against a router with the scoping deleted. Scoping tests run
against real Postgres or they are decoration.

**The fix:** the `pg_engine` / `pg_db` / `pg_client` fixtures, and from P0b-3
a guard that **fails rather than skips** when `CI` is set
(`tests/conftest._no_postgres`), plus a CI step asserting the real-database
count is a pass count and not a skip count.

## 8. dev-mail — the effective configuration was asserted nowhere

FilmBill, three phases. `smtp_security_from` maps a false `SMTP_USE_TLS` to
**implicit_tls**, deliberately. The dev stack therefore opened an SMTP_SSL
socket to Mailpit's plaintext port 1025 and every send died with
`[SSL: WRONG_VERSION_NUMBER]`. P0b-0 fixed the mechanism and tested the
mechanism; nothing asserted what the dev compose file actually resolved to.
The invite links, 2FA codes and backup-address codes that three acceptance
walkthroughs depended on were never delivered.

**The fix:** `test_dev_mail_config.py` reads the compose file and pushes its
values through the real config path.

## 9. The vacuous `Storage.prototype` spy

FilmBill. An assertion satisfied by the **absence** of the thing under test:
the spy was installed on a prototype method the code path never reached, so
"it was not called" was true for the wrong reason and would have stayed true
after the feature was deleted.

**The rule it illustrates:** assert a positive marker. "The secret is not in
this row" passes for a row that is empty for any reason at all; "the row
contains `[redacted]`" is evidence the redaction ran.

## 10. The dead MinIO images and a warm Docker cache

FilmBill, `docs/prompts/18-minio-fork-and-image-guard.md`. MinIO withdrew
`minio/minio` and `minio/mc` from Docker Hub. Both were **pinned to exact
tags** in two compose files, which is normally the careful thing to do, and
it made no difference: the references stopped resolving and nothing failed,
because every machine that ran the stack already had the layers cached. A
green smoke test on a warm cache says nothing about a fresh host.

**The fix:** `.github/workflows/image-references.yml` asks the registry with
`docker manifest inspect` rather than the local daemon.

## 11. `test_smtp_security_modes` asserted a value nothing set

FilmBill. The test asserted the effective SMTP mode was one of two values,
and passed only because no environment it ran in set `SMTP_SECURITY` at all.
An assertion whose subject is unset is an assertion about the default, under
the name of the thing it was supposed to be about.

## 12. CI was intermittently red for weeks over a readiness gate

FilmBill, `docs/prompts/14-ci-web-flake.md`. `logo-never-clears.test.tsx`
waited for an `<h2>` that paints on first render, then reached synchronously
for a button that only exists once `/site-settings` has resolved. A race the
fast machine always wins, so it appeared only in CI, as an error naming the
element and not the cause. CI was red from run #13 to #21; a commit titled
"and fix CI" appeared to fix it, because growing the suite (338 → 361 → 391)
only moved the odds.

**The fix, and the method:** gate on the fetched value, and include a clause
that holds even when the fixture's data equals the pre-fetch default —
otherwise every `queryBy…().toBeNull()` after it passes vacuously against a
page that never loaded. Reproduce before fixing by giving the fetch mock a
20 ms delay, then confirm green at 20/50/120 ms. That turned intermittent
into certain and found a second racy test in the same file CI had never
shown. Never a sleep, a longer timeout or `retry:` in the vitest config —
those hide the whole class. A `getBy*` on genuinely unconditional UI is
correct and catches real regressions, so do not convert a suite to `findBy*`
wholesale.

## 13. A dump that exists is not a proven backup

FilmBill, the server drill of 2026-10-20
(`docs/prompts/19-filmbill-P0b-3.md`, part 6). The backup script ran, the
file appeared, the restore command worked. The first two dumps held an
**empty** `company_bank_accounts`: a restore that "worked" and recovered
nothing anybody would miss. A backup is proven only by restoring data you can
recognise — add a recognisable row, find it in the dump, change it, restore,
see it come back.

**The fix:** `docs/deploy/server-test.md` Step 9 is that drill, and the
backup script prints the same in two lines.

## 14. A race test that passed with the constraint deleted

FilmBill, P0b-3 part 2. Two threads were released on a barrier and both
posted the same IBAN, to prove that `UNIQUE (company_id, iban)` decides the
loser. It passed with the unique constraint removed from the model: the first
request simply committed before the second one read, so the router's own
check answered and the database was never asked. The test was measuring the
application layer while claiming to measure the database one — case 4 of the
mechanism above, by accident of scheduling.

**The fix:** the barrier sits *inside* the duplicate read, so both requests
are past it before either inserts, and only the constraint can decide. The
mutation that removes the constraint is what proves it.

## 15. A readiness gate that matched a filter's own `<option>`

FilmBill, P0b-3 part 5. The audit-log screen builds its Action and User
filters from the same response it renders rows from, so
`findByText('company.updated')` matched two elements and threw. Nine tests
failed at once and **every one of them reported the gate rather than its
subject** — the same shape as instance 12, wearing a different hat: a gate
that can fail for a reason unrelated to the test makes the whole file report
the gate.

**The fix:** gate on a value that appears only in a fetched row — here the
event's IP, which no filter contains.
