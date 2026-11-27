"""A device's weight, size and cover picture (B2b-2 part 5). Real PostgreSQL.

The bucket is replaced by an in-memory one (`FakeS3`) because CI has no
object store. It replaces the S3 CLIENT at the two places that create one
— `services/s3_service.get_s3_client`, which `put_object` calls, and the
name `routers/files.py` imported, which the object proxy calls — and every
test that uploads asserts the fake really holds the objects afterwards, so
a path that skipped the store could not pass (rule 17b). Everything else —
Pillow, the token, the proxy route, the database — is real.

What this file is about: the TYPE is decided by the content (a text file
renamed `.jpg` is refused), and a thumbnail URL reads that device's
folder and no other company's.
"""

import io
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.device import Device
from apps.api.models.site_settings import SiteSettings

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_device,
    make_superadmin,
    make_user,
)


class FakeS3:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[Key] = bytes(Body)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket, Key, Range=None):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        data = self.objects[Key]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def delete_object(self, Bucket, Key):  # pragma: no cover - nothing may call it
        raise AssertionError(f"an object was deleted: {Key}")


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("apps.api.services.s3_service.get_s3_client", lambda *a, **k: fake)
    monkeypatch.setattr("apps.api.routers.files.get_s3_client", lambda *a, **k: fake)
    return fake


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "pic-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner, "device": make_device(pg_db, company, name="Alexa 35")}


def _h(world, user=None, company=None):
    return auth_headers(user or world["owner"], company or world["company"])


def picture(kind="PNG", size=(800, 600), color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=kind)
    return out.getvalue()


def _upload(pg_client, headers, device_id, payload, name="photo.png", content_type="image/png"):
    return pg_client.post(
        f"/devices/{device_id}/image", headers=headers, files={"file": (name, payload, content_type)}
    )


# ─── Weight and size ────────────────────────────────────────────────────────


def test_weight_and_size_keep_their_decimals_and_refuse_negatives(pg_client, pg_db, world):
    device = world["device"]
    saved = pg_client.patch(
        f"/devices/{device.id}",
        headers=_h(world),
        json={"weight_kg": "12.345", "length_cm": "60.5", "width_cm": "30", "height_cm": "25.0"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["weight_kg"], body["length_cm"], body["width_cm"], body["height_cm"]) == (
        "12.345",
        "60.5",
        "30.0",
        "25.0",
    )
    pg_db.expire_all()
    row = pg_db.get(Device, device.id)
    assert (row.weight_kg, row.length_cm) == (Decimal("12.345"), Decimal("60.5"))

    negative = pg_client.patch(f"/devices/{device.id}", headers=_h(world), json={"height_cm": "-1"})
    assert negative.status_code == 422
    assert negative.json()["detail"].startswith("The height cannot be negative.")
    too_fine = pg_client.patch(f"/devices/{device.id}", headers=_h(world), json={"weight_kg": "1.2345"})
    assert too_fine.status_code == 422 and "at most 3 decimals" in too_fine.json()["detail"]
    pg_db.expire_all()
    assert pg_db.get(Device, device.id).weight_kg == Decimal("12.345")

    made = pg_client.post("/devices", headers=_h(world), json={"name": "Dolly", "weight_kg": "-0.5"})
    assert made.status_code == 422


def test_staff_see_size_and_weight(pg_client, pg_db, world):
    device = world["device"]
    pg_client.patch(f"/devices/{device.id}", headers=_h(world), json={"weight_kg": "3.9"})
    staff = make_user(pg_db, "pic-staff@example.com")
    grant(pg_db, world["company"], staff, CompanyRole.staff)
    pg_db.commit()
    read = pg_client.get(f"/devices/{device.id}", headers=_h(world, staff))
    assert read.json()["weight_kg"] == "3.900"
    refused = pg_client.patch(f"/devices/{device.id}", headers=_h(world, staff), json={"weight_kg": "1"})
    assert refused.status_code == 404


# ─── The picture ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["JPEG", "PNG", "WEBP"])
def test_jpeg_png_and_webp_are_accepted_resized_and_given_a_thumbnail(pg_client, pg_db, world, s3, kind):
    device = world["device"]
    response = _upload(pg_client, _h(world), device.id, picture(kind, size=(3000, 2000)), name=f"x.{kind.lower()}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["image_url"].startswith("/files/object/") and body["thumbnail_url"].startswith("/files/object/")

    pg_db.expire_all()
    key = pg_db.get(Device, device.id).image_s3_key
    assert key.startswith(f"devices/{world['company'].id}/{device.id}/")
    # The positive marker: the store really holds both, re-encoded.
    thumb_key = key.replace(".webp", "-thumb.webp")
    assert set(s3.objects) == {key, thumb_key}
    big = Image.open(io.BytesIO(s3.objects[key]))
    small = Image.open(io.BytesIO(s3.objects[thumb_key]))
    assert (big.format, max(big.size)) == ("WEBP", 1600)
    assert (small.format, max(small.size)) == ("WEBP", 160)

    # And the browser gets the thumbnail through the proxy with that URL.
    served = pg_client.get(body["thumbnail_url"])
    assert served.status_code == 200
    assert served.content == s3.objects[thumb_key]


def test_a_text_file_renamed_jpg_is_refused(pg_client, pg_db, world, s3):
    device = world["device"]
    response = _upload(
        pg_client, _h(world), device.id, b"this is a shopping list, not a photo\n", name="camera.jpg", content_type="image/jpeg"
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "That file is not a JPEG, PNG or WebP picture. Choose a photo of the device."
    assert s3.objects == {}
    pg_db.expire_all()
    assert pg_db.get(Device, device.id).image_s3_key is None


def test_a_real_picture_of_another_format_is_refused(pg_client, world, s3):
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    response = _upload(pg_client, _h(world), world["device"].id, gif.getvalue(), name="anim.png")
    assert response.status_code == 422 and "not a JPEG, PNG or WebP" in response.json()["detail"]
    assert s3.objects == {}


def test_an_oversized_file_is_refused(pg_client, world, s3):
    response = _upload(pg_client, _h(world), world["device"].id, b"\x89PNG" + b"0" * (5 * 1024 * 1024))
    assert response.status_code == 422
    assert "the limit is 5 MB" in response.json()["detail"]
    assert s3.objects == {}


def test_replace_and_remove_keep_the_objects_and_say_so_in_the_audit(pg_client, pg_db, world, s3):
    device = world["device"]
    first = _upload(pg_client, _h(world), device.id, picture())
    assert first.status_code == 200
    pg_db.expire_all()
    first_key = pg_db.get(Device, device.id).image_s3_key
    second = _upload(pg_client, _h(world), device.id, picture("JPEG", color=(0, 0, 200)))
    pg_db.expire_all()
    second_key = pg_db.get(Device, device.id).image_s3_key
    assert second_key != first_key
    assert first_key in s3.objects and second_key in s3.objects  # nothing hard-deleted

    removed = pg_client.post(f"/devices/{device.id}/image/remove", headers=_h(world))
    assert removed.status_code == 200
    assert (removed.json()["image_url"], removed.json()["thumbnail_url"]) == (None, None)
    pg_db.expire_all()
    assert pg_db.get(Device, device.id).image_s3_key is None
    events = [
        (e.action, e.changes["image_s3_key"])
        for e in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == device.id, AuditEvent.action.like("device.image%"))
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ]
    assert events == [
        ("device.image_changed", [None, first_key]),
        ("device.image_changed", [first_key, second_key]),
        ("device.image_removed", [second_key, None]),
    ]


def test_staff_see_the_picture_and_cannot_change_it(pg_client, pg_db, world, s3):
    device = world["device"]
    _upload(pg_client, _h(world), device.id, picture())
    staff = make_user(pg_db, "pic-staff2@example.com")
    grant(pg_db, world["company"], staff, CompanyRole.staff)
    pg_db.commit()
    read = pg_client.get(f"/devices/{device.id}", headers=_h(world, staff)).json()
    assert read["thumbnail_url"].startswith("/files/object/")
    listed = pg_client.get("/devices", headers=_h(world, staff)).json()["devices"][0]
    assert listed["thumbnail_url"].startswith("/files/object/")
    assert _upload(pg_client, _h(world, staff), device.id, picture()).status_code == 404
    assert pg_client.post(f"/devices/{device.id}/image/remove", headers=_h(world, staff)).status_code == 404


def test_a_thumbnail_is_served_only_to_its_own_company(pg_client, pg_db, world, s3):
    """Company B's member never gets A's URL (404 on the device), and A's
    URL cannot be walked over to B's folder: the token is scoped to A's
    device, so the same filename under B's folder resolves to nothing."""
    other = make_company(pg_db, "Other Films GmbH")
    bo = make_user(pg_db, "pic-bo@example.com")
    grant(pg_db, other, bo, CompanyRole.owner)
    pg_db.commit()
    theirs = make_device(pg_db, other, name="Their camera")

    mine = _upload(pg_client, _h(world), world["device"].id, picture()).json()
    their_upload = _upload(pg_client, _h(world, bo, other), theirs.id, picture(color=(0, 200, 0)))
    assert their_upload.status_code == 200, their_upload.text
    assert len(s3.objects) == 4  # both companies' pictures really stored

    # B's member asking for A's device: not found, so no URL at all.
    assert pg_client.get(f"/devices/{world['device'].id}", headers=_h(world, bo, other)).status_code == 404
    assert "Their camera" not in pg_client.get("/devices", headers=_h(world)).text

    # A's token, aimed at B's thumbnail.
    token = parse_qs(urlparse(mine["thumbnail_url"]).query)["token"][0]
    pg_db.expire_all()
    their_key = pg_db.get(Device, theirs.id).image_s3_key.replace(".webp", "-thumb.webp")
    relative = their_key.split("/", 1)[1]  # "<company>/<device>/<file>"
    walked = pg_client.get(f"/files/object/{relative}", params={"token": token})
    assert walked.status_code == 404
    # The same token does read A's own thumbnail (the check above is not a
    # broken token passing for a refusal).
    own = pg_client.get(mine["thumbnail_url"])
    assert own.status_code == 200 and own.content == s3.objects[
        pg_db.get(Device, world["device"].id).image_s3_key.replace(".webp", "-thumb.webp")
    ]

    # And uploading to B's device from A: not found, nothing stored.
    before = dict(s3.objects)
    assert _upload(pg_client, _h(world), theirs.id, picture()).status_code == 404
    assert s3.objects == before
