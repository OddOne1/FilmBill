Before anything else, run `git remote -v`. This prompt is for the FilmBill repo (OddOne1/FilmBill). If the remote is anything else, stop, say so, and do nothing.

# Claude Code prompt — FilmBill P1a-2: users add their own rates and no-tax cases

> Pasted inline. Save this entire message as the next free slot in `docs/prompts/` (check `docs/prompts/INDEX.md`), add the INDEX line, then work.
> Origin: Mathias, 2026-10-23: "this app is for users all over Europe: one or several reduced rates, maybe several standard rates; the number is up to the user; it starts with one main rate, one reduced rate and a plus button; there must also be the option of no tax (reverse charge inside the EU, and other situations)." Builds on P1a (`41-`, `42-`; commits up to `e67d48f`). Read CLAUDE.md and obey it (same rules as P1a: money is Decimal, region packs are data, company isolation tests, migrations only, generated API types, behavioural tests, real-FAIL mutations, three-tier report).
> **Commit order matters.** Each numbered part is its own commit, green on its own. Do not start a part while the previous one is red.
> This must land before P1b (parties) and P1c (catalog): both will reference tax classes and codes.

## Decisions (binding, Mathias 2026-10-23; the two marked [Guessing] are mine, report anything that argues against them)
1. A company can have **any number of domestic rates**. Each is a pair: a **tax class** (what an item is labelled with) and its **tax code** (the rate). 1:1.
2. A company can have **any number of no-tax cases** (codes with rate 0).
3. **The EN 16931 category stays a system list**, never user-invented. A new rate is always category `S` (standard-rated, covers reduced rates too) with a rate above 0 and below 100. A no-tax case chooses one of: `Z` zero-rated, `E` exempt, `AE` reverse charge, `K` intra-community supply, `G` export, `O` not subject to tax; its rate is always 0.
4. **No hard delete anywhere.** Only deactivate (`is_active=false`). A code or class that anything still points at cannot be deactivated (Part 2).
5. **[Guessing]** Countries with a shipped pack (AT, DE) keep their full legal set. Any other country starts with exactly two rates, **Standard** and **Reduced**, both inactive with rate 0 until the user types a real rate ("set your rate"), plus the Add buttons.
6. **[Guessing]** A custom no-tax case is never chosen by the shipped rules. It is selected explicitly as an item override, party override or (P2) document override. The engine and the pack rules do not change.
7. Austrian pack gets one new row (WKO page "ermäßigte Umsatzsteuersätze", Stand 24.06.2026): class `reduced_3`, code `AT-4.9`, rate 4.9, **inactive by default**, note "basic foods only; enable if you rebill such items", `notes_reviewed` false. Reduced = 10 %, reduced_2 = 13 % stay as built.

## Step 0, before changing anything — report these
1. `git status -sb`, `git log origin/main..HEAD --oneline`, the declared test environment and the baseline (expected: API 1288 passed / 1 skipped, `-m realdb` 361 / 0; web 454). Say if different.
2. What exactly happens today when a company's country has **no** pack (for example `FR`): which pack seeds, what the Tax screen shows. This decides Part 3.
3. Every place that reads a tax class key or code (engine, pack loader, seeding, API, screens), so that user-created classes are proven to work through each of them.

## Part 1 — create, edit, deactivate (API, migration only if a column is missing)
- `POST /company/tax/rates` `{label, rate, code?}` → creates class + `S` code in one transaction. Class `key` is generated (`custom_<n>`, immutable, unique per company); `label` is editable. `code` optional: default is `<COUNTRY>-<rate with dots>` and collisions get a suffix; user-chosen codes match `^[A-Z0-9][A-Z0-9._-]{0,19}$`. Returns both.
- `POST /company/tax/no-tax-cases` `{label, category, code?, legal_notes}` → creates a rate-0 code in one of the six categories above; no class. `legal_notes` is `{lang: text}`, `notes_reviewed` false.
- `PATCH` for classes (label only) and extend the code PATCH from P1a. A rate edit on a code is allowed (rates change by law); the response and the screen say it applies to documents created afterwards.
- Validation: rate bounds per category; a company has at most **30** codes (report the number you chose and why); unique `(company_id, code)`.
- Audit (in the caller's transaction, old/new values): `tax_code.created`, `tax_code.updated` (exists), `tax_class.created`, `tax_class.updated`. Redaction unaffected.
- No new permission keys: edit uses the P1a edit permission, read uses `company.view`. Another company's data is 404. Regenerate `api.gen.ts`.

## Part 2 — reference guard (so deactivating cannot silently break proposals)
- One function `references_to_code(db, code_id)` / `references_to_class(db, class_id)` returns a list of `(kind, name)` referrers. Today it knows **tax rules** (outcomes pointing at that class or code, active rules only). P1b, P1c and P2 will register theirs; leave a clear registration point and a test that proves a referrer registered at test time is honoured.
- Deactivating a referenced code or class is **refused with 409 and a sentence naming the referrers** ("EU-AE is used by rule 'EU reverse charge, services'"). Unreferenced ones deactivate; reactivating always works.
- Mutation, a real FAIL line: remove the guard → the test deactivating `EU-AE` fails. Revert.

## Part 3 — packs and seeding
- eu_generic start state per decision 5; other-country behaviour from Step 0 item 2 made explicit and tested (a company with country `FR` gets Standard + Reduced and the EU no-tax set, nothing else).
- The "set your rate" condition generalises: any category-`S` code with rate 0 and inactive.
- AT pack row per decision 7. Packs stay data; loader schema updated if needed; loading twice changes nothing; never re-seed over a company's edits.
- Migration backfill only if existing companies need the new AT row (report what happened on the dev DB; the dev company has no country).

## Part 4 — engine proof (no engine change expected)
Golden fixtures, written out, for a company with three user-created rates: item with class `custom_1` to a domestic business buyer → that code and rate; to a verified EU business buyer (services) → reverse charge as before; with a custom no-tax case selected as item override → that code, rate 0, its note, `would_have_applied` populated; with a party override → same; item beats party. If the engine needs a change to pass these, stop and report why before changing it.
**Mutations, a real FAIL line each:** make the engine ignore custom class keys; let a custom no-tax case be chosen by a shipped rule. Revert.

## Part 5 — the screen (Settings → Company → Tax → Codes)
- Two sections. **Rates**: the domestic rates with name, rate, active, "set your rate" where it applies, and a **+ Add rate** button (dialog: name, rate %, optional code). **No-tax cases**: name, kind, legal note, reviewed flag, active, and a **+ Add no-tax case** button (dialog: kind chosen from the six with one plain-language line each in de and en, name, legal note per language, a line saying the wording is the user's responsibility and should be checked with their advisor).
- Editing and deactivating from the row; a refused deactivation shows the 409 sentence, not a generic error. Without the edit permission the buttons are absent and the screen says why (17c).
- **Test a case**: the item-class selector lists all active classes (custom ones included); the override selectors include active custom no-tax cases.
- Component tests with the real API shapes (rule 11); a test that the plus buttons exist for an editor and not for a viewer; a test that a refused deactivation shows the referrer's name.
**Mutation, a real FAIL line:** the web computes a rate itself instead of showing the API's. Revert.

## Part 6 — documentation (separate commit)
SCOPE §16: next free D-number for decisions 1–7 (categories stay a system list and why: EN 16931 export; no hard delete; guard; the two [Guessing] items). Update `docs/prompts` index. Report the exact diff.

## Acceptance for Mathias — dev compose (about 10 minutes)
1. Settings → Company: set the country to `FR` (a country with no pack). Tax → Codes shows **Rates**: Standard and Reduced, both "set your rate", and the EU no-tax cases. Nothing else.
2. Set Standard to 21 and Reduced to 9 (test numbers, not advice). Click **+ Add rate**, name "Super-reduced", rate 4.5.
3. Test a case: buyer FR business, class Super-reduced → your new code at 4.5 %. DE business with verified ID, services → reverse charge as before.
4. **+ Add no-tax case**: kind "Exempt", name "Exempt – training", a note in your language. In Test a case choose it as item override → rate 0, your note, "would have been …" shown.
5. Try to deactivate EU-AE → refused with a sentence naming the rule. Deactivate "Super-reduced" → it disappears from the class selector; reactivate it.
6. Audit log, filter `tax_code.created` and `tax_class.created`: rows with the values you typed.
7. Set the country back to AT afterwards only if you want your earlier AT checks to still apply (say what happened to the FR rows).

## Report format
A 1–3 sentence plain-language summary first. Then three tiers: run and observed, statically checked only, not checked. List every migration, new audit action, the full API and web test counts with the environment declared and which new tests ran as real-DB in CI, the SCOPE diff, and the answers to Step 0. State what only the server can verify. Push when every part is green and CI shows the real-DB tests running.
