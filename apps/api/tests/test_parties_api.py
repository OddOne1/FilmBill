"""The parties endpoints, against a real PostgreSQL.

Fourteen endpoints and no DELETE, which is asserted on the OpenAPI document
rather than on a handler's behaviour — the same shape as P1a-2's, and the
same reason: the way to keep "nothing is ever deleted" true is for there to
be no route.

Three themes:

  * **who may do what**, over the whole role ladder rather than a sample.
    View is owner/admin/accountant/producer (a producer sends the invoice, so
    a producer sees who it goes to); edit drops the accountant, because a
    party's address and VAT ID end up snapshotted on a document.
  * **the tax preview is the same engine**, with the buyer side built from
    the resolved recipient. The web computes no tax anywhere, and the test
    compares the endpoint's answer with the engine's for the same inputs.
  * **a party's tax override is a referrer**, so deactivating the code it
    points at is refused with the customer named. That is the P1a-2 registry
    doing its job through a source registered by this module.
"""

import uuid

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.party import Party, PartyVatCheck
from apps.api.models.site_settings import SiteSettings
from apps.api.models.tax import TaxCode
from apps.api.services import party_service, tax_service

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_party,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    """An Austrian company with its tax pack, number series and one owner."""
    from apps.api.services.number_series import seed_number_series

    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "parties-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    # The session too, so helpers that must write a row directly use the
    # same one the client does rather than opening a second.
    return {"company": company, "owner": owner, "db": pg_db}


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _create(pg_client, world, **body):
    """Create a party through the endpoint; add a tax ID if one was asked for.

    `vat_id` is no longer a field on `POST /parties` — identifiers are their
    own rows with their own endpoints (D59) — but these tests are about
    buyer types, duplicates, search and audit, not about how a number gets
    written. So the parameter is kept and the row is created directly, which
    keeps each test exercising its own subject.

    The endpoint that writes identifiers has its own tests in
    `test_party_tax_ids.py`, including the normalisation and the refusals
    that used to be asserted here. That is deliberate and not a gap: an
    assertion about `POST /parties` refusing a malformed VAT ID would now be
    asserting that FastAPI rejects an unknown field, which proves nothing
    about the normalising.
    """
    vat_id = body.pop("vat_id", None)
    tax_id_kind = body.pop("tax_id_kind", "vat")
    payload = {"type": "organisation", "name": "ORF"}
    payload.update(body)
    response = pg_client.post("/parties", headers=_headers(world), json=payload)
    assert response.status_code == 201, response.text
    created = response.json()

    if vat_id:
        from apps.api.models.party import PartyTaxId

        db = world["db"]
        db.add(
            PartyTaxId(
                company_id=world["company"].id,
                party_id=uuid.UUID(created["id"]),
                kind=tax_id_kind,
                country=(
                    "CH"
                    if tax_id_kind == "uid_ch"
                    else vat_id[:2]
                    if len(vat_id) >= 2 and vat_id[:2].isalpha()
                    else None
                ),
                value=vat_id,
                value_as_entered=vat_id,
                is_primary=True,
            )
        )
        db.commit()
        created = pg_client.get(
            f"/parties/{created['id']}", headers=_headers(world)
        ).json()
    return created


def _mark_verified(pg_db, world, party, *, status="valid", value="DE123456789"):
    """Record a completed check against the party's primary identifier.

    Pointed at the identifier row, which is what makes it count (D59): a
    check with a NULL `tax_id_id` is not evidence about anything, and while
    this helper left it NULL every "not verified" assertion in the file
    passed because no check could be found rather than because the check
    said no — trap 18, found here.
    """
    row = pg_db.query(Party).filter(Party.id == uuid.UUID(party["id"])).one()
    tax_id = party_service.primary_tax_id(pg_db, row)
    assert tax_id is not None, "the party has no identifier to check"
    pg_db.add(
        PartyVatCheck(
            company_id=world["company"].id,
            party_id=row.id,
            tax_id_id=tax_id.id,
            vat_id=value,
            country=tax_id.country,
            kind=tax_id.kind,
            provider="vies",
            status=status,
            response={},
        )
    )
    pg_db.commit()
    return row


def _events(pg_db, action: str) -> list[AuditEvent]:
    return (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == action)
        .order_by(AuditEvent.created_at)
        .all()
    )


# ─── Create, read, update ───────────────────────────────────────────────────


def test_creating_an_organisation_allocates_a_customer_number(pg_client, world):
    body = _create(pg_client, world, name="ORF", is_customer=True)

    assert body["type"] == "organisation"
    assert body["party_number"] == "K-0001"
    assert body["is_active"] is True
    # Derived, not stored: an organisation is a business.
    assert body["buyer_type"] == "business"
    assert body["buyer_type_reason"] == "organisation"


def test_a_party_that_is_neither_gets_no_number(pg_client, world):
    body = _create(pg_client, world, type="person", name="Maria Huber")

    assert body["party_number"] is None
    assert body["buyer_type"] == "private"
    assert body["buyer_type_reason"] == "person"


def test_a_person_with_a_vat_id_is_a_business(pg_client, world):
    """A sole trader. §17 asks whether that is the right reading."""
    body = _create(
        pg_client, world, type="person", name="Maria Huber", vat_id="ATU12345678"
    )

    assert body["buyer_type"] == "business"
    assert body["buyer_type_reason"] == "person_with_vat_id"


# `test_the_vat_id_is_normalised_on_write`,
# `test_a_malformed_vat_id_is_refused_with_a_sentence` and
# `test_a_swiss_number_is_accepted_as_free_text` lived here until P1b-2.
# They moved to `test_party_tax_ids.py`, with the write path they are
# about: `POST /parties` no longer accepts a number at all, so asserting
# a refusal here would only prove that FastAPI rejects an unknown field —
# which says nothing about the normalising and would read as coverage
# while being none (rule 17b).

def test_a_department_needs_an_organisation(pg_client, world):
    response = pg_client.post(
        "/parties",
        headers=_headers(world),
        json={"type": "department", "name": "Kultur"},
    )
    assert response.status_code == 409, response.text
    assert "belongs to an organisation" in response.json()["detail"]


def test_a_department_under_a_person_is_refused_by_name(pg_client, world):
    person = _create(pg_client, world, type="person", name="Maria")

    response = pg_client.post(
        "/parties",
        headers=_headers(world),
        json={"type": "department", "name": "Kultur", "parent_id": person["id"]},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "cannot belong to a person" in detail
    assert "Maria" in detail


def test_another_companys_parent_is_404(pg_client, pg_db, world):
    other = make_company(pg_db, "Somebody Else GmbH")
    theirs = make_party(pg_db, other, name="Their ORF")

    response = pg_client.post(
        "/parties",
        headers=_headers(world),
        json={"type": "person", "name": "Maria", "parent_id": str(theirs.id)},
    )

    assert response.status_code == 404, response.text


def test_the_type_cannot_be_changed(pg_client, world):
    """Turning a person into an organisation would have to re-validate its
    parent, its children and its number. `extra="forbid"` says so."""
    party = _create(pg_client, world, type="person", name="Maria")

    response = pg_client.patch(
        f"/parties/{party['id']}",
        headers=_headers(world),
        json={"type": "organisation"},
    )

    assert response.status_code == 422, response.text
    assert "type" in response.text


def test_a_party_number_stays_editable(pg_client, world):
    """A company migrating from another system has its own numbers."""
    party = _create(pg_client, world, is_customer=True)

    response = pg_client.patch(
        f"/parties/{party['id']}",
        headers=_headers(world),
        json={"party_number": "ALT-42"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["party_number"] == "ALT-42"


def test_the_sub_resources_come_with_the_party(pg_client, world):
    """Three round trips to render a detail page is three chances for a
    half-loaded screen."""
    party = _create(pg_client, world)
    pg_client.post(
        f"/parties/{party['id']}/addresses",
        headers=_headers(world),
        json={
            "kind": "billing",
            "street": "Würzburggasse 30",
            "zip": "1136",
            "city": "Wien",
            "country": "AT",
            "is_default": True,
        },
    )
    pg_client.post(
        f"/parties/{party['id']}/contact-points",
        headers=_headers(world),
        json={"kind": "email", "value": "office@orf.at", "is_primary": True},
    )

    body = pg_client.get(
        f"/parties/{party['id']}", headers=_headers(world)
    ).json()

    assert [row["city"] for row in body["addresses"]] == ["Wien"]
    assert [row["value"] for row in body["contact_points"]] == ["office@orf.at"]


def test_adding_a_default_address_demotes_the_previous_one(pg_client, world):
    """A partial unique index refuses two defaults, so the endpoint has to
    clear the old one rather than hand the user an IntegrityError."""
    party = _create(pg_client, world)
    for street in ("First", "Second"):
        response = pg_client.post(
            f"/parties/{party['id']}/addresses",
            headers=_headers(world),
            json={"kind": "billing", "street": street, "is_default": True},
        )
        assert response.status_code == 201, response.text

    body = pg_client.get(f"/parties/{party['id']}", headers=_headers(world)).json()
    defaults = [row for row in body["addresses"] if row["is_default"]]
    assert [row["street"] for row in defaults] == ["Second"]


# ─── The hierarchy and the resolved recipient ───────────────────────────────


@pytest.fixture
def orf(pg_client, world):
    """The S5 chain, through the API."""
    org = _create(pg_client, world, name="ORF", is_customer=True, vat_id="ATU16360407")
    pg_client.post(
        f"/parties/{org['id']}/addresses",
        headers=_headers(world),
        json={
            "kind": "billing",
            "street": "Würzburggasse 30",
            "zip": "1136",
            "city": "Wien",
            "country": "AT",
            "is_default": True,
        },
    )
    dept = _create(
        pg_client, world, type="department", name="Redaktion Kultur",
        parent_id=org["id"],
    )
    person = _create(
        pg_client, world, type="person", name="Maria Huber", parent_id=dept["id"]
    )
    return {"org": org, "dept": dept, "person": person}


def test_the_hierarchy_is_listed_from_the_organisation(pg_client, world, orf):
    body = pg_client.get(
        f"/parties/{orf['org']['id']}/children", headers=_headers(world)
    ).json()

    assert [row["name"] for row in body] == ["Redaktion Kultur"]


def test_the_resolved_recipient_carries_every_source(pg_client, world, orf):
    """Acceptance item 3: the screen greys an inherited value and names its
    origin, which it can only do if the API says where each came from."""
    body = pg_client.get(
        f"/parties/{orf['person']['id']}/resolved", headers=_headers(world)
    ).json()

    assert body["own_level"] == "person"
    assert body["contact_name"] == {"value": "Maria Huber", "source": "person"}
    assert body["invoice_name"]["value"] == "ORF"
    assert body["invoice_name"]["source"] == "organisation"
    assert body["billing_address"]["source"] == "organisation"
    assert body["billing_address"]["value"]["city"] == "Wien"
    assert body["vat_id"] == {"value": "ATU16360407", "source": "organisation"}
    assert body["names"]["organisation"] == "ORF"
    assert body["buyer_type"] == "business"


def test_a_departments_own_address_wins(pg_client, world, orf):
    """Acceptance item 4."""
    pg_client.post(
        f"/parties/{orf['dept']['id']}/addresses",
        headers=_headers(world),
        json={
            "kind": "billing",
            "street": "Argentinierstraße 30a",
            "zip": "1040",
            "city": "Wien",
            "country": "AT",
            "is_default": True,
        },
    )

    body = pg_client.get(
        f"/parties/{orf['person']['id']}/resolved", headers=_headers(world)
    ).json()

    assert body["billing_address"]["source"] == "department"
    assert body["billing_address"]["value"]["street"] == "Argentinierstraße 30a"


# ─── Archiving ──────────────────────────────────────────────────────────────


def test_archiving_an_organisation_with_active_children_is_refused_by_name(
    pg_client, world, orf
):
    """Acceptance item 7's second half. "It has children" leaves somebody
    opening rows one at a time to find out which."""
    response = pg_client.post(
        f"/parties/{orf['org']['id']}/archive", headers=_headers(world)
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "Redaktion Kultur" in detail
    assert "never deletes" in detail


def test_archiving_works_once_the_children_are_archived(pg_client, world, orf):
    for key in ("person", "dept", "org"):
        response = pg_client.post(
            f"/parties/{orf[key]['id']}/archive", headers=_headers(world)
        )
        assert response.status_code == 200, (key, response.text)
        assert response.json()["is_active"] is False


def test_an_archived_party_can_be_reactivated(pg_client, world):
    party = _create(pg_client, world, type="person", name="Maria")
    pg_client.post(f"/parties/{party['id']}/archive", headers=_headers(world))

    response = pg_client.post(
        f"/parties/{party['id']}/reactivate", headers=_headers(world)
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True


def test_there_is_no_delete_route_anywhere_under_parties(pg_client):
    """Asserted on the OpenAPI document, which is the API's own statement of
    what exists. Nothing is ever deleted, and the way to keep that true is
    for there to be no route rather than for a handler to refuse."""
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    party_paths = {
        path: set(operations)
        for path, operations in schema["paths"].items()
        if path.startswith("/parties")
    }
    assert len(party_paths) >= 10, "this test stopped covering the party API"
    for path, methods in party_paths.items():
        assert "delete" not in methods, f"{path} has a DELETE"


# ─── The list and its search ────────────────────────────────────────────────


def test_the_search_covers_five_fields(pg_client, world):
    """Somebody looking for a customer types what they remember and does not
    know which field it was."""
    org = _create(
        pg_client, world, name="Österreichischer Rundfunk",
        trading_name="ORF", is_customer=True, vat_id="ATU16360407",
    )
    pg_client.post(
        f"/parties/{org['id']}/contact-points",
        headers=_headers(world),
        json={"kind": "email", "value": "office@orf.at", "is_primary": True},
    )
    _create(pg_client, world, name="Kamera GmbH", is_supplier=True)

    for needle in ("rundfunk", "ORF", "K-0001", "ATU163", "office@orf"):
        body = pg_client.get(
            f"/parties?search={needle}", headers=_headers(world)
        ).json()
        assert [row["id"] for row in body["parties"]] == [org["id"]], needle


def test_the_role_filters_narrow_the_list(pg_client, world):
    _create(pg_client, world, name="A customer", is_customer=True)
    _create(pg_client, world, name="A supplier", is_supplier=True)
    _create(pg_client, world, type="person", name="A person")

    for role, expected in (
        ("customer", ["A customer"]),
        ("supplier", ["A supplier"]),
        ("person", ["A person"]),
    ):
        body = pg_client.get(
            f"/parties?role={role}", headers=_headers(world)
        ).json()
        assert [row["name"] for row in body["parties"]] == expected, role


def test_archived_parties_are_hidden_unless_asked_for(pg_client, world):
    party = _create(pg_client, world, type="person", name="Gone")
    pg_client.post(f"/parties/{party['id']}/archive", headers=_headers(world))

    visible = pg_client.get("/parties", headers=_headers(world)).json()
    assert visible["total"] == 0

    all_of_them = pg_client.get(
        "/parties?include_inactive=true", headers=_headers(world)
    ).json()
    assert all_of_them["total"] == 1


def test_the_list_pages(pg_client, world):
    for index in range(5):
        _create(pg_client, world, type="person", name=f"Person {index}")

    body = pg_client.get("/parties?limit=2&offset=2", headers=_headers(world)).json()

    assert body["total"] == 5
    assert len(body["parties"]) == 2
    assert body["offset"] == 2


# ─── Duplicates: warnings, never refusals ───────────────────────────────────


def test_the_same_tax_id_warns_and_still_saves(pg_db, world):
    """Blocking a create on a guess is worse than a duplicate: somebody who
    cannot save a customer cannot invoice them.

    Asserted on the service rather than on `POST /parties`, for the same
    reason the email signal below it is: the create body does not carry a
    number any more (D59 — identifiers are their own rows), so the router has
    nothing to pass. The endpoint that DOES carry one returns its own
    duplicate warning, tested in `test_party_tax_ids.py`.
    """
    first = make_party(pg_db, world["company"], name="ORF", vat_id="ATU16360407")
    second = make_party(pg_db, world["company"], name="ORF Enterprise")
    pg_db.commit()

    found = party_service.find_duplicates(
        pg_db,
        company_id=world["company"].id,
        vat_id="ATU16360407",
        exclude_id=second.id,
    )

    assert [entry["reason"] for entry in found] == ["vat_id"]
    assert found[0]["party_id"] == first.id
    assert "ATU16360407" in found[0]["detail"]
    # And it says which KIND, which the old single column could not.
    assert "vat identifier" in found[0]["detail"]


def test_a_retired_identifier_still_warns_and_says_it_is_retired(pg_db, world):
    """The mix-up this exists to catch: a number retired on one party and
    typed onto another. Silence there would let two parties hold the same
    number with nobody told."""
    from .company_factories import make_party_tax_id

    first = make_party(pg_db, world["company"], name="Alt GmbH")
    make_party_tax_id(
        pg_db,
        world["company"],
        first,
        value="ATU16360407",
        country="AT",
        is_active=False,
    )
    second = make_party(pg_db, world["company"], name="Neu GmbH")
    pg_db.commit()

    found = party_service.find_duplicates(
        pg_db,
        company_id=world["company"].id,
        vat_id="ATU16360407",
        exclude_id=second.id,
    )

    assert [entry["reason"] for entry in found] == ["vat_id"]
    assert "retired" in found[0]["detail"]


def test_the_same_email_warns_case_insensitively(pg_db, world):
    """Asserted on the service, because the email signal needs an email to
    compare and the create body does not carry one — a contact point is its
    own request. The router passes whatever it has; this is the rule itself.
    """
    first = make_party(pg_db, world["company"], name="ORF")
    from apps.api.models.party import PartyContactPoint

    pg_db.add(
        PartyContactPoint(
            company_id=world["company"].id,
            party_id=first.id,
            kind="email",
            value="Office@ORF.at",
            purpose="general",
            is_primary=True,
        )
    )
    second = make_party(pg_db, world["company"], name="Another one")
    pg_db.commit()

    found = party_service.find_duplicates(
        pg_db,
        company_id=world["company"].id,
        email="office@orf.at",
        exclude_id=second.id,
    )

    assert [entry["reason"] for entry in found] == ["email"]
    assert found[0]["party_id"] == first.id


def test_the_same_name_and_postcode_is_a_hint_not_a_certainty(pg_db, world):
    """A holding company and its subsidiary legitimately share both, which is
    why this one is a warning and the merge tool is later."""
    from .company_factories import make_party_address

    first = make_party(pg_db, world["company"], name="Film GmbH")
    make_party_address(pg_db, world["company"], first, zip="1010")
    second = make_party(pg_db, world["company"], name="Film GmbH")
    pg_db.commit()

    found = party_service.find_duplicates(
        pg_db,
        company_id=world["company"].id,
        name="film gmbh",
        zip_code="1010",
        exclude_id=second.id,
    )

    assert [entry["reason"] for entry in found] == ["name_and_zip"]


def test_a_duplicate_warning_never_blocks_an_edit(pg_client, world):
    """A warning on a PATCH is still only a warning.

    The signal is the party's own primary identifier now, so this creates
    one on each party and edits the second — which is the shape a user
    actually produces: two customers that turn out to share a number.
    """
    first = _create(pg_client, world, name="ORF", vat_id="ATU16360407")
    other = _create(pg_client, world, name="Other", vat_id="ATU16360407")

    response = pg_client.patch(
        f"/parties/{other['id']}",
        headers=_headers(world),
        json={"name": "Other GmbH"},
    )

    assert response.status_code == 200, response.text
    warnings = response.json()["duplicates"]
    assert warnings, "no warning at all"
    assert warnings[0]["party_id"] == first["id"]


# ─── Permissions and isolation ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "role, expected",
    [
        (CompanyRole.owner, 200),
        (CompanyRole.admin, 200),
        (CompanyRole.accountant, 200),
        (CompanyRole.producer, 200),
        (CompanyRole.staff, 404),
        (CompanyRole.tax_advisor, 404),
    ],
)
def test_who_may_read_the_party_list(pg_client, pg_db, world, role, expected):
    """A producer sends the invoice, so a producer sees who it goes to. The
    tax advisor's access is the ARCHIVE, where the recipient is already
    snapshotted on the document they are reading."""
    company = world["company"]
    member = make_user(pg_db, f"reader-{role.value}@example.com")
    grant(pg_db, company, member, role)
    pg_db.commit()

    response = pg_client.get("/parties", headers=auth_headers(member, company))
    assert response.status_code == expected, response.text


@pytest.mark.parametrize(
    "role, expected",
    [
        (CompanyRole.owner, 201),
        (CompanyRole.admin, 201),
        (CompanyRole.producer, 201),
        (CompanyRole.accountant, 404),
        (CompanyRole.staff, 404),
        (CompanyRole.tax_advisor, 404),
    ],
)
def test_who_may_create_a_party(pg_client, pg_db, world, role, expected):
    """Edit drops the accountant: a party's address and VAT ID end up
    snapshotted on a document, so changing one is structural."""
    company = world["company"]
    member = make_user(pg_db, f"writer-{role.value}@example.com")
    grant(pg_db, company, member, role)
    pg_db.commit()

    response = pg_client.post(
        "/parties",
        headers=auth_headers(member, company),
        json={"type": "organisation", "name": f"By {role.value}"},
    )
    assert response.status_code == expected, response.text


def test_another_companys_party_is_404(pg_client, pg_db, world):
    other = make_company(pg_db, "Somebody Else GmbH")
    theirs = make_party(pg_db, other, name="Their customer")

    for method, path in (
        ("get", f"/parties/{theirs.id}"),
        ("patch", f"/parties/{theirs.id}"),
        ("get", f"/parties/{theirs.id}/resolved"),
        ("post", f"/parties/{theirs.id}/archive"),
    ):
        response = pg_client.request(
            method,
            path,
            headers=_headers(world),
            json={"name": "Stolen"} if method == "patch" else None,
        )
        assert response.status_code == 404, (path, response.text)


# ─── Audit ──────────────────────────────────────────────────────────────────


def test_creating_a_party_writes_an_event_with_the_values(pg_client, pg_db, world):
    _create(pg_client, world, name="ORF", is_customer=True, vat_id="ATU16360407")

    events = _events(pg_db, "party.created")
    assert len(events) == 1
    changes = events[0].changes
    assert changes["name"] == [None, "ORF"]
    assert changes["type"] == [None, "organisation"]
    assert changes["is_customer"] == [None, True]
    # `vat_id` is deliberately NOT here: it is not a party field any more,
    # and a change to an identifier is audited on the identifier itself
    # under `party_tax_id.*`, which says which kind changed and keeps the
    # old value as a retired row (D59).
    assert "vat_id" not in changes
    assert events[0].entity_type == "parties"


def test_an_edit_writes_a_diff(pg_client, pg_db, world):
    party = _create(pg_client, world, name="ORF")

    pg_client.patch(
        f"/parties/{party['id']}",
        headers=_headers(world),
        json={"name": "Österreichischer Rundfunk"},
    )

    events = _events(pg_db, "party.updated")
    assert len(events) == 1
    assert events[0].changes["name"] == ["ORF", "Österreichischer Rundfunk"]


def test_an_address_change_is_recorded_inside_the_partys_own_diff(
    pg_client, pg_db, world
):
    """"What changed about this customer" is the question, and three event
    types for one answer makes the log harder to read rather than richer."""
    party = _create(pg_client, world)

    pg_client.post(
        f"/parties/{party['id']}/addresses",
        headers=_headers(world),
        json={"kind": "billing", "street": "Würzburggasse 30", "city": "Wien"},
    )

    events = _events(pg_db, "party.updated")
    assert len(events) == 1
    assert "addresses" in events[0].changes
    assert "Würzburggasse 30" in str(events[0].changes["addresses"])


def test_archiving_and_reactivating_are_their_own_events(pg_client, pg_db, world):
    party = _create(pg_client, world, type="person", name="Maria")

    pg_client.post(f"/parties/{party['id']}/archive", headers=_headers(world))
    pg_client.post(f"/parties/{party['id']}/reactivate", headers=_headers(world))

    assert len(_events(pg_db, "party.archived")) == 1
    assert len(_events(pg_db, "party.reactivated")) == 1
    assert _events(pg_db, "party.archived")[0].changes["is_active"] == [True, False]


def test_a_party_iban_is_masked_in_the_audit_row(pg_client, pg_db, world):
    """Exactly like the company's own (D43): an audit log must not become a
    way round the gate on the thing it describes. A supplier's account is as
    much a payment instruction as the company's."""
    party = _create(pg_client, world, name="Kamera GmbH", is_supplier=True)

    response = pg_client.post(
        f"/parties/{party['id']}/bank-accounts",
        headers=_headers(world),
        json={"iban": "AT61 1904 3002 3457 3201", "label": "Haupt"},
    )
    assert response.status_code == 201, response.text
    # Served in full to somebody holding `parties.view`.
    assert response.json()["bank_accounts"][0]["iban"] == "AT611904300234573201"

    event = _events(pg_db, "party_bank_account.created")[0]
    assert event.changes["iban"] == [None, "AT…3201"]
    assert "1904300234573201" not in str(event.changes)


def test_a_refused_create_leaves_no_event(pg_client, pg_db, world):
    assert (
        pg_client.post(
            "/parties",
            headers=_headers(world),
            json={"type": "organisation", "name": "Doomed", "vat_id": "ATU1"},
        ).status_code
        == 422
    )

    assert _events(pg_db, "party.created") == []


# ─── The tax preview: the same engine ───────────────────────────────────────


def _preview(pg_client, world, party_id, **body):
    payload = {"supply_kind": "services"}
    payload.update(body)
    response = pg_client.post(
        f"/parties/{party_id}/tax-preview", headers=_headers(world), json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_an_austrian_organisation_gets_the_home_rate(pg_client, world, orf):
    body = _preview(pg_client, world, orf["org"]["id"])

    assert body["code"] == "AT-20"
    assert body["treatment"] == "domestic"
    assert body["seller_country"] == "AT"


def test_a_german_organisation_with_a_verified_id_reverse_charges(
    pg_client, pg_db, world
):
    """Acceptance item 6. Verified means: the latest check of the CURRENT
    number said valid."""
    party = _create(
        pg_client, world, name="Berlin GmbH", tax_country="DE",
        vat_id="DE123456789",
    )
    row = _mark_verified(
        pg_db, world, party, status="valid", value="DE123456789"
    )

    body = _preview(pg_client, world, party["id"])

    assert body["code"] == "EU-AE"
    assert body["treatment"] == "eu_reverse_charge"


def test_the_same_party_unverified_gets_the_home_rate_and_a_warning(
    pg_client, world
):
    party = _create(
        pg_client, world, name="Berlin GmbH", tax_country="DE",
        vat_id="DE123456789",
    )

    body = _preview(pg_client, world, party["id"])

    assert body["code"] == "AT-20"
    assert [w["code"] for w in body["warnings"]] == ["buyer_vat_id_not_verified"]


def test_a_swiss_organisation_is_not_taxable_for_services_and_export_for_goods(
    pg_client, world
):
    party = _create(pg_client, world, name="Zürich AG", tax_country="CH")

    assert _preview(pg_client, world, party["id"])["code"] == "EU-O"
    assert (
        _preview(pg_client, world, party["id"], supply_kind="goods")["code"]
        == "EU-G"
    )


def test_a_private_person_abroad_gets_the_home_rate(pg_client, world):
    party = _create(
        pg_client, world, type="person", name="Hans Muster", tax_country="CH"
    )

    body = _preview(pg_client, world, party["id"])

    assert body["code"] == "AT-20"
    assert body["treatment"] == "domestic"


def test_a_department_inherits_its_organisations_verified_id(
    pg_client, pg_db, world
):
    """The inheritance and the verified flag travelling together, end to end."""
    org = _create(
        pg_client, world, name="Berlin GmbH", tax_country="DE",
        vat_id="DE123456789",
    )
    row = _mark_verified(
        pg_db, world, org, status="valid", value="DE123456789"
    )
    dept = _create(
        pg_client, world, type="department", name="Einkauf", parent_id=org["id"]
    )

    body = _preview(pg_client, world, dept["id"])

    assert body["code"] == "EU-AE", "the department did not inherit the ID"


def test_a_party_override_is_honoured_and_shows_what_it_replaced(
    pg_client, pg_db, world
):
    """SCOPE S2b through a party: "Austrian VAT whatever the customer".

    The customer is a VERIFIED German business on purpose. Unverified, the
    rules would answer the domestic rate anyway — the same code the override
    gives — and `would_have_applied` would correctly be null, which is what
    an earlier version of this test asserted the opposite of. Verified, the
    rules say reverse charge and the override overrules it, which is the case
    P2's yellow warning exists for.
    """
    party = _create(
        pg_client,
        world,
        name="Studio customer",
        tax_country="DE",
        vat_id="DE123456789",
        tax_override={"kind": "domestic_for_foreign_business"},
    )
    row = _mark_verified(
        pg_db, world, party, status="valid", value="DE123456789"
    )

    body = _preview(pg_client, world, party["id"])

    assert body["code"] == "AT-20"
    assert body["applied_rule"] == "override:party"
    assert body["would_have_applied"]["code"] == "EU-AE"
    assert body["would_have_applied"]["treatment"] == "eu_reverse_charge"


def test_an_override_that_changes_nothing_reports_nothing(pg_client, world):
    """The other half, and the reason the test above needs a verified ID: an
    unverified EU business already gets the domestic rate, so the override
    agrees with the rules and there is nothing to warn about."""
    party = _create(
        pg_client,
        world,
        name="Studio customer",
        tax_country="DE",
        tax_override={"kind": "domestic_for_foreign_business"},
    )

    body = _preview(pg_client, world, party["id"])

    assert body["code"] == "AT-20"
    assert body["would_have_applied"] is None


def test_the_preview_matches_the_engine_for_the_same_inputs(
    pg_client, pg_db, world, orf
):
    """The web computes no tax anywhere, so the party screen's answer and the
    Tax screen's come from one function with one set of inputs. Compared
    field by field, including the reasons — which is what would diverge first
    if the endpoint built its inputs differently."""
    from apps.api.billing.parties import tax_inputs
    from apps.api.billing.tax import TaxInputs, propose_tax
    from apps.api.services.tax_service import engine_tables

    party = pg_db.query(Party).filter(Party.id == uuid.UUID(orf["org"]["id"])).one()
    inputs = tax_inputs(party_service.resolve(pg_db, party))
    codes, classes, rules = engine_tables(pg_db, company_id=world["company"].id)
    pure = propose_tax(
        TaxInputs(
            seller_country="AT",
            buyer_country=inputs["buyer_country"],
            buyer_type=inputs["buyer_type"],
            buyer_vat_verified=inputs["buyer_vat_verified"],
            supply_kind="services",
            item_class="standard",
        ),
        codes,
        classes,
        rules,
        "en",
    )

    body = _preview(pg_client, world, orf["org"]["id"])

    assert body["code"] == pure.code
    assert body["treatment"] == pure.treatment
    assert body["rate"] == str(pure.rate)
    assert body["reasons"] == list(pure.reasons)
    assert [w["code"] for w in body["warnings"]] == list(pure.warning_codes())


def test_the_preview_writes_nothing(pg_client, pg_db, world, orf):
    before = (pg_db.query(Party).count(), pg_db.query(AuditEvent).count())

    for _ in range(3):
        _preview(pg_client, world, orf["person"]["id"])

    pg_db.expire_all()
    assert (pg_db.query(Party).count(), pg_db.query(AuditEvent).count()) == before


# ─── A party as a referrer of a tax code ────────────────────────────────────


def test_deactivating_a_code_a_party_points_at_is_refused_by_name(
    pg_client, pg_db, world
):
    """Acceptance item 7. The P1a-2 registry, through a source this module
    registers — so the guard knows about parties without the tax router
    knowing anything about them."""
    _create(
        pg_client,
        world,
        name="Studio customer",
        tax_override={"kind": "code", "code": "AT-13"},
    )
    code = (
        pg_db.query(TaxCode)
        .filter(
            TaxCode.company_id == world["company"].id, TaxCode.code == "AT-13"
        )
        .one()
    )
    from apps.api.models.tax import TaxClass

    tax_class = (
        pg_db.query(TaxClass)
        .filter_by(company_id=world["company"].id, key="reduced_2")
        .one()
    )

    response = pg_client.patch(
        f"/company/tax/classes/{tax_class.id}",
        headers=_headers(world),
        json={"is_active": False},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "customer “Studio customer”" in detail
    pg_db.expire_all()
    assert code.is_active is True


def test_an_archived_party_no_longer_holds_a_code_open(pg_client, pg_db, world):
    """An archived customer is not a reason to keep a code switched on."""
    party = _create(
        pg_client,
        world,
        name="Studio customer",
        tax_override={"kind": "code", "code": "AT-13"},
    )
    pg_client.post(f"/parties/{party['id']}/archive", headers=_headers(world))

    from apps.api.models.tax import TaxClass

    tax_class = (
        pg_db.query(TaxClass)
        .filter_by(company_id=world["company"].id, key="reduced_2")
        .one()
    )
    response = pg_client.patch(
        f"/company/tax/classes/{tax_class.id}",
        headers=_headers(world),
        json={"is_active": False},
    )

    assert response.status_code == 200, response.text


def test_a_domestic_for_foreign_business_override_points_at_no_code(
    pg_client, pg_db, world
):
    """It names a KIND, not a code, so it holds nothing open."""
    _create(
        pg_client,
        world,
        name="Studio customer",
        tax_override={"kind": "domestic_for_foreign_business"},
    )

    from apps.api.models.tax import TaxClass

    tax_class = (
        pg_db.query(TaxClass)
        .filter_by(company_id=world["company"].id, key="reduced_2")
        .one()
    )
    response = pg_client.patch(
        f"/company/tax/classes/{tax_class.id}",
        headers=_headers(world),
        json={"is_active": False},
    )

    assert response.status_code == 200, response.text
