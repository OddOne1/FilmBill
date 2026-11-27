"""A device's cover picture (B2b-2 part 5). No commit here.

**The logo's pattern, not a second one.** Like the site logo and the avatar
(`routers/site_settings.py`, `routers/users.py`), the bytes go through this
API container into the bucket with `s3_service.put_object`, and a browser
reads them back only through the token-scoped object proxy
(`routers/files.proxy_url_for`) — the bucket is never reachable from outside
(CLAUDE.md rule 16). What this adds, because a picture comes from a phone or
a camera and not from an administrator:

* **The type is decided by the content.** Pillow opens the bytes and they
  must decode as JPEG, PNG or WebP; the file name and the browser's content
  type are not consulted. A text file renamed `.jpg` is refused.
* **A size limit** (`MAX_IMAGE_BYTES`, 5 MB) and a pixel limit, so a
  decompression bomb is refused before it is decoded.
* **Resized and re-encoded** to WebP at most `MAX_EDGE` pixels on the long
  side, the camera's rotation applied, metadata (GPS included) dropped; and
  a thumbnail of at most `THUMB_EDGE` pixels stored beside it.

**Where it lives.** `devices/<company>/<device>/<uuid>.webp` and
`…/<uuid>-thumb.webp`. The proxy token is scoped to the device's own folder,
so a URL handed to one company's member reads that device's objects and
nothing else. The URL is only handed out in responses gated by
`devices.view` in that company.

**Replacing or removing keeps the old objects**, exactly as a replaced logo
does: the column moves to the new key (or to NULL) and the old key is in
the audit event, which is the cleanup path a later storage sweep can use.
Nothing is hard-deleted here.
"""

import io
import posixpath
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from . import s3_service

MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: Pixels, not bytes: a 5 MB PNG can still decode to gigabytes.
MAX_PIXELS = 50_000_000
MAX_EDGE = 1600
THUMB_EDGE = 160
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class ImageRefused(ValueError):
    """A sentence for the person; the router answers 422."""


def thumbnail_key(image_key: str) -> str:
    root, _ = posixpath.splitext(image_key)
    return f"{root}-thumb.webp"


def _encode(image: Image.Image, edge: int) -> bytes:
    copy = image.copy()
    copy.thumbnail((edge, edge), Image.LANCZOS)
    out = io.BytesIO()
    copy.save(out, format="WEBP", quality=85, method=4)
    return out.getvalue()


def process(payload: bytes) -> tuple[bytes, bytes]:
    """`(picture, thumbnail)` as WebP bytes, or `ImageRefused`."""
    if len(payload) > MAX_IMAGE_BYTES:
        raise ImageRefused(
            f"That picture is {len(payload) / (1024 * 1024):.1f} MB and the limit is "
            f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB. Choose a smaller photo of the device."
        )
    if not payload:
        raise ImageRefused("That file is empty. Choose a photo of the device.")
    try:
        with Image.open(io.BytesIO(payload)) as probe:
            kind = probe.format
            width, height = probe.size
            if kind not in ALLOWED_FORMATS:
                raise ImageRefused(
                    "That file is not a JPEG, PNG or WebP picture. Choose a photo of the device."
                )
            if width * height > MAX_PIXELS:
                raise ImageRefused(
                    f"That picture is {width} × {height} pixels, too large to process. "
                    f"Choose a smaller photo of the device."
                )
            probe.load()
            image = ImageOps.exif_transpose(probe)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    except ImageRefused:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise ImageRefused(
            "That file is not a JPEG, PNG or WebP picture. Choose a photo of the device."
        ) from None
    return _encode(image, MAX_EDGE), _encode(image, THUMB_EDGE)


def store(company_id: uuid.UUID, device_id: uuid.UUID, payload: bytes) -> str:
    """Process and upload; return the new picture's key."""
    picture, thumb = process(payload)
    key = f"devices/{company_id}/{device_id}/{uuid.uuid4()}.webp"
    s3_service.put_object(key, picture, content_type="image/webp", cache_control="max-age=86400")
    s3_service.put_object(thumbnail_key(key), thumb, content_type="image/webp", cache_control="max-age=86400")
    return key
