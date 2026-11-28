"""Opening a short map share link once, to learn where it points (L1b).

`maps.app.goo.gl/…`, `goo.gl/maps/…` and Apple's short links carry no
position; they redirect to a full link that does. This follows the
redirects **without reading any page**: each hop is a GET whose body is never
read, at most `MAX_HOPS`, each within `TIMEOUT_SECONDS`, and the first hop
must be a short-link host (`SHORT_LINK_HOSTS`) and every later one a map host
(`MAP_HOSTS`). As soon as a `Location` reads as a position (or a consent
page's `continue` link does), it stops — Google's page itself is never
requested. Anything else is refused with a sentence.

Mathias allowed exactly this on 2026-11-28 (L1b decision 3). It is the only
place a pasted link causes a request.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlsplit

import httpx

from ..billing.planning import map_links

MAX_HOPS = 5
TIMEOUT_SECONDS = 5.0
USER_AGENT = "FilmBill (self-hosted production software)"


class ResolveError(ValueError):
    """A sentence for the form."""


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def resolve(
    url: str,
    *,
    client: Optional[httpx.Client] = None,
    short_hosts: frozenset[str] = map_links.SHORT_LINK_HOSTS,
    map_hosts: frozenset[str] = map_links.MAP_HOSTS,
) -> map_links.MapLinkInfo:
    """What a pasted link says, opening it only if it is a short link."""
    current = (url or "").strip()
    if current and not current.lower().startswith(("http://", "https://")):
        current = "https://" + current
    info = map_links.parse_map_url(current)
    host = _host(current)
    refused_short = host == "goo.gl" and not urlsplit(current).path.startswith("/maps")
    if not (info.short or (host in short_hosts and not info.has_position and not refused_short)):
        return info
    own = client is None
    http = client or httpx.Client(
        follow_redirects=False, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
    )
    try:
        for _hop in range(MAX_HOPS):
            with http.stream("GET", current) as response:
                location = response.headers.get("location")
            if not location:
                break
            current = urljoin(current, location)
            host = _host(current)
            if host not in map_hosts and host not in short_hosts:
                raise ResolveError(
                    "This short link leads somewhere other than Google Maps or Apple Maps. "
                    "Open it in your browser and paste the full map link instead."
                )
            found = map_links.parse_map_url(current)
            if found.has_position or (found.refusal is None and not found.short):
                return found
    except httpx.HTTPError:
        raise ResolveError(
            "The short link could not be opened just now. Try again, or open it in your browser and paste the full map link."
        ) from None
    finally:
        if own:
            http.close()
    raise ResolveError(
        "This short link did not lead to a place. Open it in your browser and paste the full map link instead."
    )
