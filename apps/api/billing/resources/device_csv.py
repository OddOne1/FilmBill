"""The gear list as a CSV file: reading it, planning the import, writing it.

Columns `name;category;quantity;price;serials;notes`, serials separated by
`|`. Read with the catalog's `read_rows`, so a BOM, a Windows encoding and a
`;`/`,` separator mean what they mean everywhere else.

**An import only creates.** It never edits, archives or retires an existing
device or unit, and it never adds serial numbers to a device that is already
on file. A line naming a device that already exists is `duplicate`:

  * an ACTIVE device of that name → held back and cannot be ticked, because
    one company has one active device of a name; the line has to be renamed
    in the file to become a separate device;
  * an ARCHIVED device of that name → held back unless ticked, and ticking
    it creates a new, active device beside the archived one.

A second line with a name an earlier line already used is an error for the
same reason.

**Price:** German decimal comma or English dot, and A2's refusal of the
ambiguous dot (`1.500` is one and a half in one file and fifteen hundred in
the other, so it is refused with a sentence rather than guessed).
"""

import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Mapping, Optional, Sequence

from ..catalog.csv_import import ImportFormatError, normalise_decimal_text, read_rows

try:  # pragma: no cover - exercised by whichever import path is in use
    from ...core.rounding import DecimalParseError, quantize_amount, to_decimal
except ImportError:  # pragma: no cover
    from core.rounding import DecimalParseError, quantize_amount, to_decimal  # type: ignore[no-redef]

__all__ = [
    "DEVICE_COLUMNS",
    "DeviceImportContext",
    "DeviceImportPlan",
    "DeviceRow",
    "DeviceValues",
    "ImportFormatError",
    "export_device_csv",
    "name_key",
    "parse_price",
    "plan_device_file",
    "plan_device_import",
    "serial_key",
    "split_serials",
]

DEVICE_COLUMNS: tuple[str, ...] = ("name", "category", "quantity", "price", "serials", "notes")
DEVICE_REQUIRED: tuple[str, ...] = ("name",)

DEVICE_ALIASES: Mapping[str, str] = {
    "bezeichnung": "name",
    "geraet": "name",
    "gerät": "name",
    "device": "name",
    "kategorie": "category",
    "menge": "quantity",
    "anzahl": "quantity",
    "stueck": "quantity",
    "stück": "quantity",
    "qty": "quantity",
    "quantity_owned": "quantity",
    "preis": "price",
    "einkaufspreis": "price",
    "purchase_price": "price",
    "seriennummern": "serials",
    "seriennummer": "serials",
    "serial_numbers": "serials",
    "serial": "serials",
    "notizen": "notes",
    "anmerkungen": "notes",
}

SERIAL_SEPARATOR = "|"
MAX_SERIAL_LENGTH = 128
MAX_QUANTITY = 100_000

ACTION_CREATE = "create"
ACTION_DUPLICATE = "duplicate"
ACTION_ERROR = "error"

_GROUPED_WITHOUT_DECIMALS = re.compile(r"^-?\d{1,3}(\.\d{3})+$")


# ─── Keys ───────────────────────────────────────────────────────────────────


def name_key(name: Optional[str]) -> str:
    """`" Alexa  35 "` → `"alexa 35"`: one active device of a name."""
    return " ".join((name or "").split()).casefold()


def serial_key(serial: Optional[str]) -> str:
    """`" ab-123 "` → `"ab-123"`, and `"AB-123"` → `"ab-123"`: one serial."""
    return (serial or "").strip().casefold()


def clean_serial(raw: Optional[str]) -> str:
    """The serial as it is stored and shown: stripped, otherwise as typed."""
    return (raw or "").strip()


def split_serials(text: Optional[str]) -> list[str]:
    """`"A1 | A2||a1"` → `["A1", "A2", "a1"]` — blanks dropped, nothing
    else; duplicates are the caller's to report, not silently merged."""
    return [part for part in (clean_serial(p) for p in (text or "").split(SERIAL_SEPARATOR)) if part]


# ─── Values ─────────────────────────────────────────────────────────────────


def parse_price(raw: Optional[str]) -> Optional[Decimal]:
    """A net purchase price, or `None` when blank. Raises `ValueError`."""
    stripped = (raw or "").strip()
    if not stripped:
        return None
    if _GROUPED_WITHOUT_DECIMALS.match(stripped.replace("\xa0", "").replace(" ", "")):
        raise ValueError(
            f"The price {stripped!r} is ambiguous: one and a half thousand in a "
            f"German file, one and a half in an English one. Write it without "
            f"the dot (1500) or with a decimal comma (1.500,00)."
        )
    text = normalise_decimal_text(stripped)
    if text is None:
        return None
    try:
        value = quantize_amount(to_decimal(text, what="'price'"))
    except DecimalParseError:
        raise ValueError(
            f"The price {raw!r} is not an amount. Write it as 65000, 65000.00 "
            f"or 65.000,00 — a currency symbol is fine."
        ) from None
    if value < 0:
        raise ValueError(f"The price is {value}. A purchase price cannot be negative.")
    return value


def parse_quantity(raw: Optional[str]) -> int:
    """Whole units, 0 when blank. Raises `ValueError`."""
    text = (raw or "").strip()
    if not text:
        return 0
    if not re.fullmatch(r"\d+", text):
        raise ValueError(
            f"The quantity {text!r} is not a whole number. Write how many you "
            f"own, such as 3."
        )
    value = int(text)
    if value > MAX_QUANTITY:
        raise ValueError(f"The quantity {value} is more than {MAX_QUANTITY}. Check the column.")
    return value


@dataclass(frozen=True)
class DeviceValues:
    name: str
    category: Optional[str] = None
    quantity: int = 0
    price: Optional[Decimal] = None
    serials: tuple[str, ...] = ()
    notes: Optional[str] = None


@dataclass(frozen=True)
class DeviceRow:
    line_number: int
    action: str
    name: Optional[str] = None
    values: Optional[DeviceValues] = None
    message: Optional[str] = None
    #: Serials of this line left out because the line repeats them.
    skipped_serials: tuple[str, ...] = ()
    duplicate_of_device_id: Optional[str] = None
    #: False for a duplicate of an ACTIVE device: it cannot be ticked.
    can_include: bool = False

    @property
    def is_error(self) -> bool:
        return self.action == ACTION_ERROR


@dataclass(frozen=True)
class DeviceImportContext:
    #: `name_key → (device id, name, is_active)`. When both an active and an
    #: archived device share a key, the active one.
    devices_by_key: Mapping[str, tuple[str, str, bool]] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceImportPlan:
    rows: tuple[DeviceRow, ...]
    ignored_columns: tuple[str, ...] = ()
    delimiter: str = ";"

    @property
    def creates(self) -> tuple[DeviceRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_CREATE)

    @property
    def duplicates(self) -> tuple[DeviceRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_DUPLICATE)

    @property
    def errors(self) -> tuple[DeviceRow, ...]:
        return tuple(row for row in self.rows if row.is_error)

    def to_create(self, include_lines=()) -> tuple[DeviceRow, ...]:
        ticked = set(include_lines)
        return tuple(
            row
            for row in self.rows
            if row.action == ACTION_CREATE
            or (
                row.action == ACTION_DUPLICATE
                and row.can_include
                and row.line_number in ticked
            )
        )

    @property
    def digest(self) -> str:
        payload = [
            {
                "line": row.line_number,
                "action": row.action,
                "values": asdict(row.values) if row.values else None,
                "duplicate_of": row.duplicate_of_device_id,
                "can_include": row.can_include,
            }
            for row in self.rows
            if not row.is_error
        ]
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ─── The plan ───────────────────────────────────────────────────────────────


def _values(row: Mapping[str, str]) -> tuple[DeviceValues, tuple[str, ...]]:
    name = " ".join((row.get("name") or "").split())
    if not name:
        raise ValueError("This line has no name. Every device needs one, such as Alexa 35.")
    if len(name) > 255:
        raise ValueError(f"The name is {len(name)} characters long; it can be at most 255.")
    category = " ".join((row.get("category") or "").split()) or None
    quantity = parse_quantity(row.get("quantity"))
    price = parse_price(row.get("price"))
    kept: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for serial in split_serials(row.get("serials")):
        if len(serial) > MAX_SERIAL_LENGTH:
            raise ValueError(
                f"The serial number {serial[:20]!r}… is longer than "
                f"{MAX_SERIAL_LENGTH} characters."
            )
        key = serial_key(serial)
        if key in seen:
            skipped.append(serial)
            continue
        seen.add(key)
        kept.append(serial)
    notes = (row.get("notes") or "").strip() or None
    return (
        DeviceValues(
            name=name,
            category=category,
            quantity=quantity,
            price=price,
            serials=tuple(kept),
            notes=notes,
        ),
        tuple(skipped),
    )


def plan_device_import(
    rows: Sequence[Mapping[str, str]],
    context: DeviceImportContext,
    *,
    ignored_columns: tuple[str, ...] = (),
    delimiter: str = ";",
) -> DeviceImportPlan:
    planned: list[DeviceRow] = []
    seen_names: dict[str, int] = {}
    for raw in rows:
        line = int(raw.get("_line") or 0)
        shown = (raw.get("name") or "").strip() or None
        try:
            values, skipped = _values(raw)
        except ValueError as error:
            planned.append(DeviceRow(line_number=line, action=ACTION_ERROR, name=shown, message=str(error)))
            continue
        key = name_key(values.name)
        note = (
            f" {len(skipped)} repeated serial number{'s' if len(skipped) != 1 else ''} "
            f"on this line will be skipped: {', '.join(skipped)}."
            if skipped
            else ""
        )
        if key in seen_names:
            planned.append(
                DeviceRow(
                    line_number=line,
                    action=ACTION_ERROR,
                    name=values.name,
                    message=(
                        f"Line {seen_names[key]} of this file already has a device "
                        f"called {values.name}. Put all its serial numbers on one line."
                    ),
                )
            )
            continue
        seen_names[key] = line
        existing = context.devices_by_key.get(key)
        if existing is not None:
            device_id, existing_name, active = existing
            planned.append(
                DeviceRow(
                    line_number=line,
                    action=ACTION_DUPLICATE,
                    name=values.name,
                    values=values,
                    skipped_serials=skipped,
                    duplicate_of_device_id=device_id,
                    can_include=not active,
                    message=(
                        f"You already have a device called {existing_name}. An import "
                        f"never changes it; to add this line as a separate device, "
                        f"give it another name in the file."
                        if active
                        else f"An archived device is called {existing_name}. Tick the "
                        f"line to add this one as a new device beside it."
                    )
                    + note,
                )
            )
            continue
        planned.append(
            DeviceRow(
                line_number=line,
                action=ACTION_CREATE,
                name=values.name,
                values=values,
                skipped_serials=skipped,
                message=note.strip() or None,
            )
        )
    return DeviceImportPlan(rows=tuple(planned), ignored_columns=ignored_columns, delimiter=delimiter)


def plan_device_file(payload: bytes | str, context: DeviceImportContext) -> DeviceImportPlan:
    rows, ignored, delimiter = read_rows(
        payload, columns=DEVICE_COLUMNS, required=DEVICE_REQUIRED, aliases=DEVICE_ALIASES
    )
    return plan_device_import(rows, context, ignored_columns=ignored, delimiter=delimiter)


# ─── The export ─────────────────────────────────────────────────────────────


def _de_amount(value: Optional[Decimal]) -> str:
    """`65000,00` — a decimal comma and no grouping, like every export here."""
    if value is None:
        return ""
    return format(value, "f").replace(".", ",")


def export_device_csv(devices: Sequence[DeviceValues]) -> bytes:
    """The import's own six columns, `;`, UTF-8 with a BOM."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(DEVICE_COLUMNS)
    for device in devices:
        writer.writerow(
            [
                device.name,
                device.category or "",
                str(device.quantity),
                _de_amount(device.price),
                SERIAL_SEPARATOR.join(device.serials),
                device.notes or "",
            ]
        )
    return "﻿".encode("utf-8") + buffer.getvalue().encode("utf-8")
