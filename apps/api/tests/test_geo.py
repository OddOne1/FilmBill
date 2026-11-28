"""Distances and groups of shooting locations (L1b). Pure; the numbers are
written out."""

import pytest

from apps.api.billing.planning import Point, clusters, distance_km

WIEN = (48.2082, 16.3738)
MOEDLING = (48.0858, 16.2887)
LINZ = (48.3064, 14.2858)
TULLN = (48.3297, 16.0542)


@pytest.mark.parametrize(
    "a, b, km",
    [
        (WIEN, LINZ, 155.0),      # Stephansplatz to Linz Hauptplatz
        (WIEN, MOEDLING, 15.0),
        (WIEN, TULLN, 27.0),
        (WIEN, WIEN, 0.0),
    ],
)
def test_distance_km(a, b, km):
    assert round(distance_km(*a, *b)) == round(km)


TABLE = [
    ("one place: one group", [("wien", WIEN)], [["wien"]]),
    ("Wien and Mödling, 15 km: one group", [("wien", WIEN), ("moedling", MOEDLING)], [["wien", "moedling"]]),
    ("Wien and Linz, 155 km: two groups", [("wien", WIEN), ("linz", LINZ)], [["wien"], ["linz"]]),
    (
        "a chain: Mödling–Wien 15 km, Wien–Tulln 27 km (Mödling–Tulln 37 km) is one group",
        [("moedling", MOEDLING), ("tulln", TULLN), ("wien", WIEN)],
        [["moedling", "tulln", "wien"]],
    ),
    ("nothing: no groups", [], []),
]


@pytest.mark.parametrize("why, points, expected", TABLE, ids=[row[0] for row in TABLE])
def test_clusters_table(why, points, expected):
    assert clusters([Point(k, *ll) for k, ll in points]) == expected, why
