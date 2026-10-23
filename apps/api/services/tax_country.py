"""Changing a company's country without destroying its tax configuration.

**Why this exists.** Until now the region pack was installed once, when the
country was first set, and a later change installed nothing: a company that
mis-typed `DE` and corrected it to `AT` kept German codes for ever, silently,
and every invoice it issued carried the wrong rate. Mathias, 2026-10-23: fix
it now, while there is no real data, through an explicit action with a
preview.

**The shape of the answer is plan → confirm.** Not "change the country and
reconcile": a country change touches rates, so somebody has to see what will
happen to their own edits before it happens. `plan_country_change` computes
the whole thing and writes nothing; `apply_country_change` performs exactly
that plan. A test asserts the two agree, because a preview that is not what
happens is worse than no preview — it is a promise.

**Non-destructive, in five specific senses** (SCOPE D53):

  1. a rate, name, note or active flag the user **edited** is never touched —
     "edited" means the row differs from what its pack declares, which is
     checkable rather than guessed;
  2. a user-created rate (`custom_*`) or no-tax case is never touched at all;
  3. nothing is ever deleted — an old country's codes are DEACTIVATED, and
     they keep explaining the documents that used them;
  4. an old code something still points at stays **active**, named in the
     plan, because deactivating it would make every sale that resolves to it
     unpriceable (the P1a-2 reference registry decides this);
  5. a shipped class whose key the new country has no equivalent for is kept
     exactly as it is, and listed — Austria's 13 % and 4.9 % classes have no
     German counterpart, and dropping them would lose the only explanation of
     the items labelled with them.

**Finalized documents are unaffected by design**, because a finalized line
snapshots the code and rate it used (CLAUDE.md rule 3, SCOPE §5.4). Nothing
here could reach them even if it tried.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..billing.tax import packs
from ..models.company import Company
from ..models.tax import TaxClass, TaxCode, TaxRule
from . import tax_references

#: Apply the new country's pack, non-destructively. The recommended mode.
MODE_APPLY_PACK = "apply_pack"
#: Change `address_country` and nothing else. For somebody who has already
#: configured their tax by hand and wants the country corrected only.
MODE_COUNTRY_ONLY = "country_only"
MODES = (MODE_APPLY_PACK, MODE_COUNTRY_ONLY)


class CountryChangeError(ValueError):
    """A country change that cannot be planned or applied as asked."""


@dataclass
class CountryChangePlan:
    """What a country change would do. Rendered for the dialog, and applied.

    Lists rather than counts in the data, counts in the sentences: a count
    tells somebody whether to worry and a list tells them what about.
    """

    from_country: Optional[str]
    to_country: str
    pack: str
    mode: str
    #: True when the company has no tax rows at all — the old first-set path,
    #: which needs no dialog and no plan.
    first_set: bool = False

    codes_added: list[str] = field(default_factory=list)
    #: `(class key, old code, new code)` for a class that moves.
    classes_repointed: list[tuple[str, str, str]] = field(default_factory=list)
    #: `(class key, code)` — a shipped class the new country has no key for.
    classes_without_equivalent: list[tuple[str, str]] = field(default_factory=list)
    #: `(class key, code)` — a shipped class the company did not have yet.
    classes_added: list[tuple[str, str]] = field(default_factory=list)
    codes_deactivated: list[str] = field(default_factory=list)
    #: Reactivated because an active shipped class now points at them. A
    #: round trip (AT → DE → AT) ends here rather than with a dead class.
    codes_reactivated: list[str] = field(default_factory=list)
    #: `(code, [referrer sentences])`
    codes_kept_because_referenced: list[tuple[str, list[str]]] = field(
        default_factory=list
    )
    #: Codes left exactly as they are because they are yours: a rate you
    #: edited, a rate you created, a no-tax case you created.
    untouched: list[str] = field(default_factory=list)
    rules_replaced: int = 0
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        """The numbers that go into the audit row and the summary line."""
        return {
            "codes_added": len(self.codes_added),
            "classes_repointed": len(self.classes_repointed),
            "classes_added": len(self.classes_added),
            "classes_without_equivalent": len(self.classes_without_equivalent),
            "codes_deactivated": len(self.codes_deactivated),
            "codes_reactivated": len(self.codes_reactivated),
            "codes_kept_because_referenced": len(
                self.codes_kept_because_referenced
            ),
            "untouched": len(self.untouched),
            "rules_replaced": self.rules_replaced,
        }

    def sentences(self) -> list[str]:
        """The plan in plain words, for the dialog.

        Built here rather than in the web app for the same reason the
        proposal's `reasons` are: the server decided it, so the server says
        what it decided, and every API consumer reads the same wording.
        """
        said: list[str] = []
        if self.mode == MODE_COUNTRY_ONLY:
            said.append(
                f"The country becomes {self.to_country} and nothing else "
                "changes. Your tax codes, classes and rules stay exactly as "
                "they are — including the ones that came from "
                f"{self.from_country or 'the previous country'}'s pack."
            )
            return said

        if self.codes_added:
            said.append(
                f"{len(self.codes_added)} tax code(s) are added: "
                f"{', '.join(self.codes_added)}."
            )
        if self.classes_repointed:
            moves = ", ".join(
                f"{key}: {old} → {new}" for key, old, new in self.classes_repointed
            )
            said.append(
                f"{len(self.classes_repointed)} rate(s) move to the new "
                f"country's code — {moves}. The names you gave them are kept."
            )
        if self.classes_added:
            said.append(
                f"{len(self.classes_added)} rate(s) the new country has and "
                "you did not are added: "
                + ", ".join(f"{key} ({code})" for key, code in self.classes_added)
                + "."
            )
        if self.codes_reactivated:
            said.append(
                f"{len(self.codes_reactivated)} code(s) are switched back on "
                f"because a rate now uses them again: "
                f"{', '.join(self.codes_reactivated)}."
            )
        if self.codes_deactivated:
            said.append(
                f"{len(self.codes_deactivated)} code(s) from "
                f"{self.from_country or 'the previous country'} are switched "
                f"off: {', '.join(self.codes_deactivated)}. Nothing is "
                "deleted — they still explain the documents that used them."
            )
        if self.codes_kept_because_referenced:
            said.append(
                f"{len(self.codes_kept_because_referenced)} code(s) stay "
                "switched on because something still points at them: "
                + "; ".join(
                    f"{code} ({', '.join(referrers)})"
                    for code, referrers in self.codes_kept_because_referenced
                )
                + "."
            )
        if self.classes_without_equivalent:
            said.append(
                f"{len(self.classes_without_equivalent)} rate(s) have no "
                f"equivalent in {self.to_country} and are kept as they are: "
                + ", ".join(
                    f"{key} ({code})"
                    for key, code in self.classes_without_equivalent
                )
                + "."
            )
        if self.untouched:
            said.append(
                f"{len(self.untouched)} code(s) of your own are left "
                f"untouched: {', '.join(self.untouched)}."
            )
        if self.rules_replaced:
            said.append(
                f"The {self.rules_replaced} shipped tax rules are replaced by "
                f"{self.to_country}'s."
            )
        if not said:
            said.append("Nothing would change except the country itself.")
        return said


# ─── Is this row still the pack's, or has somebody edited it? ───────────────


def _pack_entry(pack, code: str):
    return pack.code(code)


def _code_is_pristine(row: TaxCode, entry) -> bool:
    """Whether `row` still says exactly what its pack declared.

    The definition of "the user edited this", and it is deliberately
    checkable rather than a flag somebody has to remember to set. `is_active`
    is NOT part of it: a shipped code's active flag moves with its class (the
    1:1 pair rule from P1a-2), so it is managed here rather than owned by the
    user.
    """
    if entry is None:
        return False
    return (
        row.rate == entry.rate
        and row.name == entry.name
        and dict(row.legal_notes or {}) == dict(entry.legal_notes or {})
        and not row.notes_reviewed
    )


def _is_unset_rate(rate, category: str) -> bool:
    """An `S` code at 0 %: a rate nobody has set yet.

    P1a-2's "set your rate" condition, and the one asymmetry in the pair
    rule. Retiring a rate switches the class and its code off together, but
    switching one ON is not symmetric: a 0 % standard code that machinery
    activated would propose **0 % tax**, which is the worst thing this
    system can do quietly — it is exactly why the generic pack ships that
    code inactive. So a class may be active while its unset code is not; the
    engine then answers `blocking` with `code_inactive`, which is what the
    plan's warning promises. Only a user typing a rate switches one on.
    """
    return category == "S" and (rate or 0) == 0


def _activation_for(row: Optional[TaxCode], entry, class_active: bool) -> bool:
    """Whether a shipped class's code should be active after a re-point."""
    rate = row.rate if row is not None else (entry.rate if entry else 0)
    category = row.category if row is not None else (entry.category if entry else "")
    if _is_unset_rate(rate, category):
        return False
    return bool(class_active)


def _rule_signature(position: int, name: str, conditions, outcome, is_active: bool):
    import json

    return (
        position,
        name,
        json.dumps(conditions or {}, sort_keys=True),
        json.dumps(outcome or {}, sort_keys=True),
        bool(is_active),
    )


# ─── Planning ───────────────────────────────────────────────────────────────


def has_tax_rows(db: Session, *, company_id: uuid.UUID) -> bool:
    """Whether this company has any tax configuration at all."""
    return (
        db.query(TaxCode.id).filter(TaxCode.company_id == company_id).first()
        is not None
    )


def plan_country_change(
    db: Session,
    *,
    company: Company,
    country: str,
    mode: str = MODE_APPLY_PACK,
) -> CountryChangePlan:
    """What changing the country to `country` would do. Writes nothing."""
    wanted = (country or "").strip().upper()
    if len(wanted) != 2 or not wanted.isalpha():
        raise CountryChangeError(
            f"{country!r} is not a country. Use a two-letter ISO code, for "
            "example AT or DE."
        )
    if mode not in MODES:
        raise CountryChangeError(
            f"{mode!r} is not a mode. Use {MODE_APPLY_PACK} (install the new "
            f"country's rates) or {MODE_COUNTRY_ONLY} (change the country "
            "only)."
        )

    current = (company.address_country or "").strip().upper() or None
    pack_name = packs.pack_name_for_country(wanted) or packs.GENERIC_PACK
    plan = CountryChangePlan(
        from_country=current,
        to_country=wanted,
        pack=pack_name,
        mode=mode,
        first_set=not has_tax_rows(db, company_id=company.id),
    )

    if mode == MODE_COUNTRY_ONLY:
        plan.warnings.append(
            "Your tax configuration is not touched, so it may still be the "
            f"previous country's. Check Settings → Company → Tax afterwards."
        )
        return plan

    new_pack = packs.load_pack(pack_name)
    if plan.first_set:
        # The old path: seeding does the whole job and there is nothing to
        # reconcile. Described so the dialog can be skipped with confidence.
        plan.codes_added = [entry.code for entry in new_pack.codes]
        plan.classes_added = [
            (entry.key, entry.code) for entry in new_pack.classes
        ]
        plan.rules_replaced = len(new_pack.rules)
        _warn_about_unset_standard_from_pack(plan, new_pack)
        return plan

    codes = {
        row.code: row
        for row in db.query(TaxCode).filter(TaxCode.company_id == company.id)
    }
    classes = {
        row.key: row
        for row in db.query(TaxClass).filter(TaxClass.company_id == company.id)
    }

    # ── codes the new pack brings and the company has not got ───────────
    for entry in new_pack.codes:
        if entry.code not in codes:
            plan.codes_added.append(entry.code)

    # ── the shipped classes ─────────────────────────────────────────────
    new_class_codes = {entry.key: entry for entry in new_pack.classes}
    #: Which code each shipped class will point at afterwards, so the code
    #: bookkeeping below knows what is still in use.
    targets: dict[str, str] = {}

    for key, row in sorted(classes.items(), key=lambda item: item[1].position):
        if row.origin_pack is None:
            # A rate the company created. Not ours to move.
            continue
        wanted_entry = new_class_codes.get(key)
        current_code_string = _code_of(codes, row.code_id)
        if wanted_entry is None:
            plan.classes_without_equivalent.append((key, current_code_string))
            targets[key] = current_code_string
            continue
        targets[key] = wanted_entry.code
        if wanted_entry.code != current_code_string:
            plan.classes_repointed.append(
                (key, current_code_string, wanted_entry.code)
            )

    for key, entry in new_class_codes.items():
        if key not in classes:
            plan.classes_added.append((key, entry.code))
            targets[key] = entry.code

    # ── the old country's codes ─────────────────────────────────────────
    in_use = set(targets.values())
    new_pack_codes = {entry.code for entry in new_pack.codes}

    for code, row in sorted(codes.items()):
        if row.origin_pack is None:
            plan.untouched.append(code)
            continue
        if code in new_pack_codes:
            # The new pack's own. If an active class points at it and it is
            # off, it has to come back on or that class resolves nothing —
            # unless it is an unset 0 % rate, which only a user switches on.
            if (
                code in in_use
                and not row.is_active
                and _class_wanting(classes, new_class_codes, code)
                and not _is_unset_rate(row.rate, row.category)
            ):
                plan.codes_reactivated.append(code)
            continue
        if code in in_use:
            # A class with no equivalent still points at it.
            continue
        entry = _pack_entry(packs.load_pack(row.origin_pack), code) if row.origin_pack else None
        if not _code_is_pristine(row, entry):
            plan.untouched.append(code)
            continue
        referrers = tax_references.references_to_code(db, row.id)
        if referrers:
            plan.codes_kept_because_referenced.append(
                (code, [str(referrer) for referrer in referrers])
            )
            continue
        if row.is_active:
            plan.codes_deactivated.append(code)

    # ── the rules ───────────────────────────────────────────────────────
    existing_rules = (
        db.query(TaxRule)
        .filter(TaxRule.company_id == company.id)
        .order_by(TaxRule.position, TaxRule.name)
        .all()
    )
    existing_signatures = [
        _rule_signature(
            row.position, row.name, row.conditions, row.outcome, row.is_active
        )
        for row in existing_rules
    ]
    wanted_signatures = [
        _rule_signature(
            entry.position, entry.name, entry.conditions, entry.outcome,
            entry.is_active,
        )
        for entry in sorted(new_pack.rules, key=lambda entry: entry.position)
    ]
    if existing_signatures != wanted_signatures:
        old_pack_pristine = _rules_are_a_pack_set(db, company, existing_rules)
        if old_pack_pristine:
            plan.rules_replaced = len(new_pack.rules)
        else:
            plan.warnings.append(
                "Your tax rules have been edited, so they are left exactly as "
                "they are. Check them against the new country afterwards."
            )

    _warn_about_unset_standard(plan, db, company, codes, targets, new_pack)
    return plan


def _code_of(codes: dict, code_id) -> str:
    for code, row in codes.items():
        if row.id == code_id:
            return code
    return ""


def _class_wanting(classes: dict, new_class_codes: dict, code: str) -> bool:
    """Whether an ACTIVE shipped class will point at `code` afterwards."""
    for key, entry in new_class_codes.items():
        if entry.code != code:
            continue
        row = classes.get(key)
        if row is None:
            return entry.is_active
        return bool(row.is_active)
    return False


def _rules_are_a_pack_set(db: Session, company: Company, rows) -> bool:
    """Whether every rule is still exactly as some shipped pack declared it.

    Compared against the pack each row came from, so a company that has
    switched country twice is still judged against the right source.
    """
    if not rows:
        return True
    for row in rows:
        if row.origin_pack is None:
            return False
        try:
            pack = packs.load_pack(row.origin_pack)
        except packs.PackError:  # pragma: no cover - a pack that vanished
            return False
        match = next(
            (
                entry
                for entry in pack.rules
                if entry.position == row.position and entry.name == row.name
            ),
            None,
        )
        if match is None:
            return False
        if _rule_signature(
            row.position, row.name, row.conditions, row.outcome, row.is_active
        ) != _rule_signature(
            match.position, match.name, match.conditions, match.outcome,
            match.is_active,
        ):
            return False
    return True


def _warn_about_unset_standard_from_pack(plan: CountryChangePlan, pack) -> None:
    standard = pack.tax_class("standard")
    entry = pack.code(standard.code) if standard else None
    if entry is not None and not entry.is_active:
        plan.warnings.append(
            f"{plan.to_country} has no rates in FilmBill, so the standard "
            "rate arrives at 0 % and switched off: proposals will be blocked "
            "until you set your rate."
        )


def _warn_about_unset_standard(
    plan: CountryChangePlan, db, company, codes, targets, new_pack
) -> None:
    """Say so when the standard rate will not be usable afterwards."""
    target = targets.get("standard")
    if target is None:
        return
    row = codes.get(target)
    entry = new_pack.code(target)
    will_be_active = (
        target in plan.codes_reactivated
        or (row.is_active if row is not None else bool(entry and entry.is_active))
    )
    zero = (row.rate if row is not None else (entry.rate if entry else 0)) == 0
    if not will_be_active or zero:
        plan.warnings.append(
            f"The standard rate for {plan.to_country} is "
            f"{'0 % and ' if zero else ''}not usable yet: proposals will be "
            "blocked until you set your rate on the Tax screen."
        )


# ─── Applying ───────────────────────────────────────────────────────────────


def apply_country_change(
    db: Session,
    *,
    company: Company,
    country: str,
    mode: str = MODE_APPLY_PACK,
) -> CountryChangePlan:
    """Perform exactly the plan `plan_country_change` returns. No commit.

    No commit because the caller owns the transaction: the country, the codes
    and the audit row land together or not at all, which is the only reading
    under which a failed save cannot leave a company half-moved.

    The plan is recomputed here rather than passed in. A plan handed back by a
    client is a plan a client could edit, and "apply what you were shown" has
    to mean "apply what the server would show for these inputs" — the test
    `test_applying_produces_exactly_what_the_preview_showed` is what keeps the
    two honest.
    """
    plan = plan_country_change(db, company=company, country=country, mode=mode)

    if mode == MODE_COUNTRY_ONLY:
        company.address_country = plan.to_country
        db.flush()
        return plan

    if plan.first_set:
        company.address_country = plan.to_country
        from .tax_service import seed_tax_configuration

        seed_tax_configuration(db, company=company)
        db.flush()
        return plan

    new_pack = packs.load_pack(plan.pack)
    codes = {
        row.code: row
        for row in db.query(TaxCode).filter(TaxCode.company_id == company.id)
    }
    classes = {
        row.key: row
        for row in db.query(TaxClass).filter(TaxClass.company_id == company.id)
    }

    # 1. the new pack's missing codes
    for code in plan.codes_added:
        entry = new_pack.code(code)
        row = TaxCode(
            company_id=company.id,
            code=entry.code,
            name=entry.name,
            rate=entry.rate,
            category=entry.category,
            legal_notes=dict(entry.legal_notes),
            notes_reviewed=False,
            reporting_mapping={},
            is_active=entry.is_active,
            origin_pack=entry.origin_pack,
        )
        db.add(row)
        codes[code] = row
    if plan.codes_added:
        db.flush()

    # 2. re-point the shipped classes, carrying the class's own active flag
    #    onto its code — a rate is a 1:1 pair (P1a-2) and a live code behind a
    #    dead class, or the reverse, is a state nothing can explain.
    for key, _old, new_code in plan.classes_repointed:
        row = classes[key]
        target = codes[new_code]
        row.code_id = target.id
        target.is_active = _activation_for(
            target, new_pack.code(new_code), row.is_active
        )

    # 3. add the shipped classes this company did not have
    position = _next_position(db, company.id)
    for key, code in plan.classes_added:
        entry = next(e for e in new_pack.classes if e.key == key)
        row = TaxClass(
            company_id=company.id,
            key=key,
            label=entry.label,
            code_id=codes[code].id,
            position=entry.position or position,
            is_active=entry.is_active,
            origin_pack=entry.origin_pack,
        )
        db.add(row)
        classes[key] = row
        codes[code].is_active = _activation_for(
            codes[code], new_pack.code(code), entry.is_active
        )

    # 4. switch the ones the plan named, and only those
    for code in plan.codes_reactivated:
        codes[code].is_active = True
    for code in plan.codes_deactivated:
        codes[code].is_active = False

    # 5. the rules, only when the plan said so
    if plan.rules_replaced:
        db.query(TaxRule).filter(TaxRule.company_id == company.id).delete(
            synchronize_session=False
        )
        for entry in sorted(new_pack.rules, key=lambda entry: entry.position):
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

    company.address_country = plan.to_country
    db.flush()
    return plan


def _next_position(db: Session, company_id: uuid.UUID) -> int:
    from sqlalchemy import func

    highest = (
        db.query(func.max(TaxClass.position))
        .filter(TaxClass.company_id == company_id)
        .scalar()
    )
    return int(highest or 0) + 10
