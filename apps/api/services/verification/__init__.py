"""Which provider answers about which kind of identifier.

Registered by data, not by a chain of `if`s: a kind gains or loses a
provider when a register opens or closes an interface, and rule 7 says that
is data. The table below is the whole mapping.

**A kind with no provider is `not_checkable`, and that is a decision with a
source in each case:**

  * `uid_ch` — no official programmatic interface to the Swiss UID register
    could be found (every documented path redirects to an error page, no
    terms for automated querying are published), so FilmBill does not query
    it at all rather than scraping a government register under unknown
    terms. Format checking still applies: the check digit comes from
    eCH-0097 (D61).
  * `ein` — no public register anyone may query for a third party's EIN, and
    no published check digit.
  * `tax_number`, `company_register`, `other` — per-country, mostly not
    public, and a company's own accountant is the authority.
  * `vat` outside the EU, including `GB` since Brexit — VIES does not cover
    it. HMRC publishes its own API; nobody has asked for it yet, and adding
    one speculatively would mean shipping a provider nobody has tested
    against a real number.

For all of those the route to evidence is `manual-confirmation`, recorded as
a human confirmation and never dressed up as a registry answer (D66).
"""

from __future__ import annotations

from typing import Optional

from ...billing.parties.verification import (
    PartyFacts,
    Requester,
    TaxIdVerifier,
    VerificationResult,
    not_checkable,
)
from .abn_provider import AbnProvider
from .vies_provider import ViesProvider

__all__ = [
    "AbnProvider",
    "ViesProvider",
    "PartyFacts",
    "Requester",
    "VerificationResult",
    "provider_for",
    "verify",
]


def _providers(
    vies_transport=None, abn_transport=None, abn_guid: Optional[str] = None
) -> tuple[TaxIdVerifier, ...]:
    """The providers, in the order they are offered a kind.

    Built per call rather than held as a module-level list, because each one
    takes an injectable transport and a module-level instance would make the
    test suite's fakes global state. The objects are cheap — they hold a
    callable and a string.
    """
    return (
        ViesProvider(transport=vies_transport),
        AbnProvider(transport=abn_transport, guid=abn_guid),
    )


def provider_for(
    kind: str,
    country: Optional[str],
    *,
    vies_transport=None,
    abn_transport=None,
    abn_guid: Optional[str] = None,
) -> Optional[TaxIdVerifier]:
    """The provider for this kind and country, or None.

    None means `not_checkable`, which is a legitimate answer and not a
    failure — see the module docstring for why each kind has none.
    """
    for provider in _providers(vies_transport, abn_transport, abn_guid):
        if provider.supports(kind, country):
            return provider
    return None


def verify(
    value: str,
    *,
    kind: str,
    country: Optional[str],
    party: Optional[PartyFacts] = None,
    requester: Optional[Requester] = None,
    vies_transport=None,
    abn_transport=None,
    abn_guid: Optional[str] = None,
) -> VerificationResult:
    """Check one identifier with whichever provider covers it.

    The single entry point, so the "no provider" case is answered in one
    place and in the same words every time. A caller that had to handle
    `None` itself would eventually handle it as an error, and an error looks
    like a problem with the number.
    """
    provider = provider_for(
        kind,
        country,
        vies_transport=vies_transport,
        abn_transport=abn_transport,
        abn_guid=abn_guid,
    )
    if provider is None:
        return not_checkable(value, kind=kind, country=country)
    return provider.check(
        value,
        kind=kind,
        country=country,
        party=party or PartyFacts(),
        requester=requester or Requester(),
    )
