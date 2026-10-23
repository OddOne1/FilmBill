"""A company's OWN rates and no-tax cases, through the whole chain.

P1a proved the engine against the shipped packs. This proves it against rates
a user typed — which is the same engine, deliberately: `propose_tax` builds
its lookups from whatever classes and codes it is handed, so a key called
`custom_1` is not a special case, and the point of these tests is to show
that rather than to assume it.

**Driven end to end, through the API.** The rates are created with
`POST /company/tax/rates`, so one test covers the create path, the row, the
`tax_service.engine_tables` translation and the proposal together. A pure
table over hand-built dataclasses would prove the engine and skip the two
places a custom class could actually get lost: the class→code join that
resolves `code_id`, and the screen's own selector.

Expectations are written out. Nothing here is computed from the thing under
test.

**The decision this file pins:** a custom no-tax case is never chosen by a
shipped rule. That is structural, not a promise — a pack rule may only name a
code the pack itself defines (the loader refuses anything else) and a
company's own code is in no pack — and the last test says so by checking the
property rather than by trusting it.
"""

from decimal import Decimal

import pytest

from apps.api.billing.tax import PackError, load_pack
from apps.api.models.company import CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.services import tax_service

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
)

#: The three rates the fixture company adds, and the one no-tax case.
#: Deliberately not Austria's real numbers: a rate nobody could mistake for a
#: shipped one is a rate that cannot pass a test by coincidence.
CUSTOM_RATES = (
    ("Super-reduced", "4.5", "custom_1", "AT-4.5"),
    ("Events", "7.7", "custom_2", "AT-7.7"),
    ("Second standard", "23", "custom_3", "AT-23"),
)
EXEMPT_LABEL = "Exempt – training"
EXEMPT_CODE = "AT-E"
EXEMPT_NOTE_DE = "Steuerfrei: Bildungsleistung."
EXEMPT_NOTE_EN = "Exempt: training services."


@pytest.fixture
def world(pg_db, pg_client):
    """An Austrian company, its pack, three user rates and one no-tax case."""
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "custom-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()
    headers = auth_headers(owner, company)

    for label, rate, expected_key, expected_code in CUSTOM_RATES:
        created = pg_client.post(
            "/company/tax/rates",
            headers=headers,
            json={"label": label, "rate": rate},
        )
        assert created.status_code == 201, created.text
        # The fixture asserts its own setup: a test that silently got
        # `custom_4` would then be checking a class nothing else mentions.
        assert created.json()["tax_class"]["key"] == expected_key
        assert created.json()["tax_code"]["code"] == expected_code

    exempt = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=headers,
        json={
            "label": EXEMPT_LABEL,
            "category": "E",
            "legal_notes": {"de": EXEMPT_NOTE_DE, "en": EXEMPT_NOTE_EN},
        },
    )
    assert exempt.status_code == 201, exempt.text
    assert exempt.json()["code"] == EXEMPT_CODE

    return {"company": company, "owner": owner, "headers": headers}


def _propose(pg_client, world, **body):
    response = pg_client.post(
        "/company/tax/propose", headers=world["headers"], json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


# ─── A custom class resolves, everywhere ────────────────────────────────────


@pytest.mark.parametrize(
    "label, rate, key, code",
    CUSTOM_RATES,
    ids=[entry[2] for entry in CUSTOM_RATES],
)
def test_a_domestic_sale_in_a_custom_class_gets_that_class_code_and_rate(
    pg_client, world, label, rate, key, code
):
    """The whole point. `custom_1` is not a special case to the engine."""
    body = _propose(pg_client, world, buyer_country="AT", item_class=key)

    assert body["blocking"] is False
    assert body["treatment"] == "domestic"
    assert body["code"] == code
    assert body["rate"] == str(Decimal(rate).quantize(Decimal("0.0001")))
    assert body["applied_rule_position"] == 20
    assert body["warnings"] == []
    assert body["would_have_applied"] is None
    assert f"Tax code “{code}” applies at {rate} %." in body["reasons"]


def test_a_custom_class_is_offered_by_the_classes_endpoint(pg_client, world):
    """The screen's selector reads this, so a class the API does not serve is
    a class nobody can pick however well the engine handles it."""
    rows = pg_client.get(
        "/company/tax/classes", headers=world["headers"]
    ).json()
    by_key = {row["key"]: row for row in rows}

    for label, rate, key, code in CUSTOM_RATES:
        assert by_key[key]["label"] == label
        assert by_key[key]["code"] == code
        assert by_key[key]["is_active"] is True
        assert by_key[key]["is_custom"] is True
    # The pack's own classes are still there and still marked as the pack's.
    assert by_key["standard"]["is_custom"] is False


def test_a_verified_eu_business_buyer_still_reverse_charges(pg_client, world):
    """Unchanged by any of this: the shipped rule matches before the item's
    class is ever consulted, which is what "the rules decide the treatment
    and the class decides the domestic rate" means."""
    body = _propose(
        pg_client,
        world,
        buyer_country="DE",
        buyer_vat_verified=True,
        supply_kind="services",
        item_class="custom_1",
    )

    assert body["treatment"] == "eu_reverse_charge"
    assert body["code"] == "EU-AE"
    assert body["rate"] == "0.0000"
    assert body["applied_rule_position"] == 30


def test_an_unverified_eu_business_buyer_gets_the_custom_domestic_rate(
    pg_client, world
):
    """Rule 50 resolves `{class: "@item"}`, so the custom class is where the
    rate comes from — the one place a custom key reaches a shipped rule."""
    body = _propose(
        pg_client,
        world,
        buyer_country="DE",
        buyer_vat_verified=False,
        item_class="custom_3",
    )

    assert body["treatment"] == "domestic"
    assert body["code"] == "AT-23"
    assert body["rate"] == "23.0000"
    assert body["applied_rule_position"] == 50
    assert [warning["code"] for warning in body["warnings"]] == [
        "buyer_vat_id_not_verified"
    ]


def test_a_retired_custom_rate_stops_being_proposable(pg_client, pg_db, world):
    """And says so rather than guessing. Retiring switches the class and the
    code together, so the engine meets an inactive code and blocks."""
    from apps.api.models.tax import TaxClass

    retired = (
        pg_db.query(TaxClass)
        .filter(
            TaxClass.company_id == world["company"].id,
            TaxClass.key == "custom_2",
        )
        .one()
    )
    assert (
        pg_client.patch(
            f"/company/tax/classes/{retired.id}",
            headers=world["headers"],
            json={"is_active": False},
        ).status_code
        == 200
    )

    body = _propose(pg_client, world, buyer_country="AT", item_class="custom_2")

    assert body["blocking"] is True
    assert body["rate"] is None
    assert [warning["code"] for warning in body["warnings"]] == ["code_inactive"]


# ─── A custom no-tax case, chosen as an override ────────────────────────────


def test_a_custom_no_tax_case_as_an_item_override(pg_client, world):
    """Rate 0, the user's own note, and what the rules would have done.

    This is the shape SCOPE S8's yellow warning is built on, and the reason
    `would_have_applied` has to be populated: an invoice that silently
    dropped a 20 % line to 0 % is the single most expensive thing this
    screen could do quietly.
    """
    body = _propose(
        pg_client,
        world,
        buyer_country="AT",
        item_class="custom_1",
        item_override={"kind": "code", "code": EXEMPT_CODE},
        lang="de",
    )

    assert body["blocking"] is False
    assert body["code"] == EXEMPT_CODE
    assert body["rate"] == "0.0000"
    assert body["legal_note"] == EXEMPT_NOTE_DE
    assert body["applied_rule"] == "override:item"
    assert body["applied_rule_position"] is None
    # `custom`, because EN 16931 `E` means both "exempt" and the
    # small-business exemption — the engine will not pick between two
    # meanings of one letter, and a user-defined case is honestly custom.
    assert body["treatment"] == "custom"

    would = body["would_have_applied"]
    assert would is not None
    assert would["code"] == "AT-4.5"
    assert would["rate"] == "4.5000"
    assert would["treatment"] == "domestic"
    assert would["applied_rule_position"] == 20

    # The user's own wording is unreviewed like everything else.
    assert [warning["code"] for warning in body["warnings"]] == [
        "legal_note_not_reviewed"
    ]


def test_the_same_case_as_a_party_override(pg_client, world):
    body = _propose(
        pg_client,
        world,
        buyer_country="AT",
        item_class="custom_1",
        party_override={"kind": "code", "code": EXEMPT_CODE},
    )

    assert body["code"] == EXEMPT_CODE
    assert body["rate"] == "0.0000"
    assert body["applied_rule"] == "override:party"
    assert body["would_have_applied"]["code"] == "AT-4.5"


def test_the_item_override_beats_the_party_override(pg_client, world):
    """D48, on user-created codes. The item is the more specific statement."""
    body = _propose(
        pg_client,
        world,
        buyer_country="AT",
        item_class="custom_1",
        item_override={"kind": "code", "code": "AT-23"},
        party_override={"kind": "code", "code": EXEMPT_CODE},
    )

    assert body["code"] == "AT-23"
    assert body["rate"] == "23.0000"
    assert body["applied_rule"] == "override:item"
    assert body["would_have_applied"]["code"] == "AT-4.5"


def test_a_custom_no_tax_case_overrides_a_reverse_charge_too(pg_client, world):
    """An exemption the advisor named beats the shipped reverse charge, and
    the proposal still shows what it replaced."""
    body = _propose(
        pg_client,
        world,
        buyer_country="DE",
        buyer_vat_verified=True,
        supply_kind="services",
        item_class="custom_1",
        item_override={"kind": "code", "code": EXEMPT_CODE},
    )

    assert body["code"] == EXEMPT_CODE
    assert body["would_have_applied"]["code"] == "EU-AE"
    assert body["would_have_applied"]["treatment"] == "eu_reverse_charge"


def test_a_retired_no_tax_case_blocks_rather_than_being_ignored(
    pg_client, pg_db, world
):
    """Ignoring the override would charge the rules' answer on a line
    somebody had deliberately exempted — silently."""
    from apps.api.models.tax import TaxCode

    code = (
        pg_db.query(TaxCode)
        .filter(
            TaxCode.company_id == world["company"].id,
            TaxCode.code == EXEMPT_CODE,
        )
        .one()
    )
    assert (
        pg_client.patch(
            f"/company/tax/codes/{code.id}",
            headers=world["headers"],
            json={"is_active": False},
        ).status_code
        == 200
    )

    body = _propose(
        pg_client,
        world,
        buyer_country="AT",
        item_class="custom_1",
        item_override={"kind": "code", "code": EXEMPT_CODE},
    )

    assert body["blocking"] is True
    assert body["rate"] is None
    assert [warning["code"] for warning in body["warnings"]] == ["code_inactive"]


# ─── The decision: no shipped rule can pick a user's code ───────────────────


def test_no_shipped_rule_names_a_code_outside_its_own_pack():
    """Why a custom no-tax case can never be chosen automatically.

    It is structural rather than a promise: every shipped rule's outcome
    names either `@item` — the line's own class — or a code the pack itself
    defines, and the loader refuses anything else. A company's own code is in
    no pack, so no rule can reach it. It is selected explicitly, as an
    override.

    Checked over all three packs, as a property of the DATA rather than by
    trusting the sentence above.
    """
    for name in ("eu_generic", "at", "de"):
        pack = load_pack(name)
        defined = {entry.code for entry in pack.codes}
        keys = {entry.key for entry in pack.classes}
        for rule in pack.rules:
            (kind, value), = rule.outcome["code"].items()
            if kind == "code":
                assert value in defined, f"{name}/{rule.name} names {value}"
            else:
                assert value == "@item" or value in keys, (
                    f"{name}/{rule.name} names class {value}"
                )


def test_the_loader_is_what_makes_that_true(tmp_path):
    """The guard behind the property above, exercised directly.

    A pack that could name an undefined code could name a company's own one —
    which is exactly the mutation this earns its place against.
    """
    (tmp_path / "probe.yaml").write_text(
        'pack: probe\ncountries: []\n'
        'codes:\n  - code: XX-20\n    name: 20 %\n    rate: "20"\n'
        "    category: S\n"
        "classes:\n  - key: standard\n    label: 20 %\n    code: XX-20\n"
        "rules:\n  - position: 10\n    name: Reaches outside\n"
        "    conditions: {}\n    outcome:\n      treatment: custom\n"
        "      code: { code: AT-E }\n"
    )

    with pytest.raises(PackError) as caught:
        load_pack("probe", directory=tmp_path)
    assert "AT-E" in str(caught.value)
    assert "does not define" in str(caught.value)
