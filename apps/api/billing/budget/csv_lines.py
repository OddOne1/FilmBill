"""Budget lines in and out of a spreadsheet.

**Out** (`export_csv`): one row per line, with the section it is in and the
price, cost and margin the ENGINE computed for it. UTF-8 with a BOM and
semicolons, because the spreadsheet this file is opened in is a German Excel
and without the BOM it reads `Straße` as `StraÃŸe`.

**In** (`plan_from_file` → `plan_lines`): the same plan→apply shape A1's
catalog import has, and the reasons are that module's reasons —

  * the preview and the apply compute the SAME plan from the same file, and
    the apply refuses if the digest it computes differs from the one the
    preview showed;
  * every row carries a sentence, because "12 rows, 3 errors" is not
    something anybody can act on;
  * **nothing is ever deleted.** A line the file does not mention is left
    exactly as it is. An import that removed what it did not see would make
    a partial export a way to wipe a budget.

**The match key is `(section code, description)`, not an id.** Export, change
two numbers in Excel, import into a DUPLICATE of the budget — which is
acceptance step 7, and the duplicate's line ids are all different, so an
id-based match would re-create every row instead of updating it. Two lines in
one section with the same description are therefore ambiguous and are
refused, naming both line numbers: the file cannot say which one it means,
and picking one would be a coin toss over somebody's money.

**An unknown section code is an error, never an invention.** The same rule
A1's import has for categories: a typo would otherwise become a permanent
section, and the budget would grow a "Equipmnet" nobody asked for.
"""

import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

try:  # pragma: no cover - exercised by whichever import path is in use
    from ..catalog.csv_import import (
        ImportFormatError,
        detect_delimiter,
        normalise_decimal_text,
    )
    from ...core.rounding import (
        DecimalParseError,
        quantize_amount,
        quantize_qty,
        to_decimal,
    )
except ImportError:  # pragma: no cover
    from billing.catalog.csv_import import (  # type: ignore[no-redef]
        ImportFormatError,
        detect_delimiter,
        normalise_decimal_text,
    )
    from core.rounding import (  # type: ignore[no-redef]
        DecimalParseError,
        quantize_amount,
        quantize_qty,
        to_decimal,
    )


#: The columns the EXPORT writes, in this order.
#:
#: The three computed ones (`price`, `cost`, `margin`) are there because the
#: file exists to be read by a person, and a budget line without its own
#: total is a row somebody has to multiply out by hand. They are **not**
#: imported — the engine computes them (rule 2) — and the import reports them
#: as ignored columns so that is visible rather than assumed.
EXPORT_COLUMNS: tuple[str, ...] = (
    "section_code",
    "section_name",
    "description",
    "kind",
    "quantity",
    "period_value",
    "period_unit",
    "factor",
    "unit_price",
    "unit_cost",
    "discount_mode",
    "discount_value",
    "price",
    "cost",
    "margin",
)

#: The columns the IMPORT reads.
COLUMNS: tuple[str, ...] = (
    "section_code",
    "description",
    "kind",
    "quantity",
    "period_value",
    "period_unit",
    "factor",
    "unit_price",
    "unit_cost",
    "discount_mode",
    "discount_value",
    "notes",
)

#: Without these two a row cannot be placed or found again.
REQUIRED_COLUMNS: tuple[str, ...] = ("section_code", "description")

#: German spellings for the same columns, same spirit as A1's alias table:
#: short, explicit, and aimed at the file a German production company
#: actually exports.
_HEADER_ALIASES: Mapping[str, str] = {
    "konto": "section_code",
    "kontonummer": "section_code",
    "abschnitt": "section_code",
    "bezeichnung": "description",
    "beschreibung": "description",
    "art": "kind",
    "menge": "quantity",
    "anzahl": "quantity",
    "tage": "period_value",
    "zeitraum": "period_value",
    "einheit": "period_unit",
    "faktor": "factor",
    "einzelpreis": "unit_price",
    "verkaufspreis": "unit_price",
    "einzelkosten": "unit_cost",
    "kosten": "unit_cost",
    "rabatt_art": "discount_mode",
    "rabatt": "discount_value",
    "notizen": "notes",
    # What the export writes for the computed columns, so a round trip
    # reports them as ignored under the names they were written with.
    "preis": "price",
    "marge": "margin",
}

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_ERROR = "error"
#: Deliberately absent: `delete`. See the module docstring.

_DISCOUNT_MODES = ("percent", "amount")


@dataclass(frozen=True)
class BudgetLineValues:
    """One row's values, parsed. `None` means "the file did not say"."""

    section_code: str
    description: str
    kind: Optional[str] = None
    quantity: Optional[Decimal] = None
    period_value: Optional[Decimal] = None
    period_unit: Optional[str] = None
    factor: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    unit_cost: Optional[Decimal] = None
    discount_mode: Optional[str] = None
    discount_value: Optional[Decimal] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class BudgetImportRow:
    """What one line of the file would do, and the sentence that says so."""

    line_number: int
    action: str
    message: str
    section_code: str = ""
    description: str = ""
    values: Optional[BudgetLineValues] = None
    #: The existing line this row updates, where there is one.
    line_id: Optional[str] = None
    section_id: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return self.action == ACTION_ERROR


@dataclass(frozen=True)
class BudgetImportPlan:
    """Every row, in file order, plus the counts and the digest."""

    rows: tuple[BudgetImportRow, ...]
    ignored_columns: tuple[str, ...] = ()
    delimiter: str = ";"

    @property
    def creates(self) -> tuple[BudgetImportRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_CREATE)

    @property
    def updates(self) -> tuple[BudgetImportRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_UPDATE)

    @property
    def errors(self) -> tuple[BudgetImportRow, ...]:
        return tuple(row for row in self.rows if row.is_error)

    @property
    def digest(self) -> str:
        """A fingerprint of what this plan would DO. Hex, 16 characters.

        Over the actionable rows only, and not over the messages: a reworded
        error sentence must not invalidate a preview somebody is reading, and
        an error row changes nothing either way. `default=str` renders
        `Decimal("850.00")` as `"850.00"`, so it is stable across processes
        in a way a float never would be.
        """
        payload = [
            {
                "line": row.line_number,
                "action": row.action,
                "line_id": row.line_id,
                "section_id": row.section_id,
                "values": asdict(row.values) if row.values else None,
            }
            for row in self.rows
            if not row.is_error
        ]
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class BudgetImportContext:
    """What already exists in this budget, as plain data.

    Keys are normalised the way the file's values are normalised: a section
    code case-insensitively and stripped (somebody typed it into a
    spreadsheet), a description likewise.
    """

    #: `lower(code) → section id`.
    sections_by_code: Mapping[str, str] = field(default_factory=dict)
    #: `(lower(code), lower(description)) → line id`.
    lines_by_key: Mapping[tuple, str] = field(default_factory=dict)
    #: The keys that appear MORE than once, so the plan can refuse them
    #: instead of updating whichever row a dict happened to keep.
    ambiguous_keys: frozenset = frozenset()
    #: The item kinds this installation knows. Empty means "do not check",
    #: which is what a caller with no catalog loaded wants.
    kinds: frozenset = frozenset()
    #: The engine's period units.
    period_units: frozenset = frozenset()


# ─── Reading the file ───────────────────────────────────────────────────────


def _normalise_header(name: str) -> str:
    cleaned = (name or "").strip().lstrip("﻿").lower()
    cleaned = cleaned.replace("-", "_").replace(" ", "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return _HEADER_ALIASES.get(cleaned, cleaned)


def _decode(payload) -> str:
    if isinstance(payload, str):
        return payload.lstrip("﻿")
    for codec in ("utf-8-sig", "cp1252"):
        try:
            return payload.decode(codec)
        except UnicodeDecodeError:
            continue
    raise ImportFormatError(
        "the file is not readable as UTF-8 or Windows-1252 text. Save it "
        "from the spreadsheet as CSV UTF-8 and try again."
    )


def read_line_rows(payload) -> tuple[list[dict], tuple[str, ...], str]:
    """`(rows, ignored columns, delimiter)`. Raises `ImportFormatError`."""
    text = _decode(payload)
    if not text.strip():
        raise ImportFormatError("the file is empty.")

    delimiter = detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        raw_header = next(reader)
    except StopIteration:  # pragma: no cover - guarded by the blank check
        raise ImportFormatError("the file has no header row.") from None

    header = [_normalise_header(name) for name in raw_header]
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        raise ImportFormatError(
            f"the header row is missing the column(s) {', '.join(missing)}. "
            f"This import reads {', '.join(COLUMNS)} — the first line of the "
            f"file has to name at least {', '.join(REQUIRED_COLUMNS)}, and it "
            f"was read as {', '.join(name for name in header if name)} with "
            f"{delimiter!r} as the separator."
        )

    ignored = tuple(sorted({name for name in header if name and name not in COLUMNS}))

    rows: list[dict] = []
    for offset, raw in enumerate(reader, start=2):
        if not any((value or "").strip() for value in raw):
            continue
        row: dict = {"_line": str(offset)}
        for index, name in enumerate(header):
            if not name:
                continue
            row[name] = raw[index].strip() if index < len(raw) else ""
        rows.append(row)

    if not rows:
        raise ImportFormatError("the file has a header row and no data rows below it.")
    return rows, ignored, delimiter


# ─── Values ─────────────────────────────────────────────────────────────────


def _number(raw: str, *, column: str, money: bool) -> Optional[Decimal]:
    """A German or an English decimal, at the right scale, or `None`.

    `money` picks the scale: amounts are `NUMERIC(18,2)` and quantities,
    periods, factors and percentages are `NUMERIC(18,4)` (rule 1). The
    separator rule itself is `normalise_decimal_text`, shared with A1's
    import so `1.234,56` means one thing in this system.
    """
    text = normalise_decimal_text(raw)
    if text is None:
        return None
    try:
        value = to_decimal(text, what=f"{column!r}")
    except DecimalParseError:
        raise ValueError(
            f"{column} is not a number: {raw!r}. Write it as 850, 850.00 or "
            f"850,00 — a currency symbol and thousands separators are fine."
        ) from None
    return quantize_amount(value) if money else quantize_qty(value)


def _values_from_row(
    row: Mapping[str, str], context: BudgetImportContext
) -> BudgetLineValues:
    """Parse one row, raising `ValueError` with a sentence per bad field."""
    section_code = (row.get("section_code") or "").strip()
    description = (row.get("description") or "").strip()
    if not section_code:
        raise ValueError(
            "section_code is empty, so there is nowhere to put this line. "
            "Write the account number of the section it belongs in."
        )
    if not description:
        raise ValueError(
            "description is empty. It is what this import matches a line on, "
            "so a row without one could only ever create a new line and two "
            "runs of the same file would make two of them."
        )

    kind = (row.get("kind") or "").strip().lower() or None
    if kind and context.kinds and kind not in context.kinds:
        raise ValueError(
            f"{kind!r} is not an item kind. The ones this system knows are "
            f"{', '.join(sorted(context.kinds))}."
        )

    period_unit = (row.get("period_unit") or "").strip().lower() or None
    if period_unit and context.period_units and period_unit not in context.period_units:
        raise ValueError(
            f"{period_unit!r} is not a period unit. The engine prices "
            f"{', '.join(sorted(context.period_units))}."
        )

    discount_mode = (row.get("discount_mode") or "").strip().lower() or None
    if discount_mode and discount_mode not in _DISCOUNT_MODES:
        raise ValueError(
            f"{discount_mode!r} is not a discount mode. Use percent or "
            f"amount, or leave both discount columns empty."
        )

    values = BudgetLineValues(
        section_code=section_code,
        description=description,
        kind=kind,
        quantity=_number(row.get("quantity", ""), column="quantity", money=False),
        period_value=_number(
            row.get("period_value", ""), column="period_value", money=False
        ),
        period_unit=period_unit,
        factor=_number(row.get("factor", ""), column="factor", money=False),
        unit_price=_number(row.get("unit_price", ""), column="unit_price", money=True),
        unit_cost=_number(row.get("unit_cost", ""), column="unit_cost", money=True),
        discount_mode=discount_mode,
        discount_value=_number(
            row.get("discount_value", ""), column="discount_value", money=False
        ),
        notes=(row.get("notes") or "").strip() or None,
    )

    if values.discount_value is not None and values.discount_mode is None:
        raise ValueError(
            "there is a discount value and no discount_mode, so nothing says "
            "whether it is a percentage or an amount."
        )
    if values.factor is not None and values.factor <= 0:
        raise ValueError(
            f"the factor is {values.factor}. A factor of zero would price "
            f"the line at nothing, and it reads as an empty field rather "
            f"than as a decision."
        )
    return values


# ─── The plan ───────────────────────────────────────────────────────────────


def plan_lines(
    rows: Sequence[Mapping[str, str]],
    context: BudgetImportContext,
    *,
    ignored_columns: Sequence[str] = (),
    delimiter: str = ";",
) -> BudgetImportPlan:
    """What these rows would do. Decides everything; writes nothing."""
    planned: list[BudgetImportRow] = []
    #: Keys this file has already used, so a file that names one line twice
    #: is refused on the second mention rather than applying both.
    seen: set[tuple] = set()

    for row in rows:
        line_number = int(row.get("_line", "0") or 0)
        section_code = (row.get("section_code") or "").strip()
        description = (row.get("description") or "").strip()

        try:
            values = _values_from_row(row, context)
        except ValueError as error:
            planned.append(
                BudgetImportRow(
                    line_number=line_number,
                    action=ACTION_ERROR,
                    message=str(error),
                    section_code=section_code,
                    description=description,
                )
            )
            continue

        code_key = values.section_code.lower()
        section_id = context.sections_by_code.get(code_key)
        if section_id is None:
            planned.append(
                BudgetImportRow(
                    line_number=line_number,
                    action=ACTION_ERROR,
                    message=(
                        f"this budget has no section with the code "
                        f"{values.section_code!r}. An import never creates "
                        f"one — a typo would become a permanent section. Add "
                        f"the section in the sheet first, or correct the code."
                    ),
                    section_code=section_code,
                    description=description,
                    values=values,
                )
            )
            continue

        key = (code_key, values.description.lower())
        if key in context.ambiguous_keys:
            planned.append(
                BudgetImportRow(
                    line_number=line_number,
                    action=ACTION_ERROR,
                    message=(
                        f"section {values.section_code} has more than one "
                        f"line called {values.description!r}, so this row "
                        f"cannot say which it means. Rename one of them in "
                        f"the sheet and export again."
                    ),
                    section_code=section_code,
                    description=description,
                    values=values,
                )
            )
            continue
        if key in seen:
            planned.append(
                BudgetImportRow(
                    line_number=line_number,
                    action=ACTION_ERROR,
                    message=(
                        f"this file already has a row for {values.description!r} "
                        f"in section {values.section_code}. Two rows for one "
                        f"line would apply one of them and discard the other."
                    ),
                    section_code=section_code,
                    description=description,
                    values=values,
                )
            )
            continue
        seen.add(key)

        line_id = context.lines_by_key.get(key)
        if line_id is None:
            planned.append(
                BudgetImportRow(
                    line_number=line_number,
                    action=ACTION_CREATE,
                    message=(
                        f"new line {values.description!r} in section "
                        f"{values.section_code}."
                    ),
                    section_code=section_code,
                    description=description,
                    values=values,
                    section_id=section_id,
                )
            )
        else:
            planned.append(
                BudgetImportRow(
                    line_number=line_number,
                    action=ACTION_UPDATE,
                    message=(
                        f"updates {values.description!r} in section "
                        f"{values.section_code}; columns the file leaves "
                        f"blank are kept."
                    ),
                    section_code=section_code,
                    description=description,
                    values=values,
                    line_id=line_id,
                    section_id=section_id,
                )
            )

    return BudgetImportPlan(
        rows=tuple(planned),
        ignored_columns=tuple(ignored_columns),
        delimiter=delimiter,
    )


def plan_from_file(payload, context: BudgetImportContext) -> BudgetImportPlan:
    """Read and plan in one call. The service's entry point."""
    rows, ignored, delimiter = read_line_rows(payload)
    return plan_lines(
        rows, context, ignored_columns=ignored, delimiter=delimiter
    )


# ─── The export ─────────────────────────────────────────────────────────────


def export_csv(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """`EXPORT_COLUMNS` as UTF-8-with-BOM, semicolon-separated.

    A BOM because the file is opened in a German Excel, which reads a
    BOM-less UTF-8 CSV as Windows-1252 and turns every umlaut into mojibake.
    Semicolons for the same audience — Excel in a German locale treats the
    comma as the decimal separator and a comma-separated file as one column.

    Every value is already a string: the amounts come from the engine as
    strings (rule 1) and are written through unchanged, so nothing here
    re-formats or re-rounds a number.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(["" if row.get(name) is None else row.get(name) for name in EXPORT_COLUMNS])
    return "﻿".encode("utf-8") + buffer.getvalue().encode("utf-8")
