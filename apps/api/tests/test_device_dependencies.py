"""Dependencies between devices (B2b-2 part 3, SCOPE §4.4.3). Real PostgreSQL.

What this file is about: a link that would make a LOOP is refused — two
devices, three devices — and the setup hint's quantities are MULTIPLIED by
the quantity per parent (2 batteries per camera × 1 charger per battery →
2 chargers per camera). Every write is read back from the database.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.device import DeviceAssociation
from apps.api.models.site_settings import SiteSettings

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_device,
    make_device_association,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "deps-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    camera = make_device(pg_db, company, name="Alexa 35")
    battery = make_device(pg_db, company, name="Battery V-Mount 150 Wh", category="Power")
    charger = make_device(pg_db, company, name="Charger", category="Power")
    return {"company": company, "owner": owner, "camera": camera, "battery": battery, "charger": charger}


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _add(pg_client, world, parent, child, **body):
    payload = {"associated_device_id": str(child.id), **body}
    return pg_client.post(f"/devices/{parent.id}/dependencies", headers=_h(world), json=payload)


def _active(pg_db, company_id):
    pg_db.expire_all()
    return (
        pg_db.query(DeviceAssociation)
        .filter(DeviceAssociation.company_id == company_id, DeviceAssociation.is_active.is_(True))
        .all()
    )


def test_create_update_remove_and_the_reverse_list(pg_client, pg_db, world):
    camera, battery = world["camera"], world["battery"]
    made = _add(pg_client, world, camera, battery, quantity_per_parent=2, booking_mode="always", pricing="included")
    assert made.status_code == 201, made.text
    row = made.json()["needs"][0]
    assert (row["associated"]["name"], row["quantity_per_parent"], row["booking_mode"], row["pricing"]) == (
        "Battery V-Mount 150 Wh",
        2,
        "always",
        "included",
    )
    assert row["show_on_documents"] is False
    stored = _active(pg_db, world["company"].id)
    assert [(l.device_id, l.associated_device_id, l.quantity_per_parent) for l in stored] == [
        (camera.id, battery.id, 2)
    ]

    reverse = pg_client.get(f"/devices/{battery.id}/dependencies", headers=_h(world)).json()
    assert reverse["needs"] == []
    assert [entry["parent"]["name"] for entry in reverse["used_by"]] == ["Alexa 35"]

    changed = pg_client.patch(
        f"/devices/{camera.id}/dependencies/{row['id']}",
        headers=_h(world),
        json={"quantity_per_parent": 3, "booking_mode": "ask", "pricing": "charged", "note": "Spare in the case"},
    )
    assert changed.status_code == 200, changed.text
    pg_db.expire_all()
    link = pg_db.get(DeviceAssociation, row["id"])
    assert (link.quantity_per_parent, link.booking_mode, link.pricing, link.note) == (3, "ask", "charged", "Spare in the case")

    removed = pg_client.post(f"/devices/{camera.id}/dependencies/{row['id']}/remove", headers=_h(world))
    assert removed.status_code == 200 and removed.json()["needs"] == []
    pg_db.expire_all()
    assert pg_db.get(DeviceAssociation, row["id"]).is_active is False  # kept, not deleted

    actions = [
        event.action
        for event in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == row["id"]).order_by(AuditEvent.created_at)
    ]
    assert actions == ["device_association.created", "device_association.updated", "device_association.removed"]

    # After removing, the pair can be linked again.
    assert _add(pg_client, world, camera, battery).status_code == 201


def test_one_active_link_per_pair_in_the_api_and_the_database(pg_client, pg_db, world):
    camera, battery = world["camera"], world["battery"]
    assert _add(pg_client, world, camera, battery).status_code == 201
    again = _add(pg_client, world, camera, battery)
    assert again.status_code == 409
    assert "already depends on Battery V-Mount 150 Wh" in again.json()["detail"]
    with pytest.raises(IntegrityError, match="uq_device_associations_pair_active"):
        pg_db.add(
            DeviceAssociation(company_id=world["company"].id, device_id=camera.id, associated_device_id=battery.id)
        )
        pg_db.flush()
    pg_db.rollback()


def test_a_device_cannot_depend_on_itself(pg_client, pg_db, world):
    camera = world["camera"]
    response = _add(pg_client, world, camera, camera)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Alexa 35 cannot depend on itself.")
    with pytest.raises(IntegrityError, match="ck_device_associations_not_self"):
        pg_db.add(DeviceAssociation(company_id=world["company"].id, device_id=camera.id, associated_device_id=camera.id))
        pg_db.flush()
    pg_db.rollback()


def test_a_two_device_loop_is_refused(pg_client, pg_db, world):
    camera, battery = world["camera"], world["battery"]
    assert _add(pg_client, world, camera, battery).status_code == 201
    refused = _add(pg_client, world, battery, camera)
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "Alexa 35 already depends on Battery V-Mount 150 Wh (Alexa 35 → Battery V-Mount 150 Wh), so "
        "Battery V-Mount 150 Wh cannot depend on Alexa 35 as well: that would be a loop. "
        "Remove one of the links first."
    )
    assert len(_active(pg_db, world["company"].id)) == 1


def test_a_three_device_loop_is_refused_and_names_the_chain(pg_client, pg_db, world):
    camera, battery, charger = world["camera"], world["battery"], world["charger"]
    assert _add(pg_client, world, camera, battery).status_code == 201
    assert _add(pg_client, world, battery, charger).status_code == 201
    refused = _add(pg_client, world, charger, camera)
    assert refused.status_code == 409
    assert "Alexa 35 → Battery V-Mount 150 Wh → Charger" in refused.json()["detail"]
    assert len(_active(pg_db, world["company"].id)) == 2
    # A link that is not a loop is fine: the camera may also need the charger.
    assert _add(pg_client, world, camera, charger).status_code == 201


def test_the_setup_hint_pre_multiplies_and_creates_links_with_origins_in_one_go(pg_client, pg_db, world):
    camera, battery, charger = world["camera"], world["battery"], world["charger"]
    cable = make_device(pg_db, world["company"], name="D-Tap cable")
    to_charger = make_device_association(pg_db, world["company"], battery, charger, quantity_per_parent=1)
    to_cable = make_device_association(pg_db, world["company"], battery, cable, quantity_per_parent=3)

    hint = pg_client.get(
        f"/devices/{camera.id}/dependencies/hint",
        headers=_h(world),
        params={"associated_device_id": str(battery.id), "quantity_per_parent": 2},
    )
    assert hint.status_code == 200, hint.text
    rows = {row["device"]["name"]: row for row in hint.json()["rows"]}
    assert (rows["Charger"]["quantity_per_associated"], rows["Charger"]["suggested_quantity"]) == (1, 2)
    assert (rows["D-Tap cable"]["quantity_per_associated"], rows["D-Tap cable"]["suggested_quantity"]) == (3, 6)
    assert all(row["blocked_reason"] is None for row in rows.values())

    made = _add(
        pg_client,
        world,
        camera,
        battery,
        quantity_per_parent=2,
        from_hint=[
            {"origin_association_id": str(to_charger.id)},  # takes the pre-multiplied 2
            {"origin_association_id": str(to_cable.id), "quantity_per_parent": 4},  # edited
        ],
    )
    assert made.status_code == 201, made.text
    pg_db.expire_all()
    mine = {
        link.associated_device_id: link
        for link in pg_db.query(DeviceAssociation).filter(
            DeviceAssociation.device_id == camera.id, DeviceAssociation.is_active.is_(True)
        )
    }
    assert (mine[battery.id].quantity_per_parent, mine[battery.id].origin_association_id) == (2, None)
    assert (mine[charger.id].quantity_per_parent, mine[charger.id].origin_association_id) == (2, to_charger.id)
    assert (mine[cable.id].quantity_per_parent, mine[cable.id].origin_association_id) == (4, to_cable.id)
    added_via = {row["associated"]["name"]: row["origin_device_name"] for row in made.json()["needs"]}
    assert added_via == {"Battery V-Mount 150 Wh": None, "Charger": "Battery V-Mount 150 Wh", "D-Tap cable": "Battery V-Mount 150 Wh"}


def test_a_refused_hint_row_takes_the_whole_add_with_it(pg_client, pg_db, world):
    """One transaction. (A hint row can never close a loop on its own: if the
    battery's charger led back to the camera, the battery would too, and the
    main link is refused first — so the refusal tested here is the other
    one, a hint row the camera already has.)"""
    camera, battery, charger = world["camera"], world["battery"], world["charger"]
    to_charger = make_device_association(pg_db, world["company"], battery, charger)
    make_device_association(pg_db, world["company"], camera, charger)  # the camera already needs one

    hint = pg_client.get(
        f"/devices/{camera.id}/dependencies/hint",
        headers=_h(world),
        params={"associated_device_id": str(battery.id)},
    ).json()
    assert hint["rows"][0]["blocked_reason"] == "Alexa 35 already depends on Charger."

    before = len(_active(pg_db, world["company"].id))
    response = _add(pg_client, world, camera, battery, from_hint=[{"origin_association_id": str(to_charger.id)}])
    assert response.status_code == 409
    assert len(_active(pg_db, world["company"].id)) == before
    pg_db.expire_all()
    assert (
        pg_db.query(DeviceAssociation)
        .filter(DeviceAssociation.device_id == camera.id, DeviceAssociation.associated_device_id == battery.id)
        .count()
        == 0
    )


def test_another_companys_devices_are_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    theirs = make_device(pg_db, other, name="Their battery")
    their_parent = make_device(pg_db, other, name="Their camera")
    link = make_device_association(pg_db, other, their_parent, theirs)
    assert _add(pg_client, world, world["camera"], theirs).status_code == 404
    assert pg_client.get(f"/devices/{their_parent.id}/dependencies", headers=_h(world)).status_code == 404
    assert pg_client.get(
        f"/devices/{world['camera'].id}/dependencies/hint",
        headers=_h(world),
        params={"associated_device_id": str(theirs.id)},
    ).status_code == 404
    # Their link, addressed through OUR device: not found either.
    assert pg_client.post(
        f"/devices/{world['camera'].id}/dependencies/{link.id}/remove", headers=_h(world)
    ).status_code == 404
    # And their link as a hint origin for our device.
    battery = world["battery"]
    response = _add(pg_client, world, world["camera"], battery, from_hint=[{"origin_association_id": str(link.id)}])
    assert response.status_code == 404
    pg_db.expire_all()
    assert pg_db.get(DeviceAssociation, link.id).is_active is True
    assert _active(pg_db, world["company"].id) == []


@pytest.mark.parametrize(
    "role, can_view, can_edit",
    [
        (CompanyRole.owner, True, True),
        (CompanyRole.admin, True, True),
        (CompanyRole.producer, True, True),
        (CompanyRole.accountant, True, False),
        (CompanyRole.staff, True, False),
        (CompanyRole.tax_advisor, False, False),
    ],
    ids=lambda value: value.value if isinstance(value, CompanyRole) else str(value),
)
def test_the_dependency_permission_matrix(pg_client, pg_db, world, role, can_view, can_edit):
    user = make_user(pg_db, f"deps-matrix-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    headers = _h(world, user)
    read = pg_client.get(f"/devices/{world['camera'].id}/dependencies", headers=headers)
    assert read.status_code == (200 if can_view else 404)
    made = pg_client.post(
        f"/devices/{world['camera'].id}/dependencies",
        headers=headers,
        json={"associated_device_id": str(world["battery"].id)},
    )
    assert made.status_code == (201 if can_edit else 404), made.text


def test_there_is_no_delete_route_for_dependencies(client):
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    dependency_paths = [path for path in schema["paths"] if "/dependencies" in path]
    assert dependency_paths, "the dependency routes are missing from the schema"
    assert not [path for path in dependency_paths if "delete" in schema["paths"][path]]
