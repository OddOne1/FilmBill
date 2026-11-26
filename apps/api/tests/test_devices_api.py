"""Gear: devices and the serial numbers of their units (B2b part 2).

Real PostgreSQL throughout. The thing this file is about is that the
QUANTITY OWNED and the SERIAL NUMBERS are two independent numbers: every
test that changes one reads the other back from the database afterwards.

Mathias's examples are the cases: three Alexa 35 owned and two serials known
(one bought on a production, not yet entered); two owned and three serials,
one of them retired (destroyed on a set); nothing owned and nothing known.
"""

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.device import Device, DeviceUnit
from apps.api.models.site_settings import SiteSettings
from apps.api.services.permissions import PERMISSIONS, permissions_for

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_device,
    make_device_unit,
    make_item,
    make_superadmin,
    make_user,
)
from .test_projects_api import _alembic, _scratch_database


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "gear-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _create(pg_client, world, **body):
    payload = {"name": "Alexa 35", "category": "Camera", "quantity_owned": 3, "purchase_price": "65000.00"}
    payload.update(body)
    response = pg_client.post("/devices", headers=_h(world), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _add_serials(pg_client, world, device_id, *serials):
    response = pg_client.post(
        f"/devices/{device_id}/units", headers=_h(world), json={"serial_numbers": list(serials)}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _quantity(pg_db, device_id) -> int:
    pg_db.expire_all()
    return pg_db.get(Device, device_id).quantity_owned


def _unit_id(detail, serial):
    return next(unit["id"] for unit in detail["units"] if unit["serial_number"] == serial)


# ─── Migration ──────────────────────────────────────────────────────────────


@pytest.fixture
def at_revision_0022(pg_engine):
    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0022_party_crew_flag")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0022:\n{result.stderr}")
    engine = create_engine(url)
    try:
        yield engine, url
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            ).scalars()
        )


def test_the_migration_creates_both_tables_and_the_downgrade_removes_them(at_revision_0022):
    engine, url = at_revision_0022
    assert not {"devices", "device_units"} & _tables(engine)
    up = _alembic(url, "upgrade", "0023_devices")
    assert up.returncode == 0, up.stderr
    assert {"devices", "device_units"} <= _tables(engine)
    with engine.begin() as conn:
        company = conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, default_language, "
                "fiscal_year_start_month, timezone, require_2fa_roles, tax_advisor_reports) "
                "VALUES (gen_random_uuid(), 'Mig', 'EUR', 'de', 1, 'Europe/Vienna', '[]', false) "
                "RETURNING id"
            )
        ).scalar()
        device = conn.execute(
            text(
                "INSERT INTO devices (id, company_id, name, name_key) VALUES "
                "(gen_random_uuid(), :c, 'Alexa 35', 'alexa 35') RETURNING id"
            ),
            {"c": company},
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO device_units (id, company_id, device_id, serial_number, serial_key) "
                "VALUES (gen_random_uuid(), :c, :d, 'AB-1', 'ab-1')"
            ),
            {"c": company, "d": device},
        )
    # The schema's own rules, in the migration rather than only the model.
    with pytest.raises(Exception, match="uq_device_units_device_serial"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO device_units (id, company_id, device_id, serial_number, serial_key) "
                    "VALUES (gen_random_uuid(), :c, :d, 'ab-1', 'ab-1')"
                ),
                {"c": company, "d": device},
            )
    with pytest.raises(Exception, match="ck_devices_quantity_owned"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE devices SET quantity_owned = -1"))

    down = _alembic(url, "downgrade", "0022_party_crew_flag")
    assert down.returncode == 0, down.stderr
    assert not {"devices", "device_units"} & _tables(engine)
    again = _alembic(url, "upgrade", "0023_devices")
    assert again.returncode == 0, again.stderr


# ─── The two numbers ────────────────────────────────────────────────────────


def test_three_owned_two_serials_reads_one_not_yet_recorded(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=3)
    body = _add_serials(pg_client, world, device["id"], "AX35-0001", "AX35-0002")
    counts = body["device"]["counts"]
    assert counts == {
        "owned": 3,
        "recorded": 2,
        "active": 2,
        "in_repair": 0,
        "retired": 0,
        "not_yet_recorded": 1,
        "beyond_quantity": 0,
    }
    assert _quantity(pg_db, device["id"]) == 3


def test_two_owned_three_serials_one_retired(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], quantity_owned=2)
    make_device_unit(pg_db, world["company"], device, serial_number="A")
    make_device_unit(pg_db, world["company"], device, serial_number="B")
    make_device_unit(pg_db, world["company"], device, serial_number="C", status="retired", retired_reason="destroyed")
    counts = pg_client.get(f"/devices/{device.id}", headers=_h(world)).json()["counts"]
    assert (counts["owned"], counts["recorded"], counts["retired"]) == (2, 3, 1)
    assert (counts["not_yet_recorded"], counts["beyond_quantity"]) == (0, 0)
    # And the list says the same as the detail.
    listed = pg_client.get("/devices", headers=_h(world)).json()["devices"][0]["counts"]
    assert listed == counts


def test_more_usable_serials_than_owned_is_allowed_and_counted(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=1)
    body = _add_serials(pg_client, world, device["id"], "A", "B", "C")
    assert body["device"]["counts"]["beyond_quantity"] == 2
    assert body["device"]["quantity_owned"] == 1


def test_nothing_owned_nothing_recorded(pg_client, world):
    device = _create(pg_client, world, name="Spare", quantity_owned=0, purchase_price=None)
    assert device["counts"] == {
        "owned": 0,
        "recorded": 0,
        "active": 0,
        "in_repair": 0,
        "retired": 0,
        "not_yet_recorded": 0,
        "beyond_quantity": 0,
    }


def test_adding_serial_numbers_never_changes_the_quantity_owned(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=3)
    _add_serials(pg_client, world, device["id"], "A")
    _add_serials(pg_client, world, device["id"], "B", "C", "D", "E")
    assert _quantity(pg_db, device["id"]) == 3
    assert pg_db.query(DeviceUnit).filter(DeviceUnit.device_id == device["id"]).count() == 5
    assert not (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == device["id"], AuditEvent.action == "device.quantity_set")
        .count()
    )


def test_a_pasted_list_skips_repeats_and_serials_already_recorded(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=5)
    _add_serials(pg_client, world, device["id"], "AX-1")
    body = _add_serials(pg_client, world, device["id"], "AX-2", " ax-1 ", "AX-3", "ax-2", "", "AX-4", "AX-5")
    assert [unit["serial_number"] for unit in body["created"]] == ["AX-2", "AX-3", "AX-4", "AX-5"]
    assert [(entry["serial_number"], entry["reason"]) for entry in body["skipped"]] == [
        ("ax-1", "already_recorded"),
        ("ax-2", "repeated"),
    ]
    assert "AX-1 is already recorded on Alexa 35" in body["skipped"][0]["detail"]
    assert pg_db.query(DeviceUnit).filter(DeviceUnit.device_id == device["id"]).count() == 5


def test_a_serial_in_another_case_is_the_same_serial_everywhere(pg_client, pg_db, world):
    device = _create(pg_client, world)
    detail = _add_serials(pg_client, world, device["id"], "AB-123", "CD-456")["device"]
    # Through the API: listed and skipped.
    again = _add_serials(pg_client, world, device["id"], "ab-123")
    assert again["created"] == [] and again["skipped"][0]["reason"] == "already_recorded"
    # Renaming another unit to it: refused with a sentence.
    renamed = pg_client.patch(
        f"/devices/{device['id']}/units/{_unit_id(detail, 'CD-456')}",
        headers=_h(world),
        json={"serial_number": "Ab-123"},
    )
    assert renamed.status_code == 409
    assert "AB-123 is already recorded" in renamed.json()["detail"]
    # And the same serial on ANOTHER device is fine.
    other = _create(pg_client, world, name="Alexa Mini")
    assert _add_serials(pg_client, world, other["id"], "ab-123")["created"]
    # The database itself refuses a second key, whoever writes it.
    pg_db.add(
        DeviceUnit(
            company_id=world["company"].id,
            device_id=device["id"],
            serial_number="aB-123",
            serial_key="ab-123",
        )
    )
    with pytest.raises(IntegrityError):
        pg_db.commit()
    pg_db.rollback()


# ─── Retiring and reactivating ──────────────────────────────────────────────


def _events(pg_db, unit_id, action):
    return [
        event.changes
        for event in pg_db.query(AuditEvent).filter(
            AuditEvent.entity_id == unit_id, AuditEvent.action == action
        )
    ]


def test_retiring_reduces_the_quantity_by_default_and_the_event_says_so(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=3)
    detail = _add_serials(pg_client, world, device["id"], "A", "B")["device"]
    unit = _unit_id(detail, "A")
    response = pg_client.post(
        f"/devices/{device['id']}/units/{unit}/retire",
        headers=_h(world),
        json={"reason": "destroyed", "notes": "Fell off the crane"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["quantity_before"], body["quantity_after"]) == (3, 2)
    assert body["unit"]["status"] == "retired" and body["unit"]["retired_reason"] == "destroyed"
    assert body["unit"]["retired_on"] == date.today().isoformat()
    assert body["device"]["counts"]["not_yet_recorded"] == 1
    assert _quantity(pg_db, device["id"]) == 2
    changes = _events(pg_db, unit, "device_unit.retired")[0]
    assert changes["quantity_owned"] == [3, 2]
    assert changes["quantity_adjusted"] == [None, True]
    assert changes["status"] == ["active", "retired"]


def test_retiring_with_the_box_unticked_keeps_the_quantity(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=3)
    detail = _add_serials(pg_client, world, device["id"], "A", "B")["device"]
    unit = _unit_id(detail, "B")
    response = pg_client.post(
        f"/devices/{device['id']}/units/{unit}/retire",
        headers=_h(world),
        json={"reason": "kept", "reduce_quantity": False},
    )
    assert response.status_code == 200, response.text
    assert (response.json()["quantity_before"], response.json()["quantity_after"]) == (3, 3)
    assert _quantity(pg_db, device["id"]) == 3
    changes = _events(pg_db, unit, "device_unit.retired")[0]
    assert changes["quantity_adjusted"] == [None, False]
    assert "quantity_owned" not in changes


def test_retiring_a_mistake_needs_no_reason_and_never_goes_below_zero(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=0)
    detail = _add_serials(pg_client, world, device["id"], "TYPO")["device"]
    response = pg_client.post(
        f"/devices/{device['id']}/units/{_unit_id(detail, 'TYPO')}/retire",
        headers=_h(world),
        json={"notes": "Entered by mistake"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["unit"]["retired_reason"] is None
    assert _quantity(pg_db, device["id"]) == 0


def test_reactivating_offers_to_raise_the_quantity_and_does_by_default(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], quantity_owned=1)
    unit = make_device_unit(pg_db, world["company"], device, serial_number="X", status="retired", retired_reason="lost")
    response = pg_client.post(
        f"/devices/{device.id}/units/{unit.id}/reactivate", headers=_h(world), json={}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unit"]["status"] == "active" and body["unit"]["retired_reason"] is None
    assert (body["quantity_before"], body["quantity_after"]) == (1, 2)
    assert _events(pg_db, unit.id, "device_unit.reactivated")[0]["quantity_owned"] == [1, 2]


def test_reactivating_without_raising_keeps_the_quantity(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], quantity_owned=1)
    unit = make_device_unit(pg_db, world["company"], device, serial_number="X", status="retired", retired_reason="kept")
    response = pg_client.post(
        f"/devices/{device.id}/units/{unit.id}/reactivate",
        headers=_h(world),
        json={"raise_quantity": False},
    )
    assert response.status_code == 200
    assert _quantity(pg_db, device.id) == 1


def test_in_repair_and_back_never_touch_the_quantity(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=2)
    detail = _add_serials(pg_client, world, device["id"], "A")["device"]
    unit = _unit_id(detail, "A")
    path = f"/devices/{device['id']}/units/{unit}"
    repair = pg_client.patch(path, headers=_h(world), json={"status": "in_repair"})
    assert repair.json()["device"]["counts"]["in_repair"] == 1
    back = pg_client.patch(path, headers=_h(world), json={"status": "active"})
    assert back.json()["unit"]["status"] == "active"
    assert _quantity(pg_db, device["id"]) == 2
    # A retired unit cannot be PATCHed back: that path asks about quantity.
    pg_client.post(f"{path}/retire", headers=_h(world), json={})
    refused = pg_client.patch(path, headers=_h(world), json={"status": "active"})
    assert refused.status_code == 422
    assert "Back to active" in refused.json()["detail"]


def test_the_quantity_may_always_be_set_by_hand_and_is_its_own_event(pg_client, pg_db, world):
    device = _create(pg_client, world, quantity_owned=3)
    _add_serials(pg_client, world, device["id"], "A", "B", "C")
    response = pg_client.patch(
        f"/devices/{device['id']}", headers=_h(world), json={"quantity_owned": 1, "notes": "Two sold"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["beyond_quantity"] == 2
    actions = {
        event.action: event.changes
        for event in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == device["id"])
    }
    assert actions["device.quantity_set"] == {"quantity_owned": [3, 1]}
    assert actions["device.updated"] == {"notes": [None, "Two sold"]}


# ─── Devices ────────────────────────────────────────────────────────────────


def test_one_active_device_of_a_name_and_an_archived_one_does_not_count(pg_client, world):
    first = _create(pg_client, world, name="Alexa 35")
    clash = pg_client.post("/devices", headers=_h(world), json={"name": " alexa  35 "})
    assert clash.status_code == 409
    assert "You already have a device called Alexa 35" in clash.json()["detail"]
    archived = pg_client.post(f"/devices/{first['id']}/archive", headers=_h(world))
    assert archived.json()["is_active"] is False
    second = _create(pg_client, world, name="ALEXA 35")
    assert second["is_active"] is True
    back = pg_client.post(f"/devices/{first['id']}/reactivate", headers=_h(world))
    assert back.status_code == 409
    listed = pg_client.get("/devices", headers=_h(world)).json()
    assert [row["name"] for row in listed["devices"]] == ["ALEXA 35"]
    every = pg_client.get("/devices?include_inactive=true", headers=_h(world)).json()
    assert len(every["devices"]) == 2


def test_a_device_links_only_to_an_equipment_item_of_this_company(pg_client, pg_db, world):
    camera = make_item(pg_db, world["company"], sku="CAM-1", name_de="Alexa 35 Paket", kind="equipment_rental")
    labor = make_item(pg_db, world["company"], sku="L-1", name_de="Kameramann", kind="labor")
    other_company = make_company(pg_db, "Else GmbH")
    theirs = make_item(pg_db, other_company, sku="CAM-9", name_de="Their camera")

    linked = _create(pg_client, world, catalog_item_id=str(camera.id))
    assert linked["catalog_item_name"] == "Alexa 35 Paket"
    refused = pg_client.post(
        "/devices", headers=_h(world), json={"name": "X", "catalog_item_id": str(labor.id)}
    )
    assert refused.status_code == 422 and "not equipment" in refused.json()["detail"]
    hidden = pg_client.post(
        "/devices", headers=_h(world), json={"name": "Y", "catalog_item_id": str(theirs.id)}
    )
    assert hidden.status_code == 404


def test_search_category_filter_and_suggestions(pg_client, pg_db, world):
    from .company_factories import make_item_category

    make_item_category(pg_db, world["company"], name="Grip", kind="equipment_rental")
    _create(pg_client, world, name="Alexa 35", category="Camera")
    lamp = _create(pg_client, world, name="SkyPanel S60", category="Light", purchase_price=None)
    _add_serials(pg_client, world, lamp["id"], "SP-777")

    def names(query):
        return [row["name"] for row in pg_client.get(f"/devices?{query}", headers=_h(world)).json()["devices"]]

    assert names("search=sky") == ["SkyPanel S60"]
    assert names("search=sp-777") == ["SkyPanel S60"]
    assert names("category=camera") == ["Alexa 35"]
    page = pg_client.get("/devices", headers=_h(world)).json()
    assert page["categories"][:2] == ["Camera", "Light"] and "Grip" in page["categories"]


# ─── The import ─────────────────────────────────────────────────────────────


def _gear_csv(*lines, sep=";"):
    header = sep.join(["name", "category", "quantity", "price", "serials", "notes"])
    return ("\r\n".join([header, *lines]) + "\r\n").encode("utf-8")


def _preview(pg_client, world, payload):
    response = pg_client.post(
        "/devices/import/preview", headers=_h(world), files={"file": ("gear.csv", payload, "text/csv")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _apply(pg_client, world, payload, digest, include=()):
    data = {"digest": digest}
    if include:
        data["include_lines"] = ",".join(map(str, include))
    return pg_client.post(
        "/devices/import", headers=_h(world), files={"file": ("gear.csv", payload, "text/csv")}, data=data
    )


def test_the_import_creates_exactly_what_the_preview_showed(pg_client, pg_db, world):
    existing = make_device(pg_db, world["company"], name="Alexa 35", quantity_owned=3)
    archived = make_device(pg_db, world["company"], name="Old Dolly", quantity_owned=1, is_active=False)
    payload = _gear_csv(
        "SkyPanel S60;Light;4;3.250,50;SP-1|SP-2|sp-1;",
        "Sennheiser MKH 416;Sound;2;999.90;;Boom",
        "Alexa 35;Camera;5;1;NEW-1;",
        "Old Dolly;Grip;1;;;",
        "Bad price;Light;1;1.500;;",
        "Bad qty;Light;drei;;;",
        ";Light;1;;;",
    )
    preview = _preview(pg_client, world, payload)
    rows = {row["line_number"]: row for row in preview["rows"]}
    assert (preview["created"], preview["duplicates"], preview["errors"]) == (2, 2, 3)
    assert rows[2]["price"] == "3250.50" and rows[2]["serials"] == ["SP-1", "SP-2"]
    assert "repeated serial number" in rows[2]["message"]
    assert rows[3]["price"] == "999.90"
    assert rows[4]["action"] == "duplicate" and rows[4]["can_include"] is False
    assert rows[5]["action"] == "duplicate" and rows[5]["can_include"] is True
    assert "ambiguous" in rows[6]["message"]
    assert "not a whole number" in rows[7]["message"]

    applied = _apply(pg_client, world, payload, preview["digest"], include=[4, 5])
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert (body["created"], body["units_created"], body["skipped"], body["errors"]) == (3, 2, 1, 3)
    actions = {row["line_number"]: row["action"] for row in body["rows"]}
    assert (actions[2], actions[3], actions[4], actions[5]) == ("created", "created", "skipped", "created")

    pg_db.expire_all()
    sky = pg_db.query(Device).filter(Device.name == "SkyPanel S60").one()
    assert (sky.quantity_owned, sky.purchase_price, sky.category) == (4, Decimal("3250.50"), "Light")
    assert sorted(u.serial_number for u in pg_db.query(DeviceUnit).filter(DeviceUnit.device_id == sky.id)) == ["SP-1", "SP-2"]
    # The existing active device: untouched, no unit added.
    kept = pg_db.get(Device, existing.id)
    assert kept.quantity_owned == 3 and kept.purchase_price == Decimal("65000.00")
    assert pg_db.query(DeviceUnit).filter(DeviceUnit.device_id == existing.id).count() == 0
    # The archived one stays archived; the ticked line is a new device.
    assert pg_db.get(Device, archived.id).is_active is False
    assert pg_db.query(Device).filter(Device.name == "Old Dolly").count() == 2
    applied_event = pg_db.query(AuditEvent).filter(AuditEvent.action == "devices.import_applied").one()
    assert applied_event.changes["devices_created"] == [None, 3]
    assert applied_event.changes["units_created"] == [None, 2]


def test_a_file_that_moved_since_the_preview_writes_nothing(pg_client, pg_db, world):
    payload = _gear_csv("SkyPanel S60;Light;4;;;")
    preview = _preview(pg_client, world, payload)
    _create(pg_client, world, name="SkyPanel S60")
    response = _apply(pg_client, world, payload, preview["digest"])
    assert response.status_code == 409
    assert pg_db.query(Device).count() == 1


def test_the_export_is_the_import_format(pg_client, pg_db, world):
    device = _create(pg_client, world, name="Alexa 35", quantity_owned=3, purchase_price="65000.00", notes="Body only")
    detail = _add_serials(pg_client, world, device["id"], "B-2", "A-1", "C-3")["device"]
    pg_client.post(f"/devices/{device['id']}/units/{_unit_id(detail, 'C-3')}/retire", headers=_h(world), json={"reduce_quantity": False})
    response = pg_client.get("/devices/export.csv", headers=_h(world))
    assert response.status_code == 200
    lines = response.content.decode("utf-8-sig").splitlines()
    assert lines == ["name;category;quantity;price;serials;notes", "Alexa 35;Camera;3;65000,00;A-1|B-2;Body only"]
    again = _preview(pg_client, world, response.content)
    assert again["rows"][0]["action"] == "duplicate"


# ─── Who sees what ──────────────────────────────────────────────────────────


@pytest.fixture
def every_role(pg_db, world):
    users = {}
    for role in CompanyRole:
        user = make_user(pg_db, f"gear-role-{role.value}@example.com")
        grant(pg_db, world["company"], user, role)
        users[role] = user
    pg_db.commit()
    return users


def test_the_two_keys_and_their_defaults():
    assert set(PERMISSIONS["devices.view"]) == {
        CompanyRole.owner,
        CompanyRole.admin,
        CompanyRole.producer,
        CompanyRole.accountant,
        CompanyRole.staff,
    }
    assert set(PERMISSIONS["devices.edit"]) == {CompanyRole.owner, CompanyRole.admin, CompanyRole.producer}
    assert "devices.view" in permissions_for(CompanyRole.staff)
    assert "devices.view" not in permissions_for(CompanyRole.tax_advisor)


def _every_device_request(device_id, unit_id):
    """Every operation on the gear surface. Counted against OpenAPI below,
    so a new route cannot stay out of the staff walk."""
    base = f"/devices/{device_id}"
    unit = f"{base}/units/{unit_id}"
    gear = ("gear.csv", b"name;quantity\nNew lamp;1\n", "text/csv")
    return [
        ("get", "/devices", None, None),
        ("get", "/devices/export.csv", None, None),
        ("post", "/devices", {"name": "Staff device"}, None),
        ("post", "/devices/import/preview", None, {"file": gear}),
        ("post", "/devices/import", None, {"file": gear}),
        ("get", base, None, None),
        ("patch", base, {"notes": "staff"}, None),
        ("post", f"{base}/archive", None, None),
        ("post", f"{base}/reactivate", None, None),
        ("post", f"{base}/units", {"serial_numbers": ["STAFF-1"]}, None),
        ("patch", unit, {"notes": "staff"}, None),
        ("post", f"{unit}/retire", {}, None),
        ("post", f"{unit}/reactivate", {}, None),
    ]


def test_the_gear_surface_in_the_walk_is_the_whole_surface_and_has_no_delete(client):
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    operations = sorted(
        f"{method.upper()} {path}"
        for path, methods in schema["paths"].items()
        for method in methods
        if path.startswith("/devices")
    )
    assert len(operations) == len(_every_device_request("d", "u")), operations
    assert not [op for op in operations if op.startswith("DELETE")]


def _send(pg_client, method, url, body, files, headers):
    kwargs = {"headers": headers}
    if files is not None:
        kwargs["files"] = files
    elif body is not None:
        kwargs["json"] = body
    return getattr(pg_client, method)(url, **kwargs)


def test_staff_see_devices_and_counts_but_no_price_and_can_change_nothing(
    pg_client, pg_db, world, every_role
):
    device = _create(pg_client, world, purchase_price="65432.10")
    detail = _add_serials(pg_client, world, device["id"], "AX-1")["device"]
    unit = _unit_id(detail, "AX-1")
    staff = _h(world, every_role[CompanyRole.staff])

    for method, url, body, files in _every_device_request(device["id"], unit):
        response = _send(pg_client, method, url, body, files, staff)
        expected = 200 if method == "get" else 404
        assert response.status_code == expected, f"staff {method} {url}: {response.text}"
        if method == "get":
            # The positive marker first: the API says the price is withheld…
            if url.endswith(".csv"):
                assert response.content.decode("utf-8-sig").splitlines()[1].startswith("Alexa 35;Camera;3;;AX-1")
            else:
                body_json = response.json()
                rows = body_json["devices"] if "devices" in body_json else [body_json]
                assert rows and all(row["price_visible"] is False for row in rows)
                assert all(row["purchase_price"] is None for row in rows)
                assert rows[0]["counts"]["recorded"] == 1
            # …and the stored amount appears nowhere in what staff received.
            assert "65432" not in response.text

    # The owner, through the very same requests, does see it.
    owner_detail = pg_client.get(f"/devices/{device['id']}", headers=_h(world)).json()
    assert owner_detail["price_visible"] is True and owner_detail["purchase_price"] == "65432.10"
    owner_csv = pg_client.get("/devices/export.csv", headers=_h(world)).content.decode("utf-8-sig")
    assert "65432,10" in owner_csv
    # Nothing staff sent changed anything.
    pg_db.expire_all()
    assert pg_db.query(Device).count() == 1
    assert pg_db.get(Device, device["id"]).notes is None
    assert pg_db.query(DeviceUnit).count() == 1


@pytest.mark.parametrize(
    "role, can_view, can_edit, sees_price",
    [
        (CompanyRole.owner, True, True, True),
        (CompanyRole.admin, True, True, True),
        (CompanyRole.producer, True, True, True),
        (CompanyRole.accountant, True, False, True),
        (CompanyRole.staff, True, False, False),
        (CompanyRole.tax_advisor, False, False, False),
    ],
    ids=lambda value: value.value if isinstance(value, CompanyRole) else str(value),
)
def test_the_gear_permission_matrix(pg_client, pg_db, world, every_role, role, can_view, can_edit, sees_price):
    device = make_device(pg_db, world["company"], name="Matrix cam")
    headers = _h(world, every_role[role])
    read = pg_client.get(f"/devices/{device.id}", headers=headers)
    assert read.status_code == (200 if can_view else 404)
    if can_view:
        assert read.json()["price_visible"] is sees_price
        assert read.json()["purchase_price"] == ("65000.00" if sees_price else None)
    made = pg_client.post("/devices", headers=headers, json={"name": f"By {role.value}"})
    assert made.status_code == (201 if can_edit else 404), made.text


def test_a_negative_purchase_price_is_refused_with_a_sentence(pg_client, world):
    response = pg_client.post("/devices", headers=_h(world), json={"name": "X", "purchase_price": "-1.00"})
    assert response.status_code == 422
    assert "cannot be negative" in response.json()["detail"]
