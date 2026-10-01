from typing import Optional, Dict, Any
from pydantic import BaseModel

from .auth import JsonObject


class SiteSettingsResponse(BaseModel):
    org_name: str
    logo_dark_url: Optional[str] = None
    logo_light_url: Optional[str] = None
    logo_login_url: Optional[str] = None
    favicon_url: Optional[str] = None
    #: See `JsonObject` in schemas/auth.py. `Dict[str, Any]` is not enough:
    #: Pydantic sees `Any` as no constraint and emits a bare object, which
    #: openapi-typescript reads as `Record<string, never>` — so the web app
    #: could not read a single theme token out of its own settings.
    theme_colors: Optional[JsonObject] = None
    #: IANA zone name deciding when the daily maintenance jobs run.
    timezone: str = "UTC"
    #: Whether every user on this instance must have 2FA.
    require_2fa: bool = False

    model_config = {"from_attributes": True}


class SiteSettingsUpdate(BaseModel):
    org_name: Optional[str] = None
    logo_dark_s3_key: Optional[str] = None
    logo_light_s3_key: Optional[str] = None
    logo_login_s3_key: Optional[str] = None
    favicon_s3_key: Optional[str] = None
    #: See `JsonObject` in schemas/auth.py. `Dict[str, Any]` is not enough:
    #: Pydantic sees `Any` as no constraint and emits a bare object, which
    #: openapi-typescript reads as `Record<string, never>` — so the web app
    #: could not read a single theme token out of its own settings.
    theme_colors: Optional[JsonObject] = None
    #: IANA zone name. Validated against the real zone database in the
    #: router — an unknown string here would make every wall-clock check
    #: fall back to UTC forever, silently.
    timezone: Optional[str] = None
    #: Turning this ON routes users without 2FA into forced ENROLMENT on
    #: their next correct password; it does not lock anyone out.
    require_2fa: Optional[bool] = None
