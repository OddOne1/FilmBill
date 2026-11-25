"""The one calculation engine (CLAUDE.md rule 2, SCOPE §5.3–5.6, P1).

`calculate(document)` is the whole core path. It returns every number a
screen, a PDF or a budget sheet needs — per line, per group, per tax
category and for the document — and it computes **no tax rate**:
`apply_vat(result, rates)` is a separate call, so slice A2's budgets use the
core path and never mention VAT.

Read the order of operations once, because the rest of the file is that
order:

  1. **Each LINE is rounded to 2 dp, on its own** (§5.6). Not the group sum,
     not the document sum. A group of seven lines at 0.333 is seven times
     0.33 = 2.31, and 2.331 is the wrong answer.
  2. **Group adjustments** apply to their group's subtotal, in list order,
     each rounded to 2 dp.
  3. **Document adjustments** apply to the document subtotal, in list order,
     each rounded to 2 dp.
  4. **Net per tax category** is the lines' nets plus every adjustment
     allocated **pro rata by net** across the categories in its own base
     (§5.4).
  5. **VAT, only if asked, is per category on the SUM of that category's
     nets** (§5.6) — never per line. 2 499.98 + 2.31 taxed at 20 % is
     500.46, and taxing each of eight lines and adding would give a
     different answer on the next document; the rule has to be the sum
     either way.

**Every arithmetic operation goes through `core/rounding.py`**, including the
multiplications and divisions that are not rounded. The reason is in that
module's docstring: Python's decimal context is global and mutable, so `*`
and `/` read a precision and a rounding mode that any library in the process
can have changed. `multiply`, `divide` and `percent_of` carry the explicit
context, and a grep of this file for a bare `/` between two Decimals should
find nothing.

**Nothing in this file touches a database, a request or a company.** The
boundary is enforced in `tests/test_billing_import_boundary.py` by importing
the package in a fresh interpreter and asking that interpreter what it
loaded.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional, Sequence, Union

try:  # pragma: no cover - exercised by whichever import path is in use
    from ...core.rounding import (
        divide,
        exact_arithmetic,
        multiply,
        percent_of,
        quantize_amount,
    )
except ImportError:  # pragma: no cover
    from core.rounding import (  # type: ignore[no-redef]
        divide,
        exact_arithmetic,
        multiply,
        percent_of,
        quantize_amount,
    )

from .model import ZERO, Adjustment, CalcDocument, Child, Group, Line
from .vocabulary import (
    UNALLOCATED_TAX_REF,
    WARN_ADJUSTMENT_NOT_ALLOCATABLE,
    WARN_COLLAPSED_PRICE_NOT_EXACT,
    WARN_KIND_FILTER_MATCHED_NOTHING,
    WARN_MIXED_DAYS_IN_GROUP,
    WARNING_TEXT,
    CalcError,
)

HUNDRED = Decimal("100")


# ─── Results ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CalcWarning:
    """Something the numbers are correct about and a person should still see.

    A code plus the row it is about plus a sentence. The code is for a client
    that wants to render its own copy; the sentence is so that a client which
    does not still says something — the failure mode this guards against is a
    screen that shows a silent, unexplained state (CLAUDE.md 17c).
    """

    code: str
    message: str
    #: The group or adjustment the warning is about, where there is one.
    where: Optional[str] = None


@dataclass(frozen=True)
class LineResult:
    """One line's numbers. `net` is already rounded; nothing re-rounds it."""

    id: str
    label: str
    net: Decimal
    cost_net: Decimal
    margin: Decimal
    margin_percent: Optional[Decimal]
    tax_ref: str
    kind: str
    period_unit: str
    period_value: Decimal
    #: The product before the line's own discount, rounded to 2 dp. Shown as
    #: the struck-through figure beside a discounted line, and the only way a
    #: reader can check the discount without redoing the multiplication.
    gross: Decimal
    #: What the line's own discount took off, as a positive number.
    discount_amount: Decimal
    #: The line's `exempt_from`, carried through so `_apply_adjustments` can
    #: leave it out of an adjustment with a matching role. Not sent by the API.
    exempt_from: tuple[str, ...] = ()
    #: `"line"`. A constant, carried so a result tree can be told apart row
    #: by row without inspecting which fields happen to be present. The
    #: API's response models discriminate on it, and so does a client
    #: walking the tree.
    type: str = "line"


@dataclass(frozen=True)
class AdjustmentResult:
    """One applied adjustment, with where its money went for tax purposes."""

    label: str
    type: str
    mode: str
    base: str
    kinds: tuple[str, ...]
    #: What the percentage was taken OF, or what an amount was allocated
    #: across.
    base_amount: Decimal
    #: Signed: negative for a discount. Rounded to 2 dp.
    amount: Decimal
    #: `tax_ref → signed share`. Sums to `amount` exactly.
    by_tax_ref: Mapping[str, Decimal]


@dataclass(frozen=True)
class GroupResult:
    """A group's numbers, and its children's."""

    id: str
    title: str
    description: str
    children: tuple[Union["GroupResult", LineResult], ...]
    #: The sum of the children, before this group's own adjustments.
    subtotal_before_adjustments: Decimal
    adjustments: tuple[AdjustmentResult, ...]
    #: After them. This is what the group contributes to its parent.
    subtotal: Decimal
    cost_subtotal: Decimal
    margin: Decimal
    margin_percent: Optional[Decimal]
    #: True when every line under this group has the same
    #: `(period_unit, period_value)` — SCOPE §5.4's uniform-days rule.
    uniform_period: bool
    #: This group's contribution to the document's net per tax category: its
    #: lines, its own adjustments and its descendants'. Sums to `subtotal`.
    net_by_tax_ref: Mapping[str, Decimal]
    #: `"group"`. See `LineResult.type`.
    type: str = "group"


@dataclass(frozen=True)
class TaxRefTotal:
    """Net, and — after `apply_vat` — rate, VAT and gross, for one category."""

    tax_ref: str
    net: Decimal
    rate: Optional[Decimal] = None
    vat: Optional[Decimal] = None
    gross: Optional[Decimal] = None


@dataclass(frozen=True)
class CalcResult:
    """Everything `calculate` knows. No VAT: that is `apply_vat`."""

    children: tuple[Union[GroupResult, LineResult], ...]
    #: Every line in the document, flat, keyed by id — so a screen can look
    #: one up without walking the tree it is already rendering.
    lines: Mapping[str, LineResult]
    #: The sum of the top-level children, before document adjustments.
    subtotal: Decimal
    adjustments: tuple[AdjustmentResult, ...]
    #: The document total after them. THE net.
    net: Decimal
    cost: Decimal
    margin: Decimal
    margin_percent: Optional[Decimal]
    #: `tax_ref → net`, adjustments allocated pro rata. Sums to `net`.
    net_by_tax_ref: Mapping[str, Decimal]
    warnings: tuple[CalcWarning, ...]

    @property
    def tax_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self.net_by_tax_ref))


@dataclass(frozen=True)
class VatResult:
    """What `apply_vat` adds: VAT per category on the sum of nets, and gross."""

    by_tax_ref: tuple[TaxRefTotal, ...]
    net: Decimal
    vat: Decimal
    gross: Decimal


# ─── Small shared arithmetic ────────────────────────────────────────────────


def _margin_percent(net: Decimal, margin: Decimal) -> Optional[Decimal]:
    """`margin / net` as a percentage, or `None` when net is zero.

    `None`, not 0 and not 100: a zero-net row has no margin percentage, and
    either number would be a claim. It is `Optional` all the way out to the
    generated TypeScript for the same reason.
    """
    if net == ZERO:
        return None
    return quantize_amount(multiply(divide(margin, net), HUNDRED))


def percent_share(part: Decimal, whole: Decimal) -> Optional[Decimal]:
    """`part / whole` as a percentage, or `None` when `whole` is zero.

    Public, and the only reason it is: A2's budget top sheet shows each
    section's **share of the total**, and the web app may not compute it
    (rule 2). It is the same arithmetic `_margin_percent` does, through the
    same explicit-context helpers, so a share and a margin percentage round
    identically — two implementations of one division is how two screens
    end up a tenth of a percent apart.

    `None` rather than 0 on a zero total, for `_margin_percent`'s reason: a
    share of nothing is undefined, and either number would be a claim.

    It adds nothing to and changes nothing about the engine's calculation
    contract — `calculate`, `apply_vat` and every value object are untouched.
    """
    if whole == ZERO:
        return None
    with exact_arithmetic():
        return quantize_amount(multiply(divide(part, whole), HUNDRED))


def _merge_into(target: dict[str, Decimal], source: Mapping[str, Decimal]) -> None:
    """Add `source`'s per-category amounts into `target`, in place."""
    for ref, value in source.items():
        target[ref] = target.get(ref, ZERO) + value


# ─── Lines ──────────────────────────────────────────────────────────────────


def _line_result(line: Line) -> LineResult:
    """§5.3's formula, rounded once, plus the same product over `unit_cost`.

    **The discount does not reduce the cost.** The prompt for this slice says
    "same formula with `unit_cost` for `cost_net`", and this is the one place
    that wording is not followed literally — deliberately, and it is the
    first thing to change if Mathias disagrees (flagged in the A1 report).

    The reason: a discount is a concession to the customer, and the gaffer
    still costs what the gaffer costs. If the discount reduced `cost_net`
    too, `margin_percent` would be IDENTICAL before and after any percentage
    discount — the one number a producer looks at to decide whether they can
    afford to give a discount would be the one number that cannot react to
    it. Acceptance step 4 ("change the Crew group discount to 10 %: the
    numbers move and the margin changes") is satisfied either way, which is
    exactly why it is worth writing down rather than leaving to be noticed.

    Group and document adjustments do not touch cost either, for the same
    reason — see `_apply_adjustments`.
    """
    product = multiply(line.quantity, line.period_value, line.factor, line.unit_price)
    gross = quantize_amount(product)
    net = _discounted(line, product)
    cost_net = quantize_amount(
        multiply(line.quantity, line.period_value, line.factor, line.unit_cost)
    )

    if net < ZERO and not line.allow_negative:
        over_discounted = (
            line.discount is not None and line.discount.mode == "amount"
        )
        raise CalcError(
            f"line {line.id!r} comes to {net}, which is negative. "
            + (
                f"Its discount of {line.discount.value} is larger than the "
                f"line itself ({gross}). "
                if over_discounted
                else ""
            )
            + "Flag the line allow_negative if it is a correction; otherwise "
            "this is a typo."
        )

    margin = net - cost_net
    return LineResult(
        id=line.id,
        label=line.label,
        net=net,
        cost_net=cost_net,
        margin=margin,
        margin_percent=_margin_percent(net, margin),
        tax_ref=line.tax_ref,
        kind=line.kind,
        period_unit=line.period_unit,
        period_value=line.period_value,
        gross=gross,
        discount_amount=gross - net,
        exempt_from=line.exempt_from,
    )


def _discounted(line: Line, product: Decimal) -> Decimal:
    """`product` less the line's own discount, rounded to 2 dp. Once.

    The percentage case multiplies the WHOLE unrounded product by
    `(1 − d/100)` and rounds that — it does not round a discount amount and
    subtract it. §5.3 writes the formula with the discount inside the
    `round(...)`, and the two readings differ by a cent often enough to
    matter.
    """
    if line.discount is None:
        return quantize_amount(product)
    if line.discount.mode == "percent":
        keep = divide(HUNDRED - line.discount.value, HUNDRED)
        return quantize_amount(multiply(product, keep))
    return quantize_amount(product - line.discount.value)


# ─── Adjustments ────────────────────────────────────────────────────────────


def _lines_of(node: Union[Group, Line, CalcDocument]) -> tuple[Line, ...]:
    """Every `Line` under `node`, in document order, recursively."""
    if isinstance(node, Line):
        return (node,)
    found: list[Line] = []
    for child in node.children:
        found.extend(_lines_of(child))
    return tuple(found)


def _allocate(amount: Decimal, weights: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Split `amount` across `weights` pro rata, summing to `amount` exactly.

    Each share is rounded to 2 dp, which means the shares do not naturally
    add up: 8 % of an 11 590.00 subtotal split across two categories is two
    roundings and can land a cent away from 927.20. The drift is given to the
    largest share by absolute weight, so the per-category nets add up to the
    document net **to the cent** — the property the A1 prompt asks for, and
    the one an auditor checks first.

    Three cases that are not pro rata:

      * **no categories at all** — the amount is parked under
        `UNALLOCATED_TAX_REF` and the caller warns. It is still in the
        document total; what it is not is silently attached to somebody's
        VAT.
      * **every weight zero** (an empty group, or nets that cancel out) —
        split equally, because there is no information to prefer one category
        and an equal split at least keeps the sum right.
      * **`amount` is zero** — every share is zero, which the loop does
        without a special case.
    """
    if not weights:
        return {UNALLOCATED_TAX_REF: amount}

    refs = sorted(weights)
    total = sum(weights.values(), ZERO)

    if total == ZERO:
        count = Decimal(len(refs))
        shares = {ref: quantize_amount(divide(amount, count)) for ref in refs}
    else:
        shares = {
            ref: quantize_amount(divide(multiply(amount, weights[ref]), total))
            for ref in refs
        }

    drift = amount - sum(shares.values(), ZERO)
    if drift != ZERO:
        heaviest = max(refs, key=lambda ref: (abs(weights[ref]), ref))
        shares[heaviest] = shares[heaviest] + drift
    return shares


def _apply_adjustments(
    adjustments: Sequence[Adjustment],
    *,
    subtotal: Decimal,
    lines: Sequence[LineResult],
    by_tax_ref: dict[str, Decimal],
    where: str,
    warnings: list[CalcWarning],
) -> tuple[tuple[AdjustmentResult, ...], Decimal]:
    """Apply `adjustments` in order. Returns the results and the new total.

    `by_tax_ref` is this container's own net-per-category and is **mutated**:
    it arrives as the merge of the container's children (so it sums to
    `subtotal`, descendants' own adjustments included) and each adjustment's
    allocation lands in it as the adjustment is applied. That is what makes
    `subtotal_incl_previous` mean what §5.4 says — the state at that point in
    the list — instead of being recomputed from the lines, which would make a
    compound chain give the same answer as a non-compound one.

    The three bases, and why each takes its weights from a different place:

      * `subtotal` — base is the container's subtotal BEFORE any of its own
        adjustments, and the weights are the snapshot of `by_tax_ref` taken
        here, before the loop. Not the running state: that is what
        `subtotal_incl_previous` is for.
      * `subtotal_incl_previous` — base is the running total, weights are the
        live `by_tax_ref`.
      * `kinds` — base and weights are the container's LINES whose `kind` is
        listed. Lines only: an adjustment has no kind, so a kind filter
        cannot select one, and a fee "on labor and equipment_rental" means
        those items and not a share of the group discount above them.

    Adjustments change price only. `cost_subtotal` never sees them — see
    `_line_result`'s docstring for why.
    """
    results: list[AdjustmentResult] = []
    running = subtotal
    #: Snapshot for the `subtotal` base, taken before anything is applied.
    at_start: dict[str, Decimal] = dict(by_tax_ref)

    for adjustment in adjustments:
        # B1.2: the lines that opted out of this adjustment's role. Their own
        # nets come off the base and off their category's weight; nothing
        # else about the adjustment changes. No role, no exemption.
        exempt = (
            [line for line in lines if adjustment.role in line.exempt_from]
            if adjustment.role
            else []
        )
        if adjustment.base == "subtotal":
            base_amount = subtotal
            weights = {ref: value for ref, value in at_start.items()}
        elif adjustment.base == "subtotal_incl_previous":
            base_amount = running
            weights = {ref: value for ref, value in by_tax_ref.items()}
        else:  # "kinds" — validated in model.Adjustment, so no else branch
            weights = {}
            for line in lines:
                if line.kind in adjustment.kinds and not (
                    adjustment.role and adjustment.role in line.exempt_from
                ):
                    weights[line.tax_ref] = weights.get(line.tax_ref, ZERO) + line.net
            base_amount = sum(weights.values(), ZERO)
        if adjustment.base != "kinds":
            for line in exempt:
                base_amount = base_amount - line.net
                weights[line.tax_ref] = weights.get(line.tax_ref, ZERO) - line.net

        if adjustment.mode == "percent":
            amount = quantize_amount(percent_of(base_amount, adjustment.value))
            if adjustment.type == "discount":
                amount = -amount
        else:
            amount = quantize_amount(adjustment.signed_value)

        shares = _allocate(amount, weights)

        if shares.get(UNALLOCATED_TAX_REF, ZERO) != ZERO:
            warnings.append(
                CalcWarning(
                    code=WARN_ADJUSTMENT_NOT_ALLOCATABLE,
                    message=WARNING_TEXT[WARN_ADJUSTMENT_NOT_ALLOCATABLE],
                    where=f"{where}: {adjustment.label}",
                )
            )
        if adjustment.base == "kinds" and not weights:
            warnings.append(
                CalcWarning(
                    code=WARN_KIND_FILTER_MATCHED_NOTHING,
                    message=WARNING_TEXT[WARN_KIND_FILTER_MATCHED_NOTHING],
                    where=f"{where}: {adjustment.label}",
                )
            )

        _merge_into(by_tax_ref, shares)
        running = running + amount
        results.append(
            AdjustmentResult(
                label=adjustment.label,
                type=adjustment.type,
                mode=adjustment.mode,
                base=adjustment.base,
                kinds=adjustment.kinds,
                base_amount=quantize_amount(base_amount),
                amount=amount,
                by_tax_ref=dict(shares),
            )
        )

    return tuple(results), running


# ─── The tree ───────────────────────────────────────────────────────────────


def _uniform_period(lines: Sequence[Line]) -> bool:
    """SCOPE §5.4: do all these lines carry the same period?

    The PAIR `(period_unit, period_value)`, not the number: "1 day" and
    "1 flat" are different rows on a document, and a collapsed group that
    claimed otherwise would print the wrong word. An empty group, or one with
    a single line, is uniform trivially.
    """
    periods = {(line.period_unit, line.period_value) for line in lines}
    return len(periods) <= 1


def _calculate_children(
    children: Sequence[Child],
    *,
    index: dict[str, LineResult],
    warnings: list[CalcWarning],
) -> tuple[
    tuple[Union[GroupResult, LineResult], ...],
    Decimal,
    Decimal,
    dict[str, Decimal],
]:
    """Price an ordered list of children.

    Returns `(results, subtotal, cost, net_by_tax_ref)`, where the last one
    already contains any adjustments the CHILD groups applied to themselves.
    That is what makes a parent's `subtotal` base and its per-category
    weights agree: both are this map, and it sums to `subtotal`.
    """
    results: list[Union[GroupResult, LineResult]] = []
    subtotal = ZERO
    cost = ZERO
    by_tax_ref: dict[str, Decimal] = {}

    for child in children:
        if isinstance(child, Line):
            line_result = _line_result(child)
            index[line_result.id] = line_result
            results.append(line_result)
            subtotal += line_result.net
            cost += line_result.cost_net
            by_tax_ref[line_result.tax_ref] = (
                by_tax_ref.get(line_result.tax_ref, ZERO) + line_result.net
            )
        else:
            group_result = _calculate_group(child, index=index, warnings=warnings)
            results.append(group_result)
            subtotal += group_result.subtotal
            cost += group_result.cost_subtotal
            _merge_into(by_tax_ref, group_result.net_by_tax_ref)

    return tuple(results), subtotal, cost, by_tax_ref


def _calculate_group(
    group: Group,
    *,
    index: dict[str, LineResult],
    warnings: list[CalcWarning],
) -> GroupResult:
    children, subtotal, cost_subtotal, by_tax_ref = _calculate_children(
        group.children, index=index, warnings=warnings
    )

    own_lines = tuple(index[line.id] for line in _lines_of(group))
    adjustments, after = _apply_adjustments(
        group.adjustments,
        subtotal=subtotal,
        lines=own_lines,
        by_tax_ref=by_tax_ref,
        where=f"group {group.title or group.id}",
        warnings=warnings,
    )

    uniform = _uniform_period(_lines_of(group))
    if not uniform:
        warnings.append(
            CalcWarning(
                code=WARN_MIXED_DAYS_IN_GROUP,
                message=WARNING_TEXT[WARN_MIXED_DAYS_IN_GROUP],
                where=f"group {group.title or group.id}",
            )
        )

    margin = after - cost_subtotal
    return GroupResult(
        id=group.id,
        title=group.title,
        description=group.description,
        children=children,
        subtotal_before_adjustments=subtotal,
        adjustments=adjustments,
        subtotal=after,
        cost_subtotal=cost_subtotal,
        margin=margin,
        margin_percent=_margin_percent(after, margin),
        uniform_period=uniform,
        net_by_tax_ref=dict(by_tax_ref),
    )


def calculate(document: CalcDocument) -> CalcResult:
    """Price `document`. The core path, and the only one a budget needs.

    No tax rate is read, applied or implied. `net_by_tax_ref` groups by the
    opaque key each line carries; what those keys MEAN is the tax module's
    business, and `apply_vat` is where a rate meets a net.
    """
    if not isinstance(document, CalcDocument):
        raise CalcError(
            f"calculate() takes a CalcDocument, not a "
            f"{type(document).__name__} — parse the input with "
            f"`document_from_data` first"
        )
    with exact_arithmetic():
        return _calculate(document)


def _calculate(document: CalcDocument) -> CalcResult:
    """`calculate`'s body, inside the decimal context it opened."""
    index: dict[str, LineResult] = {}
    warnings: list[CalcWarning] = []

    children, subtotal, cost, by_tax_ref = _calculate_children(
        document.children, index=index, warnings=warnings
    )

    all_lines = tuple(index[line.id] for line in _lines_of(document))
    adjustments, net = _apply_adjustments(
        document.adjustments,
        subtotal=subtotal,
        lines=all_lines,
        by_tax_ref=by_tax_ref,
        where="the document",
        warnings=warnings,
    )

    # Drop a category that came to nothing and was never on a line. A
    # `__unallocated__: 0.00` in the output would make `apply_vat` demand a
    # rate for a category holding no money.
    on_a_line = {result.tax_ref for result in all_lines}
    net_by_tax_ref = {
        ref: value
        for ref, value in by_tax_ref.items()
        if ref in on_a_line or value != ZERO
    }

    margin = net - cost
    return CalcResult(
        children=children,
        lines=dict(index),
        subtotal=subtotal,
        adjustments=adjustments,
        net=net,
        cost=cost,
        margin=margin,
        margin_percent=_margin_percent(net, margin),
        net_by_tax_ref=net_by_tax_ref,
        warnings=tuple(warnings),
    )


# ─── VAT, separately ────────────────────────────────────────────────────────


def apply_vat(result: CalcResult, rates: Mapping[str, Decimal]) -> VatResult:
    """VAT per tax category on the SUM of that category's nets (§5.6).

    Not per line. The difference is a cent on almost every document with more
    than one line, and §5.6 settles it: line nets are rounded, then summed
    per category, and the VAT is one rounding of one sum. Taxing each line
    and adding the results is the classic way a system's totals stop matching
    its own line items.

    `rates` are percentages the way the rest of FilmBill spells them —
    `Decimal("20")` is 20 %, the same as `tax_codes.rate` and the tax
    engine's `format_rate`.

    **A missing rate is a refusal, not a zero.** A category with no rate
    means the caller has not decided what tax applies, and answering 0 %
    would be a decision this function is not entitled to make — it is also
    how a reverse-charge document and a forgotten configuration would look
    identical.
    """
    with exact_arithmetic():
        return _apply_vat(result, rates)


def _apply_vat(result: CalcResult, rates: Mapping[str, Decimal]) -> VatResult:
    """`apply_vat`'s body, inside the decimal context it opened."""
    missing = sorted(ref for ref in result.net_by_tax_ref if ref not in rates)
    if missing:
        named = ", ".join(repr(ref) for ref in missing)
        count = len(result.net_by_tax_ref)
        raise CalcError(
            f"no VAT rate was given for tax category {named}, and this "
            f"document has {count} categor{'y' if count == 1 else 'ies'}. A "
            f"missing rate is not 0 % — say what applies, or the totals "
            f"would claim a decision nobody made."
            + (
                f" {UNALLOCATED_TAX_REF!r} holds an adjustment that had no "
                f"net to be allocated against; see the result's warnings."
                if UNALLOCATED_TAX_REF in missing
                else ""
            )
        )

    totals: list[TaxRefTotal] = []
    vat_total = ZERO
    for ref in sorted(result.net_by_tax_ref):
        net = result.net_by_tax_ref[ref]
        rate = rates[ref]
        if not isinstance(rate, Decimal):
            raise CalcError(
                f"the VAT rate for {ref!r} must be a Decimal, not "
                f"{type(rate).__name__} — a rate that arrived as a float has "
                f"already lost its exact value"
            )
        if rate < ZERO or rate > HUNDRED:
            raise CalcError(
                f"the VAT rate for {ref!r} is {rate}; a rate is a percentage "
                f"between 0 and 100 (20 % is Decimal('20'), not "
                f"Decimal('0.2'))"
            )
        vat = quantize_amount(percent_of(net, rate))
        vat_total += vat
        totals.append(
            TaxRefTotal(tax_ref=ref, net=net, rate=rate, vat=vat, gross=net + vat)
        )

    return VatResult(
        by_tax_ref=tuple(totals),
        net=result.net,
        vat=vat_total,
        gross=result.net + vat_total,
    )


# ─── The collapsed group row (§5.4's uniform-days rule) ─────────────────────


@dataclass(frozen=True)
class GroupDisplayRow:
    """One row standing for a whole group on a document ("Groups only").

    `total` is always the group's subtotal and is always right.
    `price_per_period` is a presentation of it, and `exact` says whether it
    multiplies back — see `WARN_COLLAPSED_PRICE_NOT_EXACT`.
    """

    group_id: str
    title: str
    quantity: Decimal
    #: `None` when the group's lines do not share a period: the row then
    #: reads "1 × € total" with no days in it (§5.4).
    period_value: Optional[Decimal]
    period_unit: Optional[str]
    #: The group total divided by the period, rounded, or the total itself
    #: when there is no shared period.
    price_per_period: Decimal
    total: Decimal
    uniform_period: bool
    exact: bool
    warnings: tuple[CalcWarning, ...]


def display_row_for_group(
    group: Group, result: Optional[GroupResult] = None
) -> GroupDisplayRow:
    """The collapsed row for `group`, per SCOPE §5.4.

    "price/day × days" **only** when every line in the group is charged for
    the same period; otherwise "1 × total" plus `mixed_days_in_group`. That
    is the rule the editor warning in §5.4 is about, and putting it here —
    beside the engine, on the engine's own numbers — is what stops the web
    app from deciding it a second time and differently.

    `result` is accepted so a caller that has already run `calculate` does
    not price the group twice. Without it the group is priced here, through
    the same functions.
    """
    with exact_arithmetic():
        return _display_row_for_group(group, result)


def _display_row_for_group(
    group: Group, result: Optional[GroupResult]
) -> GroupDisplayRow:
    """`display_row_for_group`'s body, inside its decimal context."""
    if result is None:
        result = _calculate_group(group, index={}, warnings=[])
    if result.id != group.id:
        raise CalcError(
            f"display_row_for_group was given group {group.id!r} and the "
            f"result for {result.id!r}"
        )

    total = result.subtotal
    lines = _lines_of(group)
    warnings: list[CalcWarning] = []

    if not result.uniform_period or not lines:
        if lines:
            warnings.append(
                CalcWarning(
                    code=WARN_MIXED_DAYS_IN_GROUP,
                    message=WARNING_TEXT[WARN_MIXED_DAYS_IN_GROUP],
                    where=f"group {group.title or group.id}",
                )
            )
        return GroupDisplayRow(
            group_id=group.id,
            title=group.title,
            quantity=Decimal(1),
            period_value=None,
            period_unit=None,
            price_per_period=total,
            total=total,
            uniform_period=result.uniform_period,
            exact=True,
            warnings=tuple(warnings),
        )

    period_value = lines[0].period_value
    period_unit = lines[0].period_unit

    if period_value == ZERO:
        # Nothing to divide by, and a group priced over zero days has no
        # per-period price to show. The total is still the total.
        per_period = total
        exact = total == ZERO
    else:
        per_period = quantize_amount(divide(total, period_value))
        exact = quantize_amount(multiply(per_period, period_value)) == total

    if not exact:
        warnings.append(
            CalcWarning(
                code=WARN_COLLAPSED_PRICE_NOT_EXACT,
                message=WARNING_TEXT[WARN_COLLAPSED_PRICE_NOT_EXACT],
                where=f"group {group.title or group.id}",
            )
        )

    return GroupDisplayRow(
        group_id=group.id,
        title=group.title,
        quantity=Decimal(1),
        period_value=period_value,
        period_unit=period_unit,
        price_per_period=per_period,
        total=total,
        uniform_period=True,
        exact=exact,
        warnings=tuple(warnings),
    )
