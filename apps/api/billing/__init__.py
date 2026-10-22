"""Billing: the calculation side of FilmBill, with no framework in it.

SCOPE principle P1 reserves `billing/calc` for the one calculation engine of
CLAUDE.md rule 2. This package is where that engine and its neighbours live,
and the rule for everything under it is the same:

**No database, no FastAPI, no ORM models.** Pure Python over plain data
structures, so the money and tax decisions can be tested as functions — a
table of inputs and expected outputs — rather than through a request and a
session. `apps/api/tests/test_billing_import_boundary.py` fails if that ever
stops being true, because the boundary is only worth anything while it is
enforced by something other than good intentions.

The service layer above it (`apps/api/services/`) owns the translation: it
loads rows, converts them to the dataclasses here, calls a function, and
writes the answer back. Nothing in here knows that a row exists.
"""
