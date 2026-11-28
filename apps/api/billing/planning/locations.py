"""Which location a day happens at (L1 part 2). Pure.

**The day's own, else the strip's.** A strip carries the default location
for all its days; a day that happens somewhere else carries its own. An
archived location is still RETURNED — a plan that used it keeps showing it,
marked archived — and the result says so, so the screen can mark it rather
than silently drop it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Optional, Protocol, TypeVar

SOURCE_DAY = "day"
SOURCE_STRIP = "strip"


class LocationLike(Protocol):
    is_active: bool


L = TypeVar("L", bound=LocationLike)


@dataclass(frozen=True)
class EffectiveLocation(Generic[L]):
    location: Optional[L]
    #: `day`, `strip`, or None when neither has one.
    source: Optional[str]

    @property
    def archived(self) -> bool:
        return self.location is not None and not self.location.is_active


def effective_location(day_location: Optional[L], strip_location: Optional[L]) -> EffectiveLocation[L]:
    if day_location is not None:
        return EffectiveLocation(location=day_location, source=SOURCE_DAY)
    if strip_location is not None:
        return EffectiveLocation(location=strip_location, source=SOURCE_STRIP)
    return EffectiveLocation(location=None, source=None)
