"""How many of a device are free on a day, and over a period (B2c-2).

SCOPE §4.4.7: for a device and the days of a booking, the picker shows
**owned · available · booked · scheduled** per day, and over the period
**the lowest day governs**. This module is that rule and nothing else — pure
Python over plain integers, the same boundary as `billing/calc`, so the rule
can be pinned as a table of numbers (`tests/fixtures/gear_availability/`).

* `available = owned − scheduled − booked`, and it may be negative;
* `overbooked = available < 0`, `shortage = max(0, −available)`;
* a `cancelled` booking never counts;
* the booking being edited never counts against itself (`exclude`);
* over a period, the day with the lowest `available` is the `worst_day`
  (the earliest of equal days), and its `available` is `min_available`.

Overbooking **warns and never blocks** (decided, Mathias 2026-11-26) — that
is the caller's business; this module only counts.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

STATUS_SCHEDULED = "scheduled"
STATUS_BOOKED = "booked"
STATUS_CANCELLED = "cancelled"
STATUSES: tuple[str, ...] = (STATUS_SCHEDULED, STATUS_BOOKED, STATUS_CANCELLED)


@dataclass(frozen=True)
class Holding:
    """One booking of the device on one day: how many, in which status."""

    booking_id: object
    quantity: int
    status: str


@dataclass(frozen=True)
class DayAvailability:
    date: date
    owned: int
    scheduled: int
    booked: int
    available: int
    overbooked: bool
    shortage: int
    #: The bookings counted on the day, in the order given.
    holdings: tuple[Holding, ...] = ()


@dataclass(frozen=True)
class PeriodAvailability:
    owned: int
    days: tuple[DayAvailability, ...] = field(default_factory=tuple)
    #: The lowest day's numbers. With no days at all, the device is simply
    #: owned: `min_available == owned` and there is no worst day.
    min_available: int = 0
    worst_day: Optional[date] = None
    scheduled: int = 0
    booked: int = 0
    overbooked: bool = False
    shortage: int = 0


def _check(owned: int, holdings: Iterable[Holding]) -> None:
    if not isinstance(owned, int) or owned < 0:
        raise ValueError(f"owned must be a whole number of at least 0, got {owned!r}")
    for holding in holdings:
        if not isinstance(holding.quantity, int) or holding.quantity < 1:
            raise ValueError(f"a booking's quantity must be at least 1, got {holding.quantity!r}")
        if holding.status not in STATUSES:
            raise ValueError(f"unknown booking status {holding.status!r}")


def day(
    when: date,
    owned: int,
    holdings: Iterable[Holding],
    *,
    exclude: Optional[object] = None,
) -> DayAvailability:
    """The numbers for one day. `exclude` is the booking being edited."""
    rows = list(holdings)
    _check(owned, rows)
    counted = tuple(
        h
        for h in rows
        if h.status != STATUS_CANCELLED and (exclude is None or h.booking_id != exclude)
    )
    scheduled = sum(h.quantity for h in counted if h.status == STATUS_SCHEDULED)
    booked = sum(h.quantity for h in counted if h.status == STATUS_BOOKED)
    # Everything counted holds the device: the filter above is the ONE place
    # a cancelled booking (or the booking being edited) is left out.
    available = owned - sum(h.quantity for h in counted)
    return DayAvailability(
        date=when,
        owned=owned,
        scheduled=scheduled,
        booked=booked,
        available=available,
        overbooked=available < 0,
        shortage=max(0, -available),
        holdings=counted,
    )


def period(
    owned: int,
    by_day: dict[date, Iterable[Holding]],
    dates: Iterable[date],
    *,
    exclude: Optional[object] = None,
) -> PeriodAvailability:
    """Every day in `dates` (a day with no entry in `by_day` has no
    bookings), and the lowest day's numbers for the whole period."""
    days = tuple(day(d, owned, by_day.get(d, ()), exclude=exclude) for d in sorted(set(dates)))
    if not days:
        _check(owned, ())
        return PeriodAvailability(owned=owned, days=(), min_available=owned)
    worst = days[0]
    for entry in days[1:]:
        if entry.available < worst.available:
            worst = entry
    return PeriodAvailability(
        owned=owned,
        days=days,
        min_available=worst.available,
        worst_day=worst.date,
        scheduled=worst.scheduled,
        booked=worst.booked,
        overbooked=worst.overbooked,
        shortage=worst.shortage,
    )
