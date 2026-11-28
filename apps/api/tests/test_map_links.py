"""Links to maps and directions, and reading a pasted pair (L1 part 1).

`billing/planning/map_links.py` is pure, so its behaviour is pinned by the
golden cases in `fixtures/map_links/cases.json`, every number written out.
The walk refuses an empty case list, checks that every scenario the prompt
named is present, and proves its own comparison fails on a wrong URL.

The import boundary is checked twice: the package-wide probe in
`test_billing_import_boundary.py` touches every name `planning` exports
(map_links included), and the probe below imports THIS module alone in a
fresh interpreter — with its twin that imports a forbidden module and must
see it, so an empty probe cannot pass.
"""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from apps.api.billing.planning import PlaceData, directions_link, map_link, parse_coordinates
from apps.api.billing.planning.map_links import coordinates_text

CASES = json.loads((Path(__file__).parent / "fixtures" / "map_links" / "cases.json").read_text())
REPO_ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN = ("sqlalchemy", "fastapi", "starlette", "alembic")


def _place(data: dict) -> PlaceData:
    return PlaceData(
        latitude=Decimal(data["latitude"]) if data.get("latitude") is not None else None,
        longitude=Decimal(data["longitude"]) if data.get("longitude") is not None else None,
        street=data.get("street"),
        street2=data.get("street2"),
        zip=data.get("zip"),
        city=data.get("city"),
        country=data.get("country"),
    )


def _check_parse(case: dict) -> None:
    got = parse_coordinates(case["text"])
    if "refusal" in case:
        assert (got.latitude, got.longitude, got.refusal) == (None, None, case["refusal"]), case["why"]
    else:
        assert got.refusal is None, (case["why"], got.refusal)
        assert (format(got.latitude, "f"), format(got.longitude, "f")) == (case["latitude"], case["longitude"]), case["why"]


def _check_map(case: dict) -> None:
    assert map_link(_place(case["place"])) == case["link"], case["why"]


def _check_directions(case: dict) -> None:
    assert directions_link(_place(case["origin"]), _place(case["destination"])) == case["link"], case["why"]


@pytest.mark.parametrize("case", CASES["parse"], ids=lambda c: c["id"])
def test_parse_golden(case):
    _check_parse(case)


@pytest.mark.parametrize("case", CASES["map_link"], ids=lambda c: c["id"])
def test_map_link_golden(case):
    _check_map(case)


@pytest.mark.parametrize("case", CASES["directions"], ids=lambda c: c["id"])
def test_directions_golden(case):
    _check_directions(case)


def test_the_scenarios_the_prompt_named_are_all_here():
    ids = {section: {c["id"] for c in CASES[section]} for section in ("parse", "map_link", "directions")}
    # the three formats, a swapped/invalid range, a missing half
    assert {"comma-space", "space", "semicolon", "decimal-commas", "swapped", "latitude-out-of-range", "missing-half"} <= ids["parse"]
    # umlauts and spaces, only a city, the empty location, coordinates first
    assert {"address-umlauts-and-spaces", "only-a-city", "empty", "coordinates-beat-the-address"} <= ids["map_link"]
    assert len(ids["directions"]) >= 3


def test_the_comparison_notices_a_wrong_link():
    case = dict(next(c for c in CASES["map_link"] if c["id"] == "coordinates-beat-the-address"))
    case["link"] = "https://www.google.com/maps/search/?api=1&query=W%C3%A4hringer%20G%C3%BCrtel%2018-20%2C%201090%20Wien%2C%20AT"
    with pytest.raises(AssertionError):
        _check_map(case)


def test_the_comparison_notices_a_wrong_number():
    case = dict(next(c for c in CASES["parse"] if c["id"] == "comma-space"))
    case["longitude"] = "16.373801"
    with pytest.raises(AssertionError):
        _check_parse(case)


def test_the_display_text_is_what_the_form_shows_back():
    parsed = parse_coordinates("48.2082, 16.3738")
    assert coordinates_text(parsed.latitude, parsed.longitude) == "Latitude 48.2082, Longitude 16.3738"
    assert coordinates_text(None, Decimal("16")) is None


def _probe(imports: str) -> list[str]:
    program = (
        "import sys\n"
        f"{imports}\n"
        "print('|'.join(sorted(n for n in sys.modules if n.split('.')[0] in "
        f"{FORBIDDEN!r})))\n"
    )
    result = subprocess.run([sys.executable, "-c", program], cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [name for name in result.stdout.strip().split("|") if name]


def test_map_links_alone_loads_no_orm_and_no_web_framework():
    loaded = _probe(
        "import apps.api.billing.planning.map_links as m\n"
        "m.map_link(m.PlaceData(city='Wien')); m.parse_coordinates('1, 2')"
    )
    assert loaded == [], loaded


def test_the_probe_can_fail():
    assert "sqlalchemy" in _probe("import apps.api.models")
