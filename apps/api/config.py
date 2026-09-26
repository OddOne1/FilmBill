import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Find .env file - check current dir, then project root
# __file__ = apps/api/config.py, so parent.parent.parent = project root
def _find_env_file() -> str:
    project_root = Path(__file__).parent.parent.parent  # filmbill/
    candidates = [
        Path(".env"),
        Path(".env.local"),
        project_root / ".env",
        project_root / ".env.local",
    ]
    for p in candidates:
        if p.exists():
            return str(p.resolve())
    return ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore extra env vars not in model
    )

    database_url: str
    redis_url: str
    s3_storage: str = "minio"  # "s3" for AWS S3, "minio" for local MinIO
    s3_bucket: str = "filmbill"
    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"
    s3_public_endpoint: str | None = None  # External URL for presigned URLs (e.g. http://localhost:9100 when S3_ENDPOINT is http://minio:9000)
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    frontend_url: str = "http://localhost:3100"

    # Worker concurrency settings
    email_concurrency: int = 2  # Number of concurrent email sending jobs

    # ── Gotenberg (HTML → PDF) ────────────────────────────────────────────
    #
    # The only renderer in this system (SCOPE §7.3): layout JSON becomes
    # backend Jinja HTML, Gotenberg turns that HTML into the PDF, and the
    # in-app preview is the same HTML in an iframe. There is deliberately
    # no second, in-process renderer to drift away from it.
    #
    # Internal-network URL: the container publishes no port (see
    # docker-compose.dev.yml), so this is reachable from the API and the
    # worker and from nowhere else.
    gotenberg_url: str = "http://gotenberg:3000"
    #: Seconds to wait for one render before giving up. A P0a smoke page
    #: renders in well under a second; a real multi-page invoice is still
    #: comfortably inside this.
    gotenberg_timeout_seconds: int = 30

    # Email settings - supports AWS SES or any SMTP server
    # If mail_provider is "ses", uses AWS SES with aws_mail_* credentials
    # If mail_provider is "smtp", uses standard SMTP with smtp_* settings
    mail_provider: str = "ses"  # "ses" or "smtp"
    mail_from_address: str = "noreply@example.com"
    mail_from_name: str = "FilmBill"

    # AWS SES settings
    aws_mail_access_key_id: str | None = None
    aws_mail_secret_access_key: str | None = None
    aws_mail_region: str = "eu-central-1"

    # SMTP settings (for non-SES providers like SendGrid, Mailgun, Mailpit,
    # self-hosted)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    # "starttls" | "implicit_tls" | "none". None (unset) means "derive
    # it from smtp_use_tls", which is what keeps every existing .env.prod
    # working untouched. See services/email_config.smtp_security_from.
    smtp_security: str | None = None

settings = Settings()
