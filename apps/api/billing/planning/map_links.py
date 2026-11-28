"""Links to a map and to directions, and reading a pasted coordinate pair (L1).

Pure: no database, no FastAPI, no network. Every link is built from what the
location record already holds — **no key, no account, no request to any
outside service** (SCOPE, L1). Opening the link is the only moment a map
provider is involved, and that happens in the person's own browser.

* `map_link(place)` — `https://www.google.com/maps/search/?api=1&query=…`
  from the coordinates when both exist, else from the address, else `None`.
* `directions_link(origin, destination)` — `…/maps/dir/?api=1&origin=…&
  destination=…`, each end chosen the same way; `None` when either end has
  nothing to point at.
* `parse_coordinates(text)` — "48.2082, 16.3738", "48.2082 16.3738" or
  "48.2082;16.3738", a decimal comma tolerated ("48,2082; 16,3738"); returns
  a `ParsedCoordinates` with both numbers, or with a `refusal` sentence.

Coordinates are kept to **six decimals** (about 0.1 m), which is what the
database column holds: a pair copied from a map's right-click menu has
fourteen, and refusing it would be refusing the most common way of getting
one. The rounding is half-up and happens here, once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional, Protocol
from urllib.parse import quote

MAP_SEARCH_URL = "https://www.google.com/maps/search/?api=1&query="
MAP_DIRECTIONS_URL = "https://www.google.com/maps/dir/?api=1"

#: Six decimals, the database column's scale.
COORDINATE_PLACES = Decimal("0.000001")
LATITUDE_RANGE = (Decimal("-90"), Decimal("90"))
LONGITUDE_RANGE = (Decimal("-180"), Decimal("180"))

EXAMPLE = "48.2082, 16.3738"


class Place(Protocol):
    """What a link is built from. A `Location` row satisfies it, and so does
    the plain `PlaceData` below that the tests use."""

    latitude: Optional[Decimal]
    longitude: Optional[Decimal]
    street: Optional[str]
    street2: Optional[str]
    zip: Optional[str]
    city: Optional[str]
    country: Optional[str]


@dataclass(frozen=True)
class PlaceData:
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    street: Optional[str] = None
    street2: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None


@dataclass(frozen=True)
class ParsedCoordinates:
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    #: The sentence to show when the text is not a pair. None when it is.
    refusal: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.refusal is None


# ─── Reading a pasted pair ──────────────────────────────────────────────────

_NUMBER = r"[+-]?\d+(?:[.,]\d+)?"
#: Two numbers separated by a semicolon, a comma, or whitespace. A decimal
#: comma is allowed in either number, so "48,2082, 16,3738" is read as two
#: numbers with decimal commas — the regex tries the longest first number.
_PAIR = re.compile(rf"^\s*({_NUMBER})\s*(?:[;,]\s*|\s+)({_NUMBER})\s*$")
_ANY_NUMBER = re.compile(_NUMBER)


def _number(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def _plain(value: Decimal) -> str:
    """`48.208200` → `48.2082`; `16.000000` → `16`; never an exponent."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _in(value: Decimal, bounds: tuple[Decimal, Decimal]) -> bool:
    return bounds[0] <= value <= bounds[1]


def parse_coordinates(text: Optional[str]) -> ParsedCoordinates:
    """A latitude and a longitude from pasted text, or the sentence why not."""
    if text is None or not text.strip():
        return ParsedCoordinates(
            refusal=f"There are no coordinates here. Paste a latitude and a longitude, for example {EXAMPLE}."
        )
    match = _PAIR.match(text)
    if match is None:
        if len(_ANY_NUMBER.findall(text)) == 1:
            return ParsedCoordinates(
                refusal=(
                    "These coordinates have only one number. Paste a latitude and a longitude, "
                    f"for example {EXAMPLE}."
                )
            )
        return ParsedCoordinates(
            refusal=(
                "These coordinates could not be read. Paste two numbers, the latitude first, "
                f"for example {EXAMPLE}."
            )
        )
    try:
        latitude = _number(match.group(1))
        longitude = _number(match.group(2))
    except InvalidOperation:  # pragma: no cover - the regex admits only digits
        return ParsedCoordinates(refusal=f"These coordinates could not be read. Paste them like {EXAMPLE}.")
    if not _in(latitude, LATITUDE_RANGE):
        swapped = _in(longitude, LATITUDE_RANGE) and _in(latitude, LONGITUDE_RANGE)
        hint = (
            " It looks like the longitude came first: the latitude goes first."
            if swapped
            else " The latitude goes first, the longitude second."
        )
        return ParsedCoordinates(
            refusal=f"The latitude {_plain(latitude)} is outside -90 to 90.{hint} For example {EXAMPLE}."
        )
    if not _in(longitude, LONGITUDE_RANGE):
        return ParsedCoordinates(
            refusal=(
                f"The longitude {_plain(longitude)} is outside -180 to 180. "
                f"Check the second number, for example {EXAMPLE}."
            )
        )
    return ParsedCoordinates(
        latitude=latitude.quantize(COORDINATE_PLACES, rounding=ROUND_HALF_UP),
        longitude=longitude.quantize(COORDINATE_PLACES, rounding=ROUND_HALF_UP),
    )


def coordinates_text(latitude: Optional[Decimal], longitude: Optional[Decimal]) -> Optional[str]:
    """"Latitude 48.2082, Longitude 16.3738" — what the form shows back."""
    if latitude is None or longitude is None:
        return None
    return f"Latitude {_plain(latitude)}, Longitude {_plain(longitude)}"


# ─── Links ──────────────────────────────────────────────────────────────────


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def address_text(place: Place) -> Optional[str]:
    """"Spitalgasse 23, 1090 Wien, AT" — street, second line, zip and city,
    country code; whatever exists, in that order. None when nothing does."""
    town = " ".join(part for part in (_clean(place.zip), _clean(place.city)) if part)
    parts = [_clean(place.street), _clean(place.street2), town or None, _clean(place.country)]
    present = [part for part in parts if part]
    return ", ".join(present) if present else None


def _target(place: Optional[Place]) -> Optional[str]:
    """Coordinates when BOTH exist, else the address, else None."""
    if place is None:
        return None
    if place.latitude is not None and place.longitude is not None:
        return f"{_plain(Decimal(place.latitude))},{_plain(Decimal(place.longitude))}"
    return address_text(place)


def _encode(text: str) -> str:
    return quote(text, safe="")


def map_link(place: Optional[Place]) -> Optional[str]:
    target = _target(place)
    if target is None:
        return None
    return MAP_SEARCH_URL + _encode(target)


def directions_link(origin: Optional[Place], destination: Optional[Place]) -> Optional[str]:
    start = _target(origin)
    end = _target(destination)
    if start is None or end is None:
        return None
    return f"{MAP_DIRECTIONS_URL}&origin={_encode(start)}&destination={_encode(end)}"
