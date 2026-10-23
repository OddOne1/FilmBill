"""The inheritance rule, as one function over plain data.

**The rule** (SCOPE §3): each field is the first non-empty value walking
person → department → organisation. Two fields are called out separately in
SCOPE because getting them wrong is expensive rather than untidy:

  * the **billing address** and the **VAT ID** come from the nearest level
    that has them, which is the same rule — but they are the two that decide
    what the invoice says and what tax it carries, so they have their own
    tests and their own mutations;
  * the **contact name** stays the person's. It is the one field that does
    NOT inherit: an invoice addressed to "ORF" when it should say "Maria
    Huber, Redaktion Kultur" goes to a post room instead of to the person who
    ordered the work, and nobody notices for a month.

**Empty means empty.** A blank string, whitespace, `None` and an address with
no street and no city all count as absent, so a half-filled department does
not shadow a complete organisation. That is the shape of the bug this
function exists to prevent: the department row exists, so a naive
`department or organisation` picks it, and the invoice goes out with a city
and no street.
"""

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional, Sequence

#: The three levels, outermost last. The order is the lookup order.
LEVEL_PERSON = "person"
LEVEL_DEPARTMENT = "department"
LEVEL_ORGANISATION = "organisation"
LEVELS = (LEVEL_PERSON, LEVEL_DEPARTMENT, LEVEL_ORGANISATION)

#: Why a recipient counts as a business or a private person. Closed, because
#: the screen renders a sentence per reason and a reason nothing renders is a
#: reason nobody sees.
BUYER_REASONS = (
    #: `business_status` was set to `business` or `private` by hand.
    "explicit",
    #: An organisation (or a department of one) is a business.
    "organisation",
    #: A person with a VAT ID — a sole trader.
    "person_with_vat_id",
    #: A person without one.
    "person",
)


@dataclass(frozen=True)
class PartyAddressValue:
    """An address as plain data. `is_empty` is the whole reason it is a type."""

    street: Optional[str] = None
    street2: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None

    def is_empty(self) -> bool:
        """No street AND no city.

        Not "every field blank": an address with only a country on it is a
        country, not an address, and letting it shadow the organisation's
        real one is how an invoice goes out addressed to "Austria".
        """
        return not _present(self.street) and not _present(self.city)


@dataclass(frozen=True)
class PartyLevel:
    """One party in the chain, as the resolver sees it.

    Plain data and nothing else: the service builds these from rows, which is
    what keeps a session out of this module. `level` is the party's own type,
    so a person with no parent resolves from itself and still reports
    `person` as the source.
    """

    level: str
    id: Any = None
    name: Optional[str] = None
    trading_name: Optional[str] = None
    billing_address: Optional[PartyAddressValue] = None
    postal_address: Optional[PartyAddressValue] = None
    vat_id: Optional[str] = None
    tax_country: Optional[str] = None
    business_status: str = "auto"
    tax_override: Optional[Mapping[str, Any]] = None
    language: Optional[str] = None
    default_currency: Optional[str] = None
    payment_terms_days: Optional[int] = None
    payment_terms_text: Optional[str] = None
    po_reference: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    einvoice_routing: Optional[Mapping[str, Any]] = None
    #: Whether the latest VAT check for THIS level's current VAT ID said
    #: `valid`. Computed by the service (P1b part 2) and carried here so the
    #: resolver can report it beside the VAT ID it belongs to — a verified
    #: flag that travelled separately from its number is a flag that
    #: eventually describes a different number.
    vat_verified: bool = False


@dataclass(frozen=True)
class ResolvedField:
    """A value and where it came from. Both, always."""

    value: Any = None
    source: Optional[str] = None

    def inherited_from(self, own_level: str) -> bool:
        return self.source is not None and self.source != own_level


@dataclass(frozen=True)
class ResolvedRecipient:
    """What a document would use. Detached, immutable, snapshot-ready.

    Immutable on purpose: P2 stores one of these on a finalized document, and
    a value object that could be edited afterwards would be a snapshot in
    name only (rule 3).
    """

    #: The level the chain started at — whose detail page this is.
    own_level: str = LEVEL_PERSON
    #: `person`/`department`/`organisation` → the id at that level, for the
    #: screen's "from ORF — organisation" link.
    ids: Mapping[str, Any] = field(default_factory=dict)
    names: Mapping[str, Any] = field(default_factory=dict)

    contact_name: ResolvedField = field(default_factory=ResolvedField)
    invoice_name: ResolvedField = field(default_factory=ResolvedField)
    billing_address: ResolvedField = field(default_factory=ResolvedField)
    vat_id: ResolvedField = field(default_factory=ResolvedField)
    vat_verified: ResolvedField = field(default_factory=ResolvedField)
    tax_country: ResolvedField = field(default_factory=ResolvedField)
    business_status: ResolvedField = field(default_factory=ResolvedField)
    tax_override: ResolvedField = field(default_factory=ResolvedField)
    language: ResolvedField = field(default_factory=ResolvedField)
    default_currency: ResolvedField = field(default_factory=ResolvedField)
    payment_terms_days: ResolvedField = field(default_factory=ResolvedField)
    payment_terms_text: ResolvedField = field(default_factory=ResolvedField)
    po_reference: ResolvedField = field(default_factory=ResolvedField)
    email: ResolvedField = field(default_factory=ResolvedField)
    phone: ResolvedField = field(default_factory=ResolvedField)
    einvoice_routing: ResolvedField = field(default_factory=ResolvedField)

    def field(self, name: str) -> ResolvedField:
        value = getattr(self, name, None)
        return value if isinstance(value, ResolvedField) else ResolvedField()


#: Alias, because `ResolvedRecipient` is a mouthful at a call site.
Resolved = ResolvedRecipient


def _present(value: Any) -> bool:
    """Whether a scalar counts as filled in.

    A blank string and whitespace are absent, which is not pedantry: a form
    that posts `""` for an untouched box would otherwise let an empty
    department shadow a complete organisation.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def _first(chain: Sequence[PartyLevel], attribute: str) -> ResolvedField:
    """The first level with something in `attribute`, and which one it was."""
    for level in chain:
        value = getattr(level, attribute, None)
        if _present(value):
            return ResolvedField(value=value, source=level.level)
    return ResolvedField()


def _first_address(chain: Sequence[PartyLevel], attribute: str) -> ResolvedField:
    """The first level with a real address in `attribute`.

    "Real" is `PartyAddressValue.is_empty` — no street and no city is not an
    address. Without that, a department created for its name alone would
    shadow the organisation's address and the invoice would go out with a
    postcode and nothing else.
    """
    for level in chain:
        value = getattr(level, attribute, None)
        if value is not None and not value.is_empty():
            return ResolvedField(value=value, source=level.level)
    return ResolvedField()


def resolve_recipient(chain: Sequence[PartyLevel]) -> ResolvedRecipient:
    """One recipient from a person → department → organisation chain.

    `chain` is ordered innermost first and may be any length from one to
    three: a person with no parent is a chain of one and resolves from
    itself. Levels that are not in the chain are simply absent — there is no
    placeholder to confuse with an empty one.
    """
    if not chain:
        raise ValueError(
            "resolve_recipient needs at least the party itself. An empty "
            "chain is a programming error, not a recipient with no data."
        )

    own = chain[0]
    by_level = {level.level: level for level in chain}

    # The contact name does NOT inherit. It is the person's, or — for a
    # chain that starts at a department or an organisation — that level's own
    # name, because there is no person to name.
    contact_name = ResolvedField(value=own.name, source=own.level) if _present(
        own.name
    ) else ResolvedField()

    # The name the invoice is MADE OUT to is the outermost one: an invoice to
    # a person in a department of the ORF is an invoice to the ORF, addressed
    # to that person. Both are carried, because a document needs both lines.
    outermost = chain[-1]
    invoice_name = (
        ResolvedField(value=outermost.name, source=outermost.level)
        if _present(outermost.name)
        else ResolvedField()
    )

    billing = _first_address(chain, "billing_address")
    vat = _first(chain, "vat_id")
    # The verified flag travels WITH the VAT ID it belongs to, from the same
    # level. Resolved separately it would eventually describe a different
    # number than the one on the invoice.
    verified_source = by_level.get(vat.source) if vat.source else None
    vat_verified = ResolvedField(
        value=bool(verified_source.vat_verified) if verified_source else False,
        source=vat.source,
    )

    return ResolvedRecipient(
        own_level=own.level,
        ids={level.level: level.id for level in chain},
        names={level.level: level.name for level in chain},
        contact_name=contact_name,
        invoice_name=invoice_name,
        billing_address=billing,
        vat_id=vat,
        vat_verified=vat_verified,
        # Where they are for tax: the nearest level that says, and failing
        # that the billing address's country — which is the only other thing
        # in the chain that could answer it.
        tax_country=_tax_country(chain, billing),
        business_status=_business_status(chain),
        tax_override=_first(chain, "tax_override"),
        language=_first(chain, "language"),
        default_currency=_first(chain, "default_currency"),
        payment_terms_days=_first(chain, "payment_terms_days"),
        payment_terms_text=_first(chain, "payment_terms_text"),
        po_reference=_first(chain, "po_reference"),
        email=_first(chain, "email"),
        phone=_first(chain, "phone"),
        einvoice_routing=_first(chain, "einvoice_routing"),
    )


def _business_status(chain: Sequence[PartyLevel]) -> ResolvedField:
    """The first level that actually SAYS, or `auto` from the party itself.

    `business_status` cannot use the ordinary "first non-empty" rule, and
    finding that out was worth a test: the column has a NOT NULL default of
    `auto`, so every level has a value and the innermost one always wins —
    which meant a person inside an organisation marked `private` came back
    as a business. `auto` is not a value, it is the absence of one ("nobody
    has said"), so inheritance has to skip it.
    """
    for level in chain:
        status = (level.business_status or "auto").strip()
        if status and status != "auto":
            return ResolvedField(value=status, source=level.level)
    return ResolvedField(value="auto", source=chain[0].level)


def _tax_country(
    chain: Sequence[PartyLevel], billing: ResolvedField
) -> ResolvedField:
    explicit = _first(chain, "tax_country")
    if explicit.source is not None:
        return explicit
    if billing.value is not None and _present(billing.value.country):
        return ResolvedField(value=billing.value.country, source=billing.source)
    return ResolvedField()


def recipient_buyer_type(resolved: ResolvedRecipient) -> tuple[str, str]:
    """`(business | private, reason)`.

    The rule (SCOPE D55): `auto` means **business if this is an organisation
    or has a VAT ID**, and a person without one is private. `business` and
    `private` set by hand win over the derivation.

    A sole trader is the case that makes the VAT ID clause necessary: a
    person with a VAT ID is a business for every tax rule in the engine, and
    treating them as private would charge them domestic VAT on a supply that
    should reverse-charge. Whether that is the right reading is §17's new
    question for the Steuerberater.
    """
    status = resolved.business_status.value or "auto"
    if status in ("business", "private"):
        return status, "explicit"
    # An organisation, or anything inside one: a department of the ORF is as
    # much a business as the ORF.
    if resolved.own_level in (LEVEL_ORGANISATION, LEVEL_DEPARTMENT):
        return "business", "organisation"
    if LEVEL_ORGANISATION in resolved.ids or LEVEL_DEPARTMENT in resolved.ids:
        return "business", "organisation"
    if _present(resolved.vat_id.value):
        return "business", "person_with_vat_id"
    return "private", "person"


def tax_inputs(resolved: ResolvedRecipient) -> dict:
    """Exactly the buyer-side fields `propose_tax` takes.

    One translation from a party to a tax decision, here, rather than one per
    caller. The keys are the engine's own argument names so a caller cannot
    spell `buyer_vat_verified` as `vat_verified` and have it silently default
    to False — which would turn every reverse charge into domestic tax.

    `party_override` comes through in the engine's own shape, because that is
    what `parties.tax_override` stores (see the model): no translation layer
    that could disagree with the one in the tax router.
    """
    buyer_type, reason = recipient_buyer_type(resolved)
    override = resolved.tax_override.value
    return {
        "buyer_country": (resolved.tax_country.value or None),
        "buyer_type": buyer_type,
        "buyer_vat_verified": bool(resolved.vat_verified.value),
        "party_override": dict(override) if override else None,
        # Not an engine argument — carried so a screen can say WHY the buyer
        # type is what it is without deriving it a second time.
        "buyer_type_reason": reason,
    }
