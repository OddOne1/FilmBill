"""Object proxy for stored files (avatars, brand images, later: document PDFs).

Derived from FreeFrame's `routers/hls_proxy.py`, with everything HLS-specific
removed — there are no manifests, segments or video in FilmBill. What is kept
is the mechanism the rest of the platform depends on: every object a browser
needs is proxied through this API container instead of being handed out as a
direct presigned S3 URL, so the S3/MinIO bucket never has to be reachable from
outside the Docker/LAN network. Access is gated by a short-lived JWT scoped to
the object's S3 prefix (`create_object_token` / `proxy_url_for`), handed out
only to callers who already passed an authorisation check, so this route does
not have to repeat that check per request.

CLAUDE.md rule 16 is the other half of the same story: when a presigned URL is
genuinely needed, it must come from the presign client that uses
S3_PUBLIC_ENDPOINT, because Safari blocks mixed content. Proxying avoids the
question entirely for the read paths that go through here.
"""

import logging
import posixpath
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from jose import jwt, JWTError
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..config import settings
from ..services.s3_service import get_s3_client, CONTENT_TYPE_MAP

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

CHUNK_SIZE = 1024 * 1024  # 1 MB, used when streaming object bodies through


def create_object_token(s3_prefix: str, expires_hours: int = 24) -> str:
    """Create a short-lived JWT scoped to everything under an S3 prefix."""
    payload = {
        "sub": "file",
        "pfx": s3_prefix,
        "exp": datetime.now(timezone.utc) + timedelta(hours=expires_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _verify_object_token(token: str) -> str:
    """Verify a proxy token and return its s3_prefix."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("sub") != "file":
            raise HTTPException(status_code=403, detail="Invalid token type")
        return payload["pfx"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def proxy_url_for(s3_key: str, expires_hours: int = 24, download_filename: str | None = None) -> str:
    """Build a relative, token-authenticated proxy URL for a single S3 object.

    Use this everywhere a direct presigned S3 URL would otherwise be returned
    to a browser. The bucket itself never has to be reachable by the client.
    """
    prefix, filename = posixpath.split(s3_key)
    token = create_object_token(prefix, expires_hours)
    url = f"/files/object/{filename}?token={token}"
    if download_filename:
        url += f"&download={quote(download_filename)}"
    return url


# `bytes=<first>-<last>`, `bytes=<first>-` and `bytes=-<suffix-length>` are
# the three single-range forms RFC 9110 §14.1.1 defines. Anything else
# (notably a multi-range request, `bytes=0-99,200-299`) is deliberately not
# supported: serving one would mean building a multipart/byteranges body,
# and no client this proxy actually serves — a browser resuming a download,
# curl --continue-at — ever asks for one.
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

RANGE_UNSATISFIABLE = "unsatisfiable"


def _parse_range_header(value: str, total: int):
    """Resolve a Range header against a known object size.

    Returns an inclusive `(start, end)` pair, `RANGE_UNSATISFIABLE` when the
    range falls entirely past the end of the object, or `None` when the
    header should simply be ignored and the whole object served. Per RFC
    9110 §14.2 an unparseable Range header is ignored, not rejected — that
    is what keeps a malformed or multi-range header degrading into a plain
    200 instead of failing the download outright.
    """
    match = _RANGE_RE.match(value.strip())
    if not match:
        return None

    first, last = match.group(1), match.group(2)

    if not first and not last:
        return None

    if total == 0:
        return RANGE_UNSATISFIABLE

    if not first:
        # Suffix range: the last N bytes. N == 0 is meaningless, not a range.
        suffix = int(last)
        if suffix == 0:
            return RANGE_UNSATISFIABLE
        start = max(total - suffix, 0)
        end = total - 1
    else:
        start = int(first)
        end = int(last) if last else total - 1
        if start >= total:
            return RANGE_UNSATISFIABLE
        if end < start:
            return None
        end = min(end, total - 1)

    return start, end


def _sanitize_download_filename(name: str) -> str:
    safe = re.sub(r"[\x00-\x1f\x7f]", "", name)
    return safe.replace("\\", "\\\\").replace('"', '\\"')


@router.get("/object/{path:path}")
def serve_object(
    path: str,
    token: str = Query(...),
    download: str | None = Query(default=None),
    range_header: str | None = Header(default=None, alias="Range"),
):
    """Stream any object under a token's S3 prefix through this container.

    Range-capable: without `Accept-Ranges`/`Content-Length` a browser has no
    way to resume a dropped transfer, and a multi-MB PDF that fails at 80 %
    would restart from byte 0.
    """
    s3_prefix = _verify_object_token(token)

    # Prevent directory traversal
    normalised = posixpath.normpath(path)
    if normalised.startswith("..") or normalised.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    # Defense-in-depth: verify resolved key stays within the token's prefix
    s3_key = f"{s3_prefix}/{normalised}"
    if not s3_key.startswith(s3_prefix + "/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    s3 = get_s3_client()

    ext = posixpath.splitext(normalised)[1].lower()
    content_type, cache_control = CONTENT_TYPE_MAP.get(ext, ("application/octet-stream", "no-cache"))

    byte_range = None
    total = None
    if range_header:
        # The object's size has to be known before the range can be resolved
        # (an open-ended `bytes=500-` or a suffix `bytes=-500` is meaningless
        # without it), and it is the denominator of every Content-Range this
        # branch emits. One extra HEAD against the LAN bucket, and only on a
        # request that actually carries a Range header.
        try:
            head = s3.head_object(Bucket=settings.s3_bucket, Key=s3_key)
        except Exception as e:
            logger.error("Failed to stat object %s: %s", s3_key, e)
            raise HTTPException(status_code=404, detail="Object not found")

        total = head["ContentLength"]
        byte_range = _parse_range_header(range_header, total)

        if byte_range == RANGE_UNSATISFIABLE:
            raise HTTPException(
                status_code=416,
                detail="Requested range not satisfiable",
                headers={"Content-Range": f"bytes */{total}", "Accept-Ranges": "bytes"},
            )

    get_kwargs = {"Bucket": settings.s3_bucket, "Key": s3_key}
    if byte_range:
        get_kwargs["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"

    try:
        obj = s3.get_object(**get_kwargs)
    except s3.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail="Object not found")
    except Exception as e:
        logger.error("Failed to fetch object %s: %s", s3_key, e)
        raise HTTPException(status_code=404, detail="Object not found")

    headers = {"Cache-Control": cache_control, "Accept-Ranges": "bytes"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{_sanitize_download_filename(download)}"'

    if byte_range:
        start, end = byte_range
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        headers["Content-Length"] = str(end - start + 1)
        status_code = 206
    else:
        # Read defensively — every real S3 response carries it, but falling
        # back to chunked transfer encoding beats a 500 if some
        # S3-compatible backend ever omits it.
        if obj.get("ContentLength") is not None:
            headers["Content-Length"] = str(obj["ContentLength"])
        status_code = 200

    def _stream():
        body = obj["Body"]
        while True:
            chunk = body.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        _stream(),
        status_code=status_code,
        media_type=content_type,
        headers=headers,
    )
