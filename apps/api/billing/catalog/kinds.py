"""Item kinds and the system units — SCOPE §4.1 and §4.2, as data.

**`kind` is a fixed system enum and `category` is not.** SCOPE §4.1 is
explicit: "Users can add categories (e.g. 'Drones', 'Sound') and custom
units; they cannot add kinds without code — that keeps tax/reporting logic
sound." Every kind in the list below drives behaviour somewhere: `labor`
feeds the owed-per-user report (P7), `travel` carries the rebilled-cost flag
and its own tax policy (§12 S12), `per_diem` reports separately, `fee` is
what an adjustment usually is. A kind somebody could invent at runtime is a
kind no rule has a branch for, which fails silently rather than loudly.

Kept here, in the pure package, rather than in the model, because three
things need it and only one of them has a database: the ORM column, the CSV
importer (which must name the valid kinds in an error message before any row
is written), and the API's own enum for the generated TypeScript.

The migration repeats these literals rather than importing them. That is
migration 0008's and 0013's precedent and the reason CLAUDE.md rule 9 exists:
a migration is a record of what the schema looked like on a day, and
importing today's constants would make last year's migration change meaning
when the model does.
"""

from typing import Mapping, Optional

# ─── Kinds (SCOPE §4.1) ─────────────────────────────────────────────────────

KIND_LABOR = "labor"
KIND_EQUIPMENT_RENTAL = "equipment_rental"
KIND_SPACE_RENTAL = "space_rental"
KIND_TRAVEL = "travel"
KIND_PER_DIEM = "per_diem"
KIND_STORAGE = "storage"
KIND_PRODUCT = "product"
KIND_SOFTWARE_LICENSE = "software_license"
KIND_FEE = "fee"
KIND_OTHER = "other"

#: In SCOPE §4.1's own order, which is roughly "how often a production
#: company reaches for it" and is the order the pickers show.
ITEM_KINDS: tuple[str, ...] = (
    KIND_LABOR,
    KIND_EQUIPMENT_RENTAL,
    KIND_SPACE_RENTAL,
    KIND_TRAVEL,
    KIND_PER_DIEM,
    KIND_STORAGE,
    KIND_PRODUCT,
    KIND_SOFTWARE_LICENSE,
    KIND_FEE,
    KIND_OTHER,
)

#: German and English labels, and the default unit SCOPE §4.1's table gives
#: each kind. The default unit is what a new item of that kind is offered,
#: not a restriction — a `labor` item billed per hour is normal.
KIND_LABELS: Mapping[str, Mapping[str, str]] = {
    KIND_LABOR: {"de": "Personal", "en": "Crew & labour", "unit": "day"},
    KIND_EQUIPMENT_RENTAL: {"de": "Equipment-Miete", "en": "Equipment rental", "unit": "day"},
    KIND_SPACE_RENTAL: {"de": "Raummiete", "en": "Space rental", "unit": "day"},
    KIND_TRAVEL: {"de": "Reise & Transport", "en": "Travel", "unit": "flat"},
    KIND_PER_DIEM: {"de": "Diäten", "en": "Per diem", "unit": "day"},
    KIND_STORAGE: {"de": "Lagerung", "en": "Storage", "unit": "month"},
    KIND_PRODUCT: {"de": "Ware", "en": "Product", "unit": "piece"},
    KIND_SOFTWARE_LICENSE: {"de": "Softwarelizenz", "en": "Software licence", "unit": "month"},
    KIND_FEE: {"de": "Pauschale & Gebühr", "en": "Fee", "unit": "flat"},
    KIND_OTHER: {"de": "Sonstiges", "en": "Other", "unit": "flat"},
}

#: The kind whose `rebilled_cost` flag means anything (SCOPE §4.1: "Flag
#: `rebilled_cost` vs `service`"). Set on anything else it is noise, and the
#: API says so rather than storing it.
REBILLED_COST_KIND = KIND_TRAVEL


# ─── Units (SCOPE §4.1's "Default unit" column, §5.3's period units) ────────

#: The units every company starts with, and what each one maps onto in the
#: engine's `period_unit` vocabulary (`billing/calc/vocabulary.PERIOD_UNITS`).
#:
#: A company may add its own — SCOPE §4.1 says so — and a user-created unit
#: still has to map onto one of the engine's periods, because the engine has
#: a closed list and `period_unit` is what a document prints and an e-invoice
#: exports. "Drehtag" mapping to `day` is a label; a unit mapping to nothing
#: would be a line the engine cannot price.
SYSTEM_UNITS: tuple[Mapping[str, str], ...] = (
    {"key": "hour", "de": "Stunde", "en": "Hour", "period_unit": "hour"},
    {"key": "half_day", "de": "Halbtag", "en": "Half day", "period_unit": "half_day"},
    {"key": "day", "de": "Tag", "en": "Day", "period_unit": "day"},
    {"key": "night", "de": "Nacht", "en": "Night", "period_unit": "night"},
    {"key": "week", "de": "Woche", "en": "Week", "period_unit": "week"},
    {"key": "month", "de": "Monat", "en": "Month", "period_unit": "month"},
    {"key": "flat", "de": "Pauschal", "en": "Flat", "period_unit": "flat"},
    {"key": "piece", "de": "Stück", "en": "Piece", "period_unit": "piece"},
    {"key": "km", "de": "Kilometer", "en": "Kilometre", "period_unit": "km"},
)

SYSTEM_UNIT_KEYS: tuple[str, ...] = tuple(unit["key"] for unit in SYSTEM_UNITS)


def default_unit_for(kind: str) -> Optional[str]:
    """The unit key SCOPE §4.1 gives this kind, or None for an unknown one."""
    entry = KIND_LABELS.get(kind)
    return entry["unit"] if entry else None


def kind_label(kind: str, language: str = "en") -> str:
    """A kind's own name, or the key itself for one nobody has labelled."""
    entry = KIND_LABELS.get(kind)
    if entry is None:
        return kind
    return entry.get(language) or entry["en"]
