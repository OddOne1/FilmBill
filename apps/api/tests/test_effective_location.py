"""A day's effective location: its own, else its strip's (L1 part 2).

`billing/planning/locations.effective_location` is pure; this is its table.
An archived location is still RETURNED and flagged — a plan that used it
keeps showing it.
"""

from dataclasses import dataclass

import pytest

from apps.api.billing.planning import SOURCE_DAY, SOURCE_STRIP, effective_location


@dataclass(frozen=True)
class Spot:
    name: str
    is_active: bool = True


STUDIO = Spot("Studio Wien")
BARN = Spot("Barn Tulln")
OLD = Spot("Old hall", is_active=False)

TABLE = [
    # (why, day's own, strip's, expected name, expected source, archived)
    ("day set: the day's own wins over the strip's", BARN, STUDIO, "Barn Tulln", SOURCE_DAY, False),
    ("day set, strip empty", BARN, None, "Barn Tulln", SOURCE_DAY, False),
    ("day empty, strip set: the strip's", None, STUDIO, "Studio Wien", SOURCE_STRIP, False),
    ("both empty: none", None, None, None, None, False),
    ("the strip's location archived: still returned, flagged", None, OLD, "Old hall", SOURCE_STRIP, True),
    ("the day's location archived: still returned, flagged", OLD, STUDIO, "Old hall", SOURCE_DAY, True),
]


@pytest.mark.parametrize("why, own, strip, name, source, archived", TABLE, ids=[row[0] for row in TABLE])
def test_effective_location_table(why, own, strip, name, source, archived):
    got = effective_location(own, strip)
    assert (got.location.name if got.location else None, got.source, got.archived) == (name, source, archived), why


def test_the_table_covers_what_the_prompt_named():
    whys = " | ".join(row[0] for row in TABLE)
    for needed in ("day set", "day empty, strip set", "both empty", "archived"):
        assert needed in whys
