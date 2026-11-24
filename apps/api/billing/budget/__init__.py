"""The pure half of the Budget: its CSV in and out.

Nothing here touches a database, a request or a company — the same boundary
`billing/calc` and `billing/catalog` keep, and for the same reason: the plan
a file produces is the thing a preview table shows and the thing an apply
replays, and a plan that needed a session could not be a test fixture.

`services/budget_service.py` gathers what already exists into an
`ImportContext`, calls `plan_from_file`, and writes the rows the plan names.

`tracking.py` is the other half: given a line's estimate cost and the
commitments and actuals booked against it, what the budget will really
cost at the end. Pure for the same reason the engine is — the forecasting
rules have to be reviewable as a table of cents (SCOPE D83).
"""

from .tracking import (
    COMMITMENT_STATUSES,
    REMAINING_RULES,
    RULE_DEFAULT,
    RULE_ETC,
    STATUS_CANCELLED,
    STATUS_CLOSED,
    STATUS_OPEN,
    Actual,
    BudgetInput,
    Commitment,
    LineInput,
    LineTracking,
    RollupTracking,
    SectionInput,
    TrackingResult,
    closes_commitment,
    committed_open_of,
    is_finished,
    track_budget,
    track_line,
)
from .csv_lines import (
    ACTION_CREATE,
    ACTION_ERROR,
    ACTION_UPDATE,
    COLUMNS,
    EXPORT_COLUMNS,
    REQUIRED_COLUMNS,
    BudgetImportContext,
    BudgetImportPlan,
    BudgetImportRow,
    BudgetLineValues,
    de_amount,
    de_quantity,
    export_csv,
    plan_from_file,
    plan_lines,
    read_line_rows,
)

__all__ = [
    "ACTION_CREATE",
    "ACTION_ERROR",
    "ACTION_UPDATE",
    "COLUMNS",
    "EXPORT_COLUMNS",
    "REQUIRED_COLUMNS",
    "BudgetImportContext",
    "BudgetImportPlan",
    "BudgetImportRow",
    "BudgetLineValues",
    "de_amount",
    "de_quantity",
    "export_csv",
    "plan_from_file",
    "plan_lines",
    "read_line_rows",
    # tracking.py — the cost-planning half
    "COMMITMENT_STATUSES",
    "REMAINING_RULES",
    "RULE_DEFAULT",
    "RULE_ETC",
    "STATUS_CANCELLED",
    "STATUS_CLOSED",
    "STATUS_OPEN",
    "Actual",
    "BudgetInput",
    "Commitment",
    "LineInput",
    "LineTracking",
    "RollupTracking",
    "SectionInput",
    "TrackingResult",
    "closes_commitment",
    "committed_open_of",
    "is_finished",
    "track_budget",
    "track_line",
]
