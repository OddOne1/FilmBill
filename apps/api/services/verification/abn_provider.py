"""The Australian Business Register provider.

**Endpoint and documentation.** `https://abr.business.gov.au/json/AbnDetails.aspx`
with the query parameters `abn`, `callback` and `guid`, documented at
https://abr.business.gov.au/json/ ("We currently provide limited JSON
support for ABN and name searching", https://abr.business.gov.au/Tools/WebServices,
read 2026-10-24). The response-field names come from the official data
dictionary at https://abr.business.gov.au/Documentation/DataDictionary; the
check-digit rule from https://abr.business.gov.au/Help/AbnFormat.

The SOAP service at `/abrxmlsearch/abrxmlsearch.asmx` is the fuller
interface. JSON is used here because it needs no XML stack, and because the
three fields FilmBill wants — entity name, ABN status, GST registration —
are all in it.

**It is JSONP, not JSON.** The response is `callback({...})`, so the body has
to be unwrapped. A client that fed it straight to a JSON parser would get a
parse error and — if it were careless — call that `invalid`. It is not: an
unparseable body means we do not know.

**GST registration is expressed as date ranges, not a flag.** The official
data dictionary gives `goodsAndServicesTax` exactly two fields,
`effectiveFrom` and `effectiveTo`, and the element is `maxOccurs="unbounded"`
— the ABR even publishes a test ABN for the "multiple GST status" case
(76 093 555 992). So "is this entity GST-registered today" is a computation
over periods, and `9999-12-31` is how the register writes "still current".
Reading a single boolean out of it would be wrong for any entity that has
ever deregistered and registered again.

**Terms.** Free, with a registration that emails an authentication GUID
(https://abr.business.gov.au/Documentation/WebServiceRegistration). The
agreement at https://abr.business.gov.au/Tools/WebServicesAgreement permits
automated use, permits passing extracts to third parties "at your own risk",
publishes no rate limit, and imposes one hard obligation: on notice that a
record has been withdrawn, "you must immediately take all reasonable action
to delete all copies of that information in your possession or control".
Nothing in it addresses open-source redistribution; the licence is
"non-transferable", which is why **the GUID is an operator-supplied
environment variable and FilmBill ships none**. Without it the provider
returns `not_configured` with instructions — never an error, and never
`invalid`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any, Callable, Optional

from ...billing.parties import name_match
from ...billing.parties.verification import (
    INVALID,
    NOT_CONFIGURED,
    UNAVAILABLE,
    VALID,
    PartyFacts,
    Requester,
    VerificationResult,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME = "abn"
ABN_URL = "https://abr.business.gov.au/json/AbnDetails.aspx"
#: Same as VIES's, and for the same reason: a user pressing a button waits
#: for this, and eight seconds is already a long time to watch a spinner.
TIMEOUT_SECONDS = 8.0
RETRIES = 1
#: The environment variable an operator sets. Named for what it is so that
#: `grep ABN_LOOKUP_GUID` finds the documentation as well as the code.
GUID_ENV = "ABN_LOOKUP_GUID"

#: What the register writes for "no end date". Treated as open-ended rather
#: than as a date in the year 9999, which would otherwise be compared and
#: happen to work until somebody wrote a smarter comparison.
_OPEN_ENDED = ("9999-12-31", "0001-01-01", "", None)

#: `callback({...})` or `callback({...});`
_JSONP = re.compile(r"^[A-Za-z_$][\w$]*\s*\(\s*(?P<body>.*?)\s*\)\s*;?\s*$", re.S)

Transport = Callable[[str, float], tuple[int, str]]


def default_transport(url: str, timeout: float) -> tuple[int, str]:
    """One GET. Returns the status and the raw body text.

    Text rather than parsed JSON, because the body is JSONP and the
    unwrapping belongs with the code that knows that — a transport that
    parsed would have to know the format too.
    """
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "FilmBill"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


class AbnProvider:
    """Asks the Australian Business Register about an ABN."""

    name = PROVIDER_NAME

    def __init__(
        self,
        transport: Optional[Transport] = None,
        *,
        guid: Optional[str] = None,
        url: str = ABN_URL,
    ) -> None:
        self._transport = transport or default_transport
        #: Read at construction, so a test can pass one explicitly and an
        #: installation reads the environment. `None` means not configured,
        #: which is a status rather than an error.
        self._guid = guid if guid is not None else os.environ.get(GUID_ENV)
        self._url = url

    def supports(self, kind: str, country: Optional[str]) -> bool:
        # The country is irrelevant: an ABN is an ABN. Accepting it for any
        # country is deliberate — a party registered in Australia with a
        # `tax_country` of NZ still has a real ABN.
        return kind == "abn"

    def check(
        self,
        value: str,
        *,
        kind: str,
        country: Optional[str],
        party: PartyFacts,
        requester: Requester,
    ) -> VerificationResult:
        if kind != "abn":
            raise ValueError(
                f"AbnProvider was asked about {kind!r}. The registry picks "
                "the provider; this is a wiring bug."
            )

        if not self._guid:
            return VerificationResult(
                status=NOT_CONFIGURED,
                provider=self.name,
                value=value,
                kind=kind,
                country=country or "AU",
                detail=(
                    "Checking an ABN needs a free authentication GUID from "
                    "the Australian Business Register. Register at "
                    "abr.business.gov.au/Documentation/WebServiceRegistration "
                    "— they email the GUID — and set it as "
                    f"{GUID_ENV} in the environment. Until then this number "
                    "is stored as given and is not checked; nothing about it "
                    "is in doubt."
                ),
            )

        query = urllib.parse.urlencode(
            {"abn": value, "callback": "callback", "guid": self._guid}
        )
        url = f"{self._url}?{query}"

        attempts = max(1, RETRIES + 1)
        last_detail = ""
        for attempt in range(attempts):
            try:
                status_code, text = self._transport(url, TIMEOUT_SECONDS)
            except Exception as error:  # noqa: BLE001 - see services/vies.py
                # Broad on purpose, and the message is never logged: the URL
                # carries the GUID, and a transport error often quotes the
                # URL back. The class is enough to diagnose with.
                logger.warning(
                    "ABN lookup failed at the transport layer (%s), "
                    "attempt %s of %s",
                    type(error).__name__,
                    attempt + 1,
                    attempts,
                )
                last_detail = (
                    "The Australian Business Register could not be reached. "
                    "This says nothing about the number — it will be tried "
                    "again."
                )
                continue

            result = self._interpret(value, country, party, status_code, text)
            if result.status != UNAVAILABLE or attempt == attempts - 1:
                return result
            last_detail = result.detail

        return VerificationResult(
            status=UNAVAILABLE,
            provider=self.name,
            value=value,
            kind=kind,
            country=country or "AU",
            detail=last_detail,
        )

    def _interpret(
        self,
        value: str,
        country: Optional[str],
        party: PartyFacts,
        status_code: int,
        text: str,
    ) -> VerificationResult:
        """Turn a response into a result. Order matters.

        Non-200 first, then the JSONP unwrapping, then the register's own
        exception field, and only then the ABN record. Each of those is a
        way of failing to get an answer, and every one of them must land on
        `unavailable` rather than on a verdict about the number.
        """
        base = dict(
            provider=self.name, value=value, kind="abn", country=country or "AU"
        )

        if status_code != 200:
            return VerificationResult(
                status=UNAVAILABLE,
                detail=(
                    f"The Australian Business Register answered with HTTP "
                    f"{status_code}. That is a problem with the service, not "
                    "with this number."
                ),
                raw={"_status_code": status_code},
                **base,
            )

        body = _unwrap_jsonp(text)
        if body is None:
            return VerificationResult(
                status=UNAVAILABLE,
                detail=(
                    "The register's answer could not be read. It is normally "
                    "JSONP — callback({...}) — and this was not. Nothing "
                    "about the number is in doubt."
                ),
                raw={"_unparsed": text[:500]},
                **base,
            )

        # The register reports its own failures inside a 200 body, exactly
        # as VIES does. `Message` is what the JSON endpoint uses; the SOAP
        # service calls it `exceptionDescription`.
        message = _clean(body.get("Message")) or _clean(
            body.get("exceptionDescription")
        )
        abn = _clean(body.get("Abn"))

        if message and not abn:
            lowered = message.lower()
            if "guid" in lowered or "unauthor" in lowered or "credential" in lowered:
                # A rejected GUID is a configuration problem, not a finding.
                return VerificationResult(
                    status=NOT_CONFIGURED,
                    detail=(
                        "The Australian Business Register rejected the "
                        f"authentication GUID: “{message}”. Check "
                        f"{GUID_ENV} in the environment. This says nothing "
                        "about the number."
                    ),
                    raw=dict(body),
                    **base,
                )
            if "not" in lowered and (
                "valid" in lowered or "found" in lowered or "exist" in lowered
            ):
                # The register positively says there is no such ABN. That is
                # an answer, and the only shape of answer that is `invalid`.
                return VerificationResult(
                    status=INVALID,
                    detail=(
                        f"The Australian Business Register has no record of "
                        f"{value}: “{message}”. That is the register's own "
                        "answer."
                    ),
                    raw=dict(body),
                    **base,
                )
            return VerificationResult(
                status=UNAVAILABLE,
                detail=(
                    f"The register replied with a message rather than a "
                    f"record: “{message}”. Nothing about the number is in "
                    "doubt."
                ),
                raw=dict(body),
                **base,
            )

        if not abn:
            return VerificationResult(
                status=UNAVAILABLE,
                detail=(
                    "The register's answer contained no ABN at all, so it "
                    "could not be read as a result."
                ),
                raw=dict(body),
                **base,
            )

        entity_name = _clean(body.get("EntityName")) or _clean(
            body.get("OrganisationName")
        )
        abn_status = (_clean(body.get("AbnStatus")) or "").lower()
        gst_from = _clean(body.get("Gst"))
        active = abn_status == "active"
        gst_registered = _gst_is_current(gst_from, _clean(body.get("GstTo")))

        comparison = name_match.compare_names(party.name, entity_name)
        address_text = " ".join(
            part
            for part in (
                _clean(body.get("AddressState")),
                _clean(body.get("AddressPostcode")),
            )
            if part
        )
        address_result = name_match.compare_addresses(
            party.address_text, address_text or None
        )

        if not active:
            # "Cancelled" is a positive answer: the register knows this ABN
            # and says it is not current. An invoice relying on it would be
            # relying on a registration that has ended.
            return VerificationResult(
                status=INVALID,
                detail=(
                    f"The register has this ABN but its status is "
                    f"“{abn_status or 'unknown'}”, not active. That is the "
                    "register's own answer — the number exists and is no "
                    "longer current."
                ),
                registered_name=entity_name,
                registered_address=address_text or None,
                registration_active=False,
                gst_registered=gst_registered,
                name_match=comparison.result,
                address_match=address_result,
                raw=dict(body),
                **base,
            )

        detail = (
            f"The Australian Business Register confirms {value} is active"
            + (
                " and registered for GST."
                if gst_registered
                else ". It is not shown as registered for GST, which matters "
                "for Australian tax but not for whether the number is real."
            )
            + f" {comparison.detail}"
        )
        return VerificationResult(
            status=VALID,
            detail=detail.strip(),
            registered_name=entity_name,
            registered_address=address_text or None,
            registration_active=True,
            gst_registered=gst_registered,
            name_match=comparison.result,
            address_match=address_result,
            # No consultation reference: the ABR does not issue one, so
            # `qualified` stays False for every ABN check. It is an EU
            # concept and pretending otherwise would overstate the evidence.
            raw=dict(body),
            **base,
        )


def _unwrap_jsonp(text: str) -> Optional[dict]:
    """`callback({...})` → the dict, or None if it is not that.

    Tries bare JSON too, because the endpoint answers with plain JSON when
    no `callback` is given and a future caller may well omit it.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    match = _JSONP.match(stripped)
    candidate = match.group("body") if match else stripped
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean(value: Any) -> Optional[str]:
    """A trimmed string, or None for anything that is not usable.

    The ABR returns `""` for absent fields rather than omitting them, and an
    empty string compared as a name is a name that matches nothing.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _gst_is_current(
    gst_from: Optional[str], gst_to: Optional[str] = None
) -> Optional[bool]:
    """Whether the entity is GST-registered today, or None if unknowable.

    `None` rather than `False` when the register says nothing: "we do not
    know" and "they are not registered" are different facts, and the second
    one affects an Australian invoice.

    The register writes an open-ended period as `9999-12-31`, which is why
    that is a named constant rather than a date comparison that happens to
    work.
    """
    if not gst_from:
        return None
    start = _parse_date(gst_from)
    if start is None:
        return None
    today = date.today()
    if start > today:
        return False
    if gst_to in _OPEN_ENDED:
        return True
    end = _parse_date(gst_to)
    if end is None:
        return True
    return end >= today


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None
