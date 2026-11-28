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

#: L1b: what a place is used as; a location has one or more.
PlaceUse = Literal["shoot", "hotel", "hospital", "restaurant", "other"]


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
    uses: Optional[list[PlaceUse]] = Field(default=None, max_length=5)
    #: The Google or Apple Maps link it came from, as pasted.
    source_link: Optional[str] = Field(default=None, max_length=2000)
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
    #: L1b. Also add it to this project's locations (the project's tab).
    project_id: Optional[uuid.UUID] = None


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
    uses: list[PlaceUse]
    city: Optional[str] = None
    is_active: bool = True
    map_link: Optional[str] = None
    #: An OpenStreetMap view of the pin, for an iframe. Built by the server.
    embed_link: Optional[str] = None


class LocationResponse(_Coordinates):
    id: uuid.UUID
    name: str
    uses: list[PlaceUse]
    source_link: Optional[str] = None
    #: True when it came from the OpenStreetMap hospital search.
    from_openstreetmap: bool = False
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
    embed_link: Optional[str] = None
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
    kind: Literal["project", "strip", "day", "hotel_stay"]
    project_id: uuid.UUID
    project_name: str
    project_number: Optional[str] = None
    block_id: Optional[uuid.UUID] = None
    strip_name: Optional[str] = None
    date: Optional[dt.date] = None
    #: A hotel guest.
    party_name: Optional[str] = None


class LocationUsage(BaseModel):
    location_id: uuid.UUID
    count: int
    uses: list[LocationUse]


# ─── L1b ────────────────────────────────────────────────────────────────────


class MapLinkResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2000)


class MapLinkResolved(_Coordinates):
    """What a pasted Google or Apple Maps link says: the position, a name
    and an address text where the link carries them, the map view — or the
    sentence why not."""

    ok: bool
    name: Optional[str] = None
    address: Optional[str] = None
    provider: Optional[str] = None
    display: Optional[str] = None
    embed_link: Optional[str] = None
    refusal: Optional[str] = None


class HotelStayResponse(BaseModel):
    id: uuid.UUID
    location_id: uuid.UUID
    party_id: uuid.UUID
    party_name: str
    check_in: dt.date
    check_out: dt.date
    nights: int
    notes: Optional[str] = None


class HotelStayCreate(BaseModel):
    """A crew member of the production at this hotel. Without dates: their
    first assigned day to the day after their last."""

    model_config = ConfigDict(extra="forbid")

    party_id: uuid.UUID
    check_in: Optional[dt.date] = None
    check_out: Optional[dt.date] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class HotelStayUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_in: Optional[dt.date] = None
    check_out: Optional[dt.date] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    location_id: uuid.UUID
    kind: Literal["file", "text"]
    title: str
    file_name: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    body_text: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: dt.datetime


class AttachmentText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=255)
    text: str = Field(max_length=200_000)


class AttachmentDownload(BaseModel):
    url: str


class ProjectLocationEntry(BaseModel):
    location: LocationResponse
    #: Strips of this project whose location it is.
    strip_names: list[str] = Field(default_factory=list)
    #: Days of this project with it as their own location.
    day_dates: list[dt.date] = Field(default_factory=list)
    #: Hotel guests — only for a caller who may see hotels.
    stays: list[HotelStayResponse] = Field(default_factory=list)
    #: Booking confirmations; a hotel's only for a caller who may see hotels.
    attachments: list[AttachmentResponse] = Field(default_factory=list)


class ProjectLocations(BaseModel):
    project_id: uuid.UUID
    hotels_visible: bool
    locations: list[ProjectLocationEntry]


class ProjectLocationAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: uuid.UUID


class HospitalOfferResponse(_Coordinates):
    osm_ref: str
    name: str
    emergency: Optional[bool] = None
    distance_km: float
    nearest_to: str
    phone: Optional[str] = None
    street: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    website: Optional[str] = None
    map_link: Optional[str] = None
    #: Already a location of the company (added before, maybe elsewhere).
    existing_location_id: Optional[uuid.UUID] = None
    #: Already on this project's list.
    in_project: bool = False


class HospitalGroupResponse(BaseModel):
    shooting_locations: list[str]
    offers: list[HospitalOfferResponse]


class HospitalSuggestions(BaseModel):
    groups: list[HospitalGroupResponse]
    #: Shooting locations with no coordinates — no hospital can be found
    #: near them until they get some.
    missing_coordinates: list[str] = Field(default_factory=list)
    #: The sentence when OpenStreetMap did not answer.
    error: Optional[str] = None
    source: str = "OpenStreetMap"


class HospitalAdd(_Coordinates):
    """A suggestion, as it was shown, to add to the project."""

    model_config = ConfigDict(extra="forbid")

    osm_ref: str = Field(max_length=64, pattern=r"^(node|way|relation)/\d+$")
    name: str = Field(max_length=255)
    latitude: Decimal
    longitude: Decimal
    phone: Optional[str] = Field(default=None, max_length=64)
    street: Optional[str] = Field(default=None, max_length=255)
    zip: Optional[str] = Field(default=None, max_length=32)
    city: Optional[str] = Field(default=None, max_length=255)
    country: Optional[str] = Field(default=None, max_length=8)
    website: Optional[str] = Field(default=None, max_length=512)
