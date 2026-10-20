"""The one place a document number is formatted, validated or incremented.

CLAUDE.md rule 2 applies to numbers as well as to money: there is one engine,
the web app asks it for previews, and nothing else builds a number out of a
pattern and a counter. Rule 5 is what the engine has to deliver — a gap-free
sequence, assigned inside the caller's transaction and never committed on its
own.

**What `allocate_number` promises.**

  * `SELECT … FOR UPDATE` on the series row, so two callers serialise on the
    counter rather than on hope. Everything the decision depends on — the
    counter, the period, the highest value already issued in an earlier
    period — is read under that lock.
  * No commit. A caller that rolls back consumes nothing; a caller that
    commits has both the number and the row that records it, or neither.
  * One number, once. `unique (series_id, number)` is the database's answer
    and this module translates its violation into `NumberCollision`, inside a
    SAVEPOINT so the caller's transaction survives to report it.

**Out-of-order document dates: allowed, with a warning (Mathias, 2026-10-20).**
I recommended refusing. He chose to allow, so a date earlier than the latest
date already allocated in the series goes through and carries
`document_date_before_latest`. For a `yearly` series a date in an EARLIER
period draws from that period's own counter — the highest value already
allocated in that period, plus one — and the current period's counter is not
touched. That is the only reading under which both years stay gap-free: taking
the number from the current counter would leave a hole in it and put a
2025-shaped number in the middle of 2026's run.

This rule is **not tax-advised**. It is in SCOPE §16 awaiting the
Steuerberater's confirmation, and P2's finalize flow has to show the warning
and make somebody confirm it.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.company import Company
from ..models.number_series import (
    RESET_MODES,
    RESET_NEVER,
    RESET_YEARLY,
    NumberAllocation,
    NumberSeries,
)
from .schedule_window import resolve_zone

# ─── Patterns ───────────────────────────────────────────────────────────────

#: The tokens a pattern may contain. Anything else in braces is refused
#: rather than printed literally — `{YYY}` is a typo, and a typo that silently
#: becomes part of every invoice number is a typo nobody catches until the
#: numbers are already out.
TOKENS = ("{YYYY}", "{YY}", "{MM}", "{SEQ}")

_BRACED = re.compile(r"\{[^{}]*\}")

#: A year token. Required when the series restarts annually: without one the
#: numbers repeat every January and the identifier stops being unique, which
#: is the one thing §11 UStG actually demands.
YEAR_TOKENS = ("{YYYY}", "{YY}")

MIN_PADDING = 1
MAX_PADDING = 12


class InvalidPattern(ValueError):
    """A readable reason, meant to be shown to whoever typed the pattern."""


def validate_pattern(pattern: str, reset: str) -> str:
    """Return the pattern, or raise `InvalidPattern` saying what is wrong.

    Returns the value rather than a bool for the same reason
    `core/iban.validate_iban` does: a caller cannot validate one string and
    store another.
    """
    candidate = (pattern or "").strip()

    if not candidate:
        raise InvalidPattern("A pattern is required, for example AR-{YYYY}-{SEQ}.")

    unknown = sorted(
        {token for token in _BRACED.findall(candidate) if token not in TOKENS}
    )
    if unknown:
        raise InvalidPattern(
            f"{', '.join(unknown)} is not a token this pattern understands. "
            f"The tokens are {', '.join(TOKENS)}; anything else is printed "
            "as typed, which is almost never what was meant."
        )

    if "{SEQ}" not in candidate:
        raise InvalidPattern(
            "A pattern must contain {SEQ} — that is the counter. Without it "
            "every document would get the same number."
        )

    if reset not in RESET_MODES:
        raise InvalidPattern(
            f"{reset!r} is not a reset mode. Use "
            f"{RESET_YEARLY!r} or {RESET_NEVER!r}."
        )

    if reset == RESET_YEARLY and not any(
        token in candidate for token in YEAR_TOKENS
    ):
        raise InvalidPattern(
            "A series that restarts every year needs {YYYY} or {YY} in its "
            "pattern. Without a year token the counter goes back to 1 every "
            "January and last year's numbers are issued again — choose "
            "“Continue without restart” instead if that is what you want."
        )

    return candidate


def validate_padding(padding: int) -> int:
    if padding < MIN_PADDING or padding > MAX_PADDING:
        raise InvalidPattern(
            f"Padding is between {MIN_PADDING} and {MAX_PADDING} digits; "
            f"{padding} is not."
        )
    return padding


def format_number(
    pattern: str, *, value: int, document_date: date, padding: int
) -> str:
    """Substitute the tokens. The only function that turns a value into a number.

    Padding is a MINIMUM. `zfill` leaves a value longer than the padding
    alone, so 10000 with padding 4 prints `10000` — the number grows rather
    than truncating or wrapping, because a truncated number is a different
    document's number and a wrapped one is a duplicate.
    """
    return (
        pattern.replace("{YYYY}", f"{document_date.year:04d}")
        .replace("{YY}", f"{document_date.year % 100:02d}")
        .replace("{MM}", f"{document_date.month:02d}")
        .replace("{SEQ}", str(value).zfill(padding))
    )


def period_key(document_date: date) -> str:
    """The period a document date belongs to. The year, as four characters."""
    return f"{document_date.year:04d}"


def document_date_in_company_timezone(
    company: Company, moment: Optional[datetime] = None
) -> date:
    """Which calendar day `moment` is, where the company is.

    The period is derived from a DATE, and a timestamp is not one until a zone
    has been applied. 2026-12-31 23:30 UTC is already 1 January in Vienna, so
    a company there must number that document into the new year — reading the
    UTC date would put it in the old one and, on a yearly series, give it a
    number from a sequence that had already closed.

    `resolve_zone` falls back to UTC rather than raising, which is right here
    too: a settings-page typo must not make it impossible to issue an invoice.
    """
    when = moment or datetime.now(tz=resolve_zone(company.timezone))
    if when.tzinfo is None:
        # A naive timestamp from a fixture or a hand-written caller. Read as
        # UTC, which is what every DateTime column in this schema stores.
        from datetime import timezone as _tz

        when = when.replace(tzinfo=_tz.utc)
    return when.astimezone(resolve_zone(company.timezone)).date()


# ─── Results ────────────────────────────────────────────────────────────────

#: The warning code the out-of-order rule carries. A code as well as a
#: sentence, because P2's finalize flow has to branch on it (show a
#: confirmation) and branching on prose is how a translation breaks a gate.
OUT_OF_ORDER = "document_date_before_latest"


@dataclass(frozen=True)
class NumberWarning:
    code: str
    message: str
    latest_document_date: Optional[date] = None
    latest_number: Optional[str] = None


@dataclass(frozen=True)
class NumberPlan:
    """What would be, or has just been, allocated."""

    number: str
    value: int
    period: str
    warnings: list[NumberWarning] = field(default_factory=list)
    #: True when taking this number RESTARTS the counter at 1, in a period
    #: later than the one the series is currently on. False on a series that
    #: has never allocated anything (there is nothing to restart), and false
    #: when a new period continues an existing count — which is what a
    #: `never`-to-`yearly` switch does until the next boundary. The Number
    #: series screen shows it so that "Continue without restart" versus
    #: "Restart every year" is visible before anyone commits to it.
    starts_new_period: bool = False


class NumberSeriesMissing(LookupError):
    """No active series for this company and document type."""


class NumberCollision(RuntimeError):
    """The number this plan would issue has already been issued.

    Carries the number, because that is the only useful thing to say: the
    pattern or the counter was changed in a way that walks back over numbers
    that are already on documents, and the person has to see which one.
    """

    def __init__(self, number: str):
        self.number = number
        super().__init__(
            f"{number} has already been issued by this series. Change the "
            "pattern or the counter so the next number is one that has not "
            "been used."
        )


# ─── Reading the series' state ──────────────────────────────────────────────


def active_series(
    db: Session, *, company_id: uuid.UUID, document_type: str, lock: bool = False
) -> Optional[NumberSeries]:
    """The one active series for this company and document type.

    `lock` takes `SELECT … FOR UPDATE` on the row. Everything the allocation
    decision depends on is read after that lock is held, including the
    per-period maximum — so two callers cannot both read "the highest value
    in 2025 is 12" and both write 13.
    """
    query = db.query(NumberSeries).filter(
        NumberSeries.company_id == company_id,
        NumberSeries.document_type == document_type,
        NumberSeries.is_active.is_(True),
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def highest_value_in_period(db: Session, *, series_id: uuid.UUID, period: str) -> int:
    """The largest sequence value already allocated in `period`. 0 if none.

    Read from the ALLOCATIONS, never derived from `series.next_value`. That
    is the whole point of the back-dated path: `next_value` belongs to the
    current period and says nothing about 2025, and computing 2025's next
    number from it would hand out a number 2025 had already used.
    """
    return (
        db.query(func.coalesce(func.max(NumberAllocation.value), 0))
        .filter(
            NumberAllocation.series_id == series_id,
            NumberAllocation.period == period,
        )
        .scalar()
        or 0
    )


def latest_allocation(
    db: Session, *, series_id: uuid.UUID
) -> Optional[NumberAllocation]:
    """The allocation with the latest DOCUMENT date in this series.

    Document date, not `allocated_at`: "is this document dated before one we
    have already numbered" is the question, and the order they were entered in
    is not it.
    """
    return (
        db.query(NumberAllocation)
        .filter(NumberAllocation.series_id == series_id)
        .order_by(
            NumberAllocation.document_date.desc(),
            NumberAllocation.allocated_at.desc(),
        )
        .first()
    )


# ─── The engine ─────────────────────────────────────────────────────────────


def plan_number(
    db: Session,
    series: NumberSeries,
    document_date: date,
    *,
    pattern: Optional[str] = None,
    padding: Optional[int] = None,
    reset: Optional[str] = None,
) -> NumberPlan:
    """What `series` would issue for `document_date`. Reads; writes nothing.

    The single decision procedure, shared by `allocate_number` and by the
    preview endpoint. Two copies of this would be two answers to "what is the
    next number", and the one on the screen would be the one nobody tested.

    The three override arguments are for the preview of an UNSAVED edit — the
    screen shows what a pattern would produce before anybody saves it. They
    default to the series' own values, so the allocation path passes none.
    """
    effective_pattern = pattern if pattern is not None else series.pattern
    effective_padding = padding if padding is not None else series.padding
    effective_reset = reset if reset is not None else series.reset

    period = period_key(document_date)
    warnings: list[NumberWarning] = []

    latest = latest_allocation(db, series_id=series.id)
    if latest is not None and document_date < latest.document_date:
        warnings.append(
            NumberWarning(
                code=OUT_OF_ORDER,
                message=(
                    f"This document is dated {document_date.isoformat()}, "
                    f"which is before {latest.document_date.isoformat()} — "
                    f"the date of {latest.number}, the most recent document "
                    "in this series."
                ),
                latest_document_date=latest.document_date,
                latest_number=latest.number,
            )
        )

    starts_new_period = False

    if effective_reset == RESET_YEARLY:
        current = series.current_period
        if current is None or period > current:
            # A new, later period. The counter restarts — from this period's
            # own highest value rather than from 1 flatly, which matters
            # after a mode switch: a `never` series that has been issuing
            # numbers into 2026 and is then set to restart annually must
            # continue 2026 and only restart when 2027 arrives. That is the
            # "takes effect at the next period boundary" rule, and it falls
            # out of reading the allocations instead of assuming an empty
            # period.
            value = highest_value_in_period(db, series_id=series.id, period=period) + 1
            starts_new_period = current is not None and value == 1
        elif period < current:
            # Back-dated into a closed period: that period's own counter.
            # `series.next_value` is deliberately NOT read and NOT written.
            value = highest_value_in_period(db, series_id=series.id, period=period) + 1
        else:
            value = series.next_value
    else:
        # Continuous. One counter, for ever, whatever the date says. A year
        # token is still allowed in the pattern — it prints the year, it does
        # not restart the count.
        value = series.next_value

    number = format_number(
        effective_pattern,
        value=value,
        document_date=document_date,
        padding=effective_padding,
    )
    return NumberPlan(
        number=number,
        value=value,
        period=period,
        warnings=warnings,
        starts_new_period=starts_new_period,
    )


def allocate_number(
    db: Session,
    *,
    company_id: uuid.UUID,
    document_type: str,
    document_date: date,
    entity_type: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    actor=None,
) -> NumberPlan:
    """Take the next number for this document type. In the CALLER's transaction.

    No commit, deliberately and load-bearingly: a caller that rolls back
    consumes nothing, which is the only way rule 5's "gap-free" can survive a
    failed finalize. The row lock is held from here until the caller commits
    or rolls back, so the critical section is as short as the caller makes it.

    Raises `NumberSeriesMissing` when nothing is configured — a 409-shaped
    condition, not a 500, and a company always has the eight seeded series
    unless someone deactivated one.

    `actor` is who is doing it, for the audit event a back-dated allocation
    writes. Optional, and the event is written either way: an out-of-order
    document numbered by a Celery task or a CLI still has to leave a trace,
    and `Actor.system()` is what that looks like. The event goes in the
    CALLER's transaction, like the allocation — a number that was never
    committed must not leave an audit row saying it was issued.
    """
    series = active_series(
        db, company_id=company_id, document_type=document_type, lock=True
    )
    if series is None:
        raise NumberSeriesMissing(
            f"This company has no active number series for {document_type!r}. "
            "Add one in Settings → Company → Number series."
        )

    plan = plan_number(db, series, document_date)

    # The counter only moves for the CURRENT period. A back-dated allocation
    # into a closed period draws from that period's own counter and leaves
    # this one exactly where it was — otherwise entering last year's invoice
    # would punch a hole in this year's sequence.
    #
    # `current_period` never goes BACKWARDS, in either mode. On a continuous
    # series a back-dated document must not drag it into last year, because
    # the day somebody switches that series to "restart every year" the
    # boundary it restarts at would then be a date in the past.
    if series.reset == RESET_NEVER:
        series.next_value = plan.value + 1
        if series.current_period is None or plan.period > series.current_period:
            series.current_period = plan.period
    elif series.current_period is None or plan.period >= series.current_period:
        series.current_period = plan.period
        series.next_value = plan.value + 1
    # else: back-dated into a closed period on a yearly series. The series
    # row is locked and read, and deliberately not written.
    db.flush()

    # A SAVEPOINT, so that a collision leaves the caller's transaction usable
    # and reportable instead of aborted. The collision itself is the database
    # refusing to issue one number twice — `unique (series_id, number)` — and
    # it is reachable: a pattern edit, or a back-dated allocation on a
    # `never` series whose value happens to match a number that year already
    # used.
    #
    # **The savepoint has to open BEFORE the row is added.** `begin_nested()`
    # takes a snapshot, and taking a snapshot flushes whatever is already
    # pending — so adding the allocation first put its INSERT outside the
    # savepoint, where the IntegrityError escaped raw and aborted the
    # caller's transaction. Two tests in test_number_series.py caught it.
    nested = db.begin_nested()
    try:
        allocation = NumberAllocation(
            company_id=company_id,
            series_id=series.id,
            period=plan.period,
            value=plan.value,
            number=plan.number,
            document_date=document_date,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        db.add(allocation)
        db.flush()
        nested.commit()
    except IntegrityError:
        nested.rollback()
        raise NumberCollision(plan.number) from None

    # An out-of-order date is allowed (Mathias, 2026-10-20) and it is never
    # SILENT. The warning tells the caller; this tells whoever reads the log
    # next year and finds a January invoice numbered after a February one.
    #
    # Imported here rather than at module scope: `services/audit` is a leaf,
    # but this module is imported by `company_service` during company
    # creation and a top-level import would widen that chain for no reason.
    if any(warning.code == OUT_OF_ORDER for warning in plan.warnings):
        from . import audit as audit_service

        warning = next(w for w in plan.warnings if w.code == OUT_OF_ORDER)
        audit_service.record(
            db,
            actor or audit_service.Actor.system(company_id=company_id),
            allocation,
            "number.out_of_order",
            {
                "document_date": warning.latest_document_date,
                "number": warning.latest_number,
            },
            {"document_date": document_date, "number": plan.number},
            reason=warning.message,
            fields=("document_date", "number"),
        )

    return plan


def preview_number(
    db: Session,
    *,
    company: Company,
    series: Optional[NumberSeries],
    pattern: str,
    padding: int,
    reset: str,
    document_date: Optional[date] = None,
) -> NumberPlan:
    """The number that WOULD be allocated. Allocates nothing.

    Takes the pattern, padding and mode from the caller rather than from the
    series, because the screen previews an edit before it is saved — that is
    the whole reason this endpoint exists instead of the web app formatting a
    number itself (rule 2).

    `series` is None while a series is being created: there is no counter and
    no history, so the preview is value 1 in the given period.
    """
    validate_pattern(pattern, reset)
    validate_padding(padding)

    effective_date = document_date or document_date_in_company_timezone(company)

    if series is None:
        return NumberPlan(
            number=format_number(
                pattern, value=1, document_date=effective_date, padding=padding
            ),
            value=1,
            period=period_key(effective_date),
        )

    return plan_number(
        db,
        series,
        effective_date,
        pattern=pattern,
        padding=padding,
        reset=reset,
    )


# ─── Seeding ────────────────────────────────────────────────────────────────

#: The eight series every company starts with, and their German prefixes.
#:
#: Data, not code (rule 7): a region pack that wants a ninth document type or
#: a different prefix adds a row here rather than a branch anywhere. They are
#: user-editable from the moment they are seeded — these are a starting point,
#: not a policy.
#:
#: All eight start `yearly` with a `{YYYY}` token, which is what an Austrian
#: production company's accountant expects to see. "Continue without restart"
#: is one radio button away per series.
DEFAULT_SERIES: tuple[tuple[str, str, str], ...] = (
    ("quote", "AN", "Angebote"),
    ("order_confirmation", "AB", "Auftragsbestätigungen"),
    ("delivery_note", "LS", "Lieferscheine"),
    ("invoice", "AR", "Ausgangsrechnungen"),
    ("credit_note", "KR", "Rechnungskorrekturen"),
    ("self_billing", "GS", "Gutschriften (Self-Billing)"),
    ("dunning", "MA", "Mahnungen"),
    ("letter", "BR", "Briefe"),
)

DEFAULT_PADDING = 4


def seed_rows(company_id: uuid.UUID) -> list[NumberSeries]:
    """The eight series as unsaved rows. Shared by the service and the migration.

    A function rather than a literal in two places: the migration backfills
    existing companies and `create_company` seeds new ones, and the day those
    two disagree is the day a company created before the migration numbers
    its invoices differently from one created after it.
    """
    return [
        NumberSeries(
            company_id=company_id,
            document_type=document_type,
            name=name,
            pattern=f"{prefix}-{{YYYY}}-{{SEQ}}",
            reset=RESET_YEARLY,
            next_value=1,
            current_period=None,
            padding=DEFAULT_PADDING,
            is_active=True,
        )
        for document_type, prefix, name in DEFAULT_SERIES
    ]


def seed_number_series(db: Session, *, company_id: uuid.UUID) -> list[NumberSeries]:
    """Give a company the eight default series. No commit.

    Idempotent on document type: a company that already has an active series
    for a type keeps it. That is what makes this safe to call from
    `create_company` and from the migration's backfill without the two
    racing to create the same row.
    """
    existing = {
        row.document_type
        for row in db.query(NumberSeries)
        .filter(
            NumberSeries.company_id == company_id,
            NumberSeries.is_active.is_(True),
        )
        .all()
    }
    created = [row for row in seed_rows(company_id) if row.document_type not in existing]
    for row in created:
        db.add(row)
    if created:
        db.flush()
    return created
