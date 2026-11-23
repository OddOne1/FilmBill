"""The catalog's pure parts: item kinds, rental factors, the CSV importer.

SCOPE §4.1–4.3. What is here is everything about the catalog that can be
decided from plain data — the fixed kind enum, the system units, the
multi-day factor lookup, and the whole rule set of the item import including
every error sentence it produces.

What is NOT here is the tables (`models/catalog.py`) and the writing
(`services/catalog_service.py`). The split is the same one `billing/tax` and
`billing/parties` make, and it buys the same thing: the factor table in
SCOPE §4.3 is a test fixture rather than four inserts, and the import's
decisions can be asserted row by row without a database.
"""

from .csv_import import (
    ACTION_CREATE,
    ACTION_ERROR,
    ACTION_UPDATE,
    COLUMNS,
    IMPORT_ACTIONS,
    REQUIRED_COLUMNS,
    ImportContext,
    ImportFormatError,
    ImportPlan,
    ImportRow,
    ItemValues,
    detect_delimiter,
    parse_active,
    parse_decimal,
    plan_from_file,
    plan_import,
    read_rows,
)
from .factors import (
    FACTOR_RULES,
    NO_FACTOR,
    RULE_BELOW_LOWEST,
    RULE_EXACT,
    RULE_HIGHEST,
    RULE_NEXT_LOWER,
    RULE_NO_TABLE,
    FactorError,
    billable_days,
    explain_factor,
    factor_for,
    normalise_table,
)
from .kinds import (
    ITEM_KINDS,
    KIND_LABELS,
    REBILLED_COST_KIND,
    SYSTEM_UNIT_KEYS,
    SYSTEM_UNITS,
    default_unit_for,
    kind_label,
)

__all__ = [
    "ACTION_CREATE",
    "ACTION_ERROR",
    "ACTION_UPDATE",
    "COLUMNS",
    "IMPORT_ACTIONS",
    "ITEM_KINDS",
    "ImportContext",
    "ImportFormatError",
    "ImportPlan",
    "ImportRow",
    "ItemValues",
    "KIND_LABELS",
    "FACTOR_RULES",
    "NO_FACTOR",
    "RULE_BELOW_LOWEST",
    "RULE_EXACT",
    "RULE_HIGHEST",
    "RULE_NEXT_LOWER",
    "RULE_NO_TABLE",
    "FactorError",
    "REBILLED_COST_KIND",
    "REQUIRED_COLUMNS",
    "SYSTEM_UNITS",
    "SYSTEM_UNIT_KEYS",
    "billable_days",
    "default_unit_for",
    "detect_delimiter",
    "explain_factor",
    "factor_for",
    "kind_label",
    "normalise_table",
    "parse_active",
    "parse_decimal",
    "plan_from_file",
    "plan_import",
    "read_rows",
]
