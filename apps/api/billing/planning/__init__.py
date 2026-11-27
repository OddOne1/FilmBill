"""Planning: the pure rules of booking resources onto days (B2c-2).

The same boundary as `billing/calc`: no database, no FastAPI, no ORM models.
`gear_availability` counts how many of a device are free on each day and
which day of a period governs; `services/gear_booking_service.py` loads the
bookings and passes the numbers in.
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
]
