"""What goes into the calculation engine: lines, groups, adjustments.

Plain frozen dataclasses over `Decimal`, and a parser (`document_from_data`)
that turns the untyped shape an HTTP body or a fixture file has into them.
Nothing here knows about a database row, a request or a company.

**Validation happens at construction, not at calculation.** A `Line` with a
period unit the engine does not know cannot be built, so no part of the
engine has to carry an `else: # unreachable` for it. The one thing checked
later is the net itself — a discount larger than the line's own gross is only
visible once the arithmetic has run — and that lives in `engine.py` beside
the arithmetic that finds it.

**No expression language, ever.** `quantity`, `period_value`, `factor` and
the prices are numbers. SCOPE's globals (slice A2 — "crew days", "shoot
days") are resolved to numbers by the caller BEFORE the engine is reached,
and the engine never sees a name, never evaluates a string, and has no hook
through which it could. That is a security property as much as a design one:
a budget template is user input, and a template language over user input is
an evaluator over user input.

**`kind` and `tax_ref` are opaque.** The engine groups by `tax_ref` and
filters by `kind` and decides nothing about either. That is what lets the
same engine price a budget with no tax at all (slice A2 uses the core path
and never calls `apply_vat`) and an invoice with three VAT categories.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

try:  # pragma: no cover - exercised by whichever import path is in use
    from ...core.rounding import to_decimal
except ImportError:  # pragma: no cover
    from core.rounding import to_decimal

from .vocabulary import (
    ADJUSTMENT_BASES,
    ADJUSTMENT_MODES,
    ADJUSTMENT_TYPES,
    MAX_GROUP_DEPTH,
    NO_TAX_REF,
    PERIOD_UNITS,
    CalcError,
    check_member,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


# ─── Coercion ───────────────────────────────────────────────────────────────


def _number(value: Any, *, what: str, where: str) -> Decimal:
    """`value` as a `Decimal`, or a `CalcError` naming the field and the row.

    Wraps `core.rounding.to_decimal` — the one conversion in the system,
    which is also the one place a float is refused — and re-raises its
    `DecimalParseError` as a `CalcError` so that a caller catching the
    engine's error type catches every way the input can be wrong.
    """
    try:
        return to_decimal(value, what=f"{what} on {where}")
    except ValueError as error:
        raise CalcError(str(error)) from None


def _text(value: Any, *, what: str, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CalcError(
            f"{what} on {where} must be text, not {type(value).__name__}"
        )
    return value


def _required_id(value: Any, *, what: str) -> str:
    text = value if isinstance(value, str) else ""
    if not text.strip():
        raise CalcError(
            f"every {what} needs a non-empty id — it is what the result "
            f"indexes by and what a screen uses to point at the right row"
        )
    return text


# ─── Discount on a line ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Discount:
    """A line's own discount: a percentage of its gross, or a flat amount.

    Always reduces. A negative value is refused rather than treated as a
    surcharge, because a line that reads "discount −5 %" and adds money is
    the kind of thing nobody reads twice. Surcharges are adjustments
    (SCOPE §5.4) and live on the group or the document.
    """

    mode: str
    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "mode", check_member(self.mode, ADJUSTMENT_MODES, what="discount mode")
        )
        if not isinstance(self.value, Decimal):
            raise CalcError("a discount value must already be a Decimal here")
        if self.value < ZERO:
            raise CalcError(
                f"a discount cannot be negative (got {self.value}). A line "
                f"that should add money takes a surcharge adjustment on its "
                f"group or on the document instead."
            )
        if self.mode == "percent" and self.value > HUNDRED:
            raise CalcError(
                f"a discount percentage must be between 0 and 100 (got "
                f"{self.value})"
            )


# ─── Line ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Line:
    """One priced row (SCOPE §5.3).

    `net = round2(quantity × period_value × factor × unit_price × (1 −
    discount%))`, and the same product with `unit_cost` for `cost_net`.

    **The discount does NOT reduce `cost_net`.** See `engine._line_result` —
    it is the one place this file's shape and the prompt's wording come
    apart, and it is written down there because it is a decision and not an
    oversight.
    """

    id: str
    unit_price: Decimal
    quantity: Decimal = ONE
    period_value: Decimal = ONE
    period_unit: str = "day"
    factor: Decimal = ONE
    unit_cost: Decimal = ZERO
    discount: Optional[Discount] = None
    #: Opaque grouping key for tax. The engine never interprets it.
    tax_ref: str = NO_TAX_REF
    #: Opaque item kind, matched as a string by kind-filtered adjustments.
    kind: str = ""
    tags: tuple[str, ...] = ()
    #: Corrections only. Without it a negative quantity — or a net driven
    #: negative by a discount bigger than the line — is refused, because in
    #: a budget that is a typo every time.
    allow_negative: bool = False
    #: Free text for the row. Carried through untouched; the engine does not
    #: use it, and it is here so a caller does not have to keep a parallel
    #: map from line id to label.
    label: str = ""

    def __post_init__(self) -> None:
        where = f"line {self.id!r}"
        for name in ("unit_price", "quantity", "period_value", "factor", "unit_cost"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise CalcError(
                    f"{name} on {where} must already be a Decimal here — "
                    f"build lines with `line_from_data` if the value came "
                    f"from JSON"
                )
        object.__setattr__(
            self,
            "period_unit",
            check_member(self.period_unit, PERIOD_UNITS, what="period unit", where=where),
        )
        if self.factor <= ZERO:
            raise CalcError(
                f"the rental factor on {where} must be greater than zero "
                f"(got {self.factor}); a factor of 0 would price the line at "
                f"nothing and reads as an empty field rather than as a "
                f"decision"
            )
        if self.period_value < ZERO:
            raise CalcError(
                f"the period on {where} cannot be negative (got "
                f"{self.period_value}). A correction is a negative QUANTITY "
                f"on a line flagged allow_negative, not a negative number of "
                f"days."
            )
        if self.quantity < ZERO and not self.allow_negative:
            raise CalcError(
                f"the quantity on {where} is negative ({self.quantity}). "
                f"Flag the line allow_negative if it is a correction; "
                f"otherwise this is a typo."
            )
        object.__setattr__(self, "tags", tuple(self.tags))


# ─── Adjustment ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Adjustment:
    """A discount or surcharge on a group or on the document (SCOPE §5.4).

    `value` is always positive; `type` carries the sign. `base` says what the
    percentage is OF, or — for an amount — which lines the amount is
    allocated across for tax:

      * `subtotal` — the container's subtotal before any of its adjustments;
      * `subtotal_incl_previous` — that, plus the adjustments already applied
        in this container (compound, and the order of the list is the order
        of application);
      * `kinds` — only the lines in the container whose `kind` is in `kinds`.

    `signed_value` folds `type` into the number, so no arithmetic downstream
    has to remember which way a discount goes.
    """

    label: str
    type: str
    mode: str
    value: Decimal
    base: str = "subtotal"
    kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        where = f"adjustment {self.label!r}"
        object.__setattr__(
            self,
            "type",
            check_member(self.type, ADJUSTMENT_TYPES, what="adjustment type", where=where),
        )
        object.__setattr__(
            self,
            "mode",
            check_member(self.mode, ADJUSTMENT_MODES, what="adjustment mode", where=where),
        )
        object.__setattr__(
            self,
            "base",
            check_member(self.base, ADJUSTMENT_BASES, what="adjustment base", where=where),
        )
        if not isinstance(self.value, Decimal):
            raise CalcError(f"the value on {where} must already be a Decimal here")
        if self.value < ZERO:
            raise CalcError(
                f"the value on {where} is negative ({self.value}). An "
                f"adjustment's value is always positive and its `type` "
                f"decides the direction — a discount of 5 % is "
                f"type=discount, value=5."
            )
        if self.mode == "percent" and self.type == "discount" and self.value > HUNDRED:
            raise CalcError(
                f"a discount percentage must be between 0 and 100; {where} "
                f"has {self.value}"
            )
        object.__setattr__(self, "kinds", tuple(self.kinds))
        if self.base == "kinds" and not self.kinds:
            raise CalcError(
                f"{where} has base 'kinds' but names no kinds, so there is "
                f"nothing for it to apply to. List the kinds it applies to, "
                f"or use base 'subtotal'."
            )
        if self.base != "kinds" and self.kinds:
            raise CalcError(
                f"{where} lists kinds {list(self.kinds)} but its base is "
                f"{self.base!r}, so they would be ignored. Set base to "
                f"'kinds' if the filter is meant to apply."
            )

    @property
    def signed_value(self) -> Decimal:
        """`value`, negated for a discount. The only place the sign is made."""
        return -self.value if self.type == "discount" else self.value


# ─── Group and document ─────────────────────────────────────────────────────

Child = Union["Line", "Group"]


@dataclass(frozen=True)
class Group:
    """An ordered container of lines and sub-groups, with its own subtotal.

    Depth is NOT checked here — a `Group` does not know how deep it has been
    put — but in `CalcDocument`, which is the only thing that can know. See
    `_check_depth`.
    """

    id: str
    title: str = ""
    children: tuple[Child, ...] = ()
    adjustments: tuple[Adjustment, ...] = ()
    #: Shown under the title on a document. Carried through untouched.
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "adjustments", tuple(self.adjustments))
        for child in self.children:
            if not isinstance(child, (Line, Group)):
                raise CalcError(
                    f"group {self.id!r} contains a "
                    f"{type(child).__name__}; only lines and groups can be "
                    f"in a group"
                )


@dataclass(frozen=True)
class CalcDocument:
    """Everything being priced: the tree, and the document-level adjustments.

    Called a *document* because SCOPE does, not because anything here is
    issued: a budget (slice A2) is this shape too, and nothing in this
    package numbers, finalizes or renders anything.
    """

    children: tuple[Child, ...] = ()
    adjustments: tuple[Adjustment, ...] = ()
    id: str = "document"
    title: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "adjustments", tuple(self.adjustments))
        for child in self.children:
            if not isinstance(child, (Line, Group)):
                raise CalcError(
                    f"the document contains a {type(child).__name__}; only "
                    f"lines and groups can be in it"
                )
        _check_depth(self.children, depth=1)
        _check_unique_ids(self)


def _check_depth(children: Sequence[Child], *, depth: int) -> None:
    """Refuse a group nested deeper than SCOPE §5.4 allows.

    The message says what the limit is and what to do, because the person who
    hits it is dragging a group into another group in the editor and the only
    useful answer is "that is as deep as it goes, take the lines out".
    """
    for child in children:
        if isinstance(child, Group):
            if depth > MAX_GROUP_DEPTH:
                raise CalcError(
                    f"group {child.id!r} would be at nesting level {depth}, "
                    f"and a document nests groups {MAX_GROUP_DEPTH} deep at "
                    f"most (SCOPE §5.4). Put its lines directly in the group "
                    f"above it, or move the group up a level."
                )
            _check_depth(child.children, depth=depth + 1)


def _check_unique_ids(document: "CalcDocument") -> None:
    """Every line and group id appears once.

    Not cosmetic: the result indexes lines by id so a screen can look one up,
    and two lines called `l1` would silently become one entry in that index
    with the second one's numbers. The document total would still be right,
    which is what makes it hard to notice.
    """
    seen: dict[str, str] = {}

    def walk(children: Sequence[Child]) -> None:
        for child in children:
            kind = "line" if isinstance(child, Line) else "group"
            if child.id in seen:
                raise CalcError(
                    f"the id {child.id!r} is used by more than one row "
                    f"({seen[child.id]} and {kind}). Ids have to be unique "
                    f"within a document — the result is indexed by them."
                )
            seen[child.id] = kind
            if isinstance(child, Group):
                walk(child.children)

    walk(document.children)


# ─── Parsing untyped data ───────────────────────────────────────────────────
#
# One parser, used by the API router (`POST /calc/preview`) and by the golden
# fixture files. Two parsers would be two vocabularies and two sets of error
# messages, and the fixtures would stop being evidence about what the
# endpoint accepts.


def discount_from_data(data: Optional[Mapping[str, Any]], *, where: str) -> Optional[Discount]:
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise CalcError(f"the discount on {where} must be an object")
    unknown = set(data) - {"mode", "value"}
    if unknown:
        raise CalcError(
            f"the discount on {where} has unknown field(s) "
            f"{', '.join(sorted(unknown))}; a discount is {{mode, value}}"
        )
    return Discount(
        mode=data.get("mode", "percent"),
        value=_number(data.get("value", 0), what="discount value", where=where),
    )


_LINE_FIELDS = frozenset(
    {
        "id", "type", "label", "quantity", "period_value", "period_unit",
        "factor", "unit_price", "unit_cost", "discount", "tax_ref", "kind",
        "tags", "allow_negative",
    }
)


def line_from_data(data: Mapping[str, Any]) -> Line:
    """A `Line` from the untyped shape, with every value named on refusal."""
    if not isinstance(data, Mapping):
        raise CalcError(f"a line must be an object, not {type(data).__name__}")
    line_id = _required_id(data.get("id"), what="line")
    where = f"line {line_id!r}"
    unknown = set(data) - _LINE_FIELDS
    if unknown:
        raise CalcError(
            f"{where} has unknown field(s) {', '.join(sorted(unknown))}. "
            f"Known fields are {', '.join(sorted(_LINE_FIELDS))}."
        )
    tags = data.get("tags") or ()
    if isinstance(tags, str) or not isinstance(tags, Iterable):
        raise CalcError(f"tags on {where} must be a list of strings")
    return Line(
        id=line_id,
        label=_text(data.get("label"), what="label", where=where),
        unit_price=_number(data.get("unit_price", 0), what="unit_price", where=where),
        quantity=_number(data.get("quantity", 1), what="quantity", where=where),
        period_value=_number(
            data.get("period_value", 1), what="period_value", where=where
        ),
        period_unit=data.get("period_unit", "day"),
        factor=_number(data.get("factor", 1), what="factor", where=where),
        unit_cost=_number(data.get("unit_cost", 0), what="unit_cost", where=where),
        discount=discount_from_data(data.get("discount"), where=where),
        tax_ref=_text(data.get("tax_ref"), what="tax_ref", where=where),
        kind=_text(data.get("kind"), what="kind", where=where),
        tags=tuple(_text(tag, what="tag", where=where) for tag in tags),
        allow_negative=bool(data.get("allow_negative", False)),
    )


_ADJUSTMENT_FIELDS = frozenset({"label", "type", "mode", "value", "base", "kinds"})


def adjustment_from_data(data: Mapping[str, Any]) -> Adjustment:
    if not isinstance(data, Mapping):
        raise CalcError(f"an adjustment must be an object, not {type(data).__name__}")
    label = _text(data.get("label"), what="label", where="an adjustment")
    where = f"adjustment {label!r}"
    unknown = set(data) - _ADJUSTMENT_FIELDS
    if unknown:
        raise CalcError(
            f"{where} has unknown field(s) {', '.join(sorted(unknown))}. "
            f"Known fields are {', '.join(sorted(_ADJUSTMENT_FIELDS))}."
        )
    kinds = data.get("kinds") or ()
    if isinstance(kinds, str) or not isinstance(kinds, Iterable):
        raise CalcError(f"kinds on {where} must be a list of strings")
    return Adjustment(
        label=label,
        type=data.get("type", "discount"),
        mode=data.get("mode", "percent"),
        value=_number(data.get("value", 0), what="value", where=where),
        base=data.get("base", "subtotal"),
        kinds=tuple(_text(kind, what="kind", where=where) for kind in kinds),
    )


_GROUP_FIELDS = frozenset(
    {"id", "type", "title", "description", "children", "adjustments"}
)


def group_from_data(data: Mapping[str, Any]) -> Group:
    if not isinstance(data, Mapping):
        raise CalcError(f"a group must be an object, not {type(data).__name__}")
    group_id = _required_id(data.get("id"), what="group")
    where = f"group {group_id!r}"
    unknown = set(data) - _GROUP_FIELDS
    if unknown:
        raise CalcError(
            f"{where} has unknown field(s) {', '.join(sorted(unknown))}. "
            f"Known fields are {', '.join(sorted(_GROUP_FIELDS))}."
        )
    return Group(
        id=group_id,
        title=_text(data.get("title"), what="title", where=where),
        description=_text(data.get("description"), what="description", where=where),
        children=_children_from_data(data.get("children") or (), where=where),
        adjustments=tuple(
            adjustment_from_data(entry) for entry in (data.get("adjustments") or ())
        ),
    )


#: What a child may call itself. A `type` of `"group"` or `"line"` decides
#: what a child is parsed as; without one, having a `children` key does.
CHILD_TYPES: tuple[str, ...] = ("group", "line")


def _is_group(entry: Mapping[str, Any], *, where: str) -> bool:
    """Whether this child is a group. Explicit `type` wins over shape.

    The shape fallback — "it has `children`, so it is a group" — is what a
    hand-written fixture or a curl command will rely on. The explicit tag is
    what the web app sends, because an EMPTY group is a real thing a person
    can make in the editor and `{"id": "g3", "children": []}` and
    `{"id": "g3"}` would otherwise be a group and a line with no price. The
    tag removes that difference instead of documenting it.
    """
    declared = entry.get("type")
    if declared is not None:
        return check_member(
            declared, CHILD_TYPES, what="child type", where=where
        ) == "group"
    return "children" in entry


def _children_from_data(entries: Any, *, where: str) -> tuple[Child, ...]:
    """Lines and groups, in order."""
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Iterable):
        raise CalcError(f"the children of {where} must be a list")
    children: list[Child] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise CalcError(
                f"child {index} of {where} must be an object, not "
                f"{type(entry).__name__}"
            )
        if _is_group(entry, where=f"child {index} of {where}"):
            children.append(group_from_data(entry))
        else:
            children.append(line_from_data(entry))
    return tuple(children)


_DOCUMENT_FIELDS = frozenset({"id", "title", "children", "adjustments"})


def document_from_data(data: Mapping[str, Any]) -> CalcDocument:
    """The whole input, parsed. The only entry point the API layer needs."""
    if not isinstance(data, Mapping):
        raise CalcError(f"the document must be an object, not {type(data).__name__}")
    unknown = set(data) - _DOCUMENT_FIELDS
    if unknown:
        raise CalcError(
            f"the document has unknown field(s) {', '.join(sorted(unknown))}. "
            f"Known fields are {', '.join(sorted(_DOCUMENT_FIELDS))}."
        )
    return CalcDocument(
        id=_text(data.get("id"), what="id", where="the document") or "document",
        title=_text(data.get("title"), what="title", where="the document"),
        children=_children_from_data(data.get("children") or (), where="the document"),
        adjustments=tuple(
            adjustment_from_data(entry) for entry in (data.get("adjustments") or ())
        ),
    )
