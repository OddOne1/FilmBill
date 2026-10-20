"""Settings → Company → Number series.

Four endpoints, all on the singular `/company/...` path — the active company
comes from `X-Company-Id` and from nowhere else (see `middleware/company.py`),
and every handler takes its context from `require("number_series.manage")`, so
there is no way to get the context without the check.

**Nothing here formats or increments a number.** Every one of these calls
`services/number_series.py`, which is the single engine (rule 2). The preview
endpoint exists for exactly that reason: the screen needs to show the next
number while somebody is still typing a pattern, and the alternative — a
formatter in TypeScript — is two implementations of CLAUDE.md rule 5.

**There is no allocation endpoint, deliberately.** A number is taken inside
the finalize transaction and nowhere else (rule 5), and finalize is P2. An
endpoint that handed out a number on request would consume one every time
somebody reloaded a screen, and every one of those would be a gap.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.company import Membership, require
from ..models.number_series import NumberAllocation, NumberSeries
from ..schemas.number_series import (
    NumberPreviewRequest,
    NumberPreviewResponse,
    NumberSeriesCreate,
    NumberSeriesResponse,
    NumberSeriesUpdate,
)
from ..services import number_series as engine
from ..services.permissions import scoped

router = APIRouter(tags=["number-series"])


@dataclass
class _Probe:
    """The fields `plan_number` reads, with proposed values in them.

    A plain dataclass rather than an unsaved `NumberSeries`, so that asking
    "what would this edit produce" cannot put a mapped instance anywhere near
    the session's autoflush. It duck-types on exactly the five attributes the
    engine touches, and the engine's signature is annotated `NumberSeries`
    because that is what every other caller passes.
    """

    id: uuid.UUID
    pattern: str
    reset: str
    next_value: int
    current_period: Optional[str]
    padding: int


def _series_response(
    db: Session, series: NumberSeries
) -> NumberSeriesResponse:
    """One series, plus the two facts that decide whether editing it is safe.

    `allocated_count` and `latest_number` are served rather than left to the
    client to work out, because the client has no way to work them out and
    because the screen has to be able to say "this series has issued 412
    numbers" BEFORE somebody changes its pattern.
    """
    count = (
        db.query(func.count(NumberAllocation.id))
        .filter(NumberAllocation.series_id == series.id)
        .scalar()
        or 0
    )
    latest = engine.latest_allocation(db, series_id=series.id)
    return NumberSeriesResponse(
        id=series.id,
        document_type=series.document_type,
        name=series.name,
        pattern=series.pattern,
        reset=series.reset,
        next_value=series.next_value,
        current_period=series.current_period,
        padding=series.padding,
        is_active=series.is_active,
        created_at=series.created_at,
        updated_at=series.updated_at,
        allocated_count=count,
        latest_number=latest.number if latest is not None else None,
    )


def _plan_response(plan: engine.NumberPlan) -> NumberPreviewResponse:
    return NumberPreviewResponse(
        number=plan.number,
        value=plan.value,
        period=plan.period,
        starts_new_period=plan.starts_new_period,
        warnings=[
            {
                "code": warning.code,
                "message": warning.message,
                "latest_document_date": warning.latest_document_date,
                "latest_number": warning.latest_number,
            }
            for warning in plan.warnings
        ],
    )


def _series_or_404(
    db: Session, membership: Membership, series_id: uuid.UUID
) -> NumberSeries:
    """Look a series up INSIDE the active company, through `scoped()`.

    Same rule as everywhere in this layer: an id belonging to another company
    is not found rather than found-and-refused, because a 403 would confirm
    that the id names a real series somewhere on this installation.
    """
    series = (
        scoped(db.query(NumberSeries), membership)
        .filter(NumberSeries.id == series_id)
        .first()
    )
    if series is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return series


def _refuse_bad_pattern(pattern: str, reset: str, padding: int) -> None:
    """422 with the engine's own sentence.

    422 rather than 400, because this is the shape of the submitted value
    being wrong, which is what the rest of this API answers 422 for — and the
    message is the engine's, so the screen and a future CLI say the same
    thing.
    """
    try:
        engine.validate_pattern(pattern, reset)
        engine.validate_padding(padding)
    except engine.InvalidPattern as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


def _refuse_reissuing_an_allocated_number(
    db: Session,
    series: NumberSeries,
    *,
    company,
    pattern: str,
    padding: int,
    reset: str,
    next_value: int,
) -> None:
    """409 if the proposed edit would walk back over a number already issued.

    Two ways that happens, and they are the same bug from two directions:

      * `next_value` is lowered past numbers this series has already
        allocated in the current period;
      * the pattern or padding changes so that the next number spells a
        number that already exists (`AR-{YYYY}-{SEQ}` → `AR-{YY}-{SEQ}`
        re-enters a range an older pattern used).

    Checked here so the answer is a 409 naming the number rather than a
    constraint violation at finalize time, months later, in front of a
    client. The constraint is still the rule — see
    `uq_number_allocations_number` — and this is the readable version of it.
    """
    period = series.current_period
    if period is not None:
        highest = engine.highest_value_in_period(
            db, series_id=series.id, period=period
        )
        if next_value <= highest:
            taken = (
                db.query(NumberAllocation)
                .filter(
                    NumberAllocation.series_id == series.id,
                    NumberAllocation.period == period,
                    NumberAllocation.value == next_value,
                )
                .first()
            )
            named = taken.number if taken is not None else f"value {next_value}"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"This series has already issued {named}. Setting the "
                    f"next number to {next_value} would issue it again — the "
                    f"highest value used in {period} is {highest}, so the "
                    f"next number cannot be below {highest + 1}."
                ),
            )

    # What the next number would actually spell under the proposed settings.
    # Compared against the allocations, not against the counter, because the
    # collision a pattern change causes is with a number some OTHER period
    # issued.
    #
    # The probe carries the proposed values and the real series' id, so
    # `plan_number` reads the right history. `_Probe` is a plain object and
    # NOT a `NumberSeries`: constructing a mapped instance here put it within
    # reach of the session's autoflush, and the engine only ever reads
    # attributes off it.
    #
    # `company` is passed in rather than read off the series, because
    # `NumberSeries` deliberately has no `company` relationship — the
    # scoping is `company_id` plus `scoped()`, not a lazy load. Reading
    # `series.company` here raised `AttributeError` inside the threadpool and
    # the request never returned, which py-spy found and a 500 would have
    # made obvious.
    plan = engine.plan_number(
        db,
        _Probe(
            id=series.id,
            pattern=pattern,
            reset=reset,
            next_value=next_value,
            current_period=series.current_period,
            padding=padding,
        ),
        engine.document_date_in_company_timezone(company),
    )
    clash = (
        db.query(NumberAllocation)
        .filter(
            NumberAllocation.series_id == series.id,
            NumberAllocation.number == plan.number,
        )
        .first()
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"With these settings the next number would be {plan.number}, "
                f"which this series already issued on "
                f"{clash.document_date.isoformat()}. A number is issued once "
                "(CLAUDE.md rule 5) — change the pattern or the counter."
            ),
        )


# ─── Endpoints ──────────────────────────────────────────────────────────────


@router.get("/company/number-series", response_model=list[NumberSeriesResponse])
def list_number_series(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("number_series.manage")),
):
    """This company's series, in the order the screen shows them.

    Ordered by document type so the list does not reshuffle when somebody
    renames one — a list that reorders on save is a list people lose their
    place in.
    """
    query = scoped(db.query(NumberSeries), membership)
    if not include_inactive:
        query = query.filter(NumberSeries.is_active.is_(True))
    rows = query.order_by(
        NumberSeries.document_type, NumberSeries.created_at
    ).all()
    return [_series_response(db, row) for row in rows]


@router.post(
    "/company/number-series",
    response_model=NumberSeriesResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_number_series(
    body: NumberSeriesCreate,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("number_series.manage")),
):
    """Add a series. One active series per document type.

    The second active series for a document type is refused by the partial
    unique index and reported as a 409 — not silently allowed, because
    `allocate_number` asks for THE active series and two of them would make
    which one a document gets depend on row order.
    """
    _refuse_bad_pattern(body.pattern, body.reset, body.padding)

    series = NumberSeries(
        company_id=membership.company_id,
        document_type=body.document_type,
        name=body.name,
        pattern=body.pattern,
        reset=body.reset,
        next_value=body.next_value,
        current_period=None,
        padding=body.padding,
        is_active=True,
    )
    db.add(series)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This company already has an active series for "
                f"{body.document_type!r}. Deactivate it first, or edit it "
                "instead — a document type has one active series, so that "
                "which series numbers a document is never a question."
            ),
        ) from None

    db.refresh(series)
    return _series_response(db, series)


@router.patch(
    "/company/number-series/{series_id}", response_model=NumberSeriesResponse
)
def update_number_series(
    series_id: uuid.UUID,
    body: NumberSeriesUpdate,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("number_series.manage")),
):
    """Edit a series, including switching how it treats the turn of the year.

    **The two switches are not symmetrical, and that is the specification.**

      * yearly → continuous keeps the counter exactly where it is. No gap, no
        repeat: the next number is the one the yearly series would have
        issued.
      * continuous → yearly takes effect at the NEXT period boundary, not
        immediately. The current period carries on from where it is and only
        the next year restarts at 1 — which falls out of the engine reading
        the period's own allocations rather than assuming an empty period.

    Neither may re-issue an allocated number, which is what
    `_refuse_reissuing_an_allocated_number` is for.
    """
    series = _series_or_404(db, membership, series_id)
    changes = body.model_dump(exclude_unset=True)

    pattern = changes.get("pattern", series.pattern)
    reset = changes.get("reset", series.reset)
    padding = changes.get("padding", series.padding)
    next_value = changes.get("next_value", series.next_value)

    _refuse_bad_pattern(pattern, reset, padding)
    _refuse_reissuing_an_allocated_number(
        db,
        series,
        company=membership.company,
        pattern=pattern,
        padding=padding,
        reset=reset,
        next_value=next_value,
    )

    for field, value in changes.items():
        setattr(series, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This company already has another active series for "
                f"{series.document_type!r}."
            ),
        ) from None

    db.refresh(series)
    return _series_response(db, series)


@router.post(
    "/company/number-series/preview", response_model=NumberPreviewResponse
)
def preview_number(
    body: NumberPreviewRequest,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("number_series.manage")),
):
    """The number that WOULD be issued. Allocates nothing, writes nothing.

    Takes candidate values rather than reading the series, so the screen can
    preview an edit before it is saved. With a `document_date` in an earlier
    year it returns the same `document_date_before_latest` warning an
    allocation would, which is how somebody can see the consequence of a
    back-dated document before there is a finalize flow to show it in (P2).

    `document_date` absent means today in the COMPANY's timezone, not in
    UTC — 31 December 23:30 UTC is already 1 January in Vienna, and on a
    yearly series those are numbers from two different sequences.
    """
    series: Optional[NumberSeries] = None
    if body.series_id is not None:
        series = _series_or_404(db, membership, body.series_id)

    try:
        plan = engine.preview_number(
            db,
            company=membership.company,
            series=series,
            pattern=body.pattern,
            padding=body.padding,
            reset=body.reset,
            document_date=body.document_date,
        )
    except engine.InvalidPattern as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None

    return _plan_response(plan)
