"""The bridge from a spreadsheet: parsing an item CSV, and planning the import.

**Why the plan is a value and the apply step consumes it.** The A1 prompt's
requirement is that the import "applies exactly what the preview showed". The
only way to make that checkable rather than hopeful is for preview and apply
to be the SAME function: `plan_import` produces an `ImportPlan`, the preview
endpoint returns it, and the apply endpoint recomputes it from the same file
and writes what it says. Nothing in the apply path decides anything the
preview did not — there is no second parser and no second set of rules to
drift.

The plan also carries a `digest`. The apply request may send back the digest
it was shown, and a mismatch is refused with a sentence: between the preview
and the confirm, somebody in another tab can rename a category or an item can
be created with one of the file's SKUs, and the honest answer then is "this
is no longer what you looked at" rather than silently doing something else.

**Pure.** `plan_import` is handed an `ImportContext` — plain sets and
mappings of what already exists — rather than a session. So the whole rule
set, including every error sentence, is testable without a database, and the
service layer's only job is to gather the context and execute the plan.

**Never deletes, never touches an item not in the file.** There is no
`delete` action in `ImportAction` and the plan has no concept of "the items
that were not mentioned". A spreadsheet somebody filtered before exporting
is the normal case, and an import that treated absence as deletion would
empty a catalog on the first mistake.
"""

import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

try:  # pragma: no cover - exercised by whichever import path is in use
    from ...core.rounding import DecimalParseError, quantize_amount, to_decimal
except ImportError:  # pragma: no cover
    from core.rounding import (  # type: ignore[no-redef]
        DecimalParseError,
        quantize_amount,
        to_decimal,
    )

from .kinds import ITEM_KINDS

# ─── The file's shape ───────────────────────────────────────────────────────

#: The columns the A1 prompt specifies, in its order. The header row must
#: contain all of them; extra columns are reported and ignored rather than
#: refused, because a spreadsheet exported from somewhere else carries
#: baggage and refusing the whole file over a stray "Notizen" column would
#: make the feature unusable.
COLUMNS: tuple[str, ...] = (
    "sku",
    "name_de",
    "name_en",
    "kind",
    "category",
    "unit",
    "bill_rate_day",
    "cost_rate_day",
    "tax_class",
    "active",
)

#: Columns a row cannot do without. Everything else may be blank.
#:
#: `sku` is required because it is the only thing the import matches on: a
#: row with no SKU could only ever create, never update, and two runs of the
#: same file would then produce two items. `name_de` is required because an
#: item with no name is a row nobody can find again.
REQUIRED_COLUMNS: tuple[str, ...] = ("sku", "name_de", "kind")

#: Accepted for `active`, lower-cased and stripped. German spellings included
#: because the file comes out of a German-language spreadsheet.
TRUE_WORDS = frozenset({"1", "true", "yes", "y", "ja", "j", "wahr", "x", "aktiv"})
FALSE_WORDS = frozenset(
    {"0", "false", "no", "n", "nein", "falsch", "inaktiv", "archiviert"}
)

#: What `csv.Sniffer` is allowed to pick. Semicolon first: a German Excel
#: exports semicolons, and that is the common case here.
DELIMITERS = ";,\t"


class ImportFormatError(ValueError):
    """The file cannot be read as a CSV at all. Surfaces as a 422.

    Distinct from a per-ROW error, which is data and belongs in the plan. A
    file with no header, or one that is not text, has no rows to report on.
    """


# ─── Actions and rows ───────────────────────────────────────────────────────

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_ERROR = "error"
#: Deliberately absent: `delete`. See the module docstring.
IMPORT_ACTIONS: tuple[str, ...] = (ACTION_CREATE, ACTION_UPDATE, ACTION_ERROR)


@dataclass(frozen=True)
class ItemValues:
    """What a row says an item should be. Only the fields the CSV carries.

    `None` means "the column was blank", which on an UPDATE means **leave it
    alone** rather than "clear it". That is the safer of the two readings for
    a bridge from a spreadsheet: a column somebody did not fill in is far
    more often a column they did not care about than a field they wanted
    emptied, and clearing a tax class or a unit silently would change how the
    item is priced.
    """

    sku: str
    name_de: Optional[str] = None
    name_en: Optional[str] = None
    kind: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    bill_rate_day: Optional[Decimal] = None
    cost_rate_day: Optional[Decimal] = None
    tax_class: Optional[str] = None
    active: Optional[bool] = None


@dataclass(frozen=True)
class ImportRow:
    """One line of the file and what will happen to it.

    `line_number` is the line in the FILE, header counted, so it matches what
    the person sees in their spreadsheet. Off-by-one here is the difference
    between "fix row 7" and "fix some row".
    """

    line_number: int
    action: str
    #: A sentence, always — for an error it is what is wrong and what to do,
    #: and for a create or an update it is what will happen. A row with no
    #: message would be a row the preview table cannot explain (17c).
    message: str
    #: What the file said in the `sku` and `name_de` columns, whatever
    #: became of the rest of the row.
    #:
    #: Set on EVERY row, error rows included, and that is the point: `values`
    #: is None for an error, so a preview table built from it alone shows an
    #: error row with no SKU and no name — "line 5: that is not an item
    #: kind", about which of two hundred items nobody can tell. The line
    #: number is in a spreadsheet; the SKU is what a person recognises.
    sku: Optional[str] = None
    name: Optional[str] = None
    values: Optional[ItemValues] = None
    #: The id of the item this row will update, where there is one.
    item_id: Optional[str] = None
    #: Resolved ids for the names the row gave, so the apply step does not
    #: look anything up a second time and cannot resolve it differently.
    category_id: Optional[str] = None
    unit_id: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return self.action == ACTION_ERROR


@dataclass(frozen=True)
class ImportPlan:
    """Every row, in file order, plus the counts and the digest."""

    rows: tuple[ImportRow, ...]
    #: Columns in the file that are not in `COLUMNS`. Reported so somebody
    #: can see their "Notizen" column was not imported, rather than wonder.
    ignored_columns: tuple[str, ...] = ()
    #: The delimiter that was detected, for the same reason: a file parsed
    #: as one column because the delimiter was guessed wrong looks like a
    #: file full of errors, and this says which guess produced it.
    delimiter: str = ";"

    @property
    def creates(self) -> tuple[ImportRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_CREATE)

    @property
    def updates(self) -> tuple[ImportRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_UPDATE)

    @property
    def errors(self) -> tuple[ImportRow, ...]:
        return tuple(row for row in self.rows if row.is_error)

    @property
    def counts(self) -> dict[str, int]:
        return {
            ACTION_CREATE: len(self.creates),
            ACTION_UPDATE: len(self.updates),
            ACTION_ERROR: len(self.errors),
        }

    @property
    def digest(self) -> str:
        """A fingerprint of what this plan would DO. Hex, 16 characters.

        Over the actionable rows only — their line number, action, resolved
        ids and values — and not over the error rows or the messages: a
        reworded error sentence must not invalidate a preview somebody is
        looking at, and an error row changes nothing either way.

        `default=str` renders `Decimal("850.00")` as `"850.00"`, so the
        digest is stable across processes in a way `float` never would be.
        """
        payload = [
            {
                "line": row.line_number,
                "action": row.action,
                "item_id": row.item_id,
                "category_id": row.category_id,
                "unit_id": row.unit_id,
                "values": asdict(row.values) if row.values else None,
            }
            for row in self.rows
            if not row.is_error
        ]
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ImportContext:
    """What already exists, as plain data. Gathered by the service layer.

    Keys are normalised the way the lookups below normalise what the file
    says: SKUs case-sensitively (an SKU is an identifier), category and unit
    names case-INsensitively and stripped (somebody typed them into a
    spreadsheet).
    """

    #: `sku → item id`.
    items_by_sku: Mapping[str, str] = field(default_factory=dict)
    #: `lower(name) → category id`.
    categories_by_name: Mapping[str, str] = field(default_factory=dict)
    #: `lower(key or label) → unit id`.
    units_by_key: Mapping[str, str] = field(default_factory=dict)
    #: The tax class keys this company has, lower-cased.
    tax_class_keys: frozenset = frozenset()


# ─── Reading the file ───────────────────────────────────────────────────────


def _decode(payload: bytes | str) -> str:
    """Bytes to text, BOM removed, or a readable refusal.

    `utf-8-sig` first because Excel on Windows writes a BOM and a BOM left in
    place becomes part of the first header name — so `sku` arrives as
    `\\ufeffsku`, the header check fails, and the message is about a missing
    column that is visibly present. That is a twenty-minute confusion for
    one call to the right codec.

    `cp1252` as a fallback, which is what a German Excel writes when it is
    not writing UTF-8. Reported in the error if neither works, because "not
    valid UTF-8" alone does not tell anybody what to do.
    """
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


def _normalise_header(name: str) -> str:
    """`" Bill Rate Day "` → `bill_rate_day`.

    Spaces, case and hyphens are forgiven because a header row is typed by
    hand, and a file refused for a capital letter is a file somebody edits
    five times before giving up. The German column names a Vorlage might
    carry are mapped too.
    """
    cleaned = (name or "").strip().lstrip("﻿").lower()
    cleaned = cleaned.replace("-", "_").replace(" ", "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return _HEADER_ALIASES.get(cleaned, cleaned)


#: German (and a few obvious English) spellings for the same columns.
#:
#: Not an open invitation — the list is short and explicit — but the file
#: this feature exists to read is a German production company's price list,
#: and `artikelnummer` is what that column is called.
_HEADER_ALIASES: Mapping[str, str] = {
    "artikelnummer": "sku",
    "artikel_nr": "sku",
    "nummer": "sku",
    "name": "name_de",
    "bezeichnung": "name_de",
    "name_deutsch": "name_de",
    "name_englisch": "name_en",
    "art": "kind",
    "kategorie": "category",
    "einheit": "unit",
    "tagessatz": "bill_rate_day",
    "verkaufspreis_tag": "bill_rate_day",
    "kostensatz": "cost_rate_day",
    "einkaufspreis_tag": "cost_rate_day",
    "steuerklasse": "tax_class",
    "aktiv": "active",
}


def detect_delimiter(text: str) -> str:
    """`;`, `,` or a tab — whichever the header row actually uses.

    `csv.Sniffer` first, and a count on the header line when it cannot
    decide. The fallback matters more than it looks: a one-row file gives the
    sniffer almost nothing, and guessing `,` for a semicolon file turns every
    row into one column whose value contains the whole line — which the row
    checks then report as ten missing columns.
    """
    sample = text[:8192]
    header = sample.splitlines()[0] if sample.splitlines() else ""
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        counts = {candidate: header.count(candidate) for candidate in DELIMITERS}
        best = max(counts, key=lambda candidate: counts[candidate])
        return best if counts[best] else ";"


def read_rows(payload: bytes | str) -> tuple[list[dict[str, str]], tuple[str, ...], str]:
    """`(rows, ignored columns, delimiter)`. Raises `ImportFormatError`.

    Each row is a dict keyed by normalised column name, with the file's line
    number under `_line`. Blank lines are dropped — a spreadsheet export ends
    with one and it is not a row anybody wrote.
    """
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
            f"the header row is missing the column(s) "
            f"{', '.join(missing)}. The columns this import reads are "
            f"{', '.join(COLUMNS)} — the first line of the file has to name "
            f"them, and it was read as {', '.join(name for name in header if name)} "
            f"with {delimiter!r} as the separator."
        )

    ignored = tuple(
        sorted({name for name in header if name and name not in COLUMNS})
    )

    rows: list[dict[str, str]] = []
    for offset, raw in enumerate(reader, start=2):
        if not any((value or "").strip() for value in raw):
            continue
        row: dict[str, str] = {"_line": str(offset)}
        for index, name in enumerate(header):
            if not name:
                continue
            row[name] = raw[index].strip() if index < len(raw) else ""
        rows.append(row)

    if not rows:
        raise ImportFormatError(
            "the file has a header row and no data rows below it."
        )
    return rows, ignored, delimiter


# ─── Values ─────────────────────────────────────────────────────────────────


def normalise_decimal_text(raw: str) -> Optional[str]:
    """A German or an English decimal as plain digits, or `None` when blank.

    The separator rule only, with no quantization: "whichever of `.` and `,`
    comes LAST is the decimal separator, and the other one is a thousands
    separator". `parse_decimal` below quantizes the result to money;
    `billing/budget/csv_lines.py` needs the same rule at FOUR places for a
    quantity and a factor, and two copies of this rule would be two answers
    to what `1.234,56` means in a file somebody exported from Excel.

    The ambiguity is real and deliberate: `1.234` is 1.234, read as English.
    Forcing one locale would be wrong for the other half of the time, and
    every caller shows the parsed value in a preview, so it is visible
    before anything is written.
    """
    text = (raw or "").strip()
    if not text:
        return None
    text = text.replace("\xa0", "").replace(" ", "")
    for symbol in ("\u20ac", "EUR", "eur"):
        text = text.replace(symbol, "")
    text = text.strip()
    if not text:
        return None

    last_dot = text.rfind(".")
    last_comma = text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        if last_comma > last_dot:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif last_comma >= 0:
        text = text.replace(",", ".")
    return text


def parse_decimal(raw: str, *, column: str) -> Optional[Decimal]:
    """A German or an English decimal, as a `Decimal`, or `None` when blank.

    `1.234,56` and `1234.56` are both 1234.56; `1,234.56` is too. The rule
    is "whichever of `.` and `,` comes LAST is the decimal separator, and the
    other one is a thousands separator" — which is what distinguishes the two
    conventions without having to ask the user which locale their spreadsheet
    was in, and gets `1.234` (German thousands) and `1,234` (English
    thousands) wrong in exactly the same, symmetrical way.

    That ambiguity is real and is left in place deliberately: `1.234` is
    1.234 here, read as English. Forcing one locale would be wrong for the
    other half of the time, and the preview table shows the parsed amount so
    it is visible before anything is written.
    """
    text = normalise_decimal_text(raw)
    if text is None:
        return None

    try:
        return quantize_amount(to_decimal(text, what=f"{column!r}"))
    except DecimalParseError as error:
        raise ValueError(
            f"{column} is not an amount: {raw!r}. Write it as 850, 850.00 or "
            f"850,00 — a currency symbol and thousands separators are fine."
        ) from None


def parse_active(raw: str) -> Optional[bool]:
    """`ja` / `1` / `true` → True, `nein` / `0` / `false` → False, blank → None."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise ValueError(
        f"active is not a yes or a no: {raw!r}. Use 1/0, ja/nein, "
        f"true/false — or leave it blank to keep the item as it is."
    )


# ─── The plan ───────────────────────────────────────────────────────────────


def plan_import(
    rows: Sequence[Mapping[str, str]],
    context: ImportContext,
    *,
    ignored_columns: Sequence[str] = (),
    delimiter: str = ";",
) -> ImportPlan:
    """Decide what each row would do. Writes nothing and needs no session.

    Every row ends up as exactly one of create, update or error — there is no
    "skip" and no silent pass, because a row the preview did not account for
    is a row somebody will later believe was imported.

    An unresolvable name (a category or a unit or a tax class the company
    does not have) is an ERROR naming the value, not a silent null and not a
    new row created on the fly. Creating a category from a typo is how a
    catalog grows "Kamera", "Kameras" and "kamera" in one afternoon, and a
    silent null would price the item with no unit at all.
    """
    planned: list[ImportRow] = []
    seen_skus: dict[str, int] = {}

    for row in rows:
        line = int(row.get("_line", "0") or 0)
        sku = (row.get("sku") or "").strip()

        if not sku:
            planned.append(
                ImportRow(
                    line_number=line,
                    action=ACTION_ERROR,
                    name=(row.get("name_de") or "").strip() or None,
                    message=(
                        "this row has no SKU. The import matches rows to "
                        "items by SKU, so a row without one could only ever "
                        "create a new item — and running the file twice "
                        "would then create it twice. Give it an SKU, or add "
                        "the item by hand."
                    ),
                )
            )
            continue

        if sku in seen_skus:
            planned.append(
                ImportRow(
                    line_number=line,
                    action=ACTION_ERROR,
                    sku=sku,
                    name=(row.get("name_de") or "").strip() or None,
                    message=(
                        f"the SKU {sku!r} is already on line "
                        f"{seen_skus[sku]} of this file. Two rows for one "
                        f"item would both be applied and the second would "
                        f"win, so neither is."
                    ),
                )
            )
            continue
        seen_skus[sku] = line

        problem = None
        values: Optional[ItemValues] = None
        category_id: Optional[str] = None
        unit_id: Optional[str] = None

        name_de = (row.get("name_de") or "").strip()
        name_en = (row.get("name_en") or "").strip()
        kind = (row.get("kind") or "").strip().lower()
        category = (row.get("category") or "").strip()
        unit = (row.get("unit") or "").strip()
        tax_class = (row.get("tax_class") or "").strip()

        try:
            if not name_de:
                raise ValueError(
                    "this row has no name. An item with no name is a row "
                    "nobody will find again; fill in name_de."
                )
            if kind not in ITEM_KINDS:
                raise ValueError(
                    f"{row.get('kind')!r} is not an item kind. Kinds are "
                    f"fixed by the system because they drive behaviour "
                    f"(SCOPE §4.1) — use one of "
                    f"{', '.join(ITEM_KINDS)}. A label of your own belongs "
                    f"in the category column."
                )
            if category:
                category_id = context.categories_by_name.get(category.lower())
                if category_id is None:
                    raise ValueError(
                        f"there is no category called {category!r}. Create "
                        f"it under Catalog → Categories first — the import "
                        f"deliberately does not create categories, because "
                        f"a typo would become one."
                    )
            if unit:
                unit_id = context.units_by_key.get(unit.lower())
                if unit_id is None:
                    raise ValueError(
                        f"there is no unit called {unit!r}. Use one of the "
                        f"company's units, or add it under Catalog → Units "
                        f"first."
                    )
            if tax_class and tax_class.lower() not in context.tax_class_keys:
                raise ValueError(
                    f"there is no tax class {tax_class!r} in this company. "
                    f"Tax classes come from the region pack and from "
                    f"Settings → Company → Tax; leave the column blank to "
                    f"let the tax rules decide."
                )

            bill = parse_decimal(row.get("bill_rate_day", ""), column="bill_rate_day")
            cost = parse_decimal(row.get("cost_rate_day", ""), column="cost_rate_day")
            for label, amount in (("bill_rate_day", bill), ("cost_rate_day", cost)):
                if amount is not None and amount < 0:
                    raise ValueError(
                        f"{label} is {amount}. A negative rate is not a "
                        f"discount — put the discount on the line or the "
                        f"group instead."
                    )
            active = parse_active(row.get("active", ""))

            values = ItemValues(
                sku=sku,
                name_de=name_de,
                name_en=name_en or None,
                kind=kind,
                category=category or None,
                unit=unit or None,
                bill_rate_day=bill,
                cost_rate_day=cost,
                tax_class=tax_class or None,
                active=active,
            )
        except ValueError as error:
            problem = str(error)

        if problem is not None:
            planned.append(
                ImportRow(
                    line_number=line,
                    action=ACTION_ERROR,
                    sku=sku,
                    name=name_de or None,
                    message=problem,
                )
            )
            continue

        existing = context.items_by_sku.get(sku)
        if existing is None:
            planned.append(
                ImportRow(
                    line_number=line,
                    action=ACTION_CREATE,
                    sku=sku,
                    name=name_de,
                    message=f"a new item {name_de!r} will be created with SKU {sku}.",
                    values=values,
                    category_id=category_id,
                    unit_id=unit_id,
                )
            )
        else:
            planned.append(
                ImportRow(
                    line_number=line,
                    action=ACTION_UPDATE,
                    sku=sku,
                    name=name_de,
                    message=(
                        f"the existing item with SKU {sku} will be updated "
                        f"from this row. Blank columns are left as they are."
                    ),
                    values=values,
                    item_id=existing,
                    category_id=category_id,
                    unit_id=unit_id,
                )
            )

    return ImportPlan(
        rows=tuple(planned),
        ignored_columns=tuple(ignored_columns),
        delimiter=delimiter,
    )


def plan_from_file(
    payload: bytes | str, context: ImportContext
) -> ImportPlan:
    """`read_rows` then `plan_import`. The one entry point both endpoints use.

    Both of them, which is the point — see the module docstring. A preview
    that read the file differently from the apply would make "applies exactly
    what the preview showed" a sentence in a docstring rather than a
    property.
    """
    rows, ignored, delimiter = read_rows(payload)
    return plan_import(
        rows, context, ignored_columns=ignored, delimiter=delimiter
    )
