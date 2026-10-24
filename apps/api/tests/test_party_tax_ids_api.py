"""The tax-identifier endpoints.

Through the real app against real Postgres, because the behaviours that
matter here are about rows and constraints: retire-and-replace, the primary
flag moving rather than refusing, and the append-only history.

**The one that costs money if it ever changes** is
`test_a_manual_confirmation_never_verifies_an_eu_vat_id`. For a `vat` the
registry is reachable, so a human saying "trust me" in its place would put a
reverse charge on an invoice with nothing behind it. The prompt asks for that
sentence to be tested, and it is tested twice: once on the flag and once end
to end through the engine.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.party import PartyTaxId, PartyVatCheck
from apps.api.services import party_service, tax_service
from apps.api.services.number_series import seed_number_series

from .company_factories import (
    auth_headers,
    grant,
    make_superadmin,
    make_company,
    make_party,
    make_user,
)

pytestmark = pytest.mark.realdb


@pytest.fixture
def world(pg_db):
    # The setup gate blocks every protected route until an installation has
    # a superadmin, so a test that skipped this would get 503 everywhere and
    # look like a routing bug.
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "taxid-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    seed_number_series(pg_db, company_id=company.id)
    # Our own VAT ID, so a VIES check can be a qualified one.
    company.vat_id = "ATU16360407"
    pg_db.commit()
    return {"company": company, "owner": owner, "db": pg_db}


def headers(world):
    return auth_headers(world["owner"], world["company"])


@pytest.fixture
def party(pg_db, world):
    row = make_party(
        pg_db, world["company"], name="Berlin GmbH", tax_country="DE",
        is_customer=True,
    )
    pg_db.commit()
    return row


def add(pg_client, world, party, **body):
    payload = {"kind": "vat", "value": "DE123456789"}
    payload.update(body)
    response = pg_client.post(
        f"/parties/{party.id}/tax-ids", headers=headers(world), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def events(pg_db, action):
    return (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == action)
        .order_by(AuditEvent.created_at)
        .all()
    )


# ─── Creating ──────────────────────────────────────────────────────────────


def test_an_identifier_is_normalised_on_write(pg_client, world, party):
    """Uniqueness has to mean what a human means by it, and what the user
    typed is kept beside it because people recognise their own formatting."""
    body = add(pg_client, world, party, value="  de 123 456 789 ")

    assert body["value"] == "DE123456789"
    assert body["value_as_entered"] == "de 123 456 789"
    assert body["country"] == "DE"


def test_the_first_vat_like_identifier_becomes_primary_without_being_asked(
    pg_client, world, party
):
    """A party with one number and no primary flag would resolve to nothing,
    and nobody means that."""
    body = add(pg_client, world, party)

    assert body["is_primary"] is True
    assert body["is_active"] is True


def test_a_malformed_identifier_is_refused_before_any_network_call(
    pg_client, world, party
):
    """422 with the normaliser's own sentence. A registry asked about a typo
    answers "invalid", which we would then store as a finding about the
    customer when it is a finding about the typing."""
    response = pg_client.post(
        f"/parties/{party.id}/tax-ids",
        headers=headers(world),
        json={"kind": "vat", "value": "DE1"},
    )

    assert response.status_code == 422, response.text
    # P1b's own per-country sentence, kept through the translation — a
    # generic "invalid identifier" would throw away the useful part.
    assert "not a usable VAT ID" in response.json()["detail"]
    # Nothing was written.
    assert pg_client.get(
        f"/parties/{party.id}/tax-ids", headers=headers(world)
    ).json() == []


def test_a_swiss_uid_with_a_bad_check_digit_is_refused_with_the_standard(
    pg_client, world, party
):
    """eCH-0097's modulo-11 rule, applied before anything is stored."""
    response = pg_client.post(
        f"/parties/{party.id}/tax-ids",
        headers=headers(world),
        json={"kind": "uid_ch", "value": "CHE109322552"},
    )

    assert response.status_code == 422
    assert "eCH-0097" in response.json()["detail"]


def test_a_swiss_uid_is_accepted_and_is_not_verifiable(pg_client, world, party):
    """Format checked, lookup not available (D61). The response says so, so
    the screen can offer a manual confirmation instead of a Check button
    that could only ever fail."""
    body = add(pg_client, world, party, kind="uid_ch", value="CHE-109.322.551 MWST")

    assert body["value"] == "CHE109322551"
    assert body["verifiable"] is False
    assert body["next_check_at"] is None
    # The suffix the user typed is recorded — it is them telling us the
    # company is VAT-registered — without being validated.
    assert "MWST" in body["notes"]


def test_an_ein_is_accepted_and_is_not_verifiable(pg_client, world, party):
    body = add(pg_client, world, party, kind="ein", value="12-3456789")

    assert body["value"] == "123456789"
    assert body["verifiable"] is False


def test_an_abn_is_verifiable(pg_client, world, party):
    body = add(pg_client, world, party, kind="abn", value="51 824 753 556")

    assert body["value"] == "51824753556"
    assert body["verifiable"] is True
    # And scheduled, because something can answer about it.
    assert body["next_check_at"] is not None


def test_the_same_identifier_twice_is_refused_by_name(pg_client, world, party):
    add(pg_client, world, party)
    response = pg_client.post(
        f"/parties/{party.id}/tax-ids",
        headers=headers(world),
        json={"kind": "vat", "value": "DE123456789"},
    )

    assert response.status_code == 409
    assert "already has DE123456789" in response.json()["detail"]


def test_a_second_primary_moves_the_flag_rather_than_refusing(
    pg_client, world, party
):
    """A user marking a second one primary means "this one now". Refusing
    with "unset the other first" would be making them do the bookkeeping for
    a rule they cannot see."""
    first = add(pg_client, world, party)
    second = add(
        pg_client, world, party, kind="uid_ch", value="CHE109322551",
        is_primary=True,
    )

    assert second["is_primary"] is True
    rows = pg_client.get(
        f"/parties/{party.id}/tax-ids", headers=headers(world)
    ).json()
    by_id = {row["id"]: row for row in rows}
    assert by_id[first["id"]]["is_primary"] is False


def test_creating_writes_an_audit_event_with_the_value_in_full(
    pg_client, pg_db, world, party
):
    """Tax identifiers are not secret. Unlike an IBAN, which is masked, a
    VAT number appears on every invoice — redacting it in the audit log
    would make the log useless for the thing it is for."""
    add(pg_client, world, party)

    rows = events(pg_db, "party_tax_id.created")
    assert len(rows) == 1
    assert rows[0].changes["value"] == [None, "DE123456789"]
    assert rows[0].changes["kind"] == [None, "vat"]


# ─── Changing a value retires and replaces ─────────────────────────────────


def test_changing_the_value_retires_the_old_row_and_links_the_new_one(
    pg_client, pg_db, world, party
):
    """The checks on the old row are evidence for invoices already issued.
    Letting them follow a different number would make an old lookup look
    like confirmation of a number nobody has checked (D63)."""
    first = add(pg_client, world, party)

    response = pg_client.patch(
        f"/parties/{party.id}/tax-ids/{first['id']}",
        headers=headers(world),
        json={"value": "DE999999999"},
    )

    assert response.status_code == 200, response.text
    new = response.json()
    assert new["id"] != first["id"]
    assert new["value"] == "DE999999999"
    assert new["replaces_id"] == first["id"]
    assert new["is_primary"] is True

    rows = {row["id"]: row for row in pg_client.get(
        f"/parties/{party.id}/tax-ids", headers=headers(world)
    ).json()}
    # The old one is still there, retired, keeping its history.
    assert rows[first["id"]]["is_active"] is False
    assert rows[first["id"]]["is_primary"] is False


def test_a_changed_value_resets_verification(pg_client, pg_db, world, party):
    """**The reset, per identifier.** Without it the old row's `valid` would
    still be the latest completed check of the party's primary identifier,
    and the new number would arrive pre-verified — a reverse charge on a
    number nobody has ever looked up."""
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])
    assert party_service.vat_is_verified(pg_db, party) is True

    pg_client.patch(
        f"/parties/{party.id}/tax-ids/{first['id']}",
        headers=headers(world),
        json={"value": "DE999999999"},
    )
    pg_db.expire_all()

    assert party_service.vat_is_verified(pg_db, party) is False
    # And the new identifier starts its initial series from scratch.
    new = party_service.primary_tax_id(pg_db, party)
    assert new.initial_checks_done == 0
    assert new.next_check_at is not None


def test_changing_only_the_notes_does_not_retire_anything(
    pg_client, world, party
):
    """An ordinary edit. Retiring on every PATCH would turn a typo in a note
    into a lost check history."""
    first = add(pg_client, world, party)

    response = pg_client.patch(
        f"/parties/{party.id}/tax-ids/{first['id']}",
        headers=headers(world),
        json={"notes": "Confirmed by phone."},
    )

    assert response.json()["id"] == first["id"]
    assert response.json()["is_active"] is True
    assert response.json()["notes"] == "Confirmed by phone."


def test_both_sides_of_a_replacement_are_audited(pg_client, pg_db, world, party):
    first = add(pg_client, world, party)
    pg_client.patch(
        f"/parties/{party.id}/tax-ids/{first['id']}",
        headers=headers(world),
        json={"value": "DE999999999"},
    )

    retired = events(pg_db, "party_tax_id.retired")
    created = events(pg_db, "party_tax_id.created")
    assert len(retired) == 1
    assert "Replaced by DE999999999" in retired[0].reason
    # Two creates: the original and the replacement, and the second says
    # what it replaces.
    assert len(created) == 2
    assert "Replaces DE123456789" in created[1].reason


# ─── Retire and reactivate, never delete ───────────────────────────────────


def test_retiring_keeps_the_row_and_its_checks(pg_client, pg_db, world, party):
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])

    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/{first['id']}/retire",
        headers=headers(world),
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    # Nothing scheduled for a retired identifier.
    assert response.json()["next_check_at"] is None
    # The check is still there.
    history = pg_client.get(
        f"/parties/{party.id}/tax-ids/{first['id']}/checks",
        headers=headers(world),
    ).json()
    assert len(history) == 1


def test_a_retired_identifier_does_not_verify_the_party(
    pg_client, pg_db, world, party
):
    """It is not the party's number any more, whatever its checks say."""
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])
    assert party_service.vat_is_verified(pg_db, party) is True

    pg_client.post(
        f"/parties/{party.id}/tax-ids/{first['id']}/retire",
        headers=headers(world),
    )
    pg_db.expire_all()

    assert party_service.vat_is_verified(pg_db, party) is False


def test_re_adding_a_retired_number_reactivates_the_same_row(
    pg_client, pg_db, world, party
):
    """Two rows for one number would make "which of these is the evidence" a
    question with no answer."""
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])
    pg_client.post(
        f"/parties/{party.id}/tax-ids/{first['id']}/retire",
        headers=headers(world),
    )

    again = add(pg_client, world, party, is_primary=True)

    assert again["id"] == first["id"]
    assert again["is_active"] is True
    # The history came back with it.
    assert again["latest_check"]["status"] == "valid"
    assert (
        pg_db.query(PartyTaxId).filter(PartyTaxId.party_id == party.id).count()
        == 1
    )


def test_reactivating_restarts_the_initial_series(pg_client, pg_db, world, party):
    """A number may have been retired for a year, and an old `valid` is a
    historical fact rather than a current one."""
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])
    pg_client.post(
        f"/parties/{party.id}/tax-ids/{first['id']}/retire",
        headers=headers(world),
    )

    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/{first['id']}/reactivate",
        headers=headers(world),
    )

    assert response.json()["is_active"] is True
    assert response.json()["initial_checks_done"] == 0
    assert response.json()["next_check_at"] is not None


def test_there_is_no_delete_route_for_an_identifier(pg_client):
    """Asserted on the OpenAPI document, which is the API's own statement of
    what exists — the way to keep "nothing is deleted" true is for there to
    be no route, not for a handler to refuse."""
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    tax_id_paths = {
        path: set(operations)
        for path, operations in schema["paths"].items()
        if "tax-ids" in path
    }

    assert len(tax_id_paths) >= 6, "this test stopped covering the tax-ID API"
    for path, methods in tax_id_paths.items():
        assert "delete" not in methods, f"{path} has a DELETE"


# ─── Manual confirmation ───────────────────────────────────────────────────


def test_a_manual_confirmation_is_recorded_as_a_human_act(
    pg_client, pg_db, world, party
):
    """Shown as "confirmed by <user>", never as a registry check."""
    ein = add(pg_client, world, party, kind="ein", value="123456789")

    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/{ein['id']}/manual-confirmation",
        headers=headers(world),
        json={
            "note": "Letter from their accountant, filed under 2026/114.",
            "evidence_reference": "2026/114",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "manual"
    assert body["status"] == "valid"
    assert body["trigger_kind"] == "manual"
    # Never qualified: that claim belongs to a tax authority.
    assert body["qualified"] is False
    assert body["manual_note"].startswith("Letter from their accountant")
    assert body["evidence_reference"] == "2026/114"
    assert "not a registry check" in body["detail"]


def test_a_manual_confirmation_never_verifies_an_eu_vat_id(
    pg_client, pg_db, world, party
):
    """**The sentence the prompt asks to be tested.**

    For a `vat` the registry is reachable, so a human saying "trust me" in
    its place would put a reverse charge on an invoice with nothing behind
    it. The row is still written — somebody choosing to record their own
    confirmation is entitled to — and it simply does not count.
    """
    vat = add(pg_client, world, party)

    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/{vat['id']}/manual-confirmation",
        headers=headers(world),
        json={"note": "They showed me the certificate."},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "valid"
    # And it says so, pointing at what to do instead.
    assert "does not make this VAT ID verified" in response.json()["detail"]
    assert "Check now" in response.json()["detail"]

    pg_db.expire_all()
    assert party_service.vat_is_verified(pg_db, party) is False
    assert party_service.tax_id_evidence(pg_db, party) is None


def test_the_engine_still_charges_domestic_vat_after_a_manual_confirmation(
    pg_client, pg_db, world, party
):
    """The same rule, end to end. If this ever flips, a German customer's
    invoice reverse-charges on a human's word, and the seller owes the VAT.
    """
    vat = add(pg_client, world, party)
    pg_client.post(
        f"/parties/{party.id}/tax-ids/{vat['id']}/manual-confirmation",
        headers=headers(world),
        json={"note": "Trust me."},
    )

    body = pg_client.post(
        f"/parties/{party.id}/tax-preview",
        headers=headers(world),
        json={"supply_kind": "services"},
    ).json()

    assert body["treatment"] == "domestic"
    assert "buyer_vat_id_not_verified" in [w["code"] for w in body["warnings"]]


def test_a_manual_confirmation_counts_as_business_evidence_outside_the_eu(
    pg_client, pg_db, world
):
    """What it IS for (D67): a US customer where no register will answer.
    The warning goes away because somebody has put evidence on file."""
    us = make_party(
        pg_db, world["company"], name="Pacific Pictures Inc", tax_country="US",
        is_customer=True,
    )
    pg_db.commit()
    ein = add(pg_client, world, us, kind="ein", value="123456789")

    before = pg_client.post(
        f"/parties/{us.id}/tax-preview",
        headers=headers(world),
        json={"supply_kind": "services"},
    ).json()
    assert "no_business_evidence" in [w["code"] for w in before["warnings"]]

    pg_client.post(
        f"/parties/{us.id}/tax-ids/{ein['id']}/manual-confirmation",
        headers=headers(world),
        json={"note": "W-9 on file."},
    )

    after = pg_client.post(
        f"/parties/{us.id}/tax-preview",
        headers=headers(world),
        json={"supply_kind": "services"},
    ).json()
    assert "no_business_evidence" not in [w["code"] for w in after["warnings"]]
    # And the tax itself never changed — it is a warning, never a block.
    assert after["treatment"] == before["treatment"]


def test_an_expired_manual_confirmation_stops_counting(pg_client, pg_db, world):
    """`valid_until` is a person's claim about the future, and it is
    honoured."""
    us = make_party(
        pg_db, world["company"], name="Pacific Pictures Inc", tax_country="US",
        is_customer=True,
    )
    pg_db.commit()
    ein = add(pg_client, world, us, kind="ein", value="123456789")

    pg_client.post(
        f"/parties/{us.id}/tax-ids/{ein['id']}/manual-confirmation",
        headers=headers(world),
        json={
            "note": "Valid for this engagement only.",
            "valid_until": (
                datetime.now(timezone.utc) - timedelta(days=1)
            ).isoformat(),
        },
    )

    body = pg_client.post(
        f"/parties/{us.id}/tax-preview",
        headers=headers(world),
        json={"supply_kind": "services"},
    ).json()

    assert "no_business_evidence" in [w["code"] for w in body["warnings"]]


def test_a_manual_confirmation_is_audited_with_whether_it_counts(
    pg_client, pg_db, world, party
):
    vat = add(pg_client, world, party)
    pg_client.post(
        f"/parties/{party.id}/tax-ids/{vat['id']}/manual-confirmation",
        headers=headers(world),
        json={"note": "Saw the certificate."},
    )

    rows = events(pg_db, "party.tax_id_manual_confirmed")
    assert len(rows) == 1
    assert rows[0].changes["counts_as_verified"] == [None, False]
    assert rows[0].changes["note"][1] == "Saw the certificate."


# ─── Checks, history and acknowledgement ───────────────────────────────────


def test_checking_a_kind_with_no_provider_is_not_an_error(
    pg_client, world, party
):
    """"There is no register to ask" is an answer about the world, not a
    failure of ours — so 200 with a sentence, not a 500."""
    ein = add(pg_client, world, party, kind="ein", value="123456789")

    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/{ein['id']}/check", headers=headers(world)
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "not_checkable"
    assert "record a confirmation by hand" in response.json()["detail"]


def test_the_history_is_newest_first_and_only_grows(
    pg_client, pg_db, world, party
):
    """Append-only. Newest first because the question is almost always "what
    does it say now", and the history below is the answer to "has that
    changed"."""
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])
    _record_valid(pg_db, world, party, first["id"], status="invalid")

    history = pg_client.get(
        f"/parties/{party.id}/tax-ids/{first['id']}/checks",
        headers=headers(world),
    ).json()

    assert [row["status"] for row in history] == ["invalid", "valid"]


def test_acknowledging_a_flagged_change_clears_it_and_records_the_note(
    pg_client, pg_db, world, party
):
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])
    flagged = _record_valid(pg_db, world, party, first["id"], status="invalid")
    pg_db.expire_all()

    before = pg_client.get(
        f"/parties/{party.id}/tax-ids", headers=headers(world)
    ).json()[0]
    assert before["needs_attention"] is True
    assert "changed from" in before["needs_attention_reason"]

    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/{first['id']}/checks/{flagged.id}/acknowledge",
        headers=headers(world),
        json={"note": "They deregistered. Switching them to domestic."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["needs_attention"] is False
    assert response.json()["needs_attention_reason"] is None

    rows = events(pg_db, "party.tax_id_change_acknowledged")
    assert len(rows) == 1
    assert "deregistered" in rows[0].reason


def test_an_empty_acknowledgement_note_is_refused(pg_client, pg_db, world, party):
    """"ok" tells the next person nothing, and the point of an
    acknowledgement is that somebody can see what a human concluded."""
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])
    flagged = _record_valid(pg_db, world, party, first["id"], status="invalid")

    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/{first['id']}/checks/{flagged.id}/acknowledge",
        headers=headers(world),
        json={"note": ""},
    )

    assert response.status_code == 422


def test_a_check_from_another_identifier_cannot_be_acknowledged_here(
    pg_client, pg_db, world, party
):
    """Scoped by identifier as well as by company: a check id from a
    different identifier would otherwise clear the wrong flag."""
    first = add(pg_client, world, party)
    other = add(pg_client, world, party, kind="abn", value="51824753556")
    check = _record_valid(pg_db, world, party, first["id"])

    response = pg_client.post(
        f"/parties/{party.id}/tax-ids/{other['id']}/checks/{check.id}/acknowledge",
        headers=headers(world),
        json={"note": "Should not work."},
    )

    assert response.status_code == 404


# ─── The needs-attention list ──────────────────────────────────────────────


def test_the_party_list_can_be_filtered_to_flagged_parties(
    pg_client, pg_db, world, party
):
    make_party(pg_db, world["company"], name="Nothing Wrong GmbH")
    pg_db.commit()
    first = add(pg_client, world, party)
    _record_valid(pg_db, world, party, first["id"])
    _record_valid(pg_db, world, party, first["id"], status="invalid")
    pg_db.commit()

    body = pg_client.get(
        "/parties?needs_attention=true", headers=headers(world)
    ).json()

    assert body["total"] == 1
    assert body["parties"][0]["id"] == str(party.id)


def test_a_party_with_two_flagged_identifiers_appears_once(
    pg_client, pg_db, world, party
):
    """A join would return it twice and the page count would disagree with
    the rows."""
    first = add(pg_client, world, party)
    second = add(pg_client, world, party, kind="abn", value="51824753556")
    for entry in (first, second):
        _record_valid(pg_db, world, party, entry["id"])
        _record_valid(pg_db, world, party, entry["id"], status="invalid")
    pg_db.commit()

    body = pg_client.get(
        "/parties?needs_attention=true", headers=headers(world)
    ).json()

    assert body["total"] == 1
    assert len(body["parties"]) == 1


# ─── The policy endpoints ──────────────────────────────────────────────────


def test_the_policy_is_served_with_its_choices_and_defaults(pg_client, world):
    """The choices are served rather than hard-coded in the web, so the list
    cannot drift from the validator — a screen offering an interval the API
    refuses is a form that fails on save for no visible reason."""
    body = pg_client.get(
        "/company/tax/id-check-policy", headers=headers(world)
    ).json()

    assert body["initial_check_offsets"] == [0, 1, 7]
    assert body["recheck_interval_days"] == 90
    assert body["max_check_age_days"] == 180
    assert None in body["interval_choices"]
    assert 90 in body["interval_choices"]
    assert body["defaults"]["recheck_interval_days"] == 90


def test_the_policy_can_be_changed_and_is_audited(pg_client, pg_db, world):
    response = pg_client.patch(
        "/company/tax/id-check-policy",
        headers=headers(world),
        json={"recheck_interval_days": 30, "max_check_age_days": 60},
    )

    assert response.status_code == 200, response.text
    assert response.json()["recheck_interval_days"] == 30

    rows = events(pg_db, "company.tax_id_check_policy_changed")
    assert len(rows) == 1
    assert rows[0].changes["recheck_interval_days"] == [90, 30]


def test_a_partial_patch_leaves_the_other_settings_alone(pg_client, world):
    """Otherwise changing one setting would silently reset the other two to
    their defaults."""
    pg_client.patch(
        "/company/tax/id-check-policy",
        headers=headers(world),
        json={"initial_check_offsets": [0, 2]},
    )

    body = pg_client.patch(
        "/company/tax/id-check-policy",
        headers=headers(world),
        json={"recheck_interval_days": 30, "max_check_age_days": 60},
    ).json()

    assert body["initial_check_offsets"] == [0, 2]


def test_an_invalid_combination_is_refused_with_the_arithmetic(pg_client, world):
    response = pg_client.patch(
        "/company/tax/id-check-policy",
        headers=headers(world),
        json={"recheck_interval_days": 90, "max_check_age_days": 30},
    )

    assert response.status_code == 422
    assert "60 days of each cycle unverified" in response.json()["detail"]


def test_a_party_interval_override_goes_through_the_party_patch(
    pg_client, world, party
):
    response = pg_client.patch(
        f"/parties/{party.id}",
        headers=headers(world),
        json={"recheck_interval_days": 7},
    )

    assert response.status_code == 200, response.text
    assert response.json()["recheck_interval_days"] == 7


# ─── Permissions and isolation ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "role, can_read, can_edit",
    [
        (CompanyRole.owner, True, True),
        (CompanyRole.admin, True, True),
        (CompanyRole.accountant, True, False),
        (CompanyRole.producer, True, True),
        (CompanyRole.staff, False, False),
        (CompanyRole.tax_advisor, False, False),
    ],
)
def test_the_p1b_permission_matrix_is_unchanged(
    pg_client, pg_db, world, party, role, can_read, can_edit
):
    """**P1b-2 adds no permission keys.** The matrix will change over the
    build, and every check staying behind an existing key means that change
    is one place rather than a search (D58, prompt Step 0 item 3)."""
    # Prefixed apart from the fixture's own `taxid-owner@example.com`:
    # `role.value` is "owner" for the first case, and the collision made
    # this test fail on a duplicate email rather than on permissions.
    member = make_user(pg_db, f"taxid-member-{role.value}@example.com")
    grant(pg_db, world["company"], member, role)
    pg_db.commit()
    member_headers = auth_headers(member, world["company"])

    read = pg_client.get(
        f"/parties/{party.id}/tax-ids", headers=member_headers
    )
    assert (read.status_code == 200) is can_read, read.text

    write = pg_client.post(
        f"/parties/{party.id}/tax-ids",
        headers=member_headers,
        json={"kind": "vat", "value": "DE123456789"},
    )
    assert (write.status_code == 201) is can_edit, write.text


def test_another_companys_identifier_is_404(pg_client, pg_db, world):
    """Rule 6: 404, not 403 — a 403 confirms the row exists."""
    other = make_company(pg_db, "Theirs OG", address_country="AT")
    their_party = make_party(pg_db, other, name="Theirs")
    their_id = PartyTaxId(
        company_id=other.id,
        party_id=their_party.id,
        kind="vat",
        value="DE111111111",
        country="DE",
    )
    pg_db.add(their_id)
    pg_db.commit()

    response = pg_client.get(
        f"/parties/{their_party.id}/tax-ids", headers=headers(world)
    )
    assert response.status_code == 404


def test_an_identifier_from_another_party_of_the_same_company_is_404(
    pg_client, pg_db, world, party
):
    """A smaller leak than a cross-company one and the same shape of bug:
    without the party filter, an identifier id would be editable through any
    party's URL."""
    first = add(pg_client, world, party)
    other = make_party(pg_db, world["company"], name="Somebody Else GmbH")
    pg_db.commit()

    response = pg_client.patch(
        f"/parties/{other.id}/tax-ids/{first['id']}",
        headers=headers(world),
        json={"notes": "should not work"},
    )
    assert response.status_code == 404


def test_the_identifiers_come_with_the_party(pg_client, world, party):
    """One request for the detail page: a second one for the Tax tab would
    be a second chance for a half-loaded screen."""
    add(pg_client, world, party)

    body = pg_client.get(f"/parties/{party.id}", headers=headers(world)).json()

    assert [row["value"] for row in body["tax_ids"]] == ["DE123456789"]
    assert body["tax_ids"][0]["verifiable"] is True


def _record_valid(pg_db, world, party, tax_id_id, status="valid"):
    """A completed check against one identifier, written directly.

    Directly rather than through the check endpoint, because that would make
    a network call — the providers are only faked where a provider is the
    subject (`test_verification_providers.py`).
    """
    from apps.api.services import tax_id_checks
    from apps.api.billing.parties.verification import VerificationResult

    row = pg_db.get(PartyTaxId, uuid.UUID(tax_id_id))
    check = tax_id_checks.record_check(
        pg_db,
        tax_id=row,
        party=party,
        result=VerificationResult(
            status=status,
            provider="vies",
            value=row.value,
            kind=row.kind,
            country=row.country,
            detail="recorded by a test",
            registered_name="Berlin GmbH",
            name_match="match",
        ),
        trigger_kind="manual",
        actor=None,
    )
    pg_db.commit()
    return check
