"""The Add new item(s) popup: many serials, one purchase, one request,
one audit event (B2b-2 part 2). Real PostgreSQL.

What this file is about is that the SHARED purchase fields land on EVERY
unit the request creates — read back from the database, row by row — and
that the whole popup is one event with counts, not one per unit.
"""

from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.device import Device, DeviceUnit
from apps.api.models.site_settings import SiteSettings

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_device,
    make_device_unit,
    make_party,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "bulk-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _h(world):
    return auth_headers(world["owner"], world["company"])


def test_shared_purchase_fields_land_on_every_created_unit_in_one_event(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], quantity_owned=3)
    make_device_unit(pg_db, world["company"], device, serial_number="AX-1")
    seller = make_party(pg_db, world["company"], name="ARRI Rental Wien", is_supplier=True)

    response = pg_client.post(
        f"/devices/{device.id}/units",
        headers=_h(world),
        json={
            "serial_numbers": ["AX-2", "AX-3", "ax-1", "", "AX-4", "AX-5", "AX-6"],
            "purchase_price": "61750.00",
            "seller_party_id": str(seller.id),
            "purchase_reference": "RE-2026-0311",
            "acquired_on": "2026-11-20",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert [unit["serial_number"] for unit in body["created"]] == ["AX-2", "AX-3", "AX-4", "AX-5", "AX-6"]
    assert body["skipped"] == [
        {"serial_number": "ax-1", "reason": "already_recorded", "detail": "AX-1 is already recorded on Alexa 35."}
    ]

    pg_db.expire_all()
    created = (
        pg_db.query(DeviceUnit)
        .filter(DeviceUnit.device_id == device.id, DeviceUnit.serial_number != "AX-1")
        .order_by(DeviceUnit.serial_number)
        .all()
    )
    assert len(created) == 5
    for unit in created:
        assert unit.purchase_price == Decimal("61750.00"), unit.serial_number
        assert unit.seller_party_id == seller.id, unit.serial_number
        assert unit.purchase_reference == "RE-2026-0311", unit.serial_number
        assert str(unit.acquired_on) == "2026-11-20", unit.serial_number
    # The quantity is still the quantity (B2b's rule).
    assert pg_db.get(Device, device.id).quantity_owned == 3

    events = pg_db.query(AuditEvent).filter(AuditEvent.company_id == world["company"].id)
    bulk = events.filter(AuditEvent.action == "device_unit.created_bulk").all()
    assert len(bulk) == 1
    assert bulk[0].entity_id == device.id
    assert bulk[0].changes["created"] == [None, 5]
    assert bulk[0].changes["skipped"] == [None, 1]
    assert bulk[0].changes["purchase_price"] == [None, "61750.00"]
    assert events.filter(AuditEvent.action == "device_unit.created").count() == 0


def test_a_typed_seller_and_no_price_apply_to_every_unit_too(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"])
    response = pg_client.post(
        f"/devices/{device.id}/units",
        headers=_h(world),
        json={"serial_numbers": ["S-1", "S-2"], "seller_name": "  Ebay,  cam_shop_graz "},
    )
    assert response.status_code == 201, response.text
    pg_db.expire_all()
    units = pg_db.query(DeviceUnit).filter(DeviceUnit.device_id == device.id).all()
    assert {(u.seller_name, u.purchase_price) for u in units} == {("Ebay, cam_shop_graz", None)}


def test_the_csv_import_keeps_its_own_events_per_unit(pg_client, pg_db, world):
    payload = b"name;quantity;serials\nArri SkyPanel S60;2;SP-1|SP-2\n"
    preview = pg_client.post(
        "/devices/import/preview", headers=_h(world), files={"file": ("gear.csv", payload, "text/csv")}
    ).json()
    applied = pg_client.post(
        "/devices/import",
        headers=_h(world),
        files={"file": ("gear.csv", payload, "text/csv")},
        data={"digest": preview["digest"]},
    )
    assert applied.status_code == 200, applied.text
    actions = [event.action for event in pg_db.query(AuditEvent).all()]
    assert actions.count("device_unit.created") == 2
    assert "device_unit.created_bulk" not in actions
