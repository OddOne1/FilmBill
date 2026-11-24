"""The calculation engine: SCOPE §5.3–5.6, principle P1, CLAUDE.md rule 2.

One engine, pure Python over plain value objects. `calculate` is the core
path and computes no tax; `apply_vat` is the separate call that turns net per
tax category into VAT and gross. Slice A2's budgets use the first and never
the second.

**Why it is a package of its own, with nothing from the ORM in it.** Rule 2
says all line, group, adjustment, tax and total arithmetic lives here and the
web app never computes money. That is only enforceable if "here" has no way
to reach a database: a function in this package that took a `Session` would
make every golden fixture need one, and the fixture table — the only form in
which a rounding rule is reviewable by somebody who is not a programmer —
would stop being possible. `tests/test_billing_import_boundary.py` imports
this package in a fresh interpreter and asks that interpreter what it loaded.

The whole public surface is below. A caller outside `billing/` should need
`document_from_data`, `calculate`, `apply_vat` and `display_row_for_group`,
and nothing else.
"""

from .engine import (
    AdjustmentResult,
    CalcResult,
    CalcWarning,
    GroupDisplayRow,
    GroupResult,
    LineResult,
    TaxRefTotal,
    VatResult,
    apply_vat,
    calculate,
    display_row_for_group,
    percent_share,
)
from .model import (
    child_type,
    CHILD_TYPES,
    Adjustment,
    CalcDocument,
    Discount,
    Group,
    Line,
    adjustment_from_data,
    document_from_data,
    group_from_data,
    line_from_data,
)
from .vocabulary import (
    ADJUSTMENT_BASES,
    ADJUSTMENT_MODES,
    ADJUSTMENT_TYPES,
    MAX_GROUP_DEPTH,
    NO_TAX_REF,
    PERIOD_UNITS,
    UNALLOCATED_TAX_REF,
    WARNING_CODES,
    WARNING_TEXT,
    WARN_ADJUSTMENT_NOT_ALLOCATABLE,
    WARN_COLLAPSED_PRICE_NOT_EXACT,
    WARN_KIND_FILTER_MATCHED_NOTHING,
    WARN_MIXED_DAYS_IN_GROUP,
    CalcError,
)

__all__ = [
    "ADJUSTMENT_BASES",
    "ADJUSTMENT_MODES",
    "ADJUSTMENT_TYPES",
    "Adjustment",
    "AdjustmentResult",
    "CHILD_TYPES",
    "CalcDocument",
    "CalcError",
    "CalcResult",
    "CalcWarning",
    "Discount",
    "Group",
    "GroupDisplayRow",
    "GroupResult",
    "Line",
    "LineResult",
    "MAX_GROUP_DEPTH",
    "NO_TAX_REF",
    "PERIOD_UNITS",
    "TaxRefTotal",
    "UNALLOCATED_TAX_REF",
    "VatResult",
    "WARNING_CODES",
    "WARNING_TEXT",
    "WARN_ADJUSTMENT_NOT_ALLOCATABLE",
    "WARN_COLLAPSED_PRICE_NOT_EXACT",
    "WARN_KIND_FILTER_MATCHED_NOTHING",
    "WARN_MIXED_DAYS_IN_GROUP",
    "adjustment_from_data",
    "apply_vat",
    "calculate",
    "child_type",
    "display_row_for_group",
    "document_from_data",
    "group_from_data",
    "line_from_data",
    "percent_share",
]
