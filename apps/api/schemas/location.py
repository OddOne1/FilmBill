"""Locations (L1). See `models/location.py` and `services/location_service.py`.

Coordinates are serialised as strings (Decimal, like money) so that
`48.208200` never turns into `48.20819999`. A request may send them either
as `latitude` + `longitude` or as one pasted `coordinates` text, which the
server reads with `billing/planning/map_links.parse_coordinates`.

Links are computed by the server and only rendered by the web app.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

LocationKind = Literal["shoot", "hotel", "hospital", "other"]


class _Coordinates(BaseModel):
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None

    @field_serializer("latitude", "longitude")
    def _as_text(self, value: Optional[Decimal]) -> Optional[str]:
        return None if value is None else format(value, "f")


class LocationWrite(BaseModel):
    """The fields a create or an edit may send. `coordinates` (pasted text)
    and `latitude` + `longitude` are two ways to say the same thing; sending
    both is refused. An empty `coordinates` clears them."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, max_length=255)
    kind: Optional[LocationKind] = None
    street: Optional[str] = Field(default=None, max_length=255)
    street2: Optional[str] = Field(default=None, max_length=255)
    zip: Optional[str] = Field(default=None, max_length=32)
    city: Optional[str] = Field(default=None, max_length=255)
    country: Optional[str] = Field(default=None, max_length=64)
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    coordinates: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=64)
    email: Optional[str] = Field(default=None, max_length=255)
    website: Optional[str] = Field(default=None, max_length=512)
    emergency_phone: Optional[str] = Field(default=None, max_length=64)
    access_notes: Optional[str] = Field(default=None, max_length=5000)
    notes: Optional[str] = Field(default=None, max_length=5000)
    contact_party_id: Optional[uuid.UUID] = None
    contact_text: Optional[str] = Field(default=None, max_length=1000)


class LocationCreate(LocationWrite):
    name: str = Field(max_length=255)
    kind: LocationKind = "shoot"


class LocationUpdate(LocationWrite):
    pass


class LocationVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = Field(default=None, max_length=1000)


class CoordinatesParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=200)


class CoordinatesParsed(_Coordinates):
    """Either both numbers and `display` ("Latitude 48.2082, Longitude
    16.3738"), or `refusal` with the sentence why not."""

    ok: bool
    display: Optional[str] = None
    refusal: Optional[str] = None


class LocationRef(_Coordinates):
    """A location as a strip, a day or a crew row shows it."""

    id: uuid.UUID
    name: str
    kind: LocationKind
    city: Optional[str] = None
    is_active: bool = True
    map_link: Optional[str] = None


class LocationResponse(_Coordinates):
    id: uuid.UUID
    name: str
    kind: LocationKind
    street: Optional[str] = None
    street2: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    #: "Latitude 48.2082, Longitude 16.3738", or None.
    coordinates_display: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    emergency_phone: Optional[str] = None
    access_notes: Optional[str] = None
    notes: Optional[str] = None
    contact_party_id: Optional[uuid.UUID] = None
    #: The on-site contact's name — only for a caller who may see contacts
    #: (`parties.view`); null otherwise, with the id kept back as well.
    contact_party_name: Optional[str] = None
    contact_text: Optional[str] = None
    verified: bool = False
    verified_on: Optional[dt.date] = None
    verified_by_name: Optional[str] = None
    verification_note: Optional[str] = None
    is_active: bool = True
    #: Built by the server from the coordinates, else the address.
    map_link: Optional[str] = None
    #: How many strips, days, strip hotel lists and crew hotels use it.
    in_use: int = 0
    created_at: dt.datetime
    updated_at: dt.datetime


class LocationChange(BaseModel):
    """What a write answers: the location, and whether this write cleared a
    hospital's verification (with the sentence that says so)."""

    location: LocationResponse
    verification_cleared: bool = False
    message: Optional[str] = None


class LocationList(BaseModel):
    locations: list[LocationResponse]
    #: Whether hotels are in this list at all for this caller.
    hotels_visible: bool


class LocationUse(BaseModel):
    kind: Literal["strip", "day", "strip_hotel", "crew_hotel"]
    project_id: uuid.UUID
    project_name: str
    project_number: Optional[str] = None
    block_id: Optional[uuid.UUID] = None
    strip_name: Optional[str] = None
    date: Optional[dt.date] = None
    #: A crew hotel's person.
    party_name: Optional[str] = None


class LocationUsage(BaseModel):
    location_id: uuid.UUID
    count: int
    uses: list[LocationUse]
