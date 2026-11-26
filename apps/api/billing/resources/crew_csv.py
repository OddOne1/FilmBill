"""The crew list as a CSV file: reading it, planning the import, writing it.

Columns `name;roles;email;phone;notes`, roles separated by `|`. UTF-8 with or
without a BOM, `;` or `,` — the reader is the catalog's (`read_rows`), so a
file Excel wrote in Vienna is read the way every other import here reads it.

**What an import may do: create. Nothing else.** It never updates, archives
or flags an existing party. A row that looks like somebody already on file —
the same email address, or the same name once case and spacing are ignored —
is planned as `duplicate` and is skipped unless the person ticks it in the
preview. A duplicate is held back rather than refused because the guess is
sometimes wrong (two people called Anna Huber), and rather than merged
because an import that edits records it matched by name is how a crew list
quietly overwrites a customer's phone number.

**The plan is a value; preview and apply are one function.** The apply step
recomputes the plan from the same file, compares the digest the preview
showed, and creates exactly the `create` rows plus the duplicate lines that
were ticked. The ticks are not part of the digest: ticking a box must not
invalidate the preview it was ticked in.
"""

import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from ..catalog.csv_import import ImportFormatError, read_rows

__all__ = [
    "ACTION_CREATE",
    "ACTION_DUPLICATE",
    "ACTION_ERROR",
    "CREW_COLUMNS",
    "CrewImportContext",
    "CrewImportPlan",
    "CrewRow",
    "CrewValues",
    "ImportFormatError",
    "export_crew_csv",
    "normalise_name",
    "normalise_roles",
    "plan_crew_file",
    "plan_crew_import",
    "split_roles",
]

CREW_COLUMNS: tuple[str, ...] = ("name", "roles", "email", "phone", "notes")
CREW_REQUIRED: tuple[str, ...] = ("name",)

#: German and obvious English spellings of the same five columns.
CREW_ALIASES: Mapping[str, str] = {
    "role": "roles",
    "rollen": "roles",
    "rolle": "roles",
    "funktion": "roles",
    "skills": "roles",
    "e_mail": "email",
    "mail": "email",
    "telefon": "phone",
    "tel": "phone",
    "mobile": "phone",
    "mobil": "phone",
    "handy": "phone",
    "notizen": "notes",
    "notiz": "notes",
    "anmerkungen": "notes",
    "anmerkung": "notes",
}

ROLE_SEPARATOR = "|"
#: `parties.tags` is `VARCHAR(64)[]`.
MAX_ROLE_LENGTH = 64

ACTION_CREATE = "create"
ACTION_DUPLICATE = "duplicate"
ACTION_ERROR = "error"

#: Deliberately loose: one `@`, something either side, a dot after it. The
#: point is to catch a phone number typed into the email column, not to
#: validate RFC 5322.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ─── Values ─────────────────────────────────────────────────────────────────


def normalise_name(name: Optional[str]) -> str:
    """`"  anna   HUBER "` → `"anna huber"`. The key a duplicate is found by."""
    return " ".join((name or "").split()).casefold()


def normalise_roles(roles: Iterable[Optional[str]]) -> tuple[str, ...]:
    """Stripped, inner spaces collapsed, empties dropped, case-insensitive
    duplicates dropped (the first spelling wins). Raises `ValueError` for a
    role longer than the column holds."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in roles:
        role = " ".join((raw or "").split())
        if not role:
            continue
        if len(role) > MAX_ROLE_LENGTH:
            raise ValueError(
                f"The role {role[:20]!r}… is {len(role)} characters long. A "
                f"role can be at most {MAX_ROLE_LENGTH}; shorten it."
            )
        key = role.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(role)
    return tuple(out)


def split_roles(text: Optional[str]) -> tuple[str, ...]:
    """`"DoP | Camera operator"` → `("DoP", "Camera operator")`."""
    return normalise_roles((text or "").split(ROLE_SEPARATOR))


def looks_like_email(value: str) -> bool:
    return bool(_EMAIL.match(value))


@dataclass(frozen=True)
class CrewValues:
    name: str
    roles: tuple[str, ...] = ()
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class CrewRow:
    line_number: int
    action: str
    #: What the file says, as read — shown in the preview even for an error.
    name: Optional[str] = None
    values: Optional[CrewValues] = None
    message: Optional[str] = None
    #: For a duplicate: the party already on file it matched, or the earlier
    #: line of this same file, and by what.
    duplicate_of_party_id: Optional[str] = None
    duplicate_of_name: Optional[str] = None
    duplicate_of_line: Optional[int] = None
    duplicate_reason: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return self.action == ACTION_ERROR


@dataclass(frozen=True)
class CrewImportContext:
    """Who is already on file, as plain data. Gathered by the service.

    Every party of the company counts, crew or not, active or archived: an
    import that creates a second record of an archived supplier is the
    mistake this check exists for.
    """

    #: `lower(email) → (party id, party name)`.
    parties_by_email: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    #: `normalise_name(name) → (party id, party name)`.
    parties_by_name: Mapping[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class CrewImportPlan:
    rows: tuple[CrewRow, ...]
    ignored_columns: tuple[str, ...] = ()
    delimiter: str = ";"

    @property
    def creates(self) -> tuple[CrewRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_CREATE)

    @property
    def duplicates(self) -> tuple[CrewRow, ...]:
        return tuple(row for row in self.rows if row.action == ACTION_DUPLICATE)

    @property
    def errors(self) -> tuple[CrewRow, ...]:
        return tuple(row for row in self.rows if row.is_error)

    def to_create(self, include_lines: Iterable[int] = ()) -> tuple[CrewRow, ...]:
        """The rows an apply writes: every `create`, plus the duplicates whose
        line was ticked. A ticked line that is not a duplicate changes
        nothing — an error row cannot be ticked into existence."""
        ticked = set(include_lines)
        return tuple(
            row
            for row in self.rows
            if row.action == ACTION_CREATE
            or (row.action == ACTION_DUPLICATE and row.line_number in ticked)
        )

    @property
    def digest(self) -> str:
        """What this plan would do, over the non-error rows. Hex, 16 chars."""
        payload = [
            {
                "line": row.line_number,
                "action": row.action,
                "values": asdict(row.values) if row.values else None,
                "duplicate_of_party_id": row.duplicate_of_party_id,
                "duplicate_of_line": row.duplicate_of_line,
            }
            for row in self.rows
            if not row.is_error
        ]
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ─── The plan ───────────────────────────────────────────────────────────────


def _values(row: Mapping[str, str]) -> CrewValues:
    name = " ".join((row.get("name") or "").split())
    if not name:
        raise ValueError("This line has no name. Every person needs one.")
    if len(name) > 255:
        raise ValueError(
            f"The name is {len(name)} characters long; it can be at most 255."
        )
    roles = split_roles(row.get("roles"))
    email = (row.get("email") or "").strip() or None
    if email is not None and not looks_like_email(email):
        raise ValueError(
            f"{email!r} is not an email address. Correct it, or leave the "
            f"email column empty."
        )
    if email is not None and len(email) > 255:
        raise ValueError("The email address is longer than 255 characters.")
    phone = (row.get("phone") or "").strip() or None
    if phone is not None and len(phone) > 255:
        raise ValueError("The phone number is longer than 255 characters.")
    notes = (row.get("notes") or "").strip() or None
    return CrewValues(name=name, roles=roles, email=email, phone=phone, notes=notes)


def plan_crew_import(
    rows: Sequence[Mapping[str, str]],
    context: CrewImportContext,
    *,
    ignored_columns: tuple[str, ...] = (),
    delimiter: str = ";",
) -> CrewImportPlan:
    """One `CrewRow` per data row, in file order. Writes nothing.

    A row is a duplicate of a party on file by email first (near-certain),
    then by name (a hint), and of an EARLIER line of the same file by the
    same two keys — a file that lists somebody twice would otherwise create
    them twice.
    """
    planned: list[CrewRow] = []
    seen_email: dict[str, tuple[int, str]] = {}
    seen_name: dict[str, tuple[int, str]] = {}

    for raw in rows:
        line = int(raw.get("_line") or 0)
        shown = (raw.get("name") or "").strip() or None
        try:
            values = _values(raw)
        except ValueError as error:
            planned.append(
                CrewRow(line_number=line, action=ACTION_ERROR, name=shown, message=str(error))
            )
            continue

        email_key = values.email.casefold() if values.email else None
        name_key = normalise_name(values.name)
        row: Optional[CrewRow] = None

        if email_key and email_key in context.parties_by_email:
            party_id, party_name = context.parties_by_email[email_key]
            row = CrewRow(
                line_number=line,
                action=ACTION_DUPLICATE,
                name=values.name,
                values=values,
                duplicate_of_party_id=party_id,
                duplicate_of_name=party_name,
                duplicate_reason="email",
                message=f"{party_name} already uses the address {values.email}.",
            )
        elif name_key in context.parties_by_name:
            party_id, party_name = context.parties_by_name[name_key]
            row = CrewRow(
                line_number=line,
                action=ACTION_DUPLICATE,
                name=values.name,
                values=values,
                duplicate_of_party_id=party_id,
                duplicate_of_name=party_name,
                duplicate_reason="name",
                message=f"There is already a party called {party_name}.",
            )
        elif email_key and email_key in seen_email:
            earlier, earlier_name = seen_email[email_key]
            row = CrewRow(
                line_number=line,
                action=ACTION_DUPLICATE,
                name=values.name,
                values=values,
                duplicate_of_line=earlier,
                duplicate_of_name=earlier_name,
                duplicate_reason="email",
                message=f"Line {earlier} of this file has the same address {values.email}.",
            )
        elif name_key in seen_name:
            earlier, earlier_name = seen_name[name_key]
            row = CrewRow(
                line_number=line,
                action=ACTION_DUPLICATE,
                name=values.name,
                values=values,
                duplicate_of_line=earlier,
                duplicate_of_name=earlier_name,
                duplicate_reason="name",
                message=f"Line {earlier} of this file has the same name.",
            )
        else:
            row = CrewRow(
                line_number=line, action=ACTION_CREATE, name=values.name, values=values
            )

        if email_key:
            seen_email.setdefault(email_key, (line, values.name))
        seen_name.setdefault(name_key, (line, values.name))
        planned.append(row)

    return CrewImportPlan(
        rows=tuple(planned), ignored_columns=ignored_columns, delimiter=delimiter
    )


def plan_crew_file(payload: bytes | str, context: CrewImportContext) -> CrewImportPlan:
    """Read the file and plan it. Raises `ImportFormatError` for a file that
    cannot be read at all; a bad ROW is an error row, never an exception."""
    rows, ignored, delimiter = read_rows(
        payload, columns=CREW_COLUMNS, required=CREW_REQUIRED, aliases=CREW_ALIASES
    )
    return plan_crew_import(rows, context, ignored_columns=ignored, delimiter=delimiter)


# ─── The export ─────────────────────────────────────────────────────────────


def export_crew_csv(people: Sequence[CrewValues]) -> bytes:
    """The import's own five columns, `;`, UTF-8 with a BOM — so an export
    can be edited in Excel and imported again (it would come back as
    duplicates, held back, which is the point)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(CREW_COLUMNS)
    for person in people:
        writer.writerow(
            [
                person.name,
                ROLE_SEPARATOR.join(person.roles),
                person.email or "",
                person.phone or "",
                person.notes or "",
            ]
        )
    return "﻿".encode("utf-8") + buffer.getvalue().encode("utf-8")
