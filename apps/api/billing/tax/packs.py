"""Loading a region pack: YAML in, validated plain data out.

CLAUDE.md rule 7 — "rules are data" — is only worth anything if the data is
checked. A pack is the one place in FilmBill where a non-programmer is
expected to edit the thing that decides what tax an invoice carries, and the
failure that matters is not a crash. It is a pack that loads, looks right, and
is wrong in a way nothing notices: a misspelled condition key, a class
pointing at a code that was renamed, an exempt code with a rate on it. Every
one of those produces a rule that is merely never true, and a rule that is
never true means the engine falls through to the next one. So this module
refuses all of them at load time, by name, with the file in the message.

**Still pure.** It reads files and parses YAML and that is all; no session, no
request, no ORM. The service layer turns a `RegionPack` into rows.

**`extends`.** A country pack declares the rates and the classes of its own
country and inherits the treatment codes and the eight shipped rules from
`eu_generic`. One copy of the rules, which is the only way eight countries
cannot end up with eight slightly different reverse-charge rules.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from .vocabulary import (
    ITEM_CLASS_SENTINEL,
    PackError,
    validate_conditions,
    validate_outcome,
    validate_rate_for_category,
)

#: Where the shipped packs live. `apps/api/region_packs/`.
PACK_DIRECTORY = Path(__file__).resolve().parents[2] / "region_packs"

#: The pack every company falls back to, and the one the country packs extend.
GENERIC_PACK = "eu_generic"

#: How deep `extends` may go before it is a mistake. Three is already more
#: than the design needs (generic → country) and a cycle would otherwise
#: recurse until the stack ran out, which is a traceback nobody can read.
MAX_EXTENDS_DEPTH = 5


@dataclass(frozen=True)
class PackCode:
    """One tax code as a pack declares it."""

    code: str
    name: str
    rate: Decimal
    category: str
    legal_notes: Mapping[str, str] = field(default_factory=dict)
    is_active: bool = True
    #: Which pack this code came from — `eu_generic` for an inherited one,
    #: the country pack for its own. Stored on the row so the seeder can
    #: answer "did we put this here" without guessing from the code's prefix.
    origin_pack: str = GENERIC_PACK


@dataclass(frozen=True)
class PackClass:
    """A tax class and the code it resolves to, by CODE and not by id.

    By code because a pack is written before any row exists, and because a
    pack that referred to ids would be a pack that could only be written by
    the machine that already has the rows.
    """

    key: str
    label: str
    code: str
    position: int = 0
    #: Shipped switched off, for a rate that exists in law and almost nobody
    #: bills — Austria's 4.9 % foodstuffs rate. A class and its code carry
    #: this together: one of the two off is a state nothing can explain.
    is_active: bool = True
    origin_pack: str = GENERIC_PACK


@dataclass(frozen=True)
class PackRule:
    position: int
    name: str
    conditions: Mapping[str, Any]
    outcome: Mapping[str, Any]
    is_active: bool = True
    origin_pack: str = GENERIC_PACK


@dataclass(frozen=True)
class RegionPack:
    """A pack after `extends` has been resolved and everything validated."""

    pack: str
    name: str
    countries: tuple[str, ...]
    codes: tuple[PackCode, ...]
    classes: tuple[PackClass, ...]
    rules: tuple[PackRule, ...]

    def code(self, code: str) -> Optional[PackCode]:
        for entry in self.codes:
            if entry.code == code:
                return entry
        return None

    def tax_class(self, key: str) -> Optional[PackClass]:
        for entry in self.classes:
            if entry.key == key:
                return entry
        return None


# ─── Reading ────────────────────────────────────────────────────────────────


def _read(name: str, directory: Path) -> Mapping[str, Any]:
    path = directory / f"{name}.yaml"
    if not path.is_file():
        available = ", ".join(sorted(available_packs(directory))) or "none"
        raise PackError(
            f"No region pack named {name!r} in {directory}. Available: "
            f"{available}."
        )
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as error:
        raise PackError(f"{path.name} is not valid YAML: {error}") from error
    if not isinstance(loaded, Mapping):
        raise PackError(
            f"{path.name} must be a mapping at the top level, got "
            f"{type(loaded).__name__}."
        )
    return loaded


def available_packs(directory: Optional[Path] = None) -> tuple[str, ...]:
    """Every pack that exists, by name, sorted."""
    directory = directory or PACK_DIRECTORY
    if not directory.is_dir():
        return ()
    return tuple(sorted(path.stem for path in directory.glob("*.yaml")))


def _rate(value: Any, *, where: str) -> Decimal:
    """A rate as an exact `Decimal`, and a refusal for a YAML float.

    `rate: 2.5` parses as a Python float, and a float is how a rate stops
    being exact — CLAUDE.md rule 1, applied one layer earlier than usual,
    because the pack is where the number is first written down. Quoting it
    (`rate: "2.5"`) costs an author two characters.
    """
    if isinstance(value, bool) or value is None:
        raise PackError(f"{where}: rate must be a number, got {value!r}.")
    if isinstance(value, float):
        raise PackError(
            f"{where}: rate {value!r} is a YAML float, and a float cannot "
            f'hold a rate exactly. Write it as a string: rate: "{value}".'
        )
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise PackError(f"{where}: rate {value!r} is not a number.") from error


def _codes_of(raw: Mapping[str, Any], pack: str, where: str) -> list[PackCode]:
    # `or []` would be wrong here: an empty mapping is falsy, so
    # `codes: {}` — a plausible typo — would be silently read as "none"
    # instead of refused. Absent is absent; anything else must be a list.
    entries = raw.get("codes")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise PackError(
            f"{where}: `codes` must be a list, got "
            f"{type(entries).__name__}."
        )

    codes: list[PackCode] = []
    for index, entry in enumerate(entries):
        label = f"{where}, codes[{index}]"
        if not isinstance(entry, Mapping):
            raise PackError(f"{label}: must be a mapping.")
        unknown = sorted(
            set(entry)
            - {"code", "name", "rate", "category", "legal_notes", "active"}
        )
        if unknown:
            raise PackError(
                f"{label}: unknown key(s) {', '.join(unknown)}. Allowed: "
                "code, name, rate, category, legal_notes, active."
            )
        code = entry.get("code")
        if not isinstance(code, str) or not code:
            raise PackError(f"{label}: `code` must be a non-empty string.")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise PackError(f"{label} ({code}): `name` must be a non-empty string.")
        category = entry.get("category")
        if not isinstance(category, str):
            raise PackError(f"{label} ({code}): `category` must be a string.")
        rate = _rate(entry.get("rate"), where=f"{label} ({code})")
        # The same check the database and the API perform, here so that a
        # broken pack fails the test suite rather than a company's first save.
        validate_rate_for_category(code, category, rate)

        notes = entry.get("legal_notes") or {}
        if not isinstance(notes, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in notes.items()
        ):
            raise PackError(
                f"{label} ({code}): `legal_notes` must be a mapping of "
                f"language code to text, got {notes!r}."
            )

        active = entry.get("active", True)
        if not isinstance(active, bool):
            raise PackError(
                f"{label} ({code}): `active` must be true or false, got "
                f"{active!r}."
            )

        codes.append(
            PackCode(
                code=code,
                name=name,
                rate=rate,
                category=category,
                legal_notes=dict(notes),
                is_active=active,
                origin_pack=pack,
            )
        )
    return codes


def _classes_of(raw: Mapping[str, Any], pack: str, where: str) -> list[PackClass]:
    # `or []` would be wrong here: an empty mapping is falsy, so
    # `classes: {}` — a plausible typo — would be silently read as "none"
    # instead of refused. Absent is absent; anything else must be a list.
    entries = raw.get("classes")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise PackError(
            f"{where}: `classes` must be a list, got "
            f"{type(entries).__name__}."
        )

    classes: list[PackClass] = []
    for index, entry in enumerate(entries):
        label = f"{where}, classes[{index}]"
        if not isinstance(entry, Mapping):
            raise PackError(f"{label}: must be a mapping.")
        unknown = sorted(
            set(entry) - {"key", "label", "code", "position", "active"}
        )
        if unknown:
            raise PackError(
                f"{label}: unknown key(s) {', '.join(unknown)}. Allowed: "
                "key, label, code, position, active."
            )
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            raise PackError(f"{label}: `key` must be a non-empty string.")
        if key == ITEM_CLASS_SENTINEL:
            raise PackError(
                f"{label}: {ITEM_CLASS_SENTINEL!r} is reserved — it is how a "
                "rule says 'the item's own class'. Name the class something "
                "else."
            )
        text = entry.get("label")
        if not isinstance(text, str) or not text:
            raise PackError(f"{label} ({key}): `label` must be a non-empty string.")
        code = entry.get("code")
        if not isinstance(code, str) or not code:
            raise PackError(f"{label} ({key}): `code` must be a non-empty string.")
        position = entry.get("position", 0)
        if not isinstance(position, int) or isinstance(position, bool):
            raise PackError(f"{label} ({key}): `position` must be an integer.")
        active = entry.get("active", True)
        if not isinstance(active, bool):
            raise PackError(
                f"{label} ({key}): `active` must be true or false, got "
                f"{active!r}."
            )

        classes.append(
            PackClass(
                key=key,
                label=text,
                code=code,
                position=position,
                is_active=active,
                origin_pack=pack,
            )
        )
    return classes


def _rules_of(raw: Mapping[str, Any], pack: str, where: str) -> list[PackRule]:
    # `or []` would be wrong here: an empty mapping is falsy, so
    # `rules: {}` — a plausible typo — would be silently read as "none"
    # instead of refused. Absent is absent; anything else must be a list.
    entries = raw.get("rules")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise PackError(
            f"{where}: `rules` must be a list, got "
            f"{type(entries).__name__}."
        )

    rules: list[PackRule] = []
    for index, entry in enumerate(entries):
        label = f"{where}, rules[{index}]"
        if not isinstance(entry, Mapping):
            raise PackError(f"{label}: must be a mapping.")
        unknown = sorted(
            set(entry) - {"position", "name", "conditions", "outcome", "active"}
        )
        if unknown:
            raise PackError(
                f"{label}: unknown key(s) {', '.join(unknown)}. Allowed: "
                "position, name, conditions, outcome, active."
            )
        position = entry.get("position")
        if not isinstance(position, int) or isinstance(position, bool):
            raise PackError(f"{label}: `position` must be an integer.")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise PackError(f"{label}: `name` must be a non-empty string.")

        named = f"{label} ({name!r})"
        validate_conditions(entry.get("conditions") or {}, where=named)
        validate_outcome(entry.get("outcome"), where=named)

        active = entry.get("active", True)
        if not isinstance(active, bool):
            raise PackError(f"{named}: `active` must be true or false.")

        rules.append(
            PackRule(
                position=position,
                name=name,
                conditions=dict(entry.get("conditions") or {}),
                outcome=dict(entry["outcome"]),
                is_active=active,
                origin_pack=pack,
            )
        )
    return rules


# ─── Merging and checking ───────────────────────────────────────────────────


def _dropped(raw: Mapping[str, Any], where: str) -> list[str]:
    """`drop_codes`: inherited codes this pack does not want.

    Austria and Germany have their own rates, so the generic pack's two "set
    your rate" placeholders would be two permanently-empty rows belonging to
    no section of the screen. Dropping is better than inheriting-and-ignoring
    for the same reason deleting is worse than deactivating elsewhere: a row
    nothing points at and nobody will ever set is noise in a table somebody
    has to review, and here there is no history to preserve — the code never
    reached a company at all.

    Validated against the parent in `_merge`, and against the surviving
    classes and rules in `_check`, so a pack cannot drop a code it still
    needs.
    """
    entries = raw.get("drop_codes")
    if entries is None:
        return []
    if not isinstance(entries, list) or not all(
        isinstance(entry, str) and entry for entry in entries
    ):
        raise PackError(
            f"{where}: `drop_codes` must be a list of tax code strings."
        )
    return list(entries)


def _merge(base: RegionPack, raw: Mapping[str, Any], pack: str, where: str) -> tuple:
    """Child declarations replace the parent's by key; the rest is inherited."""
    codes = {entry.code: entry for entry in base.codes}
    for entry in _codes_of(raw, pack, where):
        codes[entry.code] = entry

    for code in _dropped(raw, where):
        if code not in codes:
            raise PackError(
                f"{where}: `drop_codes` names {code!r}, which neither this "
                f"pack nor {base.pack!r} defines. Allowed: "
                f"{', '.join(sorted(codes))}."
            )
        del codes[code]

    classes = {entry.key: entry for entry in base.classes}
    for entry in _classes_of(raw, pack, where):
        classes[entry.key] = entry

    # A class the parent declared, pointing at a code this pack dropped, goes
    # with it. That is the whole mechanism: Austria declares its own
    # `standard` and `reduced`, so the generic ones are replaced above and
    # nothing is left pointing at EU-STD; a pack that dropped a code and
    # forgot to replace its class is caught by `_check` instead.
    for key, entry in list(classes.items()):
        if entry.code not in codes and entry.origin_pack != pack:
            del classes[key]

    own_rules = _rules_of(raw, pack, where)
    # A child that declares any rules replaces the inherited set wholesale
    # rather than interleaving by position: two lists merged by position is a
    # list whose order depends on which file somebody read last.
    rules = own_rules if own_rules else list(base.rules)

    return list(codes.values()), list(classes.values()), rules


def _check(pack: RegionPack) -> None:
    """Everything that can only be checked once the whole pack is assembled."""
    seen: set[str] = set()
    for entry in pack.codes:
        if entry.code in seen:
            raise PackError(
                f"pack {pack.pack!r}: tax code {entry.code!r} is declared "
                "twice."
            )
        seen.add(entry.code)

    class_keys: set[str] = set()
    for entry in pack.classes:
        if entry.key in class_keys:
            raise PackError(
                f"pack {pack.pack!r}: tax class {entry.key!r} is declared twice."
            )
        class_keys.add(entry.key)
        if entry.code not in seen:
            raise PackError(
                f"pack {pack.pack!r}: tax class {entry.key!r} points at code "
                f"{entry.code!r}, which this pack does not define. Codes: "
                f"{', '.join(sorted(seen))}."
            )

    if not pack.classes:
        raise PackError(
            f"pack {pack.pack!r}: no tax classes. An item has nothing to "
            "point at, so no domestic rate could ever be resolved."
        )
    if "standard" not in class_keys:
        raise PackError(
            f"pack {pack.pack!r}: no `standard` tax class. Every item that "
            "names no class falls back to it."
        )

    for rule in pack.rules:
        target = rule.outcome["code"]
        (kind, value), = target.items()
        if kind == "code" and value not in seen:
            raise PackError(
                f"pack {pack.pack!r}, rule {rule.name!r}: outcome points at "
                f"tax code {value!r}, which this pack does not define."
            )
        if kind == "class" and value != ITEM_CLASS_SENTINEL:
            if value not in class_keys:
                raise PackError(
                    f"pack {pack.pack!r}, rule {rule.name!r}: outcome points "
                    f"at tax class {value!r}, which this pack does not "
                    f"define. Classes: {', '.join(sorted(class_keys))}."
                )

    if not pack.rules:
        raise PackError(
            f"pack {pack.pack!r}: no rules. Nothing could be proposed at all."
        )


def load_pack(name: str, *, directory: Optional[Path] = None) -> RegionPack:
    """One pack, with `extends` resolved and everything validated.

    Not cached. A pack is loaded when a company is created or when its country
    is set — a handful of times in a process's life — and a cache here would
    be a cache somebody has to remember to clear after editing a YAML file in
    development.
    """
    directory = directory or PACK_DIRECTORY
    return _load(name, directory, depth=0, seen=())


def _load(
    name: str, directory: Path, *, depth: int, seen: tuple[str, ...]
) -> RegionPack:
    if name in seen:
        raise PackError(
            f"region packs extend each other in a cycle: "
            f"{' -> '.join(seen + (name,))}."
        )
    if depth > MAX_EXTENDS_DEPTH:
        raise PackError(
            f"region pack {name!r} is more than {MAX_EXTENDS_DEPTH} `extends` "
            "deep; that is a mistake rather than a design."
        )

    raw = _read(name, directory)
    where = f"{name}.yaml"

    declared = raw.get("pack")
    if declared != name:
        raise PackError(
            f"{where}: declares `pack: {declared!r}` but is named {name!r}. "
            "The two must agree, or a pack inherited by name is not the pack "
            "that was read."
        )

    title = raw.get("name") or name
    if not isinstance(title, str):
        raise PackError(f"{where}: `name` must be a string.")

    countries_raw = raw.get("countries") or []
    if not isinstance(countries_raw, list) or not all(
        isinstance(entry, str) for entry in countries_raw
    ):
        raise PackError(
            f"{where}: `countries` must be a list of ISO 3166-1 alpha-2 codes."
        )
    countries = tuple(entry.strip().upper() for entry in countries_raw)

    parent_name = raw.get("extends")
    if parent_name is None:
        codes = _codes_of(raw, name, where)
        classes = _classes_of(raw, name, where)
        rules = _rules_of(raw, name, where)
    else:
        if not isinstance(parent_name, str):
            raise PackError(f"{where}: `extends` must be a pack name or absent.")
        parent = _load(
            parent_name, directory, depth=depth + 1, seen=seen + (name,)
        )
        codes, classes, rules = _merge(parent, raw, name, where)

    pack = RegionPack(
        pack=name,
        name=title,
        countries=countries,
        codes=tuple(codes),
        classes=tuple(sorted(classes, key=lambda entry: (entry.position, entry.key))),
        rules=tuple(sorted(rules, key=lambda entry: entry.position)),
    )
    _check(pack)
    return pack


def pack_name_for_country(
    country: Optional[str], *, directory: Optional[Path] = None
) -> Optional[str]:
    """Which pack a company in `country` gets, or None for no country.

    None is not the generic pack. A company with no country is not seeded at
    all — seeding never overwrites, so a company handed the generic pack now
    would never receive the Austrian one when its country is finally filled
    in (see `services/tax_service.py`).

    The country → pack mapping comes from the packs themselves (`countries:`)
    rather than from a dict here, so adding a pack is adding a file.
    """
    if not country or not country.strip():
        return None
    wanted = country.strip().upper()

    for name in available_packs(directory):
        if name == GENERIC_PACK:
            continue
        try:
            pack = load_pack(name, directory=directory)
        except PackError:
            # A broken pack must not make every company unseedable. The pack
            # tests fail on it loudly; this path keeps one bad file from
            # taking out company creation.
            continue
        if wanted in pack.countries:
            return name
    return GENERIC_PACK


def pack_for_country(
    country: Optional[str], *, directory: Optional[Path] = None
) -> Optional[RegionPack]:
    """The loaded pack for `country`, or None when there is no country."""
    name = pack_name_for_country(country, directory=directory)
    if name is None:
        return None
    return load_pack(name, directory=directory)


# ─── For the engine ─────────────────────────────────────────────────────────


def engine_tables(pack: RegionPack):
    """A pack as the three lists `propose_tax` takes.

    Here rather than in the engine, because "a pack, ready to be proposed
    from" is a fact about packs; the engine knows nothing about YAML and
    should go on knowing nothing. The service layer has the equivalent for
    rows (`services/tax_service.py`), and the two must agree — which is what
    `test_the_propose_endpoint_matches_the_pure_function` checks end to end.
    """
    from .engine import TaxClassData, TaxCodeData, TaxRuleData

    codes = [
        TaxCodeData(
            code=entry.code,
            name=entry.name,
            rate=entry.rate,
            category=entry.category,
            legal_notes=dict(entry.legal_notes),
            # Nothing a pack ships is reviewed. A company's own row may be.
            notes_reviewed=False,
            is_active=entry.is_active,
        )
        for entry in pack.codes
    ]
    classes = [
        TaxClassData(key=entry.key, label=entry.label, code=entry.code)
        for entry in pack.classes
    ]
    rules = [
        TaxRuleData(
            position=entry.position,
            name=entry.name,
            conditions=dict(entry.conditions),
            outcome=dict(entry.outcome),
            is_active=entry.is_active,
        )
        for entry in pack.rules
    ]
    return codes, classes, rules
