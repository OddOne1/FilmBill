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
VAT_STATUSES = (VAT_VALID, VAT_INVALID, VAT_UNAVAILABLE, VAT_NOT_APPLICABLE)


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
    #: Normalised on write: no spaces, dots or dashes, upper case. Stored with
    #: its country prefix, as everybody writes it.
    vat_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
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
    #: The number AS CHECKED, not a reference to the party's current one.
    #: That is the whole point: changing the VAT ID must not silently make an
    #: old check look like evidence for the new number.
    vat_id: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
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
            "status IN ('valid', 'invalid', 'unavailable', 'not_applicable')",
            name="ck_party_vat_checks_status",
        ),
        Index("ix_party_vat_checks_party", "party_id", "checked_at"),
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
