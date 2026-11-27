"""Cross-company isolation, for every company-scoped endpoint there is.

The case list is NOT hand-written. It is derived from the app's own OpenAPI
document, filtered to the `companies` tag, and every operation found there
must appear in `CASES` below — so adding an endpoint under that tag without
writing a case FAILS this file rather than quietly escaping it. That is the
one property that makes this suite still true in P4, when there are forty
endpoints instead of seven.

Two things are checked per scoped operation:

  1. sending `X-Company-Id: B` as a user who is only in A answers 404;
  2. naming one of B's rows in the path, while correctly scoped to A,
     answers 404 as well.

Both, because they fail independently. The first is the dependency
(`middleware/company.py`); the second is the query (`services/permissions.py::
scoped`), and an endpoint can pass the first while leaking on the second — a
membership id from another company looked up without a `company_id` filter is
found, and answering 403 or 200 for it confirms it exists.

404 and not 403 throughout: CLAUDE.md rule 6. A 403 says "this company exists
and you are not in it", which is a fact about a business relationship that
neither party published.
"""

import pytest
from fastapi.openapi.utils import get_openapi

from apps.api.models.party import PartyVatCheck

from apps.api.models.company import CompanyRole
from apps.api.services import number_series

from .company_factories import (
    auth_headers,
    grant,
    make_bank_account,
    make_budget,
    make_budget_actual,
    make_budget_adjustment,
    make_budget_commitment,
    make_budget_global,
    make_budget_line,
    make_budget_section,
    make_device,
    make_device_alternative,
    make_device_association,
    make_device_unit,
    make_company,
    make_crew_assignment,
    make_crew_role,
    make_crew_unavailability,
    make_item,
    make_item_category,
    make_number_series,
    make_party,
    make_party_tax_id,
    make_project,
    make_project_block,
    make_project_day,
    make_rental_factor_group,
    make_superadmin,
    make_tax_class,
    make_tax_code,
    make_unit,
    make_user,
    unrelated_company_id,
)

#: What makes an operation company-scoped: its PATH, not its tag.
#:
#: The company-scoped routes are spelled `/company/...` — singular, "the one
#: I am acting in" — and `/companies` (plural) is the two operations that have
#: no company context by definition. That is the design invariant stated in
#: `middleware/company.py`, and selecting on it is what makes this file
#: automatic: a new router under `/company/...` is caught here whatever tag it
#: carries.
#:
#: It used to select on the `companies` TAG, which was the same set until
#: P0b-3 added `/company/number-series` under a tag of its own. Those four
#: endpoints would have escaped silently — the file would have gone on
#: passing, and reading as coverage.
#: `/parties` is company-scoped too, and is NOT under `/company/...`.
#:
#: P1b added it as a top-level path because a customer list is not a company
#: setting, and that very nearly made fourteen endpoints invisible to this
#: file — the same way P0b-3's number-series endpoints nearly escaped when the
#: selector was a TAG. Listed explicitly rather than widened to "anything
#: scoped", because the point of this function is that adding a path somebody
#: forgot about makes a test fail rather than quietly pass.
#: `/catalog` and `/calc` join them for A1, and the reasoning repeats a third
#: time: twenty-three endpoints on top-level paths. `/calc/preview` touches no
#: company-scoped ROW at all — it is pure arithmetic — and it is in here
#: anyway, because it takes a company context through `require()` and
#: "pointing the header at a company you are not in is a 404" has to be true
#: of every endpoint that takes one, not only of the ones that read a table.
#: A2 adds `/projects`, and for the fourth time the same reasoning: a
#: project list is not a company setting, so it is a top-level path, and a
#: top-level path that is not named here is invisible to this file.
_SCOPED_PREFIXES = (
    "/company/",
    "/parties",
    "/catalog",
    "/calc",
    "/projects",
    "/budgets",
    # B2b. The crew list and the gear list — top-level paths a fifth and
    # sixth time, named here for the same reason as the four above.
    "/crew",
    "/devices",
)
_SCOPED_EXACT = ("/companies", "/company", "/parties", "/projects", "/crew", "/devices")


def _is_company_scoped(path: str) -> bool:
    return path in _SCOPED_EXACT or path.startswith(_SCOPED_PREFIXES)

#: Marks an operation that takes no company context BY DESIGN, with the
#: reason. Listing your own companies and creating a new one are the only two
#: — in both, a company context is either the answer or does not exist yet.
#:
#: Spelled as an explicit entry rather than as an exclusion list somewhere
#: else, so that adding one is a visible act in the same table as the real
#: cases. A new endpoint cannot land here by omission.
NO_COMPANY_CONTEXT = "no-company-context"

#: How to exercise each operation.
#:
#: A path may contain `{membership_id}` or `{bank_account_id}`. Both are
#: substituted with the id of a row belonging to the **other** company, which
#: is what makes the second test below a real check rather than a request for
#: something that does not exist: a router that forgot its `company_id` filter
#: FINDS that row, and answering anything but 404 for it confirms it exists.
#:
#: Every placeholder a path can carry must appear in `OTHER_COMPANY_ROWS`
#: below, or the substitution silently leaves `{…}` in the URL and the
#: endpoint 404s for the wrong reason — a green test proving nothing. There is
#: an assertion for exactly that.
#: A 1×1 PNG, for the device picture upload's isolation case.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
    b"\x00\x00\x00IEND\xaeB`\x82"
)


CASES: dict[str, object] = {
    "GET /companies": NO_COMPANY_CONTEXT,
    "POST /companies": NO_COMPANY_CONTEXT,
    "GET /company": {"body": None},
    "PATCH /company": {"body": {"trading_name": "Stolen"}},
    "GET /company/members": {"body": None},
    "POST /company/members": {
        "body": {"email": "someone-new@example.com", "role": "staff"}
    },
    "PATCH /company/members/{membership_id}": {"body": {"role": "admin"}},
    "DELETE /company/members/{membership_id}": {"body": None},
    "GET /company/bank-accounts": {"body": None},
    "POST /company/bank-accounts": {
        "body": {"iban": "AT61 1904 3002 3457 3201", "label": "Theirs"}
    },
    "PATCH /company/bank-accounts/{bank_account_id}": {"body": {"label": "Stolen"}},
    "DELETE /company/bank-accounts/{bank_account_id}": {"body": None},
    # B1.2: the production fee preset. Company-level, no row id — the
    # header is the whole boundary, which is what the first test walks.
    "GET /company/production-fee": {"body": None},
    "PUT /company/production-fee": {"body": {"percent": "8", "kinds": []}},
    # P0b-3. `document_type` is one nothing seeds, so the legitimate-request
    # half of the second test creates a series rather than colliding with a
    # seeded one — a 409 there would read as a scoping failure.
    "GET /company/number-series": {"body": None},
    "POST /company/number-series": {
        "body": {
            "document_type": "isolation_probe",
            "name": "Probe",
            "pattern": "PR-{YYYY}-{SEQ}",
        }
    },
    "PATCH /company/number-series/{series_id}": {"body": {"name": "Stolen"}},
    # The preview carries a `series_id` in its BODY rather than its path, so
    # the placeholder substitution below cannot reach it. Its own case lives
    # in test_number_series.py
    # (`test_previewing_another_companys_series_is_404`) — recorded here
    # because this table is where somebody looks for it.
    "POST /company/number-series/preview": {
        "body": {"pattern": "AR-{YYYY}-{SEQ}", "reset": "yearly", "padding": 4}
    },
    # P0b-3 part 5. Read-only, and the only endpoint on `audit_events` — the
    # table has no write endpoint and cannot have one. `audit_events` is also
    # the one business table without the `CompanyScoped` mixin, because
    # `company_id` is nullable for install-level events, so the scoping is a
    # named function (`services/audit.company_events`) rather than
    # `scoped()`. That makes this case load-bearing rather than routine.
    "GET /company/audit-events": {"body": None},
    # P1a. The tax configuration. `PATCH` carries another company's code id
    # in its PATH, so it is the half of this file that catches a missing
    # `company_id` filter rather than a missing dependency — a code id looked
    # up without one is FOUND, and answering anything but 404 confirms it
    # exists.
    "GET /company/tax/codes": {"body": None},
    "PATCH /company/tax/codes/{tax_code_id}": {"body": {"name": "Stolen"}},
    "GET /company/tax/classes": {"body": None},
    "GET /company/tax/rules": {"body": None},
    # Reads nothing of B's by id, but it DOES read B's whole tax table when
    # the header says B — which is the first of the two checks below, and the
    # one that would leak a competitor's rates.
    "POST /company/tax/propose": {"body": {"buyer_country": "DE"}},
    # P1a-2. Creating a rate or a no-tax case writes into whichever company
    # the header names, so the first check below is the whole point: a user
    # in A must not be able to add a rate to B.
    "POST /company/tax/rates": {
        "body": {"label": "Stolen rate", "rate": "7"}
    },
    "POST /company/tax/no-tax-cases": {
        "body": {"label": "Stolen case", "category": "E"}
    },
    "PATCH /company/tax/classes/{tax_class_id}": {
        "body": {"label": "Stolen"}
    },
    # Serves a fixed system list and reads no company data at all. It still
    # takes a company context, because `require()` is how an endpoint gets
    # one and an endpoint without one cannot be written here by accident.
    "GET /company/tax/no-tax-categories": {"body": None},
    # P1b part 0. Both write into whichever company the header names, so the
    # first check below is the whole point: a user in A must not be able to
    # move B's country or reinstall B's rates.
    "POST /company/tax/country-change/preview": {"body": {"country": "DE"}},
    "POST /company/tax/country-change": {
        "body": {"country": "DE", "mode": "apply_pack"}
    },
    # P1b. Fourteen endpoints on a top-level path, which is why
    # `_is_company_scoped` had to learn about `/parties`: without that they
    # would all have been invisible here.
    "GET /parties": {"body": None},
    "POST /parties": {"body": {"type": "organisation", "name": "Stolen"}},
    "GET /parties/{party_id}": {"body": None},
    "PATCH /parties/{party_id}": {"body": {"name": "Stolen"}},
    "POST /parties/{party_id}/archive": {"body": None},
    "POST /parties/{party_id}/reactivate": {"body": None},
    "GET /parties/{party_id}/children": {"body": None},
    "POST /parties/{party_id}/addresses": {
        "body": {"kind": "billing", "street": "Stolen 1"}
    },
    "POST /parties/{party_id}/contact-points": {
        "body": {"kind": "email", "value": "stolen@example.com"}
    },
    "POST /parties/{party_id}/bank-accounts": {
        "body": {"iban": "AT61 1904 3002 3457 3201"}
    },
    "GET /parties/{party_id}/resolved": {"body": None},
    "GET /parties/{party_id}/vat-checks": {"body": None},
    # Tax identifiers (P1b-2). Eleven endpoints, and every one of them takes
    # a party id — so every one is a way to read or change another
    # company's customer if the scoping is ever dropped.
    "GET /parties/{party_id}/tax-ids": {"body": None},
    "POST /parties/{party_id}/tax-ids": {
        "body": {"kind": "vat", "value": "ATU16360407"}
    },
    # P1b-4 part 3. Writes nothing, but it reads the party's country for
    # tax — another company's — so it is a 404 like the rest.
    "POST /parties/{party_id}/tax-ids/detect": {"body": {"value": "ATU16360407"}},
    "PATCH /parties/{party_id}/tax-ids/{tax_id_id}": {
        "body": {"notes": "theirs"}
    },
    "POST /parties/{party_id}/tax-ids/{tax_id_id}/retire": {"body": None},
    "POST /parties/{party_id}/tax-ids/{tax_id_id}/reactivate": {"body": None},
    # Deliberately NOT exercised against a live registry: the handler would
    # 404 before reaching a provider, which is the whole point.
    "POST /parties/{party_id}/tax-ids/{tax_id_id}/check": {"body": None},
    "GET /parties/{party_id}/tax-ids/{tax_id_id}/checks": {"body": None},
    "POST /parties/{party_id}/tax-ids/{tax_id_id}/checks/{check_id}/acknowledge": {
        "body": {"note": "should not be possible"}
    },
    "POST /parties/{party_id}/tax-ids/{tax_id_id}/manual-confirmation": {
        "body": {"note": "should not be possible"}
    },
    # The policy is company-scoped by `X-Company-Id` like the rest of
    # Settings → Company: there is no id in the path, so the isolation is
    # that A's request can only ever read or write A's policy.
    "GET /company/tax/id-check-policy": {"body": None},
    "PATCH /company/tax/id-check-policy": {"body": {"recheck_interval_days": 30}},
    "POST /parties/{party_id}/tax-preview": {"body": {"supply_kind": "services"}},
    # A1. Twenty-three endpoints on `/catalog` and `/calc`, which is why
    # `_SCOPED_PREFIXES` had to learn about both. Every `{item_id}`,
    # `{category_id}`, `{unit_id}` and `{group_id}` below is substituted with
    # a row belonging to company B — a router that dropped its `company_id`
    # filter FINDS those rows, and anything but a 404 confirms they exist.
    "GET /catalog/units": {"body": None},
    "POST /catalog/units": {
        "body": {"key": "drehtag", "label_de": "Drehtag", "period_unit": "day"}
    },
    "PATCH /catalog/units/{unit_id}": {"body": {"label_de": "Stolen"}},
    "GET /catalog/categories": {"body": None},
    "POST /catalog/categories": {"body": {"name": "Stolen", "kind": "fee"}},
    "PATCH /catalog/categories/{category_id}": {"body": {"name": "Stolen"}},
    "POST /catalog/categories/{category_id}/deactivate": {"body": None},
    "POST /catalog/categories/{category_id}/reactivate": {"body": None},
    "GET /catalog/items": {"body": None},
    "POST /catalog/items": {"body": {"name_de": "Stolen", "kind": "fee"}},
    "GET /catalog/items/{item_id}": {"body": None},
    "PATCH /catalog/items/{item_id}": {
        "body": {"name_de": "Stolen", "kind": "fee"}
    },
    "PUT /catalog/items/{item_id}/rates": {
        "body": [{"rate_type": "bill", "period_unit": "day", "amount": "1.00"}]
    },
    "POST /catalog/items/{item_id}/deactivate": {"body": None},
    "POST /catalog/items/{item_id}/reactivate": {"body": None},
    "GET /catalog/rental-factor-groups": {"body": None},
    "POST /catalog/rental-factor-groups": {"body": {"name": "Stolen"}},
    "PATCH /catalog/rental-factor-groups/{group_id}": {"body": {"name": "Stolen"}},
    "PUT /catalog/rental-factor-groups/{group_id}/factors": {
        "body": [{"days": 1, "factor": "1.0"}]
    },
    "GET /catalog/rental-factor-groups/{group_id}/factor": {
        "body": None,
        "query": {"days": 5},
    },
    # The two multipart endpoints. `files` rather than `body`, because a JSON
    # body would be refused as malformed multipart with a 422 — and a 422 is
    # not a 404, so the first test below would pass for entirely the wrong
    # reason while the second failed.
    "POST /catalog/items/import/preview": {
        "files": {"file": ("items.csv", b"sku;name_de;kind\nX-1;Stolen;fee\n", "text/csv")}
    },
    "POST /catalog/items/import": {
        "files": {"file": ("items.csv", b"sku;name_de;kind\nX-1;Stolen;fee\n", "text/csv")}
    },
    # A2. Projects and their production days. Every `{project_id}` and
    # `{day_id}` below is substituted with a row belonging to company B, so a
    # handler that dropped its `company_id` filter FINDS them — and a 200 on
    # `PATCH /projects/{id}` would mean A had just renamed B's production.
    "GET /projects": {"body": None},
    "POST /projects": {"body": {"name": "Stolen", "start_date": "2026-06-01"}},
    "GET /projects/{project_id}": {"body": None},
    "PATCH /projects/{project_id}": {"body": {"name": "Stolen"}},
    "POST /projects/{project_id}/status": {"body": {"status": "archived"}},
    "GET /projects/{project_id}/days": {"body": None},
    "POST /projects/{project_id}/days": {
        "body": {"date": "2026-07-01", "type": "shoot"}
    },
    "POST /projects/{project_id}/days/range": {
        "body": {"from": "2026-07-01", "to": "2026-07-03", "type": "shoot"}
    },
    "PATCH /projects/{project_id}/days/{day_id}": {
        "body": {"location": "Stolen"}
    },
    "POST /projects/{project_id}/days/{day_id}/remove": {"body": None},
    # B2a. The calendar and its strips. `{block_id}` is a strip of B's, in
    # B's project; a handler that looked a strip up by id alone would move,
    # resize or remove B's shooting schedule.
    "GET /projects/{project_id}/calendar": {"body": None},
    "POST /projects/{project_id}/blocks": {
        "body": {"type": "shoot", "start_date": "2026-07-01", "end_date": "2026-07-02"}
    },
    "PATCH /projects/{project_id}/blocks/{block_id}": {"body": {"label": "Stolen"}},
    "POST /projects/{project_id}/blocks/{block_id}/move": {
        "body": {"start_date": "2026-08-03"}
    },
    "POST /projects/{project_id}/blocks/{block_id}/resize": {
        "body": {"end_date": "2026-07-09"}
    },
    "PUT /projects/{project_id}/blocks/{block_id}/days": {
        "body": {"days": [{"date": "2026-07-06", "type": "travel"}]}
    },
    "POST /projects/{project_id}/blocks/{block_id}/duplicate": {
        "body": {"start_date": "2026-09-07"}
    },
    "POST /projects/{project_id}/blocks/{block_id}/remove": {"body": None},
    # A2's Budget. Twenty-four operations, every `{budget_id}`,
    # `{section_id}`, `{line_id}`, `{global_id}` and `{adjustment_id}`
    # substituted with a row belonging to company B — a handler that dropped
    # its `company_id` filter FINDS them, and a 200 on
    # `PATCH /budgets/{id}/lines/{id}` would mean A had just re-priced B's
    # production.
    "GET /projects/{project_id}/budgets": {"body": None},
    "POST /projects/{project_id}/budgets": {"body": {"name": "Stolen"}},
    "POST /projects/{project_id}/budgets/example": {"body": None},
    "GET /budgets/{budget_id}": {"body": None},
    "PATCH /budgets/{budget_id}": {"body": {"name": "Stolen"}},
    "GET /budgets/{budget_id}/calculated": {"body": None},
    "GET /budgets/{budget_id}/export.csv": {"body": None},
    "POST /budgets/{budget_id}/lock": {"body": {}},
    "POST /budgets/{budget_id}/unlock": {"body": {}},
    # B1.1 closing.
    "GET /budgets/{budget_id}/close-check": {"body": None},
    "POST /budgets/{budget_id}/close": {"body": {}},
    "POST /budgets/{budget_id}/reopen": {"body": {"reason": "Stolen"}},
    # B1.1 extras. `{line_id}` is B's own line, so a lookup by id alone
    # would FIND it.
    "POST /budgets/{budget_id}/extras": {
        "body": {"description": "Stolen", "unit_price": "1.00"}
    },
    "PATCH /budgets/{budget_id}/extras/{line_id}": {"body": {"description": "Stolen"}},
    "POST /budgets/{budget_id}/extras/{line_id}/accept": {"body": {}},
    "POST /budgets/{budget_id}/extras/{line_id}/decline": {"body": {"note": "Stolen"}},
    "POST /budgets/{budget_id}/set-primary": {"body": None},
    "POST /budgets/{budget_id}/duplicate": {"body": {}},
    "POST /budgets/{budget_id}/sections": {"body": {"name": "Stolen"}},
    "PATCH /budgets/{budget_id}/sections/{section_id}": {
        "body": {"name": "Stolen"}
    },
    "POST /budgets/{budget_id}/sections/{section_id}/remove": {"body": None},
    # A nil UUID for the section, not a placeholder: `_send` substitutes
    # placeholders in the PATH only, and the budget lookup happens before
    # the section lookup — so this still proves the budget boundary, and a
    # malformed uuid in the body would have made it a 422 for the wrong
    # reason.
    "POST /budgets/{budget_id}/lines": {
        "body": {
            "section_id": "00000000-0000-0000-0000-000000000000",
            "description": "Stolen",
        }
    },
    "PATCH /budgets/{budget_id}/lines/{line_id}": {
        "body": {"description": "Stolen"}
    },
    "POST /budgets/{budget_id}/lines/{line_id}/duplicate": {"body": None},
    "POST /budgets/{budget_id}/lines/{line_id}/remove": {"body": None},
    "POST /budgets/{budget_id}/globals": {
        "body": {"key": "stolen", "label": "Stolen"}
    },
    "PATCH /budgets/{budget_id}/globals/{global_id}": {
        "body": {"label": "Stolen"}
    },
    "POST /budgets/{budget_id}/globals/{global_id}/remove": {"body": None},
    "POST /budgets/{budget_id}/adjustments": {
        "body": {
            "label": "Stolen",
            "type": "discount",
            "mode": "percent",
            "value": "5",
        }
    },
    "PATCH /budgets/{budget_id}/adjustments/{adjustment_id}": {
        "body": {"label": "Stolen"}
    },
    "POST /budgets/{budget_id}/adjustments/{adjustment_id}/remove": {
        "body": None
    },
    # B1.2: a budget's own production fee. Naming B's budget while scoped
    # to A must 404, or A could re-price B's quote.
    "PUT /budgets/{budget_id}/production-fee": {
        "body": {"enabled": True, "percent": "12", "kinds": []}
    },
    "POST /budgets/{budget_id}/production-fee/reset": {"body": None},
    # The two multipart endpoints, `files` rather than `body` for the reason
    # the catalog's import carries: a JSON body would be refused as malformed
    # multipart with a 422, and a 422 is not a 404.
    "POST /budgets/{budget_id}/import/preview": {
        "files": {
            "file": (
                "lines.csv",
                b"section_code;description\n1100;Stolen\n",
                "text/csv",
            )
        }
    },
    # `expected_digest` is a required query parameter; without it the 422 for
    # the missing field would arrive before the company check could 404.
    "POST /budgets/{budget_id}/import": {
        "query": {"expected_digest": "0000000000000000"},
        "files": {
            "file": (
                "lines.csv",
                b"section_code;description\n1100;Stolen\n",
                "text/csv",
            )
        }
    },
    # B1's cost planning. Every one names B's budget, and the ones that take
    # an entry id name B's own commitment or actual — so a handler that
    # looked an entry up by id alone would FIND a real row, rather than 404
    # for an id that names nothing.
    "GET /budgets/{budget_id}/tracking": {"body": None},
    "GET /budgets/{budget_id}/ledger": {"body": None},
    "GET /budgets/{budget_id}/duplicates": {"query": {"amount": "100.00"}},
    "GET /budgets/{budget_id}/commitments": {"body": None},
    "POST /budgets/{budget_id}/commitments": {
        "body": {"amount": "1.00", "commitment_date": "2026-06-01"},
        # A required id in the BODY, filled with B's section in `_send`. A
        # body without it is a 422 for the missing field, which arrives
        # before the company check could 404 — a refusal for the wrong
        # reason, and a green test proving nothing.
        "body_rows": {"section_id": "section_in_b"},
    },
    "PATCH /budgets/{budget_id}/commitments/{commitment_id}": {
        "body": {"notes": "Stolen"}
    },
    "POST /budgets/{budget_id}/commitments/{commitment_id}/close": {"body": {}},
    "POST /budgets/{budget_id}/commitments/{commitment_id}/cancel": {"body": {}},
    "POST /budgets/{budget_id}/commitments/{commitment_id}/void": {
        "body": {"reason": "Stolen"}
    },
    "GET /budgets/{budget_id}/actuals": {"body": None},
    "POST /budgets/{budget_id}/actuals": {
        "body": {"amount": "1.00", "incurred_on": "2026-06-01"},
        # A required id in the BODY, filled with B's section in `_send`. A
        # body without it is a 422 for the missing field, which arrives
        # before the company check could 404 — a refusal for the wrong
        # reason, and a green test proving nothing.
        "body_rows": {"section_id": "section_in_b"},
    },
    "PATCH /budgets/{budget_id}/actuals/{actual_id}": {"body": {"notes": "Stolen"}},
    "POST /budgets/{budget_id}/actuals/{actual_id}/void": {
        "body": {"reason": "Stolen"}
    },
    "PUT /budgets/{budget_id}/lines/{line_id}/etc": {"body": {"etc": "1.00"}},
    # B1.1 "Mark finished": B's own line and section, so a handler that
    # looked them up by id alone would FIND them.
    "POST /budgets/{budget_id}/lines/{line_id}/finish": {"body": {}},
    "POST /budgets/{budget_id}/lines/{line_id}/reopen": {"body": None},
    "POST /budgets/{budget_id}/sections/{section_id}/finish": {"body": None},
    "POST /budgets/{budget_id}/sections/{section_id}/reopen": {"body": None},
    "GET /budgets/{budget_id}/actuals/export.csv": {"body": None},
    "POST /budgets/{budget_id}/actuals/import/preview": {
        "files": {
            "file": (
                "actuals.csv",
                b"date;amount;section_code\n2026-06-01;1,00;1100\n",
                "text/csv",
            )
        }
    },
    "POST /budgets/{budget_id}/actuals/import": {
        "query": {"expected_digest": "0000000000000000"},
        "files": {
            "file": (
                "actuals.csv",
                b"date;amount;section_code\n2026-06-01;1,00;1100\n",
                "text/csv",
            )
        },
    },
    # B2b part 1. The crew list has no id in any path — a crew member is a
    # party, and `/parties/{party_id}` is where one is read by id — so the
    # header is the whole boundary here, which is what the first test walks.
    # The second sends each as a legitimate request and expects success.
    "GET /crew": {"body": None},
    "GET /crew/roles": {"body": None},
    "GET /crew/export.csv": {"body": None},
    "POST /crew": {"body": {"name": "Stolen", "roles": ["DoP"]}},
    "POST /crew/import/preview": {
        "files": {"file": ("crew.csv", b"name;roles\nStolen Person;Gaffer\n", "text/csv")}
    },
    "POST /crew/import": {
        "files": {"file": ("crew.csv", b"name;roles\nStolen Person;Gaffer\n", "text/csv")}
    },
    # R1. A person's roles: B's party and B's own role row, so a lookup by
    # id alone would FIND them; the create names B's labor item too.
    "GET /parties/{party_id}/crew-roles": {"body": None},
    "POST /parties/{party_id}/crew-roles": {
        "body": {},
        "body_rows": {"item_id": "labor_item_in_b"},
    },
    "PATCH /parties/{party_id}/crew-roles/{role_id}": {"body": {"notes": "Stolen"}},
    "POST /parties/{party_id}/crew-roles/{role_id}/remove": {"body": None},
    "POST /parties/{party_id}/crew-roles/from-tag": {"body": {"tag": "Gaffer"}},
    # B2c-1. Crew on B's calendar: B's project, strip and assignment, and
    # B's person's unavailability. Bodies name nil ids: the project or party
    # in the PATH is the boundary and is looked up first.
    "GET /projects/{project_id}/crew": {"body": None},
    "POST /projects/{project_id}/blocks/{block_id}/crew": {
        "body": {"people": [{"party_id": "00000000-0000-0000-0000-000000000000"}]}
    },
    "PATCH /projects/{project_id}/crew/{assignment_id}": {"body": {"days": []}},
    "POST /projects/{project_id}/crew/{assignment_id}/status": {"body": {"status": "booked"}},
    "POST /projects/{project_id}/crew/{assignment_id}/remove": {"body": None},
    "POST /projects/{project_id}/crew/status": {
        "body": {"status": "booked", "assignment_ids": ["00000000-0000-0000-0000-000000000000"]}
    },
    "GET /crew/availability": {"query": {"from": "2026-07-06", "to": "2026-07-12"}},
    "GET /parties/{party_id}/unavailability": {"body": None},
    "POST /parties/{party_id}/unavailability": {
        "body": {"start_date": "2026-07-01", "end_date": "2026-07-02"}
    },
    "PATCH /parties/{party_id}/unavailability/{unavailability_id}": {"body": {"reason": "Stolen"}},
    "POST /parties/{party_id}/unavailability/{unavailability_id}/remove": {"body": None},
    # B2b part 2. Gear. `{device_id}` and `{device_unit_id}` are B's device
    # and one of its units, so a handler that looked either up by id alone
    # would FIND them — and a 200 on `/retire` would be A retiring B's
    # camera. (`{device_unit_id}` and not `{unit_id}`: that placeholder is
    # the catalog's unit of measure.)
    "GET /devices": {"body": None},
    "POST /devices": {"body": {"name": "Stolen device", "quantity_owned": 1}},
    "GET /devices/export.csv": {"body": None},
    "POST /devices/import/preview": {
        "files": {"file": ("gear.csv", b"name;quantity\nStolen lamp;1\n", "text/csv")}
    },
    "POST /devices/import": {
        "files": {"file": ("gear.csv", b"name;quantity\nStolen lamp;1\n", "text/csv")}
    },
    "GET /devices/{device_id}": {"body": None},
    "PATCH /devices/{device_id}": {"body": {"name": "Stolen"}},
    "POST /devices/{device_id}/archive": {"body": None},
    "POST /devices/{device_id}/reactivate": {"body": None},
    "POST /devices/{device_id}/units": {"body": {"serial_numbers": ["STOLEN-1"]}},
    "PATCH /devices/{device_id}/units/{device_unit_id}": {"body": {"notes": "Stolen"}},
    "POST /devices/{device_id}/units/{device_unit_id}/remove": {
        "body": {"status": "lost", "reason": "Stolen"}
    },
    "POST /devices/{device_id}/units/{device_unit_id}/reactivate": {"body": {}},
    # B2b-2 part 1. B's device: a set-up or a rate write found by id alone
    # would link B's device to a new item, or change B's price list.
    "POST /devices/{device_id}/rental/setup": {"body": None},
    "PUT /devices/{device_id}/rental": {"body": {"price_per_day": "1.00"}},
    # B2b-2 part 3. B's device and B's own dependency link; the hint names
    # B's second device as the one being added.
    "GET /devices/{device_id}/dependencies": {"body": None},
    "GET /devices/{device_id}/dependencies/hint": {
        "query_rows": {"associated_device_id": "second_device_in_b"},
    },
    "POST /devices/{device_id}/dependencies": {
        "body": {},
        "body_rows": {"associated_device_id": "second_device_in_b"},
    },
    "PATCH /devices/{device_id}/dependencies/{association_id}": {"body": {"note": "Stolen"}},
    "POST /devices/{device_id}/dependencies/{association_id}/remove": {"body": None},
    # B2b-2 part 4. B's device and B's own alternative row.
    "GET /devices/{device_id}/alternatives": {"body": None},
    "POST /devices/{device_id}/alternatives": {
        "body": {},
        "body_rows": {"alternative_device_id": "second_device_in_b"},
    },
    "PATCH /devices/{device_id}/alternatives/{alternative_id}": {"body": {"note": "Stolen"}},
    "POST /devices/{device_id}/alternatives/{alternative_id}/move": {"body": {"direction": "down"}},
    "POST /devices/{device_id}/alternatives/{alternative_id}/remove": {"body": None},
    # B2b-2 part 5. A real PNG, so a refusal cannot be about the content.
    "POST /devices/{device_id}/image": {
        "files": {"file": ("photo.png", _TINY_PNG, "image/png")}
    },
    "POST /devices/{device_id}/image/remove": {"body": None},
    # Pure arithmetic, no stored row — and still a company endpoint.
    "POST /calc/preview": {
        "body": {
            "document": {
                "children": [{"id": "l1", "unit_price": "100.00", "quantity": "2"}]
            }
        }
    },
}

#: Placeholder -> the key in the `two_companies` fixture holding a row that
#: belongs to company B.
OTHER_COMPANY_ROWS = {
    "{membership_id}": "membership_in_b",
    "{bank_account_id}": "bank_account_in_b",
    "{series_id}": "series_in_b",
    "{project_id}": "project_in_b",
    "{day_id}": "day_in_b",
    "{block_id}": "block_in_b",
    "{budget_id}": "budget_in_b",
    "{section_id}": "section_in_b",
    "{line_id}": "line_in_b",
    "{global_id}": "global_in_b",
    "{adjustment_id}": "adjustment_in_b",
    "{tax_code_id}": "tax_code_in_b",
    "{tax_class_id}": "tax_class_in_b",
    "{party_id}": "party_in_b",
    "{tax_id_id}": "tax_id_in_b",
    "{check_id}": "check_in_b",
    "{item_id}": "item_in_b",
    "{category_id}": "category_in_b",
    "{unit_id}": "unit_in_b",
    "{group_id}": "group_in_b",
    "{commitment_id}": "commitment_in_b",
    "{actual_id}": "actual_in_b",
    "{device_id}": "device_in_b",
    "{device_unit_id}": "device_unit_in_b",
    "{association_id}": "association_in_b",
    "{alternative_id}": "alternative_in_b",
    "{role_id}": "crew_role_in_b",
    "{assignment_id}": "crew_assignment_in_b",
    "{unavailability_id}": "unavailability_in_b",
}


def _scoped_operations() -> list[str]:
    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    found = []
    for path, operations in schema["paths"].items():
        if not _is_company_scoped(path):
            continue
        for method in operations:
            found.append(f"{method.upper()} {path}")
    return sorted(found)


def test_every_company_endpoint_has_an_isolation_case(client):
    """The guard that keeps this file honest.

    Without it, the parametrised tests below would keep passing while a new
    unscoped endpoint sat beside them untested — which is the failure mode
    that makes a security suite worse than none, because it reads as coverage.
    """
    operations = set(_scoped_operations())
    missing = sorted(operations - set(CASES))
    stale = sorted(set(CASES) - operations)

    assert not missing, (
        f"company endpoints with no isolation case: {missing}. Add one to "
        f"CASES in this file — or, if it genuinely has no company context, "
        f"mark it NO_COMPANY_CONTEXT and say why."
    )
    assert not stale, f"CASES names operations that no longer exist: {stale}"


def test_the_case_list_is_not_empty(client):
    """A filter that matched nothing would make the file above pass silently."""
    assert len(_scoped_operations()) >= 10


#: Only the operations that DO take a company context get the two checks.

_SCOPED_CASES = sorted(
    name for name, case in CASES.items() if case is not NO_COMPANY_CONTEXT
)


@pytest.fixture
def two_companies(pg_db):
    """Ada is an owner of A and nothing else. Bo owns B.

    Ada is deliberately an OWNER of her own company — the most privileged
    company role there is — so that every 404 below is about the company
    boundary and not about a permission she happens to lack.
    """
    make_superadmin(pg_db)
    ada = make_user(pg_db, "ada@example.com")
    bo = make_user(pg_db, "bo@example.com")
    company_a = make_company(pg_db, "Company A")
    company_b = make_company(pg_db, "Company B")
    grant(pg_db, company_a, ada, CompanyRole.owner)
    bo_in_b = grant(pg_db, company_b, bo, CompanyRole.owner)
    # A row of B's that is NOT a membership, so the scoped lookups in the
    # bank-accounts router are exercised against a real neighbour rather than
    # against an id that names nothing.
    # A's own number series, seeded: `POST /projects` has no placeholder in
    # its path, so the test below sends it as a LEGITIMATE request and
    # expects it to succeed — and a company with no `project` series answers
    # 409 with a sentence, which would read here as an isolation failure.
    number_series.seed_number_series(pg_db, company_id=company_a.id)
    pg_db.commit()
    bank_in_b = make_bank_account(pg_db, company_b, iban="AT61 1904 3002 3457 3201")
    # And a number series of B's, for the same reason: `/company/number-series/
    # {series_id}` looked up without a `company_id` filter FINDS this row, and
    # answering anything but 404 for it confirms it exists.
    series_in_b = make_number_series(pg_db, company_b, document_type="invoice")
    # And a tax code of B's: `PATCH /company/tax/codes/{id}` looked up without
    # a `company_id` filter would find it and rename it.
    tax_code_in_b = make_tax_code(pg_db, company_b, code="AT-20")
    tax_class_in_b = make_tax_class(pg_db, company_b, code=tax_code_in_b)
    # And a party of B's: every `/parties/{id}` endpoint looked up without a
    # `company_id` filter would FIND it, and answering anything but 404
    # confirms it exists.
    party_in_b = make_party(pg_db, company_b, name="Their customer")
    # And one of their tax identifiers, plus a check of it. Same reasoning
    # one level down: `/tax-ids/{id}` looked up without a company filter
    # would find B's row, and so would the check history under it.
    tax_id_in_b = make_party_tax_id(
        pg_db,
        company_b,
        party_in_b,
        kind="vat",
        value="DE111111111",
        country="DE",
        is_primary=True,
    )
    # And the catalog rows A1 added: an item, a category, a unit and a factor
    # group, all B's. Every `/catalog/...{id}` endpoint looked up without a
    # company filter would FIND them.
    unit_in_b = make_unit(pg_db, company_b)
    category_in_b = make_item_category(pg_db, company_b)
    group_in_b = make_rental_factor_group(pg_db, company_b)
    item_in_b = make_item(
        pg_db, company_b, category=category_in_b, unit=unit_in_b
    )
    # And A2's: a project of B's and one of its production days. A handler
    # that looked either up by id alone would find them, and `POST
    # .../days/{id}/remove` answering 200 would be A taking a day out of B's
    # shooting schedule.
    project_in_b = make_project(pg_db, company_b, client=party_in_b)
    day_in_b = make_project_day(pg_db, company_b, project_in_b)
    block_in_b = make_project_block(pg_db, company_b, project_in_b)
    # And a budget of B's with one of everything in it, so each
    # `/budgets/...{id}` lookup has a real neighbour to find rather than an
    # id that names nothing.
    budget_in_b = make_budget(pg_db, company_b, project_in_b)
    section_in_b = make_budget_section(pg_db, company_b, budget_in_b)
    line_in_b = make_budget_line(pg_db, company_b, section_in_b)
    global_in_b = make_budget_global(pg_db, company_b, budget_in_b)
    adjustment_in_b = make_budget_adjustment(pg_db, company_b, budget_in_b)
    # And B1's: a commitment of B's and an actual settling it.
    commitment_in_b = make_budget_commitment(
        pg_db, company_b, budget_in_b, section_in_b, line=line_in_b
    )
    actual_in_b = make_budget_actual(
        pg_db, company_b, budget_in_b, section_in_b, commitment=commitment_in_b
    )
    # B2b: a device of B's and one of its units.
    device_in_b = make_device(pg_db, company_b)
    device_unit_in_b = make_device_unit(pg_db, company_b, device_in_b)
    # B2b-2: a second device of B's, which B's camera depends on.
    second_device_in_b = make_device(pg_db, company_b, name="Their battery")
    association_in_b = make_device_association(pg_db, company_b, device_in_b, second_device_in_b)
    alternative_in_b = make_device_alternative(pg_db, company_b, device_in_b, second_device_in_b)
    # R1: a labor item of B's and a role on B's party.
    labor_item_in_b = make_item(pg_db, company_b, sku="L-GAF", name_de="Gaffer", kind="labor")
    crew_role_in_b = make_crew_role(pg_db, company_b, party_in_b, labor_item_in_b, own_day_rate="400.00")
    # B2c-1: B's person on B's strip, and a period B's person is away.
    crew_assignment_in_b = make_crew_assignment(
        pg_db, company_b, project_in_b, block_in_b, party_in_b, dates=[block_in_b.start_date]
    )
    unavailability_in_b = make_crew_unavailability(
        pg_db, company_b, party_in_b, block_in_b.start_date, block_in_b.start_date
    )
    check_in_b = PartyVatCheck(
        company_id=company_b.id,
        party_id=party_in_b.id,
        tax_id_id=tax_id_in_b.id,
        vat_id="DE111111111",
        country="DE",
        kind="vat",
        provider="vies",
        status="valid",
        response={},
    )
    pg_db.add(check_in_b)
    pg_db.commit()
    return {
        "ada": ada,
        "bo": bo,
        "a": company_a,
        "b": company_b,
        "membership_in_b": bo_in_b,
        "bank_account_in_b": bank_in_b,
        "series_in_b": series_in_b,
        "tax_code_in_b": tax_code_in_b,
        "tax_class_in_b": tax_class_in_b,
        "party_in_b": party_in_b,
        "tax_id_in_b": tax_id_in_b,
        "check_in_b": check_in_b,
        "item_in_b": item_in_b,
        "category_in_b": category_in_b,
        "unit_in_b": unit_in_b,
        "group_in_b": group_in_b,
        "project_in_b": project_in_b,
        "day_in_b": day_in_b,
        "block_in_b": block_in_b,
        "budget_in_b": budget_in_b,
        "section_in_b": section_in_b,
        "line_in_b": line_in_b,
        "global_in_b": global_in_b,
        "adjustment_in_b": adjustment_in_b,
        "commitment_in_b": commitment_in_b,
        "actual_in_b": actual_in_b,
        "device_in_b": device_in_b,
        "device_unit_in_b": device_unit_in_b,
        "second_device_in_b": second_device_in_b,
        "association_in_b": association_in_b,
        "alternative_in_b": alternative_in_b,
        "labor_item_in_b": labor_item_in_b,
        "crew_role_in_b": crew_role_in_b,
        "crew_assignment_in_b": crew_assignment_in_b,
        "unavailability_in_b": unavailability_in_b,
    }


def _send(client, operation: str, headers: dict, case: dict, world: dict):
    method, path = operation.split(" ", 1)
    for placeholder, key in OTHER_COMPANY_ROWS.items():
        path = path.replace(placeholder, str(world[key].id))
    if "files" in case:
        # A multipart endpoint. Sending `json=` to one gets a 422 for the
        # wrong reason — see the CSV import cases above.
        return client.request(
            method,
            path,
            headers=headers,
            files=case["files"],
            data=case.get("data"),
            params=case.get("query"),
        )
    body = case.get("body")
    if case.get("body_rows"):
        body = {
            **(body or {}),
            **{name: str(world[key].id) for name, key in case["body_rows"].items()},
        }
    params = case.get("query")
    if case.get("query_rows"):
        params = {
            **(params or {}),
            **{name: str(world[key].id) for name, key in case["query_rows"].items()},
        }
    return client.request(
        method,
        path,
        headers=headers,
        json=body,
        params=params,
    )


def _targets_another_companys_row(operation: str) -> bool:
    return any(placeholder in operation for placeholder in OTHER_COMPANY_ROWS)


def test_every_placeholder_has_a_row_in_the_other_company(client):
    """The check that keeps the substitution honest.

    A path parameter with no entry in `OTHER_COMPANY_ROWS` leaves a literal
    `{bank_account_id}` in the URL. FastAPI then 404s it — for being an
    unparseable UUID, not for being another company's row — and the isolation
    test passes while proving nothing at all. This is the one failure mode of
    this file that would be completely silent.
    """
    import re

    placeholders = {
        match
        for operation in CASES
        for match in re.findall(r"\{[a-z_]+\}", operation)
    }
    missing = sorted(placeholders - set(OTHER_COMPANY_ROWS))
    assert not missing, (
        f"path parameters with no row in the other company: {missing}. Add one "
        f"to OTHER_COMPANY_ROWS and to the two_companies fixture, or the case "
        f"for that endpoint tests nothing."
    )


@pytest.mark.parametrize("operation", _SCOPED_CASES)
def test_pointing_the_header_at_another_company_is_404(
    pg_client, two_companies, operation
):
    world = two_companies
    response = _send(
        pg_client,
        operation,
        auth_headers(world["ada"], world["b"]),
        CASES[operation],
        world,
    )
    assert response.status_code == 404, (
        f"{operation} answered {response.status_code} for a company the caller "
        f"is not in: {response.text}"
    )


@pytest.mark.parametrize("operation", _SCOPED_CASES)
def test_naming_another_companys_row_while_correctly_scoped_is_404(
    pg_client, two_companies, operation
):
    """Scoped to A, reaching for a membership that belongs to B.

    For the operations with no `{membership_id}` this is the same request as
    a normal one and must SUCCEED — which is worth keeping in the same
    parametrisation rather than filtering out, because a scoping change that
    starts 404-ing legitimate traffic is a real regression and this is where
    it shows up.
    """
    world = two_companies
    case = CASES[operation]
    response = _send(
        pg_client,
        operation,
        auth_headers(world["ada"], world["a"]),
        case,
        world,
    )

    if _targets_another_companys_row(operation):
        assert response.status_code == 404, (
            f"{operation} reached a membership belonging to another company: "
            f"{response.status_code} {response.text}"
        )
    else:
        assert response.status_code < 400, (
            f"{operation} refused a legitimate request from a company owner: "
            f"{response.status_code} {response.text}"
        )


@pytest.mark.parametrize("operation", _SCOPED_CASES)
def test_a_company_that_does_not_exist_is_404(pg_client, two_companies, operation):
    """The same answer as a company that exists and is not yours.

    Asserted rather than assumed: if "unknown company" and "not your company"
    ever answered differently, the difference would be a membership oracle —
    ask about an id, and the status tells you whether it names a real
    business.
    """
    world = two_companies
    headers = auth_headers(world["ada"])
    headers["X-Company-Id"] = unrelated_company_id()
    response = _send(pg_client, operation, headers, CASES[operation], world)
    assert response.status_code == 404


def test_a_superadmin_is_not_a_member_of_every_company(pg_client, pg_db):
    """Installation authority is not company authority.

    The single most tempting shortcut in this whole layer, and the one that
    would make every other test in this file meaningless: a superadmin who
    implicitly reaches into any company turns the boundary into a suggestion.
    """
    root = make_superadmin(pg_db)
    company = make_company(pg_db, "Someone Else GmbH")

    response = pg_client.get("/company", headers=auth_headers(root, company))

    assert response.status_code == 404, (
        "a superadmin who is not a member read a company's record: "
        f"{response.text}"
    )


def test_a_member_of_one_company_sees_only_that_one_in_the_switcher(
    pg_client, two_companies
):
    world = two_companies
    response = pg_client.get("/companies", headers=auth_headers(world["ada"]))

    assert response.status_code == 200
    names = [entry["legal_name"] for entry in response.json()]
    assert names == ["Company A"]


def test_a_request_with_no_company_header_is_refused_but_says_why(
    pg_client, two_companies
):
    """400, not 404, and the difference is deliberate.

    A missing header is a broken client, not an authorisation question, and it
    reveals nothing about which companies exist. Answering 404 would send
    whoever is debugging it looking for a row that was never the problem.
    """
    world = two_companies
    response = pg_client.get("/company", headers=auth_headers(world["ada"]))

    assert response.status_code == 400
    assert "X-Company-Id" in response.json()["detail"]
