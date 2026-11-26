"""The Tax tab's single "VAT / tax ID" field: which kind is this number? (P1b-4 part 3)

Two halves:

  * **the detection as a table**, on the pure function, with the examples
    the data file and `vat.py` already carry (no invented formats);
  * **the endpoint**, on real PostgreSQL: it reads the contact's resolved
    country for tax, lets the form's country override it, writes NOTHING,
    is gated on `parties.edit`, and is a 404 for another company's contact.

The web half — that a detected number is then added through the ordinary
create and checked at once — is `party-tax-ids.test.tsx`, with the real
API shapes.
"""

import pytest

from apps.api.billing.parties.tax_ids import detect_kind, example_for, load_formats
from apps.api.models.company import CompanyRole
from apps.api.models.party import PartyTaxId

from .company_factories import auth_headers, grant, make_company, make_party, make_user
from .test_party_tax_ids_api import world  # noqa: F401  (fixture)


# ─── The detection, as a table ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, country, kind, stored",
    [
        ("ATU16360407", None, "vat", "ATU16360407"),
        ("atu 163 604 07", "DE", "vat", "ATU16360407"),
        # No prefix: the contact's country supplies it.
        ("U16360407", "AT", "vat", "ATU16360407"),
        ("DE123456789", None, "vat", "DE123456789"),
        ("123456789", "DE", "vat", "DE123456789"),
        ("CHE-109.322.551", None, "uid_ch", "CHE109322551"),
        ("51 824 753 556", "AU", "abn", "51824753556"),
        ("12-3456789", "US", "ein", "123456789"),
    ],
)
def test_one_structured_kind_matches(value, country, kind, stored):
    found = detect_kind(value, country=country)
    assert (found.kind, found.candidates, found.message) == (kind, (kind,), None)
    assert found.normalised.value == stored


def test_an_abn_shape_is_not_an_abn_outside_australia():
    """Eleven digits is an Italian VAT number in Italy and an ABN in
    Australia; the country decides, through `direct_entry.by_country`."""
    assert detect_kind("51824753556", country="IT").kind == "vat"
    assert detect_kind("51824753556", country="AU").kind == "abn"


@pytest.mark.parametrize(
    "value, country",
    [
        # An Austrian Steuernummer: no checkable format, no EU prefix.
        ("12 345/6789", "AT"),
        ("FN 123456a", "AT"),
        # No country anywhere and no prefix: nobody can tell.
        ("12345678", None),
    ],
)
def test_a_number_nothing_structured_matches_asks_what_kind_it_is(value, country):
    found = detect_kind(value, country=country)
    assert found.kind is None
    assert found.message is None
    assert found.candidates == tuple(load_formats()["direct_entry"]["fallback"])


@pytest.mark.parametrize("value", ["ATU1234", "DE12345", "ATX16360407"])
def test_a_number_claiming_an_eu_country_that_does_not_fit_is_refused_plainly(value):
    found = detect_kind(value, country="AT")
    assert found.kind is None and found.candidates == ()
    assert found.message
    # The normaliser's own sentence, which names the country it checked.
    assert "AT" in found.message or "DE" in found.message


@pytest.mark.parametrize(
    "country, example",
    [
        ("AT", "ATU12345678"),
        ("DE", "DE123456789"),
        ("GR", "EL123456789"),
        ("CH", "CHE109322551"),
        ("AU", "51824753556"),
        # Known kinds with no worked example: no placeholder, not a made-up one.
        ("US", None),
        ("SE", None),
        (None, None),
    ],
)
def test_the_placeholder_comes_from_examples_the_data_already_has(country, example):
    assert example_for(country) == example


# ─── The endpoint ───────────────────────────────────────────────────────────


def _h(world):
    return auth_headers(world["owner"], world["company"])


def _detect(pg_client, world, party_id, **body):
    response = pg_client.post(
        f"/parties/{party_id}/tax-ids/detect", headers=_h(world), json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_contacts_country_for_tax_reads_a_number_without_a_prefix(pg_client, pg_db, world):
    party = make_party(pg_db, world["company"], name="Wien GmbH", tax_country="AT")
    body = _detect(pg_client, world, party.id, value="U16360407")
    assert body == {
        "kind": "vat",
        "candidates": ["vat"],
        "value": "ATU16360407",
        "country": "AT",
        "verifiable": True,
        "message": None,
    }
    # Asking wrote nothing.
    assert pg_db.query(PartyTaxId).filter(PartyTaxId.party_id == party.id).count() == 0


def test_the_forms_country_wins_over_the_stored_one(pg_client, pg_db, world):
    party = make_party(pg_db, world["company"], name="Sydney Pty", tax_country="AT")
    assert _detect(pg_client, world, party.id, value="51824753556")["kind"] is None
    body = _detect(pg_client, world, party.id, value="51824753556", country="AU")
    assert (body["kind"], body["verifiable"]) == ("abn", True)


def test_an_inherited_country_for_tax_is_used(pg_client, pg_db, world):
    org = make_party(pg_db, world["company"], name="ORF", tax_country="AT")
    person = make_party(pg_db, world["company"], type="person", name="Maria Huber", parent=org)
    assert _detect(pg_client, world, person.id, value="U16360407")["value"] == "ATU16360407"


def test_the_ambiguous_and_the_invalid_answers_through_the_endpoint(pg_client, pg_db, world):
    party = make_party(pg_db, world["company"], name="Wien GmbH", tax_country="AT")
    ambiguous = _detect(pg_client, world, party.id, value="12 345/6789")
    assert ambiguous["kind"] is None
    assert ambiguous["candidates"] == ["tax_number", "company_register", "other"]
    invalid = _detect(pg_client, world, party.id, value="ATU1234")
    assert (invalid["kind"], invalid["candidates"]) == (None, [])
    assert invalid["message"]


def test_the_party_carries_the_placeholder_for_its_country(pg_client, pg_db, world):
    at = make_party(pg_db, world["company"], name="Wien GmbH", tax_country="AT")
    se = make_party(pg_db, world["company"], name="Stockholm AB", tax_country="SE")
    assert pg_client.get(f"/parties/{at.id}", headers=_h(world)).json()["tax_id_example"] == "ATU12345678"
    assert pg_client.get(f"/parties/{se.id}", headers=_h(world)).json()["tax_id_example"] is None


@pytest.mark.parametrize(
    "role, allowed",
    [
        (CompanyRole.owner, True),
        (CompanyRole.admin, True),
        (CompanyRole.producer, True),
        (CompanyRole.accountant, False),
        (CompanyRole.staff, False),
        (CompanyRole.tax_advisor, False),
    ],
)
def test_only_somebody_who_may_add_a_number_may_ask(pg_client, pg_db, world, role, allowed):
    party = make_party(pg_db, world["company"], name="Wien GmbH", tax_country="AT")
    member = make_user(pg_db, f"detect-{role.value}@example.com")
    grant(pg_db, world["company"], member, role)
    pg_db.commit()
    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/detect",
        headers=auth_headers(member, world["company"]),
        json={"value": "ATU16360407"},
    )
    assert (response.status_code == 200) is allowed, response.text
    if not allowed:
        assert response.status_code in (403, 404)


def test_another_companys_contact_is_404(pg_client, pg_db, world):
    other = make_company(pg_db, "Theirs OG", address_country="AT")
    theirs = make_party(pg_db, other, name="Theirs", tax_country="AT")
    response = pg_client.post(
        f"/parties/{theirs.id}/tax-ids/detect",
        headers=_h(world),
        json={"value": "ATU16360407"},
    )
    assert response.status_code == 404
