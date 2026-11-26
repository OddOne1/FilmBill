"""The Contacts list's columns, and who sees the internal ones (P1b-4 part 4).

Real PostgreSQL. The thing this file is ABOUT is what `GET /parties` puts on
the wire, per role — so it walks every role through the request the list
page makes and reads the JSON, rather than trusting the screen to hide a
column the API had already sent.

The internal columns — VAT check state, payment terms, tax status and the
last check date — are for `parties.edit` [Guessing, SCOPE]. Contact columns
(country, city, email, phone) need only `parties.view`.
"""

import pytest

from apps.api.models.company import CompanyRole
from apps.api.models.party import PartyContactPoint, PartyVatCheck
from apps.api.services import party_service

from .company_factories import (
    auth_headers,
    grant,
    make_party,
    make_party_address,
    make_user,
)
from .test_parties_api import world  # noqa: F401  (fixture)


@pytest.fixture
def berlin(pg_db, world):
    party = make_party(
        pg_db,
        world["company"],
        name="Berlin GmbH",
        tax_country="DE",
        vat_id="DE987654321",
        is_customer=True,
    )
    party.payment_terms_days = 30
    make_party_address(
        pg_db, world["company"], party, street="Torstraße 1", zip="10119", city="Berlin", country="DE"
    )
    for kind, value in (("email", "office@berlin.example"), ("phone", "+49 30 1")):
        pg_db.add(
            PartyContactPoint(
                company_id=world["company"].id,
                party_id=party.id,
                kind=kind,
                value=value,
                purpose="general",
                is_primary=True,
            )
        )
    tax_id = party_service.primary_tax_id(pg_db, party)
    pg_db.add(
        PartyVatCheck(
            company_id=world["company"].id,
            party_id=party.id,
            tax_id_id=tax_id.id,
            vat_id=tax_id.value,
            country="DE",
            kind="vat",
            provider="vies",
            status="valid",
            response={},
        )
    )
    pg_db.commit()
    return party


RESTRICTED = ("payment_terms_days", "buyer_type", "last_check_at")


@pytest.mark.parametrize(
    "role, can_view, sees_internal",
    [
        (CompanyRole.owner, True, True),
        (CompanyRole.admin, True, True),
        (CompanyRole.producer, True, True),
        (CompanyRole.accountant, True, False),
        (CompanyRole.staff, False, False),
        (CompanyRole.tax_advisor, False, False),
    ],
)
def test_the_internal_columns_reach_only_somebody_who_may_edit(
    pg_client, pg_db, world, berlin, role, can_view, sees_internal
):
    member = make_user(pg_db, f"columns-{role.value}@example.com")
    grant(pg_db, world["company"], member, role)
    pg_db.commit()
    response = pg_client.get("/parties", headers=auth_headers(member, world["company"]))
    if not can_view:
        # Missing permission answers 404 like everything else here.
        assert response.status_code in (403, 404), response.text
        return
    assert response.status_code == 200, response.text
    row = response.json()["parties"][0]

    # Contact columns: everybody who may view.
    assert (row["country"], row["city"]) == ("DE", "Berlin")
    assert (row["primary_email"], row["primary_phone"]) == ("office@berlin.example", "+49 30 1")

    if sees_internal:
        assert row["restricted_withheld"] is False
        assert row["vat_verified"] is True
        assert row["latest_vat_check"]["status"] == "valid"
        assert row["payment_terms_days"] == 30
        assert row["buyer_type"] == "business"
        assert row["last_check_at"] is not None
        assert [t["value"] for t in row["tax_ids"]] == ["DE987654321"]
    else:
        # Withheld, and SAYING so — not a "no VAT ID" that looks like data.
        assert row["restricted_withheld"] is True
        assert row["vat_verified"] is False
        assert row["latest_vat_check"] is None
        assert row["tax_ids"] == []
        for name in RESTRICTED:
            assert row[name] is None, name
        # Nowhere else in the row either.
        assert "DE987654321" not in response.text


def test_a_person_shows_their_own_city_not_the_organisations(pg_client, pg_db, world):
    org = make_party(pg_db, world["company"], name="ORF")
    make_party_address(pg_db, world["company"], org, city="Wien")
    make_party(pg_db, world["company"], type="person", name="Maria Huber", parent=org)
    rows = {
        p["name"]: p
        for p in pg_client.get(
            "/parties", headers=auth_headers(world["owner"], world["company"])
        ).json()["parties"]
    }
    assert rows["ORF"]["city"] == "Wien"
    assert rows["Maria Huber"]["city"] is None
