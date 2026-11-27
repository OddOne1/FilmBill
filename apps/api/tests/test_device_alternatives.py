"""Alternatives for a device (B2b-2 part 4, SCOPE §4.4.5). Real PostgreSQL.

What this file is about: links are ONE-DIRECTIONAL — the reverse exists only
when it was asked for — and the ORDER is kept, with every move audited.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.device import DeviceAlternative
from apps.api.models.site_settings import SiteSettings

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_device,
    make_device_alternative,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "alt-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {
        "company": company,
        "owner": owner,
        "alexa": make_device(pg_db, company, name="Alexa 35"),
        "mini": make_device(pg_db, company, name="Alexa Mini LF"),
        "venice": make_device(pg_db, company, name="Sony Venice 2"),
    }


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _add(pg_client, world, device, alternative, **body):
    return pg_client.post(
        f"/devices/{device.id}/alternatives",
        headers=_h(world),
        json={"alternative_device_id": str(alternative.id), **body},
    )


def _pairs(pg_db, company_id):
    pg_db.expire_all()
    return {
        (row.device_id, row.alternative_device_id)
        for row in pg_db.query(DeviceAlternative).filter(
            DeviceAlternative.company_id == company_id, DeviceAlternative.is_active.is_(True)
        )
    }


def test_one_direction_by_default(pg_client, pg_db, world):
    alexa, mini = world["alexa"], world["mini"]
    response = _add(pg_client, world, alexa, mini, note="needs a different mount", price_hint="about -200 per day")
    assert response.status_code == 201, response.text
    body = response.json()
    assert [(row["alternative"]["name"], row["note"], row["price_hint"]) for row in body["alternatives"]] == [
        ("Alexa Mini LF", "needs a different mount", "about -200 per day")
    ]
    assert body["reverse_added"] is None
    assert _pairs(pg_db, world["company"].id) == {(alexa.id, mini.id)}
    # The Mini lists nothing, and says who offers it.
    theirs = pg_client.get(f"/devices/{mini.id}/alternatives", headers=_h(world)).json()
    assert theirs["alternatives"] == []
    assert [row["device"]["name"] for row in theirs["offered_by"]] == ["Alexa 35"]


def test_the_reverse_on_request(pg_client, pg_db, world):
    alexa, mini = world["alexa"], world["mini"]
    response = _add(pg_client, world, alexa, mini, also_reverse=True)
    assert response.status_code == 201, response.text
    assert response.json()["reverse_added"] is True
    assert _pairs(pg_db, world["company"].id) == {(alexa.id, mini.id), (mini.id, alexa.id)}
    # Asking again from the other side when the reverse exists: just the one.
    venice = world["venice"]
    make_device_alternative(pg_db, world["company"], venice, alexa)
    again = _add(pg_client, world, alexa, venice, also_reverse=True)
    assert again.json()["reverse_added"] is False
    assert (venice.id, alexa.id) in _pairs(pg_db, world["company"].id)
    assert len(_pairs(pg_db, world["company"].id)) == 4


def test_the_order_is_kept_and_every_move_is_audited(pg_client, pg_db, world):
    alexa, mini, venice = world["alexa"], world["mini"], world["venice"]
    _add(pg_client, world, alexa, mini)
    body = _add(pg_client, world, alexa, venice).json()
    assert [row["alternative"]["name"] for row in body["alternatives"]] == ["Alexa Mini LF", "Sony Venice 2"]
    venice_row = body["alternatives"][1]["id"]

    moved = pg_client.post(
        f"/devices/{alexa.id}/alternatives/{venice_row}/move", headers=_h(world), json={"direction": "up"}
    )
    assert moved.status_code == 200, moved.text
    assert [row["alternative"]["name"] for row in moved.json()["alternatives"]] == ["Sony Venice 2", "Alexa Mini LF"]
    # Read back from the database, in position order.
    pg_db.expire_all()
    stored = (
        pg_db.query(DeviceAlternative)
        .filter(DeviceAlternative.device_id == alexa.id)
        .order_by(DeviceAlternative.position)
        .all()
    )
    assert [row.alternative_device_id for row in stored] == [venice.id, mini.id]
    event = pg_db.query(AuditEvent).filter(AuditEvent.action == "device_alternative.reordered").one()
    assert event.changes["order"] == [[str(mini.id), str(venice.id)], [str(venice.id), str(mini.id)]]

    # Up from the top changes nothing and writes no event.
    pg_client.post(f"/devices/{alexa.id}/alternatives/{venice_row}/move", headers=_h(world), json={"direction": "up"})
    assert pg_db.query(AuditEvent).filter(AuditEvent.action == "device_alternative.reordered").count() == 1


def test_edit_and_remove_never_delete(pg_client, pg_db, world):
    alexa, mini = world["alexa"], world["mini"]
    row = _add(pg_client, world, alexa, mini).json()["alternatives"][0]["id"]
    edited = pg_client.patch(
        f"/devices/{alexa.id}/alternatives/{row}", headers=_h(world), json={"note": "PL mount", "price_hint": "+150"}
    )
    assert edited.json()["alternatives"][0]["note"] == "PL mount"
    removed = pg_client.post(f"/devices/{alexa.id}/alternatives/{row}/remove", headers=_h(world))
    assert removed.json()["alternatives"] == []
    pg_db.expire_all()
    assert pg_db.get(DeviceAlternative, row).is_active is False
    actions = [e.action for e in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == row).order_by(AuditEvent.created_at)]
    assert actions == ["device_alternative.created", "device_alternative.updated", "device_alternative.removed"]
    assert _add(pg_client, world, alexa, mini).status_code == 201  # can be added again


def test_self_link_and_duplicates_are_refused(pg_client, pg_db, world):
    alexa, mini = world["alexa"], world["mini"]
    selfish = _add(pg_client, world, alexa, alexa)
    assert selfish.status_code == 422 and "cannot be its own alternative" in selfish.json()["detail"]
    assert _add(pg_client, world, alexa, mini).status_code == 201
    duplicate = _add(pg_client, world, alexa, mini)
    assert duplicate.status_code == 409 and "already an alternative for Alexa 35" in duplicate.json()["detail"]
    with pytest.raises(IntegrityError, match="ck_device_alternatives_not_self"):
        pg_db.add(DeviceAlternative(company_id=world["company"].id, device_id=alexa.id, alternative_device_id=alexa.id))
        pg_db.flush()
    pg_db.rollback()


def test_another_companys_devices_and_rows_are_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    their_a = make_device(pg_db, other, name="Their A")
    their_b = make_device(pg_db, other, name="Their B")
    their_row = make_device_alternative(pg_db, other, their_a, their_b)
    assert _add(pg_client, world, world["alexa"], their_b).status_code == 404
    assert pg_client.get(f"/devices/{their_a.id}/alternatives", headers=_h(world)).status_code == 404
    assert pg_client.post(
        f"/devices/{world['alexa'].id}/alternatives/{their_row.id}/remove", headers=_h(world)
    ).status_code == 404
    pg_db.expire_all()
    assert pg_db.get(DeviceAlternative, their_row.id).is_active is True
    assert _pairs(pg_db, world["company"].id) == set()


@pytest.mark.parametrize(
    "role, can_view, can_edit",
    [
        (CompanyRole.producer, True, True),
        (CompanyRole.accountant, True, False),
        (CompanyRole.staff, True, False),
        (CompanyRole.tax_advisor, False, False),
    ],
    ids=lambda value: value.value if isinstance(value, CompanyRole) else str(value),
)
def test_the_alternatives_permission_matrix(pg_client, pg_db, world, role, can_view, can_edit):
    user = make_user(pg_db, f"alt-matrix-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    headers = _h(world, user)
    assert pg_client.get(f"/devices/{world['alexa'].id}/alternatives", headers=headers).status_code == (
        200 if can_view else 404
    )
    made = pg_client.post(
        f"/devices/{world['alexa'].id}/alternatives",
        headers=headers,
        json={"alternative_device_id": str(world["mini"].id)},
    )
    assert made.status_code == (201 if can_edit else 404)
