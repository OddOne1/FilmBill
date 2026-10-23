"""Resolving a person → department → organisation chain into one recipient.

SCOPE §3 and §12's S5 scenario: an invoice to the ORF, addressed to a named
editor in a named department, uses the editor's NAME and the organisation's
billing address and VAT ID — unless the department has its own.

**Pure, like `billing/tax/`.** No session, no ORM objects, no clock. The
chain goes in as plain value objects and a plain value object comes out, for
three reasons that all matter later:

  1. **inheritance is testable as a table.** "Which address does this invoice
     use" is the question with the most ways to be subtly wrong, and a table
     of chains and expected sources is the only form in which somebody who is
     not a programmer can review it;
  2. **the result is a snapshot.** P2 stores it on a finalized document, and
     a detached value object is a thing that keeps saying what it said. A
     lazy-loading ORM row would silently change when the organisation moved
     office — which is precisely what rule 3 forbids, and there is a test
     for it here;
  3. **the engine's inputs come from it.** `tax_inputs` produces exactly the
     fields `propose_tax` takes, so there is one translation from a party to
     a tax decision and not one per caller.

**Every resolved field carries its source level**, so the screen can grey an
inherited value and name where it came from ("from ORF — organisation"). A
value with no provenance is a value somebody has to guess the origin of, and
on an invoice that guess is expensive.
"""

from .vat import (
    FORMATS_FILE,
    ISO_GREECE,
    VIES_GREECE,
    NormalisedVatId,
    VatFormatError,
    is_checkable,
    iso_country,
    known_countries,
    normalise,
    normalise_and_validate,
    validate_format,
    vies_country,
)
from .resolver import (
    LEVELS,
    LEVEL_DEPARTMENT,
    LEVEL_ORGANISATION,
    LEVEL_PERSON,
    PartyAddressValue,
    PartyLevel,
    Resolved,
    ResolvedField,
    ResolvedRecipient,
    BUYER_REASONS,
    recipient_buyer_type,
    resolve_recipient,
    tax_inputs,
)

__all__ = [
    "BUYER_REASONS",
    "FORMATS_FILE",
    "ISO_GREECE",
    "NormalisedVatId",
    "VIES_GREECE",
    "VatFormatError",
    "is_checkable",
    "iso_country",
    "known_countries",
    "normalise",
    "normalise_and_validate",
    "validate_format",
    "vies_country",
    "LEVELS",
    "LEVEL_DEPARTMENT",
    "LEVEL_ORGANISATION",
    "LEVEL_PERSON",
    "PartyAddressValue",
    "PartyLevel",
    "Resolved",
    "ResolvedField",
    "ResolvedRecipient",
    "recipient_buyer_type",
    "resolve_recipient",
    "tax_inputs",
]
