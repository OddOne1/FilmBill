"""Hospitals near a point, from OpenStreetMap (L1b decision 2).

One request per point to the Overpass API (`settings.overpass_url`, default
`https://overpass-api.de/api/interpreter`): every `amenity=hospital` within
the radius, with its tags. No key, no account; a generic User-Agent and
nothing about the company or the project is sent — only a coordinate.

Answers are kept in memory for an hour per rounded point and radius, so
opening a project's Locations tab twice does not ask twice.

`emergency=yes` in OpenStreetMap means the hospital has an emergency
department; many entries do not say either way (`emergency: None`). The
suggestion list puts the ones that say yes first and shows the others as
"emergency department not confirmed".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import httpx

from ..config import settings

TIMEOUT_SECONDS = 20.0
CACHE_SECONDS = 3600
USER_AGENT = "FilmBill (self-hosted production software)"

_cache: dict[tuple, tuple[float, list["OsmHospital"]]] = {}


class HospitalSearchError(RuntimeError):
    """The map service did not answer usefully. Shown as a sentence."""


@dataclass(frozen=True)
class OsmHospital:
    osm_ref: str
    name: str
    latitude: Decimal
    longitude: Decimal
    emergency: Optional[bool]
    phone: Optional[str] = None
    street: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    website: Optional[str] = None


def query_text(latitude, longitude, radius_m: int) -> str:
    return (
        "[out:json][timeout:20];"
        f'nwr["amenity"="hospital"](around:{int(radius_m)},{float(latitude):.6f},{float(longitude):.6f});'
        "out center tags 60;"
    )


def _hospital(element: dict) -> Optional[OsmHospital]:
    tags = element.get("tags") or {}
    name = tags.get("name")
    lat = element.get("lat", (element.get("center") or {}).get("lat"))
    lng = element.get("lon", (element.get("center") or {}).get("lon"))
    if not name or lat is None or lng is None:
        return None
    street = " ".join(p for p in (tags.get("addr:street"), tags.get("addr:housenumber")) if p) or None
    emergency = tags.get("emergency")
    return OsmHospital(
        osm_ref=f"{element.get('type', 'node')}/{element.get('id')}",
        name=name,
        latitude=Decimal(str(lat)).quantize(Decimal("0.000001")),
        longitude=Decimal(str(lng)).quantize(Decimal("0.000001")),
        emergency=True if emergency == "yes" else False if emergency == "no" else None,
        phone=tags.get("phone") or tags.get("contact:phone"),
        street=street,
        zip=tags.get("addr:postcode"),
        city=tags.get("addr:city"),
        country=(tags.get("addr:country") or "").upper() or None,
        website=tags.get("website") or tags.get("contact:website"),
    )


def near(
    latitude,
    longitude,
    radius_m: int,
    *,
    url: Optional[str] = None,
    client: Optional[httpx.Client] = None,
    use_cache: bool = True,
) -> list[OsmHospital]:
    key = (round(float(latitude), 3), round(float(longitude), 3), int(radius_m), url or settings.overpass_url)
    hit = _cache.get(key)
    if use_cache and hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    try:
        response = http.post(url or settings.overpass_url, data={"data": query_text(latitude, longitude, radius_m)})
        response.raise_for_status()
        elements = response.json().get("elements", [])
    except (httpx.HTTPError, ValueError):
        raise HospitalSearchError(
            "The hospital search on OpenStreetMap did not answer just now. Try again in a minute, "
            "or add the hospital by hand."
        ) from None
    finally:
        if own:
            http.close()
    found = [h for h in (_hospital(e) for e in elements) if h is not None]
    _cache[key] = (time.monotonic(), found)
    return found
