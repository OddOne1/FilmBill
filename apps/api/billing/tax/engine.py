"""`propose_tax` — what tax FilmBill proposes for one sale, and why.

**FilmBill proposes, the user decides** (SCOPE §6.3, D19). This function is
the whole of the proposing. It takes the company's own tables as arguments
rather than reading them, so a tax decision is testable as a table of inputs
and expected outputs — which is the only form in which a tax rule is
reviewable by somebody who is not a programmer, and the reason
`apps/api/billing/` has no database in it.

**It never guesses.** No home country, no buyer country, or a resolved code
that is inactive, and the answer is `blocking: True` with a reason and no
rate at all. A plausible-looking 20 % for a country nobody configured is the
single worst thing this function could return: it would be wrong on an
invoice and look right on a screen.

**Precedence: item override > party override > rules.** [Guessing — recorded
as a decision for Mathias to confirm, SCOPE §16 D48 and §17 question 9(c).]
The item is the more specific statement: SCOPE §6.3's own example is a studio
whose advisor says "Austrian VAT regardless of the customer", and that must
hold for a customer who also carries an override. Whenever an override
changes the answer, `would_have_applied` carries what the rules alone would
have produced, so P2 can show SCOPE S8's yellow warning with the rule it
replaced.

Manual per-document and per-line overrides are NOT here. They happen in P2,
outside this function, on top of whatever it returned.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import treatments as treatment_names
from .regions import EU_MEMBER_STATES, region_of
from .vocabulary import ITEM_CLASS_SENTINEL

# ─── What goes in ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaxCodeData:
    """One tax code, as plain data.

    The service layer builds these from rows. No id: the engine answers in
    terms of the `code` a human reads, and the caller that owns the rows is
    the one that can map a code back to one.
    """

    code: str
    name: str
    rate: Decimal
    category: str
    legal_notes: Mapping[str, str] = field(default_factory=dict)
    notes_reviewed: bool = False
    is_active: bool = True


@dataclass(frozen=True)
class TaxClassData:
    """A tax class and the code it resolves to, by code."""

    key: str
    label: str
    code: str


@dataclass(frozen=True)
class TaxRuleData:
    position: int
    name: str
    conditions: Mapping[str, Any] = field(default_factory=dict)
    outcome: Mapping[str, Any] = field(default_factory=dict)
    is_active: bool = True


#: "Always use this tax code, whatever the rules say."
OVERRIDE_FIXED_CODE = "code"
#: SCOPE §6.3's second example: "use the domestic rate for foreign business
#: buyers". It applies ONLY to a business buyer in another country — for a
#: domestic sale there is nothing to override, and for a private buyer the
#: rules already answer the domestic rate.
OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS = "domestic_for_foreign_business"
OVERRIDE_KINDS: tuple[str, ...] = (
    OVERRIDE_FIXED_CODE,
    OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS,
)


@dataclass(frozen=True)
class Override:
    """A per-item or per-party tax override. `code` only for `kind="code"`."""

    kind: str
    code: Optional[str] = None


@dataclass(frozen=True)
class TaxInputs:
    """Everything the decision depends on.

    `seller_is_kleinunternehmer` last and defaulted, because it is a property
    of the company rather than of the sale and most callers will not set it.
    """

    seller_country: Optional[str]
    buyer_country: Optional[str]
    buyer_type: str = "business"
    buyer_vat_verified: bool = False
    supply_kind: str = "services"
    item_class: str = "standard"
    item_override: Optional[Override] = None
    party_override: Optional[Override] = None
    seller_is_kleinunternehmer: bool = False


# ─── What comes out ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaxWarning:
    """A warning code and the sentence that goes with it.

    Both, so the web can render the sentence without owning a copy of it and
    can still branch on the code. A warning whose text lived only in the
    frontend would be a warning the API's own consumers could not read.
    """

    code: str
    text: str


@dataclass(frozen=True)
class WouldHaveApplied:
    """What the rules alone would have produced, when an override changed it.

    Not a nested `Proposal`: everything P2's yellow warning needs is here, and
    a recursive structure would invite the question of whether the inner one
    has warnings of its own that somebody should act on.
    """

    treatment: str
    code: Optional[str]
    rate: Optional[Decimal]
    applied_rule: Optional[str]
    applied_rule_position: Optional[int]
    legal_note: Optional[str] = None


@dataclass(frozen=True)
class Proposal:
    """The answer. `blocking` means "do not use this", not "here is a guess"."""

    treatment: Optional[str]
    code: Optional[str]
    rate: Optional[Decimal]
    legal_note: Optional[str]
    applied_rule: Optional[str]
    applied_rule_position: Optional[int]
    reasons: tuple[str, ...]
    warnings: tuple[TaxWarning, ...]
    blocking: bool
    would_have_applied: Optional[WouldHaveApplied] = None

    def warning_codes(self) -> tuple[str, ...]:
        return tuple(warning.code for warning in self.warnings)


# ─── Warning texts ──────────────────────────────────────────────────────────
#
# English, and so are `reasons`. Deliberately not translated, and not the same
# decision as the legal note: the legal note goes on a CUSTOMER's invoice and
# is looked up in the document's language, while these appear on an internal
# screen beside the rest of an English admin UI. Translating half the
# application through this function would be the wrong place to start.

_WARNING_TEXTS: Mapping[str, str] = {
    "buyer_vat_id_not_verified": (
        "The buyer's VAT ID has not been verified, so a reverse charge is "
        "not proposed — an unverified ID that turns out to be wrong leaves "
        "the tax owing from you. Verify it to propose a reverse charge."
    ),
    "legal_note_not_reviewed": (
        "The legal note on this code is the generic wording FilmBill ships "
        "and has not been reviewed by anybody in this company. Check it with "
        "your tax advisor and mark it reviewed."
    ),
    "code_inactive": (
        "The tax code this resolves to is inactive, so nothing can be "
        "proposed. Set its rate and activate it first."
    ),
    "home_country_missing": (
        "This company has no country, so no tax rule can be evaluated. Set "
        "the country in Settings → Company."
    ),
    "buyer_country_missing": (
        "The buyer has no country, so there is no way to tell a domestic "
        "sale from an export. Set the buyer's country."
    ),
}


def warning_text(code: str) -> str:
    """The sentence for `code`, or the code itself if it has none."""
    return _WARNING_TEXTS.get(code, code)


def _warning(code: str) -> TaxWarning:
    return TaxWarning(code=code, text=warning_text(code))


#: Which treatment a FIXED-CODE override implies, read off the code's EN 16931
#: category. Only the unambiguous ones: `Z`, `E`, `L` and `M` can each mean
#: more than one thing (the generic pack uses `E` for both "exempt" and the
#: small-business exemption), so those land on `custom` rather than on a
#: treatment somebody might rely on.
_CATEGORY_TREATMENTS: Mapping[str, str] = {
    "S": treatment_names.DOMESTIC,
    "AE": treatment_names.EU_REVERSE_CHARGE,
    "K": treatment_names.INTRA_COMMUNITY_SUPPLY,
    "G": treatment_names.EXPORT,
    "O": treatment_names.NOT_TAXABLE_IN_COUNTRY,
}


# ─── The decision ───────────────────────────────────────────────────────────


def _blocked(reasons: Sequence[str], warnings: Sequence[TaxWarning]) -> Proposal:
    return Proposal(
        treatment=None,
        code=None,
        rate=None,
        legal_note=None,
        applied_rule=None,
        applied_rule_position=None,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        blocking=True,
    )


def _matches(rule: TaxRuleData, context: Mapping[str, Any]) -> bool:
    """Every condition in the rule holds. An empty condition set matches all.

    `is` for booleans rather than `==`: `1 == True` in Python, and a
    condition stored as `1` by some future JSON round-trip must not satisfy
    `buyer_vat_verified: true` by accident.
    """
    for key, expected in rule.conditions.items():
        actual = context.get(key)
        if isinstance(expected, bool):
            if actual is not expected:
                return False
        elif actual != expected:
            return False
    return True


def _resolve_class(
    key: str, classes: Mapping[str, TaxClassData]
) -> Optional[TaxClassData]:
    return classes.get(key)


def propose_tax(
    inputs: TaxInputs,
    codes: Iterable[TaxCodeData],
    classes: Iterable[TaxClassData],
    rules: Iterable[TaxRuleData],
    lang: str = "en",
    *,
    eu_member_states: frozenset[str] = EU_MEMBER_STATES,
) -> Proposal:
    """Propose the tax for one sale. Pure; no I/O, no session, no clock."""
    by_code = {entry.code: entry for entry in codes}
    by_class = {entry.key: entry for entry in classes}
    reasons: list[str] = []

    # ── The two facts without which nothing can be decided ──────────────
    if not inputs.seller_country or not inputs.seller_country.strip():
        return _blocked(
            ["This company has no country, so no rule could be evaluated."],
            [_warning("home_country_missing")],
        )
    seller = inputs.seller_country.strip().upper()
    reasons.append(f"The seller is in {seller}.")

    region = region_of(
        inputs.seller_country,
        inputs.buyer_country,
        eu_member_states=eu_member_states,
    )
    if region is None:
        reasons.append("The buyer has no country.")
        return _blocked(reasons, [_warning("buyer_country_missing")])

    buyer = inputs.buyer_country.strip().upper()
    where = {
        "same_country": f"in {buyer}, the same country",
        "eu_other": f"in {buyer}, another EU country",
        "non_eu": f"in {buyer}, outside the EU",
    }[region]
    reasons.append(
        f"The buyer is a {inputs.buyer_type} {where}."
    )
    if inputs.buyer_type == "business":
        reasons.append(
            "The buyer's VAT ID is verified."
            if inputs.buyer_vat_verified
            else "The buyer's VAT ID is not verified."
        )
    reasons.append(f"The supply is {inputs.supply_kind}.")
    if inputs.seller_is_kleinunternehmer:
        reasons.append("This company is registered as a small business.")

    context = {
        "seller_is_kleinunternehmer": inputs.seller_is_kleinunternehmer,
        "buyer_region": region,
        "buyer_type": inputs.buyer_type,
        "buyer_vat_verified": inputs.buyer_vat_verified,
        "supply_kind": inputs.supply_kind,
    }

    # ── What the rules alone say ────────────────────────────────────────
    rules_outcome = _from_rules(inputs, context, by_code, by_class, rules, lang)

    # ── Overrides, item first ───────────────────────────────────────────
    for source, override in (
        ("item", inputs.item_override),
        ("party", inputs.party_override),
    ):
        if override is None:
            continue
        overridden = _from_override(
            override, source, inputs, region, by_code, by_class, lang
        )
        if overridden is None:
            # The override does not apply to this sale — a "domestic rate for
            # foreign business buyers" on a domestic sale, say. Fall through
            # to the next one, and then to the rules, rather than blocking:
            # an override that does not apply is not an error.
            reasons.append(
                f"The {source} override does not apply to this sale, so the "
                "rules decide."
            )
            continue
        return _combine(overridden, rules_outcome, reasons, by_code, lang)

    return _combine(rules_outcome, None, reasons, by_code, lang)


@dataclass(frozen=True)
class _Outcome:
    """An intermediate answer, before reasons and warnings are assembled."""

    treatment: str
    code: Optional[TaxCodeData]
    applied_rule: Optional[str]
    applied_rule_position: Optional[int]
    reason: str
    #: A warning the deciding rule itself declared.
    rule_warning: Optional[str] = None
    #: Set when nothing could be resolved at all.
    failure: Optional[str] = None


def _from_rules(
    inputs: TaxInputs,
    context: Mapping[str, Any],
    by_code: Mapping[str, TaxCodeData],
    by_class: Mapping[str, TaxClassData],
    rules: Iterable[TaxRuleData],
    lang: str,
) -> _Outcome:
    """The first active rule whose conditions all hold.

    Ordered by position and then by name, so two rules at the same position
    resolve the same way on every machine — a tie broken by row order is a
    tax decision that depends on the query plan.
    """
    ordered = sorted(
        (rule for rule in rules if rule.is_active),
        key=lambda rule: (rule.position, rule.name),
    )
    for rule in ordered:
        if not _matches(rule, context):
            continue

        target = rule.outcome.get("code") or {}
        treatment = rule.outcome.get("treatment")
        resolved, failure = _target_code(target, inputs.item_class, by_code, by_class)
        return _Outcome(
            treatment=treatment,
            code=resolved,
            applied_rule=rule.name,
            applied_rule_position=rule.position,
            reason=f"Rule {rule.position} “{rule.name}” applies.",
            rule_warning=rule.outcome.get("warning"),
            failure=failure,
        )

    return _Outcome(
        treatment=None,
        code=None,
        applied_rule=None,
        applied_rule_position=None,
        reason="No rule matched this combination.",
        failure=(
            "No tax rule matched this sale, so nothing is proposed. The "
            "shipped rules cover every combination — a gap means a rule has "
            "been edited or deactivated."
        ),
    )


def _target_code(
    target: Mapping[str, Any],
    item_class: str,
    by_code: Mapping[str, TaxCodeData],
    by_class: Mapping[str, TaxClassData],
) -> tuple[Optional[TaxCodeData], Optional[str]]:
    """`{class: ...}` or `{code: ...}` → a code, or a sentence saying why not."""
    if "code" in target:
        wanted = target["code"]
        code = by_code.get(wanted)
        if code is None:
            return None, (
                f"This resolves to tax code “{wanted}”, which this company "
                "does not have. Nothing is proposed."
            )
        return code, None

    wanted_class = target.get("class")
    if wanted_class == ITEM_CLASS_SENTINEL:
        wanted_class = item_class
    tax_class = _resolve_class(wanted_class, by_class)
    if tax_class is None:
        return None, (
            f"This resolves to the tax class “{wanted_class}”, which this "
            "company does not have. Nothing is proposed."
        )
    code = by_code.get(tax_class.code)
    if code is None:
        return None, (
            f"Tax class “{tax_class.key}” points at code “{tax_class.code}”, "
            "which this company does not have. Nothing is proposed."
        )
    return code, None


def _from_override(
    override: Override,
    source: str,
    inputs: TaxInputs,
    region: str,
    by_code: Mapping[str, TaxCodeData],
    by_class: Mapping[str, TaxClassData],
    lang: str,
) -> Optional[_Outcome]:
    """What an override says, or None when it does not apply to this sale."""
    if override.kind == OVERRIDE_FIXED_CODE:
        code = by_code.get(override.code or "")
        if code is None:
            return _Outcome(
                treatment=treatment_names.CUSTOM,
                code=None,
                applied_rule=f"override:{source}",
                applied_rule_position=None,
                reason=(
                    f"The {source} override names tax code "
                    f"“{override.code}”, which this company does not have."
                ),
                failure=(
                    f"The {source} override names tax code “{override.code}”, "
                    "which this company does not have. Nothing is proposed."
                ),
            )
        return _Outcome(
            treatment=_CATEGORY_TREATMENTS.get(code.category, treatment_names.CUSTOM),
            code=code,
            applied_rule=f"override:{source}",
            applied_rule_position=None,
            reason=(
                f"The {source} override fixes this to tax code "
                f"“{code.code}”, whatever the rules say."
            ),
        )

    if override.kind == OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS:
        if region == "same_country" or inputs.buyer_type != "business":
            return None
        code, failure = _target_code(
            {"class": ITEM_CLASS_SENTINEL}, inputs.item_class, by_code, by_class
        )
        return _Outcome(
            treatment=treatment_names.DOMESTIC,
            code=code,
            applied_rule=f"override:{source}",
            applied_rule_position=None,
            reason=(
                f"The {source} override says to use the domestic rate for "
                "foreign business buyers."
            ),
            failure=failure,
        )

    return _Outcome(
        treatment=treatment_names.CUSTOM,
        code=None,
        applied_rule=f"override:{source}",
        applied_rule_position=None,
        reason=f"The {source} override is of an unknown kind.",
        failure=(
            f"The {source} override is of an unknown kind "
            f"({override.kind!r}). Nothing is proposed."
        ),
    )


def _rate_text(rate: Decimal) -> str:
    """A rate as a human writes it: `20`, `8.1667`, `0`.

    Normalised, because the same rate arrives with two different scales
    depending on where it came from: a region pack says `Decimal("20")` and a
    `NUMERIC(18,4)` column says `Decimal("20.0000")`. Both are 20 %, and a
    sentence that read "applies at 20.0000 %" in one path and "at 20 %" in
    the other is a difference a reader would try to interpret. `format(…,
    "f")` after `normalize()` because `normalize` alone turns 20 into the
    scientific `2E+1`.
    """
    return format(rate.normalize(), "f")


def _note(code: Optional[TaxCodeData], lang: str) -> Optional[str]:
    """The legal note in `lang`, or none.

    No fallback to another language, deliberately. A German invoice carrying
    an English sentence is a mistake somebody has to notice; a missing one is
    a gap the document template can show as missing.
    """
    if code is None:
        return None
    return code.legal_notes.get(lang) or None


def _combine(
    chosen: _Outcome,
    rules_outcome: Optional[_Outcome],
    reasons: list[str],
    by_code: Mapping[str, TaxCodeData],
    lang: str,
) -> Proposal:
    """Assemble the proposal: reasons, warnings, blocking, would_have_applied."""
    reasons = list(reasons)
    reasons.append(chosen.reason)

    warnings: list[TaxWarning] = []
    if chosen.rule_warning:
        warnings.append(_warning(chosen.rule_warning))

    if chosen.failure or chosen.code is None:
        reasons.append(chosen.failure or "Nothing could be resolved.")
        return _blocked(reasons, warnings)

    code = chosen.code
    if not code.is_active:
        reasons.append(
            f"Tax code “{code.code}” is inactive, so nothing is proposed."
        )
        warnings.append(_warning("code_inactive"))
        return _blocked(reasons, warnings)

    note = _note(code, lang)
    if code.legal_notes and not code.notes_reviewed:
        warnings.append(_warning("legal_note_not_reviewed"))

    reasons.append(
        f"Tax code “{code.code}” applies at {_rate_text(code.rate)} %."
    )

    would: Optional[WouldHaveApplied] = None
    if rules_outcome is not None:
        rules_code = rules_outcome.code.code if rules_outcome.code else None
        if (rules_code, rules_outcome.treatment) != (code.code, chosen.treatment):
            would = WouldHaveApplied(
                treatment=rules_outcome.treatment,
                code=rules_code,
                rate=rules_outcome.code.rate if rules_outcome.code else None,
                applied_rule=rules_outcome.applied_rule,
                applied_rule_position=rules_outcome.applied_rule_position,
                legal_note=_note(rules_outcome.code, lang),
            )

    return Proposal(
        treatment=chosen.treatment,
        code=code.code,
        rate=code.rate,
        legal_note=note,
        applied_rule=chosen.applied_rule,
        applied_rule_position=chosen.applied_rule_position,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        blocking=False,
        would_have_applied=would,
    )
