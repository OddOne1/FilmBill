"""Who still points at a tax code or a tax class.

**Why this exists.** Nothing is ever deleted (Mathias, 2026-10-23), so the
only way to retire a rate or a no-tax case is to deactivate it — and a
deactivated code is one the engine refuses to propose from. That is correct
when nothing needs it and silently destructive when something does: switch
off `EU-AE` and the reverse-charge rule still matches, still resolves to it,
and every EU B2B invoice stops being priceable with a `blocking` answer and
no clue which switch caused it. The failure would surface at the worst
moment, in P2's finalize, to somebody who did not make the change.

So deactivation asks first, and a referenced code is refused with a sentence
naming the referrer.

**The registration point.** Today the only referrer is a tax rule. P1b
(parties: a default tax treatment), P1c (catalog: an item's tax class and
override) and P2 (documents: a line's snapshotted code) each add one, and
each of them adds it HERE rather than extending an `if` in the router — a
guard whose knowledge lives in the endpoint is a guard the next endpoint
forgets. `register_code_referrers` / `register_class_referrers` take a
callable and are meant to be used as decorators at the bottom of whichever
module owns the referring table.

**`@item` is deliberately not a class referrer.** The shipped rules resolve
`{class: "@item"}`, which means "whatever class the line's item carries" —
it refers to every class and to no class in particular. If it counted as a
reference, no class could ever be retired in any company, which would make
the whole feature unusable. What a rule explicitly names (`{class:
"standard"}`) does count.
"""

import uuid
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from sqlalchemy.orm import Session

from ..billing.tax.vocabulary import ITEM_CLASS_SENTINEL
from ..models.tax import TaxClass, TaxCode, TaxRule


@dataclass(frozen=True)
class Referrer:
    """One thing that still needs this code or class.

    `kind` is a word for a sentence ("rule", "item", "customer"), `name` is
    what the user calls that thing. Both, because "used by 3 things" is not
    an answer anybody can act on.
    """

    kind: str
    name: str

    def __str__(self) -> str:
        return f"{self.kind} “{self.name}”"


CodeSource = Callable[[Session, TaxCode], Iterable[Referrer]]
ClassSource = Callable[[Session, TaxClass], Iterable[Referrer]]

_CODE_SOURCES: list[CodeSource] = []
_CLASS_SOURCES: list[ClassSource] = []


def register_code_referrers(source: CodeSource) -> CodeSource:
    """Add a source of code referrers. Returns it, so it works as a decorator.

    Idempotent on the function object: importing a module twice must not
    make every referrer appear twice in the sentence.
    """
    if source not in _CODE_SOURCES:
        _CODE_SOURCES.append(source)
    return source


def register_class_referrers(source: ClassSource) -> ClassSource:
    if source not in _CLASS_SOURCES:
        _CLASS_SOURCES.append(source)
    return source


def registered_sources() -> tuple[int, int]:
    """`(code sources, class sources)`. For tests and for a diagnostic."""
    return len(_CODE_SOURCES), len(_CLASS_SOURCES)


def references_to_code(db: Session, code_id: uuid.UUID) -> list[Referrer]:
    """Everything that still points at this code, from every source.

    An unknown id returns an empty list rather than raising: the caller has
    already resolved the row (and answered 404 if it was not theirs), so
    reaching here with a missing id means the row went away between two
    statements, and "nothing refers to a row that does not exist" is true.
    """
    code = db.query(TaxCode).filter(TaxCode.id == code_id).first()
    if code is None:
        return []
    found: list[Referrer] = []
    for source in _CODE_SOURCES:
        found.extend(source(db, code))
    return found


def references_to_class(db: Session, class_id: uuid.UUID) -> list[Referrer]:
    tax_class = db.query(TaxClass).filter(TaxClass.id == class_id).first()
    if tax_class is None:
        return []
    found: list[Referrer] = []
    for source in _CLASS_SOURCES:
        found.extend(source(db, tax_class))
    return found


def refusal_sentence(subject: str, referrers: Iterable[Referrer]) -> str:
    """The 409 body. Names the referrers, because that is the whole point.

    "EU-AE cannot be deactivated: it is used by rule “EU business, VAT ID
    verified, services: reverse charge”. Change or deactivate that first."
    """
    listed = ", ".join(str(referrer) for referrer in referrers)
    return (
        f"{subject} cannot be deactivated: it is used by {listed}. Change or "
        "deactivate that first — switching this off while something still "
        "resolves to it would leave those documents unpriceable with no "
        "indication why."
    )


# ─── The sources this release knows about ───────────────────────────────────


def _active_rules(db: Session, company_id: uuid.UUID) -> list[TaxRule]:
    return (
        db.query(TaxRule)
        .filter(TaxRule.company_id == company_id, TaxRule.is_active.is_(True))
        .order_by(TaxRule.position)
        .all()
    )


def _target_of(rule: TaxRule) -> tuple[Optional[str], Optional[str]]:
    """`(kind, value)` of a rule's outcome target, or `(None, None)`."""
    target = (rule.outcome or {}).get("code")
    if not isinstance(target, dict) or len(target) != 1:
        return None, None
    (kind, value), = target.items()
    return kind, value


@register_code_referrers
def _rules_naming_this_code(db: Session, code: TaxCode) -> list[Referrer]:
    """Active rules whose outcome is `{code: <this one>}`.

    Only ACTIVE rules: a deactivated rule resolves nothing, so it is not a
    reason to keep a code switched on. Reactivating that rule later with a
    deactivated code is a state the engine already reports (`code_inactive`,
    blocking, naming the code).
    """
    found = []
    for rule in _active_rules(db, code.company_id):
        kind, value = _target_of(rule)
        if kind == "code" and value == code.code:
            found.append(Referrer(kind="rule", name=rule.name))
    return found


@register_class_referrers
def _rules_naming_this_class(db: Session, tax_class: TaxClass) -> list[Referrer]:
    """Active rules whose outcome explicitly names this class key.

    `@item` does not count — see the module docstring.
    """
    found = []
    for rule in _active_rules(db, tax_class.company_id):
        kind, value = _target_of(rule)
        if kind == "class" and value == tax_class.key:
            found.append(Referrer(kind="rule", name=rule.name))
    return found


@register_class_referrers
def _the_classs_own_code(db: Session, tax_class: TaxClass) -> list[Referrer]:
    """Whatever refers to the class's CODE refers to the class.

    A rate is a 1:1 pair and retiring it switches both off, so a rule that
    names the code would be broken by retiring the class just as surely as
    by deactivating the code directly. Asking the code's own sources keeps
    the two answers from drifting, and means a referrer registered for codes
    in P2 protects rates without being registered twice.
    """
    code = db.query(TaxCode).filter(TaxCode.id == tax_class.code_id).first()
    if code is None:  # pragma: no cover - NOT NULL plus RESTRICT
        return []
    found: list[Referrer] = []
    for source in _CODE_SOURCES:
        found.extend(source(db, code))
    return found
