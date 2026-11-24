"""Cost planning over a budget: Committed, Actual and cost-to-complete.

Pure Python over plain value objects, the same boundary `billing/calc` keeps
and for the same reason (CLAUDE.md rule 2, SCOPE D70): the arithmetic that
answers "what will this budget really cost" has to be reviewable as a table
of inputs and expected cents, and a function that took a `Session` would make
every fixture need a database.
`tests/test_billing_import_boundary.py` imports this package in a fresh
interpreter and asks that interpreter what it loaded.

**The one rule most likely to be gotten wrong**

    exposure = actual + committed_open      and NEVER
    exposure = actual + committed_gross

A commitment that has already been invoiced must not count twice. A purchase
order for 1 000 with an invoice of 600 against it is 1 000 of exposure — 600
spent and 400 still owed — not 1 600. `committed_open` is what is left of
each OPEN commitment after the invoices linked to it, floored at zero, and
that floor is the second half of the same rule: a commitment of 700 invoiced
at 750 has 0 open and not −50, because over-invoicing is a fact about the
actual and not a credit against the order.

**The definitions, once**

* ``committed_gross`` — every commitment that is neither voided nor
  cancelled, at its full amount. What was ordered.
* ``committed_open`` — per OPEN commitment, ``max(0, amount − linked
  non-voided actuals)``. A closed commitment contributes 0 (it is settled); a
  cancelled or voided one contributes 0 (it was withdrawn).
* ``actual`` — every non-voided actual, linked to a commitment or not.
* ``exposure`` — ``actual + committed_open``. Spent, or owed.
* ``remaining`` — the user's ``etc`` when they set one, otherwise the default
  rule ``max(estimate_cost − actual, committed_open)``: whatever is left of
  the estimate, but never less than what is already on order.
* ``efc`` — ``actual + remaining``. The estimate at completion.
* ``variance`` — ``estimate_cost − efc``. **Positive means under budget.**
* ``variance_percent`` — ``variance / estimate_cost × 100``, ``None`` when
  the estimate is zero (a percentage of nothing is a claim, not a number).
* ``over_budget`` — ``efc > estimate_cost``.

**The default `remaining` rule is [Guessing]** (SCOPE D83, an open decision
for Mathias and not for the Steuerberater). `max(estimate − actual,
committed_open)` is the behaviour a production accountant usually expects —
a line keeps forecasting its estimate until the invoices overtake it — but
"take the estimate until the line is closed", "take the actual once anything
has been invoiced" and "take the larger of estimate and exposure" are all
defensible, and they give different numbers on a half-spent line. Which rule
produced a number is therefore part of the answer: every line result carries
``remaining_rule``, ``"etc"`` or ``"default"``, and the API passes it through.

**Forecast margin uses the ESTIMATE price, always**

``forecast_margin = price − efc``, where ``price`` is the estimate-phase
client price the calculation engine computed. It is not re-derived from
costs, and it does not move when costs do: the client agreed a price, and a
budget whose price crept up as the shoot got more expensive would be a
budget that could never show a loss. That is the whole point of the number —
it says what is left of the agreed price once the real cost is in.

**Unallocated entries.** A commitment or an actual may carry a section and no
line: an unbudgeted cost that belongs in Travel but matches no row. Those
contribute to a section's and the budget's ``actual`` and ``committed_open``
and are reported separately as ``unallocated`` so the number is never
mistaken for a line. Their contribution to ``remaining`` is their open
commitment — what is still owed on them — which keeps the identity
``efc = actual + remaining`` true at every level; they have no estimate of
their own, so the default rule has nothing to apply to them.

Amounts in are already 2 dp (``NUMERIC(18,2)``), so the only rounding here is
a final `quantize` to 2 dp on each sum, and percentages go through the same
`quantize_amount` the engine's own `percent_share` uses — two
implementations of one division is how two screens end up a tenth of a
percent apart.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Optional, Sequence

try:  # pragma: no cover - exercised by whichever import path is in use
    from ...core.rounding import (
        divide,
        exact_arithmetic,
        multiply,
        quantize_amount,
    )
except ImportError:  # pragma: no cover
    from core.rounding import (  # type: ignore[no-redef]
        divide,
        exact_arithmetic,
        multiply,
        quantize_amount,
    )

ZERO = Decimal("0")
HUNDRED = Decimal("100")

#: A commitment's lifecycle. `open` is owed, `closed` is settled by the
#: actuals linked to it, `cancelled` is withdrawn and was never owed.
#: Closed and cancelled both contribute 0 to `committed_open` — for
#: different reasons, which is why they are different states and not one
#: `is_open` boolean: a cancelled order is not in `committed_gross` either.
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_CANCELLED = "cancelled"
COMMITMENT_STATUSES: tuple[str, ...] = (STATUS_OPEN, STATUS_CLOSED, STATUS_CANCELLED)

#: Which rule produced a line's `remaining`. Part of the answer, not a
#: detail: the default one is [Guessing] (see the module docstring).
RULE_ETC = "etc"
RULE_DEFAULT = "default"
REMAINING_RULES: tuple[str, ...] = (RULE_ETC, RULE_DEFAULT)

__all__ = [
    "COMMITMENT_STATUSES",
    "REMAINING_RULES",
    "RULE_DEFAULT",
    "RULE_ETC",
    "STATUS_CANCELLED",
    "STATUS_CLOSED",
    "STATUS_OPEN",
    "Commitment",
    "Actual",
    "LineInput",
    "SectionInput",
    "BudgetInput",
    "LineTracking",
    "RollupTracking",
    "TrackingResult",
    "closes_commitment",
    "committed_open_of",
    "track_budget",
    "track_line",
]


# ─── What goes in ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Commitment:
    """One order, agreement or PO, as plain data.

    `id` is needed because actuals link to it by id; `status` and `voided`
    are separate because a voided commitment keeps its status (a voided
    closed commitment is still closed, it is simply out of every number).
    """

    amount: Decimal
    id: str = ""
    status: str = STATUS_OPEN
    voided: bool = False

    @property
    def counts(self) -> bool:
        """In `committed_gross`: not voided and not cancelled."""
        return not self.voided and self.status != STATUS_CANCELLED


@dataclass(frozen=True)
class Actual:
    """One supplier invoice or receipt, as plain data."""

    amount: Decimal
    id: str = ""
    voided: bool = False
    #: The commitment this settles, where there is one.
    commitment_id: Optional[str] = None

    @property
    def counts(self) -> bool:
        return not self.voided


@dataclass(frozen=True)
class LineInput:
    """One budget line and the entries booked against it."""

    estimate_cost: Decimal
    id: str = ""
    commitments: tuple[Commitment, ...] = ()
    actuals: tuple[Actual, ...] = ()
    #: The user's own estimate of what is still to be spent. `None` means
    #: "use the default rule"; `Decimal("0")` means "nothing more to spend"
    #: and is NOT the same thing.
    etc: Optional[Decimal] = None


@dataclass(frozen=True)
class SectionInput:
    """One section: its lines, its sub-sections, and its unallocated entries.

    `price` is the estimate-phase client price the engine computed for this
    section (`GroupResult.subtotal`). It is passed in rather than derived:
    this module does not price anything (rule 2).
    """

    id: str = ""
    lines: tuple[LineInput, ...] = ()
    children: tuple["SectionInput", ...] = ()
    #: Entries that carry this section and no line.
    commitments: tuple[Commitment, ...] = ()
    actuals: tuple[Actual, ...] = ()
    price: Optional[Decimal] = None


@dataclass(frozen=True)
class BudgetInput:
    """The whole budget. `price` is the engine's document net."""

    sections: tuple[SectionInput, ...] = ()
    id: str = ""
    #: Entries that carry neither a section nor a line cannot exist — a
    #: section is required — so the budget's own unallocated total is the
    #: sum of its sections'.
    price: Optional[Decimal] = None


# ─── What comes out ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LineTracking:
    """One line's cost picture. Every amount 2 dp."""

    id: str
    estimate_cost: Decimal
    committed_gross: Decimal
    committed_open: Decimal
    actual: Decimal
    exposure: Decimal
    remaining: Decimal
    #: `"etc"` or `"default"` — which rule produced `remaining`.
    remaining_rule: str
    efc: Decimal
    variance: Decimal
    variance_percent: Optional[Decimal]
    over_budget: bool
    #: The ETC the user set, or `None`. Carried so a screen can show it
    #: beside the forecast it produced without a second request.
    etc: Optional[Decimal] = None


@dataclass(frozen=True)
class RollupTracking:
    """A section's or the budget's totals, plus what only a rollup has.

    `unallocated` is the exposure of entries that carry this section (or one
    of its sub-sections) and no line. `forecast_margin` is `price − efc`
    against the ESTIMATE price — see the module docstring.
    """

    id: str
    estimate_cost: Decimal
    committed_gross: Decimal
    committed_open: Decimal
    actual: Decimal
    exposure: Decimal
    remaining: Decimal
    efc: Decimal
    variance: Decimal
    variance_percent: Optional[Decimal]
    over_budget: bool
    unallocated: Decimal
    unallocated_actual: Decimal
    unallocated_committed_open: Decimal
    price: Optional[Decimal] = None
    forecast_margin: Optional[Decimal] = None
    forecast_margin_percent: Optional[Decimal] = None
    #: The estimate-phase margin beside it (`price − estimate_cost`), so a
    #: screen can show "was 57.02 %, now 38.01 %" without computing either.
    estimate_margin: Optional[Decimal] = None
    estimate_margin_percent: Optional[Decimal] = None


@dataclass(frozen=True)
class TrackingResult:
    """The whole answer, indexed the way the engine's result is.

    `lines` and `sections` are keyed by id — including nested sections — so
    a sheet that already draws a row per line and per section can look its
    numbers up rather than walk a tree a second time.
    """

    budget: RollupTracking
    lines: Mapping[str, LineTracking] = field(default_factory=dict)
    sections: Mapping[str, RollupTracking] = field(default_factory=dict)


# ─── The arithmetic ─────────────────────────────────────────────────────────


def _sum(values) -> Decimal:
    total = ZERO
    for value in values:
        total = total + value
    return quantize_amount(total)


def _percent(part: Decimal, whole: Optional[Decimal]) -> Optional[Decimal]:
    """`part / whole × 100`, or `None` when `whole` is zero or absent.

    The same two-step as the engine's `percent_share`: divide and multiply
    inside `exact_arithmetic`, round once at the end. `None` rather than 0,
    because a percentage of nothing is a claim and either number would be
    one.
    """
    if whole is None or whole == ZERO:
        return None
    with exact_arithmetic():
        return quantize_amount(multiply(divide(part, whole), HUNDRED))


def committed_open_of(
    commitments: Sequence[Commitment], actuals: Sequence[Actual]
) -> Decimal:
    """What is still owed on these commitments. **The rule, in one place.**

    Per OPEN commitment: its amount less the non-voided actuals linked to
    it, floored at zero. Closed, cancelled and voided commitments
    contribute nothing.

    The floor is not cosmetic. A commitment of 700 invoiced at 750 has 0
    open: the extra 50 is a fact about the actual, which already counts it,
    and letting the open amount go to −50 would quietly subtract it from the
    exposure of every other order in the section.
    """
    linked: dict[str, Decimal] = {}
    for actual in actuals:
        if not actual.counts or not actual.commitment_id:
            continue
        linked[actual.commitment_id] = (
            linked.get(actual.commitment_id, ZERO) + actual.amount
        )

    open_total = ZERO
    for commitment in commitments:
        if commitment.voided or commitment.status != STATUS_OPEN:
            continue
        settled = linked.get(commitment.id, ZERO)
        rest = commitment.amount - settled
        if rest > ZERO:
            open_total = open_total + rest
    return quantize_amount(open_total)


def closes_commitment(
    commitment: Commitment, actuals: Sequence[Actual]
) -> bool:
    """Have the actuals linked to `commitment` reached its amount?

    The condition behind "a commitment closes automatically when its
    invoices arrive, and reopens when one of them is voided". Here rather
    than in the service because it is the same comparison
    `committed_open_of` makes and a second spelling of it would be a second
    answer to "is this order settled".
    """
    settled = _sum(
        actual.amount
        for actual in actuals
        if actual.counts and actual.commitment_id == commitment.id
    )
    return settled >= commitment.amount


def track_line(line: LineInput) -> LineTracking:
    """One line's numbers. The definitions in the module docstring, applied."""
    estimate_cost = quantize_amount(line.estimate_cost)
    committed_gross = _sum(
        commitment.amount for commitment in line.commitments if commitment.counts
    )
    committed_open = committed_open_of(line.commitments, line.actuals)
    actual = _sum(entry.amount for entry in line.actuals if entry.counts)
    # NOT `actual + committed_gross`. See the module docstring.
    exposure = quantize_amount(actual + committed_open)

    if line.etc is not None:
        remaining = quantize_amount(line.etc)
        rule = RULE_ETC
    else:
        # The default rule, [Guessing] — SCOPE D83.
        by_estimate = estimate_cost - actual
        remaining = quantize_amount(
            committed_open if committed_open > by_estimate else by_estimate
        )
        if remaining < ZERO:
            remaining = ZERO
        rule = RULE_DEFAULT

    efc = quantize_amount(actual + remaining)
    variance = quantize_amount(estimate_cost - efc)
    return LineTracking(
        id=line.id,
        estimate_cost=estimate_cost,
        committed_gross=committed_gross,
        committed_open=committed_open,
        actual=actual,
        exposure=exposure,
        remaining=remaining,
        remaining_rule=rule,
        efc=efc,
        variance=variance,
        variance_percent=_percent(variance, estimate_cost),
        over_budget=efc > estimate_cost,
        etc=None if line.etc is None else quantize_amount(line.etc),
    )


def _rollup(
    identifier: str,
    *,
    lines: Sequence[LineTracking],
    children: Sequence[RollupTracking],
    unallocated_commitments: Sequence[Commitment],
    unallocated_actuals: Sequence[Actual],
    price: Optional[Decimal],
) -> RollupTracking:
    """Sum lines, sub-sections and this level's own unallocated entries.

    `remaining` takes the unallocated entries' OPEN COMMITMENTS with it —
    what is still owed on a cost that matches no line is still to be spent —
    which is what keeps `efc = actual + remaining` true here as well as on a
    line. They have no estimate, so the default rule has nothing to apply.
    """
    own_actual = _sum(
        entry.amount for entry in unallocated_actuals if entry.counts
    )
    own_open = committed_open_of(unallocated_commitments, unallocated_actuals)
    own_gross = _sum(
        commitment.amount
        for commitment in unallocated_commitments
        if commitment.counts
    )

    unallocated_actual = _sum(
        [own_actual] + [child.unallocated_actual for child in children]
    )
    unallocated_open = _sum(
        [own_open] + [child.unallocated_committed_open for child in children]
    )

    estimate_cost = _sum(
        [line.estimate_cost for line in lines]
        + [child.estimate_cost for child in children]
    )
    committed_gross = _sum(
        [line.committed_gross for line in lines]
        + [child.committed_gross for child in children]
        + [own_gross]
    )
    committed_open = _sum(
        [line.committed_open for line in lines]
        + [child.committed_open for child in children]
        + [own_open]
    )
    actual = _sum(
        [line.actual for line in lines]
        + [child.actual for child in children]
        + [own_actual]
    )
    remaining = _sum(
        [line.remaining for line in lines]
        + [child.remaining for child in children]
        + [own_open]
    )
    exposure = quantize_amount(actual + committed_open)
    efc = quantize_amount(actual + remaining)
    variance = quantize_amount(estimate_cost - efc)

    forecast_margin = None if price is None else quantize_amount(price - efc)
    estimate_margin = (
        None if price is None else quantize_amount(price - estimate_cost)
    )
    return RollupTracking(
        id=identifier,
        estimate_cost=estimate_cost,
        committed_gross=committed_gross,
        committed_open=committed_open,
        actual=actual,
        exposure=exposure,
        remaining=remaining,
        efc=efc,
        variance=variance,
        variance_percent=_percent(variance, estimate_cost),
        over_budget=efc > estimate_cost,
        unallocated=quantize_amount(unallocated_actual + unallocated_open),
        unallocated_actual=unallocated_actual,
        unallocated_committed_open=unallocated_open,
        price=None if price is None else quantize_amount(price),
        forecast_margin=forecast_margin,
        forecast_margin_percent=(
            None if forecast_margin is None else _percent(forecast_margin, price)
        ),
        estimate_margin=estimate_margin,
        estimate_margin_percent=(
            None if estimate_margin is None else _percent(estimate_margin, price)
        ),
    )


def _track_section(
    section: SectionInput,
    lines: dict[str, LineTracking],
    sections: dict[str, RollupTracking],
) -> RollupTracking:
    children = [_track_section(child, lines, sections) for child in section.children]
    own_lines = []
    for line in section.lines:
        result = track_line(line)
        lines[result.id] = result
        own_lines.append(result)
    rollup = _rollup(
        section.id,
        lines=own_lines,
        children=children,
        unallocated_commitments=section.commitments,
        unallocated_actuals=section.actuals,
        price=section.price,
    )
    sections[rollup.id] = rollup
    return rollup


def track_budget(budget: BudgetInput) -> TrackingResult:
    """Every number the sheet, the drawer and the top sheet need."""
    lines: dict[str, LineTracking] = {}
    sections: dict[str, RollupTracking] = {}
    top = [_track_section(section, lines, sections) for section in budget.sections]
    return TrackingResult(
        budget=_rollup(
            budget.id,
            lines=(),
            children=top,
            unallocated_commitments=(),
            unallocated_actuals=(),
            price=budget.price,
        ),
        lines=lines,
        sections=sections,
    )
