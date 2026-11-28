"""Planning: the pure rules of booking resources onto days (B2c-2).

The same boundary as `billing/calc`: no database, no FastAPI, no ORM models.
`gear_availability` counts how many of a device are free on each day and
which day of a period governs; `services/gear_booking_service.py` loads the
bookings and passes the numbers in.

L1 adds `map_links` (links to a map and to directions, built without a key
or a network call, and reading a pasted coordinate pair) and `locations`
(a day's effective location: its own, else the strip's).
"""

from .gear_availability import (
    STATUS_BOOKED,
    STATUS_CANCELLED,
    STATUS_SCHEDULED,
    STATUSES,
    DayAvailability,
    Holding,
    PeriodAvailability,
    day,
    period,
)
from .locations import SOURCE_DAY, SOURCE_STRIP, EffectiveLocation, effective_location
from .map_links import (
    ParsedCoordinates,
    PlaceData,
    address_text,
    coordinates_text,
    directions_link,
    map_link,
    parse_coordinates,
)

__all__ = [
    "STATUS_BOOKED",
    "STATUS_CANCELLED",
    "STATUS_SCHEDULED",
    "STATUSES",
    "DayAvailability",
    "Holding",
    "PeriodAvailability",
    "day",
    "period",
    "SOURCE_DAY",
    "SOURCE_STRIP",
    "EffectiveLocation",
    "effective_location",
    "ParsedCoordinates",
    "PlaceData",
    "address_text",
    "coordinates_text",
    "directions_link",
    "map_link",
    "parse_coordinates",
]
