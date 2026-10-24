"""What a tax-identifier check asks for, and what it answers.

Pure: the interface and the result type, no transport and no database. The
providers that actually speak to a registry live in
`apps/api/services/verification/`, each with an injectable transport, and are
registered by data.

**Why an interface at all, rather than one function per registry.** Because
the rule that matters is the same for every registry and must be impossible
to get wrong in one of them:

    failing to OBTAIN an answer is never evidence about a number.

A timeout, a 5xx, a rate limit, a body we cannot parse, a missing
credential — all of those mean we do not know. Only a registry positively
saying "this number is not valid" is `invalid`. Getting that backwards turns
a reverse-charge invoice into a domestic one, or worse, tells a user their
good customer has a bad VAT number because a government service was busy.
With one `VerificationResult` and one set of status values, a new provider
has to choose from the same vocabulary, and the test suite asks every
provider the same seven questions.

**Five statuses, and the two new ones are not shades of `invalid`:**

  * `valid`       — the registry said yes.
  * `invalid`     — the registry said no. Only ever this.
  * `unavailable` — we could not get an answer. Try again later.
  * `not_configured` — the operator has not supplied a credential this
    registry requires. A deployment fact. Nothing about the number, and a
    self-hosted installation with no ABN GUID must not see "invalid".
  * `not_checkable` — no register exists that we may query for this kind.
    A fact about the world, not about this installation. The route to
    evidence is a human confirmation.

**"Cancelled" is a positive answer.** An ABN that exists and is cancelled, or
a company no longer VAT-registered, is `invalid` with the reason in `raw` —
the registry answered, and the answer is no.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

VALID = "valid"
INVALID = "invalid"
UNAVAILABLE = "unavailable"
NOT_CONFIGURED = "not_configured"
NOT_CHECKABLE = "not_checkable"
STATUSES = (VALID, INVALID, UNAVAILABLE, NOT_CONFIGURED, NOT_CHECKABLE)

#: The statuses that mean "we obtained an answer". Only these displace an
#: earlier verdict or advance the check schedule (D62) — which is why the
#: set is named here rather than spelled as a comparison at each call site,
#: where one of them would eventually be forgotten.
COMPLETED_STATUSES = (VALID, INVALID, NOT_CHECKABLE)


@dataclass(frozen=True)
class PartyFacts:
    """What we know about the party, for the registries that compare.

    A plain value object rather than the ORM row: a provider must not be
    able to reach the database, and a provider that was handed a `Party`
    could lazy-load half the schema inside an HTTP timeout.
    """

    name: str = ""
    street: Optional[str] = None
    postcode: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    address_text: Optional[str] = None


@dataclass(frozen=True)
class Requester:
    """Who is asking. The EU's approximate check records it.

    Empty when the company has not set its own VAT ID, and the consequence
    is visible rather than silent: the lookup is still made, the result is
    marked `simple` instead of `qualified`, and the screen says so.
    """

    country: Optional[str] = None
    vat_number: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return bool(self.country and self.vat_number)


@dataclass(frozen=True)
class VerificationResult:
    """One answer from one registry, ready to be stored.

    Immutable, because it becomes an append-only row: the evidence for a
    reverse charge. A result that could be edited between the call and the
    insert is not evidence of anything.
    """

    status: str
    provider: str
    #: The identifier as asked about, so the stored row says what it is
    #: about even if the identifier is later retired.
    value: str = ""
    kind: str = ""
    country: Optional[str] = None
    #: A sentence for a person. Every status has one, including the good
    #: ones: a chip with no explanation beside it is a chip people learn to
    #: ignore (rule 17c's reasoning, applied to a result).
    detail: str = ""
    registered_name: Optional[str] = None
    registered_address: Optional[str] = None
    #: For registries that report more than existence. `None` means "this
    #: registry does not say", which is different from `False`.
    registration_active: Optional[bool] = None
    gst_registered: Optional[bool] = None
    #: The registry's own reference for this consultation, when it gives
    #: one. VIES returns `requestIdentifier` and only on an approximate
    #: check with a valid requester.
    consultation_ref: Optional[str] = None
    #: True only when the call carried our own VAT ID as requester AND a
    #: consultation reference came back. Stored as a fact about what was
    #: requested, not inferred later from whether a field is populated.
    qualified: bool = False
    name_match: Optional[str] = None
    address_match: Optional[str] = None
    #: Everything the registry returned, verbatim, kept beside the fields
    #: lifted out of it. A reviewer in two years will want the body, not our
    #: reading of it.
    raw: dict = field(default_factory=dict)
    #: The registry's own error token, when there was one.
    error: Optional[str] = None
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(
                f"{self.status!r} is not a verification status. "
                f"The statuses are: {', '.join(STATUSES)}."
            )
        if self.provider == "manual" and self.qualified:
            # The same rule the database enforces. Here too, because this
            # object is what the service writes and the claim "a tax
            # authority confirmed this" is the one nobody may make by
            # accident (D66).
            raise ValueError(
                "A manual confirmation is never a qualified consultation."
            )

    @property
    def is_completed(self) -> bool:
        """Whether this counts as having obtained an answer."""
        return self.status in COMPLETED_STATUSES


@runtime_checkable
class TaxIdVerifier(Protocol):
    """A registry FilmBill can ask about one kind of identifier."""

    #: What goes in the stored row's `provider` column.
    name: str

    def supports(self, kind: str, country: Optional[str]) -> bool:
        """Whether this provider can answer about that kind and country."""
        ...

    def check(
        self,
        value: str,
        *,
        kind: str,
        country: Optional[str],
        party: PartyFacts,
        requester: Requester,
    ) -> VerificationResult:
        """Ask. Never raises for a registry's behaviour.

        A provider returns `unavailable` rather than raising, because every
        caller would otherwise have to translate exceptions into statuses
        and one of them would eventually translate a timeout into `invalid`.
        It may still raise for a programming error — a kind it does not
        support — which is a bug, not a registry outcome.
        """
        ...


def not_checkable(
    value: str, *, kind: str, country: Optional[str] = None, reason: str = ""
) -> VerificationResult:
    """The answer for a kind no register will answer about.

    Its own constructor because it is returned from several places and the
    sentence must be the same in all of them: a user who sees two different
    explanations for the same situation assumes one of them is a bug.
    """
    return VerificationResult(
        status=NOT_CHECKABLE,
        provider="none",
        value=value,
        kind=kind,
        country=country,
        detail=(
            reason
            or (
                "There is no register FilmBill can query for this kind of "
                "identifier, so it cannot be checked automatically. You can "
                "record a confirmation by hand instead, and it will be shown "
                "as exactly that."
            )
        ),
    )
