"""Actuals in and out of a spreadsheet — the bridge from a bank export.

**What this file is for.** The costs are already somewhere: a bank or card
export, the bookkeeper's sheet, a column somebody keeps in Numbers. Typing
them twice is how a cost tracker stops being used in week two. So there is
one import, with the plan→digest→apply shape A1's catalog import and A2's
budget-line import both have, and the reasons are those modules' reasons —

  * the preview and the apply compute the SAME plan from the same file, and
    the apply refuses if the digest it computes differs from the one the
    preview showed;
  * every row carries a sentence, because "40 rows, 6 errors" is not
    something anybody can act on;
  * **nothing is ever updated and nothing is ever deleted.** This import
    only CREATES actuals. A row that matches an existing entry is flagged
    `duplicate` and skipped unless the user ticks it; an entry the file does
    not mention is left exactly as it is. An import that could edit or void
    existing costs would make a stale export a way to rewrite what a shoot
    cost, and the correction path is deliberately the slower one: a new
    entry plus a void, both audited.

**The columns**, in this order:

    date;description;payee;reference;amount;section_code;line_description;paid_on

`date` is `incurred_on` — when the cost arose. `paid_on` is optional and is
the whole of payment status in B1. `payee` is matched against the company's
parties by name and falls back to free text, because a bank export says
"KAMERA GMBH" and not a UUID, and refusing the row until somebody creates a
party is how costs end up back in the spreadsheet.

**Placing a row.** `section_code` names the section (by code, or by name when
the row has no code — a sub-section usually has none); `line_description`
names a line inside it and is optional. A row with no resolvable section is
an **error row**, never an invention: the same rule A1's import has for
categories, because a typo would otherwise become a permanent section and
the budget would grow a "Travle" nobody asked for. A row whose
`line_description` matches nothing in that section is **not** an error — it
becomes an unallocated cost on the section, which is exactly what it is.

**German decimal commas, and the ambiguous dot is refused.** `1.234,56` is
1234.56; `10.000` on its own is refused, because it is ten in an English file
and ten thousand in a German one and this file format (`;`, a UTF-8 BOM) is
the German one. Guessing is a factor of a thousand either way. The rule
itself is `normalise_decimal_text`, shared with A1 and A2 so that `1.234,56`
means one thing in this system.

**Out** (`export_actuals_csv`): the same columns, so the round trip Mathias
does — export, change a number in a spreadsheet, add a row, import — reads
back. Changing an amount in the file and importing it does NOT change the
entry: the row comes back as a duplicate on amount or as a second cost, and
the preview says which. That is deliberate and is stated in the preview's
own sentence, because the alternative is an import that silently restates
history.
"""

import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
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
        to_decimal,
    )

#: This import only ever creates. `ACTION_DUPLICATE` is a row that WOULD be
#: created and is held back because an entry like it is already there;
#: `ACTION_ERROR` is a row that cannot be placed at all.
ACTION_CREATE = "create"
ACTION_DUPLICATE = "duplicate"
ACTION_ERROR = "error"
ACTIONS: tuple[str, ...] = (ACTION_CREATE, ACTION_DUPLICATE, ACTION_ERROR)

#: The columns, in the order the export writes them and the import documents.
COLUMNS: tuple[str, ...] = (
    "date",
    "description",
    "payee",
    "reference",
    "amount",
    "section_code",
    "line_description",
    "paid_on",
)
EXPORT_COLUMNS: tuple[str, ...] = COLUMNS

#: Without a date, an amount and a section a row cannot become an entry.
REQUIRED_COLUMNS: tuple[str, ...] = ("date", "amount")
SECTION_COLUMNS: tuple[str, ...] = ("section_code", "section_name")

#: German spellings, the same spirit as A1's and A2's alias tables: short,
#: explicit, and aimed at the file a German production company exports from
#: its bank or its bookkeeping.
_HEADER_ALIASES: Mapping[str, str] = {
    "datum": "date",
    "belegdatum": "date",
    "leistungsdatum": "date",
    "beschreibung": "description",
    "bezeichnung": "description",
    "text": "description",
    "verwendungszweck": "description",
    "empfaenger": "payee",
    "empfänger": "payee",
    "lieferant": "payee",
    "zahlungsempfaenger": "payee",
    "zahlungsempfänger": "payee",
    "beleg": "reference",
    "belegnummer": "reference",
    "rechnungsnummer": "reference",
    "referenz": "reference",
    "betrag": "amount",
    "summe": "amount",
    "konto": "section_code",
    "kontonummer": "section_code",
    "abschnitt": "section_code",
    "abschnittsname": "section_name",
    "position": "line_description",
    "budgetzeile": "line_description",
    "bezahlt_am": "paid_on",
    "zahlungsdatum": "paid_on",
}

#: Digits grouped by dots in threes and nothing else. A2's refusal, verbatim
#: in intent and shared in implementation below.
_GROUPED_WITHOUT_DECIMALS = re.compile(r"^-?\d{1,3}(\.\d{3})+$")

#: The date spellings a German spreadsheet or bank export actually produces.
#: ISO first, because that is what the export writes and what a round trip
#: has to read back.
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%d/%m/%Y",
    "%Y/%m/%d",
)

__all__ = [
    "ACTIONS",
    "ACTION_CREATE",
    "ACTION_DUPLICATE",
    "ACTION_ERROR",
    "COLUMNS",
    "EXPORT_COLUMNS",
    "REQUIRED_COLUMNS",
    "ActualImportContext",
    "ActualImportPlan",
    "ActualImportRow",
    "ActualValues",
    "de_date",
    "duplicate_key",
    "export_actuals_csv",
    "plan_actuals",
    "plan_actuals_from_file",
    "read_actual_rows",
]


# ─── Values ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActualValues:
    """One row's values, parsed. Everything the entry needs and nothing else."""

    incurred_on: date
    amount: Decimal
    description: str = ""
    payee_text: str = ""
    reference: str = ""
    paid_on: Optional[date] = None
    #: The party the payee text matched, where it matched one.
    party_id: Optional[str] = None


@dataclass(frozen=True)
class ActualImportRow:
    """What one line of the file would do, and the sentence that says so."""

    line_number: int
    action: str
    message: str
    section_code: str = ""
    description: str = ""
    values: Optional[ActualValues] = None
    section_id: Optional[str] = None
    line_id: Optional[str] = None
    #: For a `duplicate` row: the id of the entry it looks like, so the
    #: preview can link to it rather than describe it.
    duplicate_of: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return self.action == ACTION_ERROR


@dataclass(frozen=True)
class ActualImportPlan:
    """Every row, in file order, plus the counts and the digest."""

    rows: tuple[ActualImportRow, ...]
    ignored_columns: tuple[str, ...] = ()
    delimiter: str = ";"

    @property
    def creates(self) -> tuple[ActualImportRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_CREATE)

    @property
    def duplicates(self) -> tuple[ActualImportRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_DUPLICATE)

    @property
    def errors(self) -> tuple[ActualImportRow, ...]:
        return tuple(row for row in self.rows if row.is_error)

    @property
    def digest(self) -> str:
        """A fingerprint of what this plan would DO. Hex, 16 characters.

        Over the actionable rows only, and **including the duplicates**: a
        duplicate row is a decision the user makes on the preview they are
        looking at, so it has to be part of what the apply is pinned to. A
        reworded message must not invalidate a preview somebody is reading,
        which is why the messages are not in it. `default=str` renders
        `Decimal("850.00")` as `"850.00"` and a `date` as `"2026-11-24"`, so
        it is stable across processes in a way a float never would be.
        """
        payload = [
            {
                "line": row.line_number,
                "action": row.action,
                "section_id": row.section_id,
                "line_id": row.line_id,
                "duplicate_of": row.duplicate_of,
                "values": asdict(row.values) if row.values else None,
            }
            for row in self.rows
            if not row.is_error
        ]
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ActualImportContext:
    """What already exists in this budget, as plain data.

    Keys are normalised the way the file's values are: stripped and
    lowercased, because somebody typed them into a spreadsheet.
    """

    #: `lower(code) → section id`.
    sections_by_code: Mapping[str, str] = field(default_factory=dict)
    #: `lower(name) → section id`, for sections addressed by name.
    sections_by_name: Mapping[str, str] = field(default_factory=dict)
    ambiguous_section_names: frozenset = frozenset()
    #: `(section id, lower(description)) → line id`.
    lines_by_key: Mapping[tuple, str] = field(default_factory=dict)
    #: Keys that occur more than once, so the plan refuses them instead of
    #: picking whichever row a dict happened to keep.
    ambiguous_line_keys: frozenset = frozenset()
    #: `lower(name) → party id`, for matching a payee from a bank export.
    parties_by_name: Mapping[str, str] = field(default_factory=dict)
    ambiguous_party_names: frozenset = frozenset()
    #: `duplicate key → entry id` for every non-voided actual already in this
    #: budget. The key is `duplicate_key` below.
    existing_by_key: Mapping[tuple, str] = field(default_factory=dict)


def duplicate_key(
    payee: str, reference: str, amount: Decimal
) -> tuple[str, str, str]:
    """The key a duplicate warning is about: `(payee, reference, amount)`.

    **Not the date**, and not the section. A cost that arrives twice is
    usually the same invoice entered twice — the same supplier, the same
    invoice number, the same money — and the two entries differ in the date
    somebody happened to type. Including the date would make the check miss
    exactly the case it is for.

    The payee is whichever of the party name and the free text is present:
    `(“Kamera GmbH”, “RE-2026-114”, 1000.00)` is the same cost whether the
    first entry pointed at a party and the second typed the name.

    Spelled here, once, because the service computes it over the stored rows
    and the planner computes it over the file, and two spellings of a key
    that drives a warning is a warning that fires on half the cases.
    """
    return (
        (payee or "").strip().lower(),
        (reference or "").strip().lower(),
        str(quantize_amount(amount)),
    )


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


def read_actual_rows(payload) -> tuple[list[dict], tuple[str, ...], str]:
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
    if not any(name in header for name in SECTION_COLUMNS):
        missing.append("section_code (or section_name)")
    if missing:
        raise ImportFormatError(
            f"the header row is missing the column(s) {', '.join(missing)}. "
            f"This import reads {', '.join(COLUMNS)} — the first line of the "
            f"file has to name at least {', '.join(REQUIRED_COLUMNS)} and a "
            f"section column, and it was read as "
            f"{', '.join(name for name in header if name)} with "
            f"{delimiter!r} as the separator."
        )

    known = set(COLUMNS) | set(SECTION_COLUMNS)
    ignored = tuple(sorted({name for name in header if name and name not in known}))

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


def _amount(raw: str, *, column: str) -> Decimal:
    """A German or an English decimal as money, or a `ValueError` sentence.

    The ambiguous-dot refusal is A2's, for A2's reason: a spreadsheet turned
    `1.0000` into `10.000` once and the import read ten.
    """
    stripped = (raw or "").strip()
    if not stripped:
        raise ValueError(f"{column} is empty. Every cost needs an amount.")
    if _GROUPED_WITHOUT_DECIMALS.match(stripped.replace("\xa0", "").replace(" ", "")):
        raise ValueError(
            f"{column} is {stripped!r}, which is ambiguous: ten in an English "
            f"file, ten thousand in a German one. Write it without the dot "
            f"(10 or 10000), or with a decimal comma (10,000 is ten)."
        )
    text = normalise_decimal_text(raw)
    if text is None:
        raise ValueError(f"{column} is empty. Every cost needs an amount.")
    try:
        value = to_decimal(text, what=f"{column!r}")
    except DecimalParseError:
        raise ValueError(
            f"{column} is not a number: {raw!r}. Write it as 850, 850.00 or "
            f"850,00 — a currency symbol and thousands separators are fine."
        ) from None
    value = quantize_amount(value)
    if value <= Decimal("0"):
        raise ValueError(
            f"{column} is {value}, and an actual is always positive. A "
            f"correction is a new entry plus a void of the wrong one, not a "
            f"negative row — a negative amount would make every total depend "
            f"on which rows you happened to include."
        )
    return value


def _date(raw: str, *, column: str, required: bool) -> Optional[date]:
    text = (raw or "").strip()
    if not text:
        if required:
            raise ValueError(
                f"{column} is empty. Write it as 2026-11-24 or 24.11.2026."
            )
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"{column} is not a date I can read: {raw!r}. Write it as "
        f"2026-11-24 or 24.11.2026."
    )


# ─── Planning ───────────────────────────────────────────────────────────────


def _resolve_section(
    row: Mapping[str, str], context: ActualImportContext
) -> tuple[Optional[str], str]:
    """`(section id, message)`. The id is `None` when the row cannot be placed."""
    code = (row.get("section_code") or "").strip()
    name = (row.get("section_name") or "").strip()

    if code:
        found = context.sections_by_code.get(code.lower())
        if found:
            return found, ""
        return None, (
            f"no section in this budget has the code {code!r}. An unknown "
            f"code is never invented — a typo would become a permanent "
            f"section. Add the section to the budget first, or correct the "
            f"code in the file."
        )
    if name:
        if name.lower() in context.ambiguous_section_names:
            return None, (
                f"this budget has more than one section called {name!r}, so "
                f"the file cannot say which one it means. Give the row a "
                f"section_code instead."
            )
        found = context.sections_by_name.get(name.lower())
        if found:
            return found, ""
        return None, (
            f"no section in this budget is called {name!r}. Add it to the "
            f"budget first, or correct the name in the file."
        )
    return None, (
        "the row names no section. Every cost belongs somewhere in the "
        "account structure, even when it matches no budget line — fill in "
        "section_code."
    )


def _resolve_line(
    row: Mapping[str, str], section_id: str, context: ActualImportContext
) -> tuple[Optional[str], str]:
    """`(line id, note)`. No line is not an error — it is an unallocated cost."""
    description = (row.get("line_description") or "").strip()
    if not description:
        return None, "on the section, with no line"
    key = (section_id, description.lower())
    if key in context.ambiguous_line_keys:
        return None, (
            f"two lines in that section are called {description!r}, so the "
            f"cost goes on the section itself rather than on a coin toss"
        )
    found = context.lines_by_key.get(key)
    if found:
        return found, f"on the line “{description}”"
    return None, (
        f"no line in that section is called {description!r}, so this is an "
        f"unallocated cost on the section"
    )


def _resolve_party(
    payee: str, context: ActualImportContext
) -> tuple[Optional[str], str]:
    """`(party id, note)`. An unmatched payee stays free text, never an error.

    A bank export says "KAMERA GMBH". Matching it to a party is a
    convenience; failing to match it is not a problem, because `payee_text`
    exists exactly so that a one-off supplier does not have to be created
    before its receipt can be recorded.
    """
    name = (payee or "").strip()
    if not name:
        return None, ""
    if name.lower() in context.ambiguous_party_names:
        return None, (
            f", kept as free text because two parties are called {name!r}"
        )
    found = context.parties_by_name.get(name.lower())
    if found:
        return found, f", matched to the party “{name}”"
    return None, f", payee “{name}” as free text"


def plan_actuals(
    rows: Sequence[Mapping[str, str]],
    context: ActualImportContext,
    *,
    ignored_columns: Sequence[str] = (),
    delimiter: str = ";",
) -> ActualImportPlan:
    """What these rows would do. Pure, and the thing both halves compute.

    Duplicates are detected against the entries already in the budget AND
    against the earlier rows of this same file: a bank export that lists the
    same invoice twice is the commonest way a duplicate arrives, and a check
    that only looked at the database would wave both rows through.
    """
    planned: list[ActualImportRow] = []
    seen_in_file: dict[tuple, int] = {}

    for row in rows:
        line_number = int(row.get("_line") or 0)
        code = (row.get("section_code") or "").strip()

        section_id, refusal = _resolve_section(row, context)
        if section_id is None:
            planned.append(
                ActualImportRow(
                    line_number=line_number,
                    action=ACTION_ERROR,
                    message=refusal,
                    section_code=code,
                    description=(row.get("description") or "").strip(),
                )
            )
            continue

        try:
            incurred_on = _date(row.get("date", ""), column="date", required=True)
            paid_on = _date(row.get("paid_on", ""), column="paid_on", required=False)
            amount = _amount(row.get("amount", ""), column="amount")
        except ValueError as error:
            planned.append(
                ActualImportRow(
                    line_number=line_number,
                    action=ACTION_ERROR,
                    message=str(error),
                    section_code=code,
                    description=(row.get("description") or "").strip(),
                )
            )
            continue

        line_id, where = _resolve_line(row, section_id, context)
        payee = (row.get("payee") or "").strip()
        party_id, payee_note = _resolve_party(payee, context)
        description = (row.get("description") or "").strip()
        reference = (row.get("reference") or "").strip()

        values = ActualValues(
            incurred_on=incurred_on,
            amount=amount,
            description=description,
            payee_text="" if party_id else payee,
            reference=reference,
            paid_on=paid_on,
            party_id=party_id,
        )
        key = duplicate_key(payee, reference, amount)

        existing = context.existing_by_key.get(key)
        earlier = seen_in_file.get(key)
        if existing or earlier is not None:
            looks_like = (
                f"an entry already in this budget"
                if existing
                else f"row {earlier} of this file"
            )
            planned.append(
                ActualImportRow(
                    line_number=line_number,
                    action=ACTION_DUPLICATE,
                    message=(
                        f"{amount} {where}{payee_note} — this looks like "
                        f"{looks_like}: same payee, same reference, same "
                        f"amount. It is held back. Tick it to import it "
                        f"anyway; a supplier really can invoice the same "
                        f"amount twice."
                    ),
                    section_code=code,
                    description=description,
                    values=values,
                    section_id=section_id,
                    line_id=line_id,
                    duplicate_of=existing,
                )
            )
            continue

        seen_in_file[key] = line_number
        planned.append(
            ActualImportRow(
                line_number=line_number,
                action=ACTION_CREATE,
                message=(
                    f"a new actual of {amount} on "
                    f"{incurred_on.isoformat()} {where}{payee_note}."
                ),
                section_code=code,
                description=description,
                values=values,
                section_id=section_id,
                line_id=line_id,
            )
        )

    return ActualImportPlan(
        rows=tuple(planned),
        ignored_columns=tuple(ignored_columns),
        delimiter=delimiter,
    )


def plan_actuals_from_file(payload, context: ActualImportContext) -> ActualImportPlan:
    """Read the file and plan it. `ImportFormatError` for an unusable file."""
    rows, ignored, delimiter = read_actual_rows(payload)
    return plan_actuals(
        rows, context, ignored_columns=ignored, delimiter=delimiter
    )


# ─── Out ────────────────────────────────────────────────────────────────────


def de_amount(value: Optional[Decimal]) -> str:
    """`Decimal("1200.00")` → `"1200,00"`. A decimal comma, no grouping.

    A2's `de_amount`, and the same reason: this file is opened in a German
    Excel, and a dot in it is read as a thousands separator.
    """
    if value is None:
        return ""
    return f"{quantize_amount(value):f}".replace(".", ",")


def de_date(value: Optional[date]) -> str:
    """ISO, always. `""` for none.

    ISO rather than `24.11.2026` although the numbers are German, because a
    spreadsheet that recognises a date cell reformats it on save and ISO is
    the one spelling every locale reads back correctly. The import accepts
    both.
    """
    return "" if value is None else value.isoformat()


def export_actuals_csv(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """UTF-8 with a BOM, semicolons, `EXPORT_COLUMNS` in order.

    The BOM and the semicolon for A2's reason: Excel on a German-language
    Windows opens that correctly by double-clicking, and a comma-separated
    UTF-8 file without a BOM arrives as one column of mojibake.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(EXPORT_COLUMNS),
        delimiter=";",
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, "") for name in EXPORT_COLUMNS})
    return ("﻿" + buffer.getvalue()).encode("utf-8")
