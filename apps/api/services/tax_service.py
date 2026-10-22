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
from typing import Optional

from sqlalchemy.orm import Session

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
