"""Messages that name whose problem it is (B1.2 part 4).

The rule (CLAUDE.md): a message names the subject ("Your company", "The
customer", "This line"), says what is wrong in one short sentence and what to
do in one more, in the product's own words — "customer", not "buyer"; "your
company", not "this company".

Asserted through the two endpoints a person actually reads them from — the
customer's tax tab (`POST /parties/{id}/tax-preview`) and the settings test
case (`POST /company/tax/propose`) — against the real engine, so a screen
showing the server's text verbatim shows these sentences. Plus a guard over
the message dictionaries, with a probe proving the guard can fail.
"""

import re

import pytest

from apps.api.billing.calc.vocabulary import WARNING_TEXT as CALC_WARNING_TEXT
from apps.api.billing.tax.engine import _WARNING_TEXTS as TAX_WARNING_TEXTS
from apps.api.services.vies import _ERROR_TEXTS as VIES_ERROR_TEXTS

from .test_budget_tracking_api import _headers, world  # noqa: F401 — fixtures

COMPANY_MISSING = "Your company has no country. Set it in Settings → Company."
CUSTOMER_MISSING = "The customer has no country. Set it on the customer."


def _no_company_country(world):
    world["company"].address_country = None
    world["db"].commit()


def _customer(pg_client, world, **body):
    body.setdefault("type", "organisation")
    body.setdefault("name", "Kunde GmbH")
    response = pg_client.post("/parties", headers=_headers(world), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _tax_tab(pg_client, world, party):
    response = pg_client.post(
        f"/parties/{party['id']}/tax-preview", headers=_headers(world), json={}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _test_case(pg_client, world, **body):
    response = pg_client.post("/company/tax/propose", headers=_headers(world), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _texts(proposal):
    return {warning["code"]: warning["text"] for warning in proposal["warnings"]}


# ─── The two country messages, where they are read ──────────────────────────


def test_no_company_country_says_your_company_in_the_customer_tax_tab(pg_client, world):
    _no_company_country(world)
    # The customer HAS a country: the message must not be about them.
    party = _customer(pg_client, world, tax_country="DE")
    proposal = _tax_tab(pg_client, world, party)
    assert proposal["blocking"] is True
    assert _texts(proposal) == {"home_country_missing": COMPANY_MISSING}
    assert proposal["reasons"] == ["Your company has no country, so no tax rule was checked."]
    assert "customer" not in " ".join(_texts(proposal).values()).lower()


def test_no_company_country_says_your_company_in_the_settings_test_case(pg_client, world):
    _no_company_country(world)
    proposal = _test_case(pg_client, world, buyer_country="DE")
    assert _texts(proposal) == {"home_country_missing": COMPANY_MISSING}
    assert proposal["seller_country"] is None


def test_a_customer_without_a_country_gets_the_customer_message(pg_client, world):
    party = _customer(pg_client, world)
    proposal = _tax_tab(pg_client, world, party)
    assert _texts(proposal) == {"buyer_country_missing": CUSTOMER_MISSING}
    assert proposal["reasons"][0] == "Your company is in AT."
    assert proposal["reasons"][-1] == "The customer has no country, so no tax rule was checked."
    test_case = _test_case(pg_client, world)
    assert _texts(test_case) == {"buyer_country_missing": CUSTOMER_MISSING}


def test_an_unverified_customer_vat_id_says_customer(pg_client, world):
    proposal = _test_case(
        pg_client, world, buyer_country="DE", buyer_type="business", buyer_vat_verified=False
    )
    text = _texts(proposal).get("buyer_vat_id_not_verified", "")
    assert text.startswith("The customer's VAT ID is not verified")
    assert "buyer" not in " ".join(proposal["reasons"]).lower()


# ─── The guard over the message dictionaries ────────────────────────────────

DICTIONARIES = {
    "tax engine warnings": TAX_WARNING_TEXTS,
    "calculation warnings": CALC_WARNING_TEXT,
    "VIES faults": VIES_ERROR_TEXTS,
}

FORBIDDEN = re.compile(r"this company|the buyer", re.IGNORECASE)
LIMIT = 220


def _offences(dictionaries):
    found = []
    for name, texts in dictionaries.items():
        for code, text in texts.items():
            if FORBIDDEN.search(text):
                found.append(f"{name}/{code}: says {FORBIDDEN.search(text).group(0)!r}")
            if len(text) > LIMIT:
                found.append(f"{name}/{code}: {len(text)} characters")
    return found


def test_the_message_dictionaries_follow_the_rule():
    assert sum(len(texts) for texts in DICTIONARIES.values()) >= 10
    assert _offences(DICTIONARIES) == []


@pytest.mark.parametrize(
    "text",
    [
        "This company has no country. Set it in Settings → Company.",
        "The buyer has no country. Set it.",
        "x" * (LIMIT + 1),
    ],
    ids=["this-company", "the-buyer", "too-long"],
)
def test_the_guard_can_fail(text):
    assert _offences({"probe": {"probe": text}}) != []
