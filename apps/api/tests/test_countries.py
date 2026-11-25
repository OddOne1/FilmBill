"""Countries: one list, stored two letters, refused when unknown (B1.2 part 3).

The list is `apps/api/data/countries.json`. These tests pin that it is a
sound ISO 3166-1 table, that the web's copy is the same bytes, that every
country a region pack, a tax rule or a VAT check can name is in it, that the
VIES spelling of Greece stays where VIES is called, and that the API refuses
a country code that is not in it on every field that stores one.
"""

import json
import re
from pathlib import Path

import pytest

from apps.api.billing.parties import vat
from apps.api.billing.parties.tax_ids import normalise as normalise_tax_id
from apps.api.billing.tax.packs import available_packs, load_pack
from apps.api.billing.tax.regions import EU_MEMBER_STATES
from apps.api.core.countries import DATA_FILE, countries, country_codes, normalise_country
from apps.api.core.iban import IBAN_LENGTHS

from .test_budget_tracking_api import _headers, world  # noqa: F401 — fixtures

WEB_COPY = Path(__file__).resolve().parents[2] / "web" / "lib" / "countries.json"


# ─── The list ───────────────────────────────────────────────────────────────


def test_the_list_is_a_sound_iso_table():
    rows = countries()
    # ISO 3166-1's 249 officially assigned codes, plus Kosovo's XK — not
    # ISO-assigned, but the code IBANs, the EU and most banks use for it.
    assert len(rows) == 250
    for row in rows:
        assert re.fullmatch(r"[A-Z]{2}", row["alpha2"]), row
        assert re.fullmatch(r"[A-Z]{3}", row["alpha3"]), row
        assert row["name"].strip() == row["name"] and row["name"], row
    for key in ("alpha2", "alpha3", "name"):
        values = [row[key] for row in rows]
        assert len(values) == len(set(values)), f"duplicate {key}"
    by_code = {row["alpha2"]: row for row in rows}
    assert (by_code["AT"]["alpha3"], by_code["AT"]["name"]) == ("AUT", "Austria")
    assert (by_code["DE"]["alpha3"], by_code["GR"]["alpha3"]) == ("DEU", "GRC")
    assert (by_code["GB"]["alpha3"], by_code["CH"]["alpha3"]) == ("GBR", "CHE")
    assert (by_code["XK"]["alpha3"], by_code["XK"]["name"]) == ("XKX", "Kosovo")


def test_the_web_copy_is_the_same_file():
    """The web reads its own copy (its image does not contain the API's
    files); this is what keeps the two one list. Fix: copy
    apps/api/data/countries.json over apps/web/lib/countries.json."""
    assert WEB_COPY.read_bytes() == DATA_FILE.read_bytes()


def test_every_country_a_pack_or_rule_can_name_is_in_the_list():
    codes = country_codes()
    named: dict[str, str] = {}
    for name in available_packs():
        for country in load_pack(name).countries:
            named[country] = f"region pack {name}"
    for country in EU_MEMBER_STATES:
        named.setdefault(country, "EU_MEMBER_STATES (tax rules' buyer region)")
    for country in IBAN_LENGTHS:
        named.setdefault(country, "IBAN lengths")
    missing = {country: where for country, where in named.items() if country not in codes}
    assert missing == {}
    assert len(named) > 27, "the walk found almost nothing — is a source empty?"


def test_every_vat_prefix_maps_to_a_listed_country_except_northern_ireland():
    """The VAT formats are keyed by the VIES spelling. Mapped to ISO, each is
    a listed country — except XI, Northern Ireland's VAT prefix, which is
    not an ISO country and is deliberately not in the list."""
    prefixes = set(vat._formats()[0])
    assert {"EL", "AT", "DE"} <= prefixes
    unmapped = {
        prefix for prefix in prefixes if vat.iso_country(prefix) not in country_codes()
    }
    assert unmapped <= {"XI"}, unmapped


# ─── Greece: EL to VIES, GR everywhere else ─────────────────────────────────


def test_greece_is_gr_in_the_list_and_el_only_where_vies_is_called():
    assert "GR" in country_codes()
    assert "EL" not in country_codes()
    assert vat.vies_country("GR") == "EL"
    assert vat.iso_country("EL") == "GR"
    stored = normalise_tax_id("vat", "EL123456789")
    assert stored.country == "GR"


def test_el_is_refused_as_a_stored_country():
    with pytest.raises(ValueError):
        normalise_country("EL")


@pytest.mark.parametrize(
    "given, stored", [("at", "AT"), (" de ", "DE"), ("", None), (None, None)]
)
def test_normalise_trims_and_upper_cases(given, stored):
    assert normalise_country(given) == stored


def test_an_unknown_code_is_refused_with_a_sentence():
    with pytest.raises(ValueError) as caught:
        normalise_country("XX")
    assert "XX" in str(caught.value) and "for example AT" in str(caught.value)


# ─── Every field that stores a country refuses an unknown one ───────────────


def test_the_company_country_is_validated(pg_client, world):
    """Refused as a 422 before anything else is looked at — including the
    "this company already has tax codes" 409 a real change would meet."""
    response = pg_client.patch(
        "/company", headers=_headers(world), json={"address_country": "XX"}
    )
    assert response.status_code == 422, response.text
    assert "is not a country code" in response.text


def test_a_new_company_country_is_validated(pg_client, world):
    response = pg_client.post(
        "/companies",
        headers=_headers(world),
        json={"legal_name": "Nowhere OG", "address_country": "QQ"},
    )
    assert response.status_code == 422, response.text


def _party(pg_client, world, **body):
    body.setdefault("type", "organisation")
    body.setdefault("name", "Kunde GmbH")
    return pg_client.post("/parties", headers=_headers(world), json=body)


def test_a_party_tax_country_is_validated(pg_client, world):
    assert _party(pg_client, world, tax_country="ZZ").status_code == 422
    response = _party(pg_client, world, tax_country="de")
    assert response.status_code == 201, response.text
    assert response.json()["tax_country"] == "DE"


def test_a_party_address_country_is_validated(pg_client, world):
    party = _party(pg_client, world).json()
    response = pg_client.post(
        f"/parties/{party['id']}/addresses",
        headers=_headers(world),
        json={"kind": "billing", "city": "Wien", "country": "OO"},
    )
    assert response.status_code == 422, response.text
    response = pg_client.post(
        f"/parties/{party['id']}/addresses",
        headers=_headers(world),
        json={"kind": "billing", "city": "Wien", "country": "at"},
    )
    assert response.status_code == 201, response.text
    (address,) = [row for row in response.json()["addresses"] if row["city"] == "Wien"]
    assert address["country"] == "AT"


def test_the_tax_test_case_buyer_country_is_validated(pg_client, world):
    response = pg_client.post(
        "/company/tax/propose",
        headers=_headers(world),
        json={"buyer_country": "XY"},
    )
    assert response.status_code == 422, response.text


def test_a_country_change_to_an_unknown_code_is_refused(pg_client, world):
    response = pg_client.post(
        "/company/tax/country-change/preview",
        headers=_headers(world),
        json={"country": "XY", "mode": "apply_pack"},
    )
    assert response.status_code == 422, response.text


def test_the_web_copy_parses_like_the_api_file():
    assert json.loads(WEB_COPY.read_text(encoding="utf-8"))["countries"][0]["alpha2"] == "AD"
