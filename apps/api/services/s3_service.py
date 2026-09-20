import os
import re
import boto3
from botocore.exceptions import ClientError
from ..config import settings

# S3 Content-Type and Cache-Control mappings.
#
# Only the types FilmBill actually stores: brand images and avatars today,
# generated documents from P2 on. An unknown extension falls back to
# application/octet-stream + no-cache in get_content_type() below, which is
# the safe answer rather than a guess.
CONTENT_TYPE_MAP = {
    ".jpg": ("image/jpeg", "max-age=86400"),
    ".jpeg": ("image/jpeg", "max-age=86400"),
    ".png": ("image/png", "max-age=86400"),
    ".webp": ("image/webp", "max-age=86400"),
    ".svg": ("image/svg+xml", "max-age=86400"),
    ".ico": ("image/x-icon", "max-age=86400"),
    ".json": ("application/json", "max-age=86400"),
    # Documents are re-generated on demand and may be replaced by a revision
    # (CLAUDE.md rule 3), so a stale copy in a proxy cache would be a wrong
    # invoice rather than a stale thumbnail.
    ".pdf": ("application/pdf", "no-store"),
    ".xml": ("application/xml", "no-store"),
    ".zip": ("application/zip", "no-store"),
}

def _is_aws_s3() -> bool:
    """Check if using AWS S3 (vs MinIO/local). Controlled by S3_STORAGE env var."""
    return settings.s3_storage.lower() == "s3"

def get_s3_client():
    """
    Create S3 client. Auto-detects AWS vs MinIO:
    - S3_STORAGE=s3 -> use AWS S3 (no endpoint_url)
    - Otherwise -> use custom endpoint (MinIO or S3-compatible)
    """
    if _is_aws_s3():
        # Real AWS S3 - don't pass endpoint_url
        return boto3.client(
            "s3",
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
    else:
        # MinIO or S3-compatible storage
        return boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

def _get_presign_client():
    """
    Client for generating presigned URLs. Uses s3_public_endpoint if set,
    so presigned URLs are reachable from the browser (e.g. localhost:9100
    instead of minio:9000 inside Docker).

    CLAUDE.md rule 16: every browser-facing presigned URL must come from
    HERE, not from get_s3_client(). A URL built against the internal
    endpoint is either unreachable or plain http:// from an https:// page,
    which Safari blocks as mixed content.
    """
    endpoint = settings.s3_public_endpoint or (None if _is_aws_s3() else settings.s3_endpoint)
    kwargs = {
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region,
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **kwargs)

def ensure_bucket_exists():
    """Create the S3 bucket if it does not exist. Called on app startup."""
    s3 = get_s3_client()
    try:
        s3.head_bucket(Bucket=settings.s3_bucket)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ("404", "NoSuchBucket"):
            # For AWS S3 in non-us-east-1 regions, need LocationConstraint
            if _is_aws_s3() and settings.s3_region != "us-east-1":
                s3.create_bucket(
                    Bucket=settings.s3_bucket,
                    CreateBucketConfiguration={"LocationConstraint": settings.s3_region}
                )
            else:
                s3.create_bucket(Bucket=settings.s3_bucket)
        elif error_code == "403":
            # Bucket exists but we don't have access, or using wrong credentials
            # For AWS S3, bucket likely already exists - skip creation
            if _is_aws_s3():
                pass  # Assume bucket exists, will fail on actual operations if not
            else:
                raise
        else:
            raise

    # CORS for browser-facing requests against the bucket. No public-read
    # bucket policy is set: nothing in FilmBill is world-readable from S3.
    # Every read a browser makes goes through routers/files.py instead.
    if not _is_aws_s3():
        try:
            s3.put_bucket_cors(
                Bucket=settings.s3_bucket,
                CORSConfiguration={
                    "CORSRules": [
                        {
                            "AllowedHeaders": ["*"],
                            "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
                            "AllowedOrigins": [settings.frontend_url, "http://localhost:3100"],
                            "ExposeHeaders": ["ETag", "Content-Length", "x-amz-request-id"],
                            "MaxAgeSeconds": 3600,
                        }
                    ]
                },
            )
        except ClientError:
            pass  # CORS config failed, non-critical


def get_content_type(key: str) -> tuple[str, str]:
    """Return (content_type, cache_control) for a given S3 key."""
    ext = os.path.splitext(key)[1].lower()
    return CONTENT_TYPE_MAP.get(ext, ("application/octet-stream", "no-cache"))


def head_object_size(s3_key: str) -> int:
    """The real byte size of a stored object, straight from S3.

    Raises whatever boto3 raises — a missing key, a network blip, a refused
    connection. The caller decides what a failure means, because that answer
    differs by call site; swallowing it here would make every caller's
    handling implicit.
    """
    s3 = get_s3_client()
    head = s3.head_object(Bucket=settings.s3_bucket, Key=s3_key)
    return int(head["ContentLength"])


def generate_presigned_get_url(s3_key: str, expires_in: int = 3600, download_filename: str | None = None) -> str:
    """Generate a presigned GET URL for an object.

    Goes through _get_presign_client() — see CLAUDE.md rule 16.

    Args:
        s3_key: The S3 object key.
        expires_in: URL expiry in seconds.
        download_filename: If set, adds Content-Disposition: attachment header
                          so the browser downloads with this filename.
    """
    s3 = _get_presign_client()
    params: dict = {"Bucket": settings.s3_bucket, "Key": s3_key}
    if download_filename:
        safe_name = re.sub(r'[\x00-\x1f\x7f]', '', download_filename)
        safe_name = safe_name.replace('\\', '\\\\').replace('"', '\\"')
        params["ResponseContentDisposition"] = f'attachment; filename="{safe_name}"'
    return s3.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires_in,
    )

def put_object(s3_key: str, body: bytes, content_type: str | None = None, cache_control: str | None = None) -> None:
    """Upload a small object directly (brand images, avatars, documents)."""
    s3 = get_s3_client()
    kwargs = {"Bucket": settings.s3_bucket, "Key": s3_key, "Body": body}
    if content_type:
        kwargs["ContentType"] = content_type
    if cache_control:
        kwargs["CacheControl"] = cache_control
    s3.put_object(**kwargs)

def delete_object(s3_key: str) -> None:
    s3 = get_s3_client()
    s3.delete_object(Bucket=settings.s3_bucket, Key=s3_key)
