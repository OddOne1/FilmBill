"""Giving a company its tax configuration, and keeping it once it has one.

The bridge between `billing/tax/` — which knows nothing about rows — and the
three tables. Two directions:

  * **seeding**: a region pack becomes rows, once, for a company that has
    none;
  * **reading** (P1a part 4): rows become the plain dataclasses the proposal
    engine takes, so `propose_tax` never sees a `Session`.

**Seeding never overwrites.** Not "usually does not" — never. Every rate,
every legal note and every rule FilmBill ships is a starting point somebody
else's advisor may contradict (SCOPE D19), and a company that corrected
`AT-20` to 21 % must not find it back at 20 % because a second seeding ran.
So the seeder adds what is missing, by key, and touches nothing that exists.
That is also what makes it safe to call from two places: company creation,
and the moment a country is first set on a company that had none.

**A company with no country gets nothing.** Not the generic pack — nothing.
Today `POST /setup/create-superadmin` creates the first company itself (D42)
and asks for no country, so both Mathias's dev database and the LAN
server-test instance are in that state. If they were seeded generically now,
the Austrian pack they are due would never arrive, because seeding never
overwrites. Waiting until the country is known costs nothing: no document
exists until P2.
"""

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..core.money import quantize_qty

from ..billing.tax import packs
from ..models.company import Company
from ..models.tax import TaxClass, TaxCode, TaxRule


class SeedResult:
    """What one seeding run did. Returned so a caller can log or audit it.

    A small object rather than a tuple: the three counts are easy to swap and
    `result.codes` cannot be read as `result.classes` by accident.
    """

    __slots__ = ("pack", "codes", "classes", "rules")

    def __init__(
        self,
        pack: Optional[str] = None,
        codes: int = 0,
        classes: int = 0,
        rules: int = 0,
    ) -> None:
        self.pack = pack
        self.codes = codes
        self.classes = classes
        self.rules = rules

    @property
    def seeded_anything(self) -> bool:
        return bool(self.codes or self.classes or self.rules)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<SeedResult pack={self.pack!r} codes={self.codes} "
            f"classes={self.classes} rules={self.rules}>"
        )


def has_tax_configuration(db: Session, *, company_id: uuid.UUID) -> bool:
    """Whether this company has any tax codes at all.

    Codes and not rules, because codes are what everything else points at: a
    company with rules and no codes could propose nothing, and a company with
    codes is a company somebody has already configured.
    """
    return (
        db.query(TaxCode.id).filter(TaxCode.company_id == company_id).first()
        is not None
    )


def seed_tax_configuration(
    db: Session,
    *,
    company: Company,
    pack_name: Optional[str] = None,
) -> SeedResult:
    """Give `company` the pack for its country. No commit.

    No commit, for the same reason `seed_number_series` does not: this runs
    inside company creation and inside a settings save, and a commit here
    would split either of those into two transactions — leaving a company
    with tax codes and no owner, or a country that was saved while the audit
    row for saving it was not.

    `pack_name` overrides the country lookup. Only tests and a future
    "re-seed from pack X" admin action pass it; the ordinary caller passes
    nothing and lets the country decide.
    """
    name = pack_name or packs.pack_name_for_country(company.address_country)
    if name is None:
        # No country yet. Deliberately nothing — see the module docstring.
        return SeedResult()

    pack = packs.load_pack(name)
    result = SeedResult(pack=pack.pack)

    existing_codes = {
        row.code: row
        for row in db.query(TaxCode).filter(TaxCode.company_id == company.id).all()
    }
    for entry in pack.codes:
        if entry.code in existing_codes:
            continue
        row = TaxCode(
            company_id=company.id,
            code=entry.code,
            name=entry.name,
            rate=entry.rate,
            category=entry.category,
            legal_notes=dict(entry.legal_notes),
            # Every shipped text, unreviewed. The screen says so until
            # somebody in the company confirms it.
            notes_reviewed=False,
            reporting_mapping={},
            is_active=entry.is_active,
            origin_pack=entry.origin_pack,
        )
        db.add(row)
        existing_codes[entry.code] = row
        result.codes += 1

    # Flush before the classes, so the codes have ids for the foreign key.
    if result.codes:
        db.flush()

    existing_classes = {
        row.key
        for row in db.query(TaxClass).filter(TaxClass.company_id == company.id).all()
    }
    for entry in pack.classes:
        if entry.key in existing_classes:
            continue
        code_row = existing_codes.get(entry.code)
        if code_row is None:
            # The pack loader already refuses a class pointing at a code the
            # pack does not define, so this is unreachable from a valid pack.
            # Skipped rather than raised: a company half-seeded by hand must
            # not make company creation fail.
            continue
        db.add(
            TaxClass(
                company_id=company.id,
                key=entry.key,
                label=entry.label,
                code_id=code_row.id,
                position=entry.position,
                origin_pack=entry.origin_pack,
            )
        )
        result.classes += 1

    # Rules have no natural key to compare on — two identical rules are a
    # legitimate state, and a user may well have edited the shipped list —
    # so they are seeded only when the company has none at all. Anything
    # cleverer would eventually re-add a rule somebody deleted on purpose.
    has_rules = (
        db.query(TaxRule.id).filter(TaxRule.company_id == company.id).first()
        is not None
    )
    if not has_rules:
        for entry in pack.rules:
            db.add(
                TaxRule(
                    company_id=company.id,
                    position=entry.position,
                    name=entry.name,
                    conditions=dict(entry.conditions),
                    outcome=dict(entry.outcome),
                    origin_pack=entry.origin_pack,
                    is_active=entry.is_active,
                )
            )
            result.rules += 1

    if result.seeded_anything:
        db.flush()
    return result


# ─── Reading: rows → the engine's plain data ────────────────────────────────


def engine_tables(db: Session, *, company_id: uuid.UUID):
    """This company's three tables, as the dataclasses `propose_tax` takes.

    The translation layer, and the only one. `propose_tax` never sees a
    `Session` — which is what lets a tax decision be a table test — so
    something has to turn rows into plain data, and it is better that it be
    one named function than a dict comprehension in a router.

    Classes are resolved to a code STRING here rather than passed as an id,
    because the engine answers in terms of the code a human reads. A class
    whose code is missing is dropped: the engine would refuse it anyway, and
    with a worse message than the one it gives for an unknown class.
    """
    from ..billing.tax import TaxClassData, TaxCodeData, TaxRuleData

    code_rows = db.query(TaxCode).filter(TaxCode.company_id == company_id).all()
    codes = [
        TaxCodeData(
            code=row.code,
            name=row.name,
            rate=row.rate,
            category=row.category,
            legal_notes=dict(row.legal_notes or {}),
            notes_reviewed=bool(row.notes_reviewed),
            is_active=bool(row.is_active),
        )
        for row in code_rows
    ]
    by_id = {row.id: row.code for row in code_rows}

    class_rows = (
        db.query(TaxClass)
        .filter(TaxClass.company_id == company_id)
        .order_by(TaxClass.position, TaxClass.key)
        .all()
    )
    classes = [
        TaxClassData(key=row.key, label=row.label, code=by_id[row.code_id])
        for row in class_rows
        if row.code_id in by_id
    ]

    rule_rows = (
        db.query(TaxRule)
        .filter(TaxRule.company_id == company_id)
        .order_by(TaxRule.position, TaxRule.name)
        .all()
    )
    rules = [
        TaxRuleData(
            position=row.position,
            name=row.name,
            conditions=dict(row.conditions or {}),
            outcome=dict(row.outcome or {}),
            is_active=bool(row.is_active),
        )
        for row in rule_rows
    ]
    return codes, classes, rules


def propose(
    db: Session,
    *,
    company: Company,
    buyer_country: Optional[str],
    buyer_type: str = "business",
    buyer_vat_verified: bool = False,
    supply_kind: str = "services",
    item_class: str = "standard",
    item_override=None,
    party_override=None,
    lang: str = "en",
):
    """Propose the tax for one sale in `company`. Reads; changes nothing.

    The seller's country and small-business status come from the COMPANY and
    cannot be passed in. A caller that could ask "what would this be if we
    were Austrian" would get a rate for a company that does not exist, and
    somebody would eventually put it on an invoice.
    """
    from ..billing.tax import TaxInputs, propose_tax

    codes, classes, rules = engine_tables(db, company_id=company.id)
    return propose_tax(
        TaxInputs(
            seller_country=company.address_country,
            buyer_country=buyer_country,
            buyer_type=buyer_type,
            buyer_vat_verified=buyer_vat_verified,
            supply_kind=supply_kind,
            item_class=item_class,
            item_override=item_override,
            party_override=party_override,
            seller_is_kleinunternehmer=bool(company.kleinunternehmer),
        ),
        codes,
        classes,
        rules,
        lang,
    )


# ─── Adding a rate, and adding a no-tax case ────────────────────────────────
#
# Mathias, 2026-10-23: "this app is for users all over Europe: one or several
# reduced rates, maybe several standard rates; the number is up to the user."
# So the pack is a starting point and the company goes on from there, which
# means two creation paths and one hard boundary.
#
# **The boundary is the EN 16931 category.** A user invents rates and names;
# a user never invents a category. The nine category codes are what an
# e-invoice (EN 16931, and therefore every ViDA-era exchange) is validated
# against, and a category nobody recognises is a document a customer's system
# rejects — which is a failure that surfaces weeks later at the recipient. So
# a new RATE is always `S` (standard-rated, which is what a reduced rate is
# too: rated, just lower), and a new NO-TAX case picks one of the six
# zero-rate categories.

#: The six categories a no-tax case may choose, each with the one line the
#: dialog shows. German and English, because the person choosing is the
#: person who will have to defend the choice.
#:
#: `S` is deliberately absent — a rated code is a rate, created the other
#: way — and so are `L` and `M`, the Canary Islands and Ceuta/Melilla
#: regimes: they carry real rates, and a company that needs one needs a
#: region pack rather than a dropdown.
NO_TAX_CATEGORIES: dict[str, dict[str, str]] = {
    "Z": {
        "en": "Zero-rated — taxable, but the rate is 0 %.",
        "de": "Nullsatz — steuerbar, aber mit 0 % besteuert.",
    },
    "E": {
        "en": "Exempt — outside the tax for a reason in the law, with no "
        "input-tax deduction.",
        "de": "Steuerfrei — gesetzlich befreit, ohne Vorsteuerabzug.",
    },
    "AE": {
        "en": "Reverse charge — the recipient accounts for the tax.",
        "de": "Reverse Charge — die Steuerschuld geht auf den Empfänger über.",
    },
    "K": {
        "en": "Intra-community supply — goods to a VAT-registered buyer in "
        "another EU country.",
        "de": "Innergemeinschaftliche Lieferung — Waren an einen "
        "umsatzsteuerlich registrierten Käufer in einem anderen EU-Land.",
    },
    "G": {
        "en": "Export — goods leaving the EU.",
        "de": "Ausfuhr — Waren verlassen die EU.",
    },
    "O": {
        "en": "Not subject to tax — the place of supply is outside this "
        "country's tax.",
        "de": "Nicht steuerbar — der Leistungsort liegt außerhalb.",
    },
}

#: The most codes one company may hold.
#:
#: Thirty, and the number is a guard rather than a product limit. The widest
#: legal set in the EU is four or five domestic rates; add the six no-tax
#: categories and a handful of company-specific exemptions and a real
#: configuration lands around a dozen. Thirty leaves every real
#: configuration room and still bounds two things that matter: the tax picker
#: stays a list a person can read, and `engine_tables` — which loads every
#: code on every single proposal, including each keystroke of the Test-a-case
#: screen — stays a query returning tens of rows rather than thousands. A
#: company that genuinely needs more has a region-pack problem, not a
#: dropdown problem, and should say so.
MAX_CODES_PER_COMPANY = 30

#: A user-chosen code. Upper case, starts alphanumeric, 1–20 characters.
#:
#: Narrow on purpose: this string is printed on invoices, exported into
#: accounting files and matched by the advisor's software, so a space, a
#: slash or a lower-case letter is a support request waiting to happen. The
#: column holds 32 to leave room for a collision suffix.
CODE_PATTERN = r"^[A-Z0-9][A-Z0-9._-]{0,19}$"

#: Prefix for a generated code when the company has no country yet.
CODE_PREFIX_WITHOUT_COUNTRY = "TAX"

_CUSTOM_KEY_PREFIX = "custom_"


class TaxConfigurationError(ValueError):
    """A create or edit that cannot be stored as asked.

    A `ValueError` subclass so a router can turn it into a 422 (or a 409 for
    the reference guard, which raises its own type) without this module
    importing anything from the web layer.
    """


class TooManyCodes(TaxConfigurationError):
    """The company is at `MAX_CODES_PER_COMPANY`."""


def code_count(db: Session, *, company_id: uuid.UUID) -> int:
    from sqlalchemy import func

    return (
        db.query(func.count(TaxCode.id))
        .filter(TaxCode.company_id == company_id)
        .scalar()
        or 0
    )


def _refuse_if_full(db: Session, *, company_id: uuid.UUID) -> None:
    current = code_count(db, company_id=company_id)
    if current >= MAX_CODES_PER_COMPANY:
        raise TooManyCodes(
            f"This company already has {current} tax codes, which is the "
            f"maximum of {MAX_CODES_PER_COMPANY}. Deactivate one you no "
            "longer use — nothing is ever deleted, so a deactivated code "
            "still explains the documents that used it."
        )


def next_custom_class_key(db: Session, *, company_id: uuid.UUID) -> str:
    """The next free `custom_<n>` key for this company.

    Generated rather than derived from the label, and immutable afterwards.
    A key derived from "Super-reduced" would change when somebody fixes the
    spelling, and the key is what an ITEM stores — so renaming a rate would
    silently re-tax every item in it. The label is the editable part; the key
    is a handle nobody has to read.
    """
    import re

    existing = {
        row.key
        for row in db.query(TaxClass.key)
        .filter(TaxClass.company_id == company_id)
        .all()
    }
    highest = 0
    for key in existing:
        match = re.fullmatch(rf"{_CUSTOM_KEY_PREFIX}(\d+)", key or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{_CUSTOM_KEY_PREFIX}{highest + 1}"


def validate_user_code(code: str) -> str:
    """A user-chosen code, upper-cased, or a readable refusal."""
    import re

    cleaned = (code or "").strip().upper()
    if not re.fullmatch(CODE_PATTERN, cleaned):
        raise TaxConfigurationError(
            f"{code!r} is not a usable tax code. Use 1 to 20 characters: "
            "capital letters, digits, dot, underscore or hyphen, starting "
            "with a letter or a digit — it is printed on invoices and read "
            "by your advisor's software."
        )
    return cleaned


def default_code_for_rate(
    db: Session, *, company: Company, rate: Decimal
) -> str:
    """`AT-21`, `FR-4.5`, `TAX-9` — and a suffix when that is taken.

    The country plus the rate, because that is how everybody writes these
    down and because it stays recognisable in an export. `format_rate` is
    the engine's own formatter, so `Decimal("4.5000")` prints as `4.5` here
    exactly as it does in a proposal's reasons.
    """
    from ..billing.tax import format_rate

    country = (company.address_country or "").strip().upper()
    base = f"{country or CODE_PREFIX_WITHOUT_COUNTRY}-{format_rate(rate)}"

    taken = {
        row.code
        for row in db.query(TaxCode.code)
        .filter(TaxCode.company_id == company.id)
        .all()
    }
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise TaxConfigurationError(
        f"Could not find a free code starting with {base}. Give the rate a "
        "code of your own."
    )


def create_rate(
    db: Session,
    *,
    company: Company,
    label: str,
    rate: Decimal,
    code: Optional[str] = None,
) -> tuple[TaxCode, TaxClass]:
    """A new domestic rate: one `S` code and the class that points at it.

    One transaction, no commit — the caller owns both, and a class with no
    code is a class every item in it resolves nothing from.

    The rate must be above 0 and below 100. Above 0 because an unset rate is
    something only a region pack ships (the generic pack's "set your rate"
    row); a user adding a rate knows what it is. Below 100 because a
    percentage of 100 or more is a typo in every real tax system, and
    catching it here is cheaper than catching it on an invoice.
    """
    from ..billing.tax import format_rate

    cleaned_label = (label or "").strip()
    if not cleaned_label:
        raise TaxConfigurationError("A rate needs a name.")
    if rate <= 0 or rate >= 100:
        raise TaxConfigurationError(
            f"A rate of {format_rate(rate)} % is not usable. Give a "
            "percentage above 0 and below 100 — 20 means 20 %."
        )

    _refuse_if_full(db, company_id=company.id)

    chosen = (
        validate_user_code(code)
        if code
        else default_code_for_rate(db, company=company, rate=rate)
    )
    taken = (
        db.query(TaxCode.id)
        .filter(TaxCode.company_id == company.id, TaxCode.code == chosen)
        .first()
    )
    if taken is not None:
        raise TaxConfigurationError(
            f"This company already has a tax code called {chosen}. Pick "
            "another code, or edit the one that exists."
        )

    code_row = TaxCode(
        company_id=company.id,
        code=chosen,
        name=cleaned_label,
        rate=quantize_qty(rate),
        # Always `S`. A reduced rate is a rated rate — see the note on
        # NO_TAX_CATEGORIES.
        category="S",
        legal_notes={},
        notes_reviewed=False,
        reporting_mapping={},
        is_active=True,
        # NULL: the company made this, not a pack. The seeder reads this to
        # know what is not its business.
        origin_pack=None,
    )
    db.add(code_row)
    db.flush()

    position = _next_class_position(db, company_id=company.id)
    class_row = TaxClass(
        company_id=company.id,
        key=next_custom_class_key(db, company_id=company.id),
        label=cleaned_label,
        code_id=code_row.id,
        position=position,
        is_active=True,
        origin_pack=None,
    )
    db.add(class_row)
    db.flush()
    return code_row, class_row


def _next_class_position(db: Session, *, company_id: uuid.UUID) -> int:
    from sqlalchemy import func

    highest = (
        db.query(func.max(TaxClass.position))
        .filter(TaxClass.company_id == company_id)
        .scalar()
    )
    # Ten past the last one, matching the packs' spacing, so a user can still
    # be given a "move up" control later without renumbering.
    return int(highest or 0) + 10


def create_no_tax_case(
    db: Session,
    *,
    company: Company,
    label: str,
    category: str,
    code: Optional[str] = None,
    legal_notes: Optional[dict] = None,
) -> TaxCode:
    """A rate-0 code in one of the six no-tax categories. No class.

    No class on purpose: a class is what an ITEM is labelled with, and a
    no-tax case is not a property of an item — it is a property of a
    situation (this customer, this supply, this document). It is chosen as an
    override, which is also why no shipped rule can ever select one: a pack
    rule may only name a code the pack itself defines (the loader refuses
    anything else), and a company's own code is in no pack.
    """
    cleaned_label = (label or "").strip()
    if not cleaned_label:
        raise TaxConfigurationError("A no-tax case needs a name.")
    if category not in NO_TAX_CATEGORIES:
        raise TaxConfigurationError(
            f"{category!r} is not one of the no-tax categories. Choose one "
            f"of {', '.join(NO_TAX_CATEGORIES)} — a rated code is a rate, "
            "added the other way."
        )

    _refuse_if_full(db, company_id=company.id)

    chosen = (
        validate_user_code(code)
        if code
        else _default_code_for_no_tax_case(db, company=company, category=category)
    )
    taken = (
        db.query(TaxCode.id)
        .filter(TaxCode.company_id == company.id, TaxCode.code == chosen)
        .first()
    )
    if taken is not None:
        raise TaxConfigurationError(
            f"This company already has a tax code called {chosen}. Pick "
            "another code, or edit the one that exists."
        )

    notes = {
        lang: text.strip()
        for lang, text in (legal_notes or {}).items()
        if text and text.strip()
    }
    row = TaxCode(
        company_id=company.id,
        code=chosen,
        name=cleaned_label,
        # Through the one rounding helper like every other rate, so the audit
        # row reads `0.0000` — the column's scale — rather than the `0` this
        # function happened to write.
        rate=quantize_qty(Decimal("0")),
        category=category,
        legal_notes=notes,
        # The user wrote it, so it is as unreviewed as anything FilmBill
        # ships — and the dialog says the wording is theirs to check.
        notes_reviewed=False,
        reporting_mapping={},
        is_active=True,
        origin_pack=None,
    )
    db.add(row)
    db.flush()
    return row


def _default_code_for_no_tax_case(
    db: Session, *, company: Company, category: str
) -> str:
    country = (company.address_country or "").strip().upper()
    base = f"{country or CODE_PREFIX_WITHOUT_COUNTRY}-{category}"
    taken = {
        row.code
        for row in db.query(TaxCode.code)
        .filter(TaxCode.company_id == company.id)
        .all()
    }
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise TaxConfigurationError(
        f"Could not find a free code starting with {base}. Give this case a "
        "code of your own."
    )


def class_for_code(
    db: Session, *, company_id: uuid.UUID, code_id: uuid.UUID
) -> Optional[TaxClass]:
    """The class that points at `code_id`, if any.

    One, by construction: a rate is a 1:1 pair. Used to refuse flipping a
    classed code's `is_active` on its own — see `tax_classes.is_active`.
    """
    return (
        db.query(TaxClass)
        .filter(TaxClass.company_id == company_id, TaxClass.code_id == code_id)
        .first()
    )
