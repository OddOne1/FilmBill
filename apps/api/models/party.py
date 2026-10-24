"""Parties: customers, suppliers, partners, and the people inside them.

SCOPE §3. One table for all of them, because a party is one thing that wears
several hats: the production company that hires you this month invoices you
the next, and duplicating it as "customer" and "supplier" is how two records
of the same company drift apart. `is_customer` / `is_supplier` /
`is_partner` are flags, not types.

**The tree, and why it is only three deep.** `organisation` → `department` →
`person`. An organisation has no parent; a department belongs to an
organisation; a person belongs to an organisation, a department, or nothing
at all. That is the shape SCOPE §12's S5 scenario needs — invoice the ORF,
addressed to a named editor in a named department — and it is deliberately
not a general tree: an arbitrary hierarchy makes "which address does this
invoice use" a question with no bounded answer, and the resolver would have
to walk an unknown number of levels to find out.

**Nothing is ever deleted** (D51(d), and the same reasoning): a party that
appears on an issued document is part of that document's explanation. There
is no DELETE route and there is not going to be one; `is_active` and an
archive action do the work.

**The resolver does not live here.** `apps/api/billing/parties/` resolves a
chain of these into the one recipient a document would use, as plain value
objects with no session behind them — so a document snapshot is a value and
not a lazy-loading proxy, and inheritance can be tested as a table.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from ..database import Base
except ImportError:  # pragma: no cover - direct-import path for alembic
    from database import Base

try:
    from .company import CompanyScoped
except ImportError:  # pragma: no cover
    from models.company import CompanyScoped


#: The three levels of the tree. Plain strings rather than a Postgres enum,
#: for the same reason as `bookkeeping_mode` and `number_series.reset`: the
#: schema's `Literal` already puts the union into OpenAPI and therefore into
#: the generated TypeScript, while an enum type would cost a migration to
#: add a value and buy nothing.
PARTY_ORGANISATION = "organisation"
PARTY_DEPARTMENT = "department"
PARTY_PERSON = "person"
PARTY_TYPES = (PARTY_ORGANISATION, PARTY_DEPARTMENT, PARTY_PERSON)

#: Which parent each type may have. The service enforces it (a row-level
#: CHECK cannot read the parent's type without a join); the CHECK below
#: covers the one case that needs no join, and
#: `services/party_service.validate_parent` covers the rest with a sentence.
ALLOWED_PARENTS: dict[str, tuple[str, ...]] = {
    PARTY_ORGANISATION: (),
    PARTY_DEPARTMENT: (PARTY_ORGANISATION,),
    PARTY_PERSON: (PARTY_ORGANISATION, PARTY_DEPARTMENT),
}

#: How a party's buyer type is decided. `auto` derives it; the other two say
#: so explicitly and win over the derivation (SCOPE §3, D55).
BUSINESS_AUTO = "auto"
BUSINESS_BUSINESS = "business"
BUSINESS_PRIVATE = "private"
BUSINESS_STATUSES = (BUSINESS_AUTO, BUSINESS_BUSINESS, BUSINESS_PRIVATE)

ADDRESS_KINDS = ("billing", "delivery", "postal")
CONTACT_KINDS = ("email", "phone")
CONTACT_PURPOSES = ("general", "invoice")

#: What a VIES lookup can come back as.
#:
#: `unavailable` and `invalid` are kept strictly apart, and that separation
#: is the single most important thing in this module: a failure to OBTAIN an
#: answer is never evidence that a number is bad. Treating a timeout as
#: `invalid` would make FilmBill charge domestic VAT on a reverse-charge
#: invoice because a government service was busy.
VAT_VALID = "valid"
VAT_INVALID = "invalid"
VAT_UNAVAILABLE = "unavailable"
VAT_NOT_APPLICABLE = "not_applicable"
#: P1b-2 adds two. `not_configured` is a deployment fact, not a finding: the
#: operator has not supplied the credential a registry requires, and saying
#: "invalid" there would be a lie about somebody's tax number. `not_checkable`
#: is a fact about the world: no register exists that we may query.
VAT_NOT_CONFIGURED = "not_configured"
VAT_NOT_CHECKABLE = "not_checkable"
VAT_STATUSES = (
    VAT_VALID,
    VAT_INVALID,
    VAT_UNAVAILABLE,
    VAT_NOT_APPLICABLE,
    VAT_NOT_CONFIGURED,
    VAT_NOT_CHECKABLE,
)

#: Kinds of tax identifier a party can carry (SCOPE D59). Typed rather than
#: free text because what may be done with one depends entirely on which it
#: is: a `vat` can be confirmed against VIES and can carry a reverse charge,
#: an `abn` can be looked up but means nothing for EU VAT, and an `ein` has
#: no register anyone may query. One column called `vat_id` could express
#: none of that, which is why P1b's single column is gone.
TAX_ID_VAT = "vat"
TAX_ID_UID_CH = "uid_ch"
TAX_ID_ABN = "abn"
TAX_ID_EIN = "ein"
TAX_ID_TAX_NUMBER = "tax_number"
TAX_ID_COMPANY_REGISTER = "company_register"
TAX_ID_OTHER = "other"
TAX_ID_KINDS = (
    TAX_ID_VAT,
    TAX_ID_UID_CH,
    TAX_ID_ABN,
    TAX_ID_EIN,
    TAX_ID_TAX_NUMBER,
    TAX_ID_COMPANY_REGISTER,
    TAX_ID_OTHER,
)

#: The kinds that can stand for the buyer's identity in a tax decision.
#: Mirrors `vat_like_kinds` in `region_packs/tax_id_formats.yaml`; the data
#: file is the source and `billing/parties/tax_ids.py` asserts they agree,
#: because two lists that drift would make the resolver and the validator
#: disagree about which ID is the one that counts.
VAT_LIKE_KINDS = (TAX_ID_VAT, TAX_ID_UID_CH, TAX_ID_ABN)

#: Why a check ran. `initial` is one of the first-days series, `scheduled`
#: the periodic one, `retry` a second attempt after a failure to reach the
#: registry, `on_change` triggered by the ID's value changing, `manual` a
#: person pressing the button or recording a confirmation by hand.
TRIGGER_MANUAL = "manual"
TRIGGER_INITIAL = "initial"
TRIGGER_SCHEDULED = "scheduled"
TRIGGER_RETRY = "retry"
TRIGGER_ON_CHANGE = "on_change"
TRIGGER_KINDS = (
    TRIGGER_MANUAL,
    TRIGGER_INITIAL,
    TRIGGER_SCHEDULED,
    TRIGGER_RETRY,
    TRIGGER_ON_CHANGE,
)

#: How a registry's name compares with the party's own. `unknown` is its own
#: value and not a synonym for "no": several member states return a
#: placeholder instead of a name, and reporting that as a mismatch would
#: have users correcting data that was never wrong.
NAME_MATCH_MATCH = "match"
NAME_MATCH_PARTIAL = "partial"
NAME_MATCH_MISMATCH = "mismatch"
NAME_MATCH_UNKNOWN = "unknown"
NAME_MATCH_RESULTS = (
    NAME_MATCH_MATCH,
    NAME_MATCH_PARTIAL,
    NAME_MATCH_MISMATCH,
    NAME_MATCH_UNKNOWN,
)

#: Address comparison is informational only, so it has three values and no
#: `partial`: an address either agrees, differs, or was not returned.
ADDRESS_MATCH_RESULTS = ("match", "differs", "unknown")

#: `manual` is a provider name like any other, deliberately: a human
#: confirmation is stored in the same append-only table as a registry answer
#: so that one query returns the whole history, and the provider column is
#: what keeps the two from ever being mistaken for each other.
PROVIDER_MANUAL = "manual"


class Party(CompanyScoped, Base):
    """A customer, a supplier, a partner — or a department or person in one."""

    __tablename__ = "parties"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: `RESTRICT`, not `CASCADE`: deleting an organisation out from under its
    #: department is not a thing that should be possible, and nothing is
    #: deleted here anyway. The archive guard refuses while children are
    #: active, which is the reachable version of the same rule.
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # ── Identity ─────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trading_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    legal_form: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    register_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    register_court: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: Person-only, and not enforced as such: a `position` on a department
    #: ("Redaktion Kultur") is harmless, and a CHECK that refused it would be
    #: a rule somebody works around by putting it in the name.
    title: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    position: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Hats ─────────────────────────────────────────────────────────
    is_customer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_supplier: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_partner: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: A Postgres array, so Part 3's tag filter can use `&&` and `@>` rather
    #: than a LIKE over a joined string.
    #:
    #: `.with_variant(JSON, "sqlite")` because one test file
    #: (`test_email_case_insensitive.py`) builds the whole schema on an
    #: in-memory SQLite to test an email lookup, and SQLite cannot render
    #: ARRAY at all — `create_all` failed with "can't render element of type
    #: ARRAY" and took nine unrelated tests with it. The variant keeps the
    #: real column a real array and keeps that file working.
    tags: Mapped[Optional[list]] = mapped_column(
        ARRAY(String(64)).with_variant(JSON, "sqlite"),
        nullable=False,
        server_default="{}",
    )

    #: Allocated from the `customer` or `supplier` number series the first
    #: time the party is flagged as one, and editable afterwards — a company
    #: migrating from another system has numbers of its own. Unique per
    #: company WHEN SET, which is a partial index: several parties with no
    #: number at all is the normal state.
    party_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # ── How we invoice them ──────────────────────────────────────────
    language: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    default_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    payment_terms_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payment_terms_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    po_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Tax ──────────────────────────────────────────────────────────
    business_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=BUSINESS_AUTO
    )
    #: Where they are for tax. Separate from the billing address's country
    #: because they can differ — an Austrian branch of a German company bills
    #: to Vienna and is taxed in Germany — and because the resolver needs one
    #: field to answer `buyer_country` with.
    tax_country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    #: P1b's single `vat_id` column is gone. A party's identifiers live in
    #: `party_tax_ids`, because one column could not say which KIND of
    #: identifier it held, and the whole of P1b-2 turns on that difference
    #: (D59). The resolver asks for the primary active VAT-like ID.
    #:
    #: Free text, shown on the Tax tab. The case it exists for: an Austrian
    #: Kleinunternehmer is a business, invoices as a business, and holds no
    #: UID at all — so "business, and here is why there is no number" has to
    #: be sayable, or somebody will invent a VAT ID to make a form accept
    #: them (D65).
    tax_status_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Per-party override of the company's re-check interval, in days.
    #: NULL means "use the company policy". A customer whose registration is
    #: known to be in flux can be watched more closely without moving every
    #: other party with it.
    recheck_interval_days: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    #: Exactly the shape the engine's `party_override` takes —
    #: `{"kind": "code", "code": "AT-20"}` or
    #: `{"kind": "domestic_for_foreign_business"}` — so the resolver can hand
    #: it straight to `propose_tax` without a translation layer that could
    #: disagree with the one in the tax router.
    tax_override: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    #: `{peppol_id, leitweg_id, at_einvoice_id}`, all optional. A dict rather
    #: than three columns because the list grows per country and per scheme,
    #: and P2/P5 are where it is read.
    einvoice_routing: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    parent = relationship("Party", remote_side=[id], backref="children")

    __table_args__ = (
        CheckConstraint(
            "type IN ('organisation', 'department', 'person')",
            name="ck_parties_type",
        ),
        CheckConstraint(
            "business_status IN ('auto', 'business', 'private')",
            name="ck_parties_business_status",
        ),
        # The one parent rule that needs no join. The other two are the
        # service's, with a sentence — see ALLOWED_PARENTS.
        CheckConstraint(
            "type <> 'organisation' OR parent_id IS NULL",
            name="ck_parties_organisation_has_no_parent",
        ),
        CheckConstraint("id <> parent_id", name="ck_parties_not_its_own_parent"),
        Index(
            "uq_parties_company_number",
            "company_id",
            "party_number",
            unique=True,
            postgresql_where=text("party_number IS NOT NULL"),
        ),
        Index("ix_parties_company_parent", "company_id", "parent_id"),
        Index("ix_parties_company_name", "company_id", "name"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Party {self.type} {self.name!r}>"


class PartyAddress(CompanyScoped, Base):
    """A billing, delivery or postal address.

    Several per kind is allowed with one default each, because a production
    company has three delivery addresses and one of them is the usual one.
    """

    __tablename__ = "party_addresses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    street: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    street2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zip: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('billing', 'delivery', 'postal')",
            name="ck_party_addresses_kind",
        ),
        # One default per party and kind, as a PARTIAL unique index: several
        # non-default addresses of the same kind are the normal case.
        Index(
            "uq_party_addresses_default_per_kind",
            "party_id",
            "kind",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index("ix_party_addresses_party", "party_id", "kind"),
    )


class PartyContactPoint(CompanyScoped, Base):
    """An email address or a phone number, with what it is for.

    `purpose` matters because "the invoice goes to accounts payable and the
    questions go to the editor" is the normal arrangement, and sending an
    invoice to the editor is the mistake this column prevents.
    """

    __tablename__ = "party_contact_points"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="general"
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('email', 'phone')", name="ck_party_contact_points_kind"
        ),
        CheckConstraint(
            "purpose IN ('general', 'invoice')",
            name="ck_party_contact_points_purpose",
        ),
        Index(
            "uq_party_contact_points_primary",
            "party_id",
            "kind",
            "purpose",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("ix_party_contact_points_party", "party_id"),
        Index("ix_party_contact_points_value", "company_id", "value"),
    )


class PartyBankAccount(CompanyScoped, Base):
    """Where a supplier is paid. Normalised exactly like the company's own.

    Same normalisation as `CompanyBankAccount` — compact, upper case — and
    the same reason: one IBAN written two ways is two rows that look like two
    accounts. Masked in audit rows like the company's (D43's reasoning
    applies to a supplier's account just as much).
    """

    __tablename__ = "party_bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    iban: Mapped[str] = mapped_column(String(34), nullable=False)
    bic: Mapped[Optional[str]] = mapped_column(String(11), nullable=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "party_id", "iban", name="uq_party_bank_accounts_party_iban"
        ),
        Index(
            "uq_party_bank_accounts_default",
            "party_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )


class PartyTaxId(CompanyScoped, Base):
    """One typed tax identifier belonging to a party (SCOPE D59).

    **Why this replaced a column.** P1b gave a party one `vat_id`. That was
    wrong in three ways at once, and each has a customer behind it: a Swiss
    company has a UID and no EU VAT ID, an Australian one an ABN, and an
    Austrian Kleinunternehmer is a business with no number at all. A single
    untyped column cannot say which of those it is holding, so nothing
    downstream could decide what the number was good for — and "what is this
    number good for" is the only question that matters. `kind` answers it.

    **At most one primary active VAT-like ID per party.** The engine takes
    one buyer VAT ID; if a party had two, which one reached the engine would
    depend on row order, which is the kind of silence that produces a wrong
    invoice nobody can explain. Enforced by a partial unique index rather
    than in the service, because the service is not the only writer — the
    migration and the seed script are too.

    **Retired, never deleted** (CLAUDE.md, D50). A retired ID keeps its
    checks, which are the evidence for invoices already issued; deleting it
    would delete the reason a finalized document says what it says.
    """

    __tablename__ = "party_tax_ids"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Where the number is from. For `vat` it is the prefix's country, which
    #: is not always the party's `tax_country` — a German company registered
    #: for VAT in Austria holds an AT number — and the check must use the
    #: number's country, not the party's.
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    #: Normalised per kind on write: upper case, separators stripped, the
    #: Swiss MWST/TVA/IVA suffix removed. Stored normalised so that
    #: uniqueness means what a human means by it — `ATU1234 5678` and
    #: `atu12345678` are the same number, and a party should not be able to
    #: hold both and have one of them silently win.
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    #: What the user typed, kept verbatim. Normalisation is lossy and people
    #: recognise their own formatting; showing them back `CHE109322551` when
    #: they typed `CHE-109.322.551 MWST` makes them doubt it was saved.
    value_as_entered: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: When the value changes we retire the old row and create a new one
    #: pointing back at it, so the history reads as a chain rather than as
    #: two unrelated numbers. P2 needs that chain to explain an old invoice.
    replaces_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("party_tax_ids.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Scheduling state ─────────────────────────────────────────────
    #: How many of the initial series have completed. The series is
    #: "check it now, again tomorrow, again in a week" (D62) and exists
    #: because a registry that has not caught up with a new registration
    #: answers "invalid" to a number that is perfectly good.
    initial_checks_done: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: When the scheduler should next look. NULL means never — a kind with
    #: no provider is not scheduled at all, which is how a US EIN avoids
    #: generating an hourly attempt that can only fail.
    next_check_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Consecutive failures to REACH the registry. Separate from any verdict
    #: about the number, and reset by any completed check.
    unavailable_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: Set when a check differs from the one before it in a way that a human
    #: should see (D63). Cleared only by an explicit acknowledgement with a
    #: note, never by a later check — a change that resolved itself still
    #: happened, and somebody should know it did.
    needs_attention: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    needs_attention_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    party = relationship("Party", backref="tax_ids")

    __table_args__ = (
        CheckConstraint(
            "kind IN ('vat', 'uid_ch', 'abn', 'ein', 'tax_number', "
            "'company_register', 'other')",
            name="ck_party_tax_ids_kind",
        ),
        CheckConstraint("value <> ''", name="ck_party_tax_ids_value_not_empty"),
        CheckConstraint(
            "initial_checks_done >= 0 AND unavailable_streak >= 0",
            name="ck_party_tax_ids_counters_not_negative",
        ),
        # The same (kind, value) twice on one party is a duplicate, not two
        # facts. Retired rows are included on purpose: re-adding a number
        # someone retired should reactivate that row and keep its checks,
        # not start a second history of the same number.
        Index(
            "uq_party_tax_ids_party_kind_value",
            "party_id",
            "kind",
            "value",
            unique=True,
        ),
        # One primary ACTIVE VAT-like ID per party. Partial, because a party
        # may well hold a retired VAT ID and a current one, and because
        # non-VAT-like kinds may each have their own primary.
        Index(
            "uq_party_tax_ids_primary_vat_like",
            "party_id",
            unique=True,
            postgresql_where=text(
                "is_primary AND is_active "
                "AND kind IN ('vat', 'uid_ch', 'abn')"
            ),
        ),
        # The scheduler's query: due rows, oldest first. Partial so the
        # index holds only rows that can ever be due.
        Index(
            "ix_party_tax_ids_due",
            "next_check_at",
            postgresql_where=text("next_check_at IS NOT NULL AND is_active"),
        ),
        Index("ix_party_tax_ids_party", "party_id", "kind"),
        Index(
            "ix_party_tax_ids_attention",
            "company_id",
            postgresql_where=text("needs_attention"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PartyTaxId {self.kind}:{self.value}>"


class PartyVatCheck(CompanyScoped, Base):
    """One VIES lookup, kept for ever. Append-only at the database level.

    **Why append-only.** This row is the evidence for a reverse charge: it
    says that on this date, this number was valid according to the member
    state's own database. A record that can be edited afterwards is not
    evidence of anything — the same reasoning as `audit_events`, and the same
    triggers. What that does NOT stop is a table owner disabling the trigger
    or a superuser doing as they like; the claim is tamper-evident against
    the application, not tamper-proof (D44).

    **`status` keeps `unavailable` and `invalid` strictly apart.** A failure
    to obtain an answer is never evidence that a number is bad.
    """

    __tablename__ = "party_vat_checks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Which identifier this was a check OF. Nullable only so the P1b rows
    #: the migration repoints have somewhere to land if a party's ID cannot
    #: be reconstructed; every row written from P1b-2 on has it.
    tax_id_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("party_tax_ids.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The number AS CHECKED, not a reference to the party's current one.
    #: That is the whole point: changing the VAT ID must not silently make an
    #: old check look like evidence for the new number. Kept even though
    #: `tax_id_id` now exists, and deliberately so — the FK is `SET NULL` on
    #: delete, and evidence that forgets what it was about is not evidence.
    vat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="vat"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Which provider answered — `vies`, `abn`, or `manual`. Stored rather
    #: than derived from `kind`, because a kind may gain a provider later and
    #: an old row must keep saying who actually answered it.
    provider: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="vies"
    )
    trigger_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="manual"
    )
    #: True only when the call carried our own VAT ID as requester AND the
    #: registry returned a consultation reference. Everything else is
    #: `simple`. The distinction is the one a tax advisor will ask about, so
    #: it is stored as a fact about what was requested rather than inferred
    #: afterwards from whether a field happens to be populated.
    qualified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: What the registry has on file, and how it compares. `registered_name`
    #: is stored verbatim; the comparison is stored beside it so a later
    #: change to the matching rules cannot retroactively alter what a
    #: reviewer was shown at the time.
    registered_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registered_address: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    name_match: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    address_match: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )
    #: For registries that report more than existence: the ABN's status and
    #: whether it was GST-registered on the day we asked.
    registration_active: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    gst_registered: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    #: A human confirmation's own fields. `valid_until` is the only date in
    #: this table that is a claim about the future, and it is a person's
    #: claim, not a registry's.
    manual_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_reference: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    valid_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when this check differed from its predecessor, with the diff. The
    #: acknowledgement is recorded here too, so "who saw this and what did
    #: they say" is one row and cannot drift from the check it concerns.
    change_detected: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    #: Everything the service returned, verbatim — the trader name and
    #: address as the member state has them, which is what a reviewer
    #: compares against the party's own.
    response: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    #: VIES's `requestIdentifier`, when the call asked for one. Empty string
    #: in the ordinary lookup, so stored nullable and left NULL rather than
    #: stored as `""`.
    consultation_ref: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    checked_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('valid', 'invalid', 'unavailable', 'not_applicable', "
            "'not_configured', 'not_checkable')",
            name="ck_party_vat_checks_status",
        ),
        CheckConstraint(
            "trigger_kind IN ('manual', 'initial', 'scheduled', 'retry', "
            "'on_change')",
            name="ck_party_vat_checks_trigger_kind",
        ),
        CheckConstraint(
            "name_match IS NULL OR name_match IN ('match', 'partial', "
            "'mismatch', 'unknown')",
            name="ck_party_vat_checks_name_match",
        ),
        CheckConstraint(
            "address_match IS NULL OR address_match IN ('match', 'differs', "
            "'unknown')",
            name="ck_party_vat_checks_address_match",
        ),
        # A manual confirmation is never a qualified consultation. Enforced
        # here and not only in the service because the claim "a tax
        # authority confirmed this" is exactly the one nobody may make by
        # accident, and the service is not the only writer.
        CheckConstraint(
            "provider <> 'manual' OR qualified = false",
            name="ck_party_vat_checks_manual_is_not_qualified",
        ),
        Index("ix_party_vat_checks_party", "party_id", "checked_at"),
        Index("ix_party_vat_checks_tax_id", "tax_id_id", "checked_at"),
    )


#: The append-only triggers, written exactly like `audit_events`'.
#:
#: **No `%` anywhere in these strings, including the comments.** SQLAlchemy's
#: `DDL` runs `statement % context`, so a stray percent sign is a
#: `ValueError` at table-create time — which surfaced in P0b-3 as forty-five
#: unrelated setup errors.
APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION party_vat_checks_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = 'party_vat_checks is append-only; '
                  || TG_OP || ' is not permitted on it',
        ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

APPEND_ONLY_TRIGGERS = """
CREATE TRIGGER party_vat_checks_no_update_delete
    BEFORE UPDATE OR DELETE ON party_vat_checks
    FOR EACH ROW EXECUTE FUNCTION party_vat_checks_append_only();

CREATE TRIGGER party_vat_checks_no_truncate
    BEFORE TRUNCATE ON party_vat_checks
    FOR EACH STATEMENT EXECUTE FUNCTION party_vat_checks_append_only();
"""

APPEND_ONLY_TRIGGER_NAMES = (
    "party_vat_checks_no_update_delete",
    "party_vat_checks_no_truncate",
)

event.listen(
    PartyVatCheck.__table__,
    "after_create",
    DDL(APPEND_ONLY_FUNCTION + APPEND_ONLY_TRIGGERS).execute_if(
        dialect="postgresql"
    ),
)
