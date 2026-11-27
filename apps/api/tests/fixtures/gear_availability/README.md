# Golden gear-availability fixtures

One JSON file per scenario for `billing/planning/gear_availability.py`
(B2c-2, SCOPE §4.4.7). Each has:

* `why` — what the scenario is evidence about, every number in words.
* `owned` — the device's `quantity_owned`.
* `dates` — the days of the period asked about.
* `bookings` — one row per booking **and day**: `id`, `date`, `quantity`,
  `status` (`scheduled | booked | cancelled`).
* `exclude` — optional: the booking being edited, never counted against
  itself.
* `expect` — `days` keyed by date (owned, scheduled, booked, available,
  overbooked, shortage) and `period` (min_available, worst_day, overbooked,
  shortage): the lowest day governs.

Integers only — a device is counted, never measured. `test_gear_availability.py`
walks this directory; it refuses an empty directory, a file without numbers,
and proves its comparison fails on a wrong number.
