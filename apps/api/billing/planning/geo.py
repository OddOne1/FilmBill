"""Distances and groups of shooting locations (L1b). Pure.

`distance_km` is the great-circle distance (haversine, Earth radius
6371.0088 km), good to well under a percent at the scale of a production.

`clusters` groups shooting locations that are close together: two
locations within `threshold_km` of each other are in one group, and so is
anything reachable through a chain of such steps (single linkage). Each
group gets its own nearest hospital, so a shoot in Vienna and one in Linz
are two groups and get two hospitals, while three sets around one valley
are one group and get one. The default threshold is 30 km (Mathias's "too
far apart", L1b).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Hashable, Sequence, TypeVar

EARTH_RADIUS_KM = 6371.0088
DEFAULT_CLUSTER_KM = 30.0

K = TypeVar("K", bound=Hashable)


@dataclass(frozen=True)
class Point(Generic[K]):
    key: K
    latitude: Decimal | float
    longitude: Decimal | float


def distance_km(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def clusters(points: Sequence[Point[K]], threshold_km: float = DEFAULT_CLUSTER_KM) -> list[list[K]]:
    """Groups of keys, each group in the input order, groups ordered by
    their first member's position in the input."""
    parent = list(range(len(points)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            a, b = points[i], points[j]
            if distance_km(a.latitude, a.longitude, b.latitude, b.longitude) <= threshold_km:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    groups: dict[int, list[K]] = {}
    for i, point in enumerate(points):
        groups.setdefault(find(i), []).append(point.key)
    return [groups[root] for root in sorted(groups)]
