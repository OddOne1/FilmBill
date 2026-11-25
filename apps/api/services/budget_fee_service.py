"""The production fee: one per budget, preset on the company (B1.2 part 2).

**The fee is an ordinary adjustment with a role.** A `surcharge`, `percent`,
on the whole budget, `base = "kinds"` when the fee is limited to certain
kinds and `"subtotal"` otherwise, and `role = "production_fee"`. That is all
it takes for the engine, the tax grouping, the margin and the CSV to price it
— they already price surcharges — and it is why this module has no
arithmetic in it. It moves the price only (D78): a fee that must raise the
COST is a line of kind `fee`.

**The preset is a starting point, not a link.** A new budget is born with
the company's preset copied onto it; from then on the budget's fee is its
own. Changing it on one budget touches no other budget and not the preset;
changing the preset touches no existing budget. "Reset to preset" copies the
preset again. Two customers on one project can therefore be quoted 8 % and
12 % — which is the case that asked for this (Mathias, 2026-11-24).

**At most one per budget** — `uq_budget_adjustments_one_production_fee`, a
partial unique index over active rows, is the rule; this module updates the
one row in place rather than adding a second, and the generic adjustment
endpoints refuse to touch a row that carries a role.
"""

from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..billing.catalog.kinds import ITEM_KINDS
from ..models.company import Company
from ..models.project import Budget, BudgetAdjustment
from . import audit
from .budget_service import BudgetError, next_adjustment_position

ROLE_PRODUCTION_FEE = "production_fee"
#: Every role an adjustment may carry. Closed: a role is something a screen
#: and a service branch on, and one nothing branches on is a typo.
ADJUSTMENT_ROLES: tuple[str, ...] = (ROLE_PRODUCTION_FEE,)

FEE_LABEL = "Production fee"

BUDGET_PRODUCTION_FEE_SET = "budget.production_fee_set"
COMPANY_DEFAULT_PRODUCTION_FEE_SET = "company.default_production_fee_set"

#: What the before/after of both audit actions carry.
FEE_AUDITED: tuple[str, ...] = ("enabled", "percent", "kinds")

HUNDRED = Decimal("100")


def check_fee(percent: Optional[Decimal], kinds: Iterable[str]) -> list[str]:
    """The fee's two inputs, refused in the user's words, or the kinds clean.

    A fee above 100 % is not something anybody quotes, and is refused rather
    than stored so a typo of "80" for "8.0" cannot reach a customer.
    """
    if percent is not None and (percent < 0 or percent > HUNDRED):
        raise BudgetError(
            f"A production fee of {percent} % is outside 0 to 100 %. Enter "
            f"the percent, for example 8."
        )
    kinds = list(dict.fromkeys(kinds or []))
    unknown = [kind for kind in kinds if kind not in ITEM_KINDS]
    if unknown:
        raise BudgetError(
            f"The production fee names unknown kinds ({', '.join(unknown)}). "
            f"Use: {', '.join(ITEM_KINDS)}."
        )
    return kinds


def fee_row(db: Session, budget: Budget) -> Optional[BudgetAdjustment]:
    """The budget's active fee adjustment, or None."""
    return (
        db.query(BudgetAdjustment)
        .filter(
            BudgetAdjustment.company_id == budget.company_id,
            BudgetAdjustment.budget_id == budget.id,
            BudgetAdjustment.role == ROLE_PRODUCTION_FEE,
            BudgetAdjustment.is_active.is_(True),
        )
        .one_or_none()
    )


def _fee_of(row: Optional[BudgetAdjustment]) -> dict:
    if row is None:
        return {"enabled": False, "percent": None, "kinds": []}
    return {
        "enabled": True,
        "percent": Decimal(row.value),
        "kinds": list(row.kinds or []) if row.base == "kinds" else [],
    }


def preset_of(company: Company) -> dict:
    percent = company.default_production_fee_percent
    return {
        "enabled": percent is not None,
        "percent": Decimal(percent) if percent is not None else None,
        "kinds": list(company.default_production_fee_kinds or []),
    }


def _same(fee: dict, preset: dict) -> bool:
    if fee["enabled"] != preset["enabled"]:
        return False
    if not fee["enabled"]:
        return True
    return fee["percent"] == preset["percent"] and sorted(fee["kinds"]) == sorted(
        preset["kinds"]
    )


def state(db: Session, budget: Budget, company: Company) -> dict:
    """What the sheet's fee control shows: this budget's fee and the preset."""
    row = fee_row(db, budget)
    fee = _fee_of(row)
    preset = preset_of(company)
    return {
        "enabled": fee["enabled"],
        "percent": fee["percent"],
        "kinds": fee["kinds"],
        "adjustment_id": row.id if row is not None else None,
        "preset_percent": preset["percent"],
        "preset_kinds": preset["kinds"],
        "differs_from_preset": not _same(fee, preset),
    }


def _write(
    db: Session,
    budget: Budget,
    *,
    enabled: bool,
    percent: Optional[Decimal],
    kinds: list[str],
) -> Optional[BudgetAdjustment]:
    """Make the budget's fee what was asked: update, create or switch off.

    One row, updated in place. Switching the fee off retires the row
    (`is_active = false`) like every other removal in a budget; switching it
    on again creates a new one, so the audit trail of the old row stays what
    it was.
    """
    row = fee_row(db, budget)
    if not enabled:
        if row is not None:
            row.is_active = False
            db.flush()
        return None
    if percent is None:
        raise BudgetError(
            "The production fee needs a percent. Enter one, or switch the fee off."
        )
    if row is None:
        row = BudgetAdjustment(
            company_id=budget.company_id,
            budget_id=budget.id,
            section_id=None,
            position=next_adjustment_position(db, budget),
            label=FEE_LABEL,
            type="surcharge",
            mode="percent",
            role=ROLE_PRODUCTION_FEE,
            is_active=True,
        )
        db.add(row)
    row.value = percent
    row.base = "kinds" if kinds else "subtotal"
    row.kinds = list(kinds)
    db.flush()
    return row


def set_fee(
    db: Session,
    budget: Budget,
    actor: audit.Actor,
    *,
    enabled: bool,
    percent: Optional[Decimal],
    kinds: Iterable[str],
    reason: Optional[str] = None,
) -> Optional[BudgetAdjustment]:
    """Set THIS budget's fee. Never touches the preset or another budget."""
    kinds = check_fee(percent, kinds)
    before = _fee_of(fee_row(db, budget))
    row = _write(db, budget, enabled=enabled, percent=percent, kinds=kinds)
    after = _fee_of(row)
    if audit.diff(before, after, fields=FEE_AUDITED):
        audit.record(
            db,
            actor,
            budget,
            BUDGET_PRODUCTION_FEE_SET,
            before,
            after,
            reason,
            fields=FEE_AUDITED,
        )
    return row


def reset_to_preset(
    db: Session, budget: Budget, company: Company, actor: audit.Actor
) -> Optional[BudgetAdjustment]:
    """Copy the company preset onto this budget again. No preset → no fee."""
    preset = preset_of(company)
    return set_fee(
        db,
        budget,
        actor,
        enabled=preset["enabled"],
        percent=preset["percent"],
        kinds=preset["kinds"],
        reason="Reset to the company preset",
    )


def apply_preset_to_new_budget(
    db: Session, budget: Budget, company: Company, actor: audit.Actor
) -> Optional[BudgetAdjustment]:
    """A new blank budget starts with the preset, when there is one."""
    preset = preset_of(company)
    if not preset["enabled"]:
        return None
    return set_fee(
        db,
        budget,
        actor,
        enabled=True,
        percent=preset["percent"],
        kinds=preset["kinds"],
        reason="From the company preset",
    )


def set_preset(
    db: Session,
    company: Company,
    actor: audit.Actor,
    *,
    percent: Optional[Decimal],
    kinds: Iterable[str],
) -> dict:
    """Change the company preset. Touches no existing budget."""
    kinds = check_fee(percent, kinds)
    before = preset_of(company)
    company.default_production_fee_percent = percent
    company.default_production_fee_kinds = kinds if percent is not None else []
    db.flush()
    after = preset_of(company)
    if audit.diff(before, after, fields=FEE_AUDITED):
        audit.record(
            db,
            actor,
            company,
            COMPANY_DEFAULT_PRODUCTION_FEE_SET,
            before,
            after,
            fields=FEE_AUDITED,
        )
    return after
