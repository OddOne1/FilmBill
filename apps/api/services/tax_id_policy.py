"""How often a tax identifier is re-checked, and for how long an answer counts.

The policy lives on `companies.tax_id_check_policy`, one JSONB column rather
than five scalar ones, for the reason rule 7 gives: the set of knobs will change
(a per-provider interval is the obvious next one), and a knob should not cost
a migration. What it is NOT is free text — every value is validated here, and
an unknown key is refused rather than ignored, because a typo'd key that is
silently dropped looks exactly like a setting that does not work.

**The defaults are [Guessing]** (D62). They are chosen to be defensible
rather than clever:

  * `initial_check_offsets = [0, 1, 7]` days. A number is checked when it is
    entered, again the next day, and again a week later. The reason is the
    opposite of paranoia: a *newly registered* business is not in VIES yet,
    so the first answer for a perfectly good number is often "invalid". One
    check would record that as a finding about the customer. Three spread
    over a week turn it into a question that answers itself.
  * `recheck_interval_days = 90`. Quarterly matches how often most small
    companies look at their own books, and VIES's own terms limit use to
    supporting a legitimate user's activity — daily polling of every
    customer is not that.
  * `max_check_age_days = 180`. Twice the interval, so one missed cycle does
    not un-verify anybody, and two do.

The combination is validated: an age shorter than the interval would
guarantee that every identifier spends part of each cycle unverified, which
is a setting nobody means to choose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..models.company import Company

#: Offered in the UI. `off` is `None` and is a legitimate choice: a
#: self-hosted installation with no outbound internet should be able to turn
#: scheduling off rather than accumulate `unavailable` rows for ever.
INTERVAL_CHOICES = (7, 30, 90, 180, 365, None)

DEFAULT_INITIAL_OFFSETS = (0, 1, 7)
DEFAULT_RECHECK_INTERVAL_DAYS = 90
DEFAULT_MAX_CHECK_AGE_DAYS = 180

#: A ceiling on the initial series. Four checks in the first week is
#: diligence; forty is a denial-of-service attempt on a government register
#: that would get the installation's IP blocked (`IP_BLOCKED` is a documented
#: VIES fault).
MAX_INITIAL_OFFSETS = 6
MAX_OFFSET_DAYS = 90

class PolicyError(ValueError):
    """An invalid policy, with a sentence saying which rule it broke."""


@dataclass(frozen=True)
class TaxIdCheckPolicy:
    """The company's check policy, validated."""

    initial_check_offsets: tuple[int, ...] = DEFAULT_INITIAL_OFFSETS
    recheck_interval_days: Optional[int] = DEFAULT_RECHECK_INTERVAL_DAYS
    max_check_age_days: Optional[int] = DEFAULT_MAX_CHECK_AGE_DAYS

    def as_dict(self) -> dict:
        return {
            "initial_check_offsets": list(self.initial_check_offsets),
            "recheck_interval_days": self.recheck_interval_days,
            "max_check_age_days": self.max_check_age_days,
        }

    def interval_for(self, party_override: Optional[int]) -> Optional[int]:
        """The interval that applies to one party.

        A party-level override of 0 is not "immediately" — it is meaningless,
        so it is treated as absent. Anything else wins over the company's.
        """
        if party_override:
            return party_override
        return self.recheck_interval_days


def validate(raw: dict) -> TaxIdCheckPolicy:
    """Turn a dict into a policy, or raise with a readable sentence."""
    unknown = set(raw) - {
        "initial_check_offsets",
        "recheck_interval_days",
        "max_check_age_days",
    }
    if unknown:
        raise PolicyError(
            f"{', '.join(sorted(unknown))} is not a setting this knows "
            "about. The settings are initial_check_offsets, "
            "recheck_interval_days and max_check_age_days."
        )

    offsets = raw.get("initial_check_offsets", list(DEFAULT_INITIAL_OFFSETS))
    if not isinstance(offsets, (list, tuple)) or not offsets:
        raise PolicyError(
            "The initial series needs at least one entry. [0] means "
            "“check it once, when it is entered”."
        )
    if len(offsets) > MAX_INITIAL_OFFSETS:
        raise PolicyError(
            f"That is {len(offsets)} checks in the first days, and the limit "
            f"is {MAX_INITIAL_OFFSETS}. Registries publish concurrency "
            "limits and block addresses that exceed them, so more checks "
            "would mean fewer answers, not better ones."
        )
    cleaned: list[int] = []
    for entry in offsets:
        if not isinstance(entry, int) or isinstance(entry, bool):
            raise PolicyError(
                f"{entry!r} is not a number of days. The initial series is a "
                "list of day offsets, for example [0, 1, 7]."
            )
        if entry < 0 or entry > MAX_OFFSET_DAYS:
            raise PolicyError(
                f"{entry} is not a usable offset. An offset is between 0 "
                f"(straight away) and {MAX_OFFSET_DAYS} days."
            )
        cleaned.append(entry)
    if sorted(cleaned) != cleaned:
        raise PolicyError(
            "The initial series has to be in order, earliest first — "
            f"{cleaned} is not. Did you mean {sorted(cleaned)}?"
        )
    if len(set(cleaned)) != len(cleaned):
        raise PolicyError(
            f"{cleaned} checks the same day twice, which would ask the "
            "registry the same question twice for the same answer."
        )

    interval = raw.get("recheck_interval_days", DEFAULT_RECHECK_INTERVAL_DAYS)
    if interval is not None:
        if not isinstance(interval, int) or isinstance(interval, bool):
            raise PolicyError(
                f"{interval!r} is not a number of days. Leave it empty to "
                "turn re-checking off."
            )
        if interval not in INTERVAL_CHOICES:
            offered = ", ".join(
                str(choice) for choice in INTERVAL_CHOICES if choice
            )
            raise PolicyError(
                f"{interval} is not one of the intervals offered ({offered}, "
                "or off). The list is deliberately short: an interval "
                "somebody typed by hand is one nobody thought about."
            )

    max_age = raw.get("max_check_age_days", DEFAULT_MAX_CHECK_AGE_DAYS)
    if max_age is not None:
        if not isinstance(max_age, int) or isinstance(max_age, bool):
            raise PolicyError(
                f"{max_age!r} is not a number of days. Leave it empty to let "
                "a check count for ever."
            )
        if max_age < 1:
            raise PolicyError(
                "A check has to count for at least a day. Leave it empty to "
                "let one count for ever."
            )

    # The combination rule. This is the one somebody actually gets wrong.
    if interval is not None and max_age is not None and max_age < interval:
        raise PolicyError(
            f"A check would stop counting after {max_age} days but would "
            f"only be repeated every {interval}, so every customer would "
            f"spend {interval - max_age} days of each cycle unverified — and "
            "an unverified EU business buyer is charged domestic VAT. Either "
            f"let a check count for at least {interval} days, or re-check at "
            f"least every {max_age}."
        )

    return TaxIdCheckPolicy(
        initial_check_offsets=tuple(cleaned),
        recheck_interval_days=interval,
        max_check_age_days=max_age,
    )


def load_policy(db: Session, *, company_id) -> TaxIdCheckPolicy:
    """The company's policy, or the defaults.

    Invalid stored data falls back to the defaults rather than raising. A
    policy row that somehow became unreadable must not make every party page
    500 — the defaults are safe, and the settings screen will refuse to save
    the bad value the moment somebody opens it.
    """
    company = db.get(Company, company_id)
    stored = (company.tax_id_check_policy or None) if company else None
    if not stored:
        return TaxIdCheckPolicy()
    try:
        return validate(dict(stored))
    except PolicyError:
        return TaxIdCheckPolicy()


def save_policy(db: Session, *, company_id, raw: dict) -> TaxIdCheckPolicy:
    """Validate and store. Caller commits (audit in the same transaction)."""
    policy = validate(raw)
    company = db.get(Company, company_id)
    if company is None:  # pragma: no cover - router scopes this already
        raise PolicyError("No such company.")
    company.tax_id_check_policy = policy.as_dict()
    return policy
