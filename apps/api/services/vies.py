"""Asking VIES whether a VAT identification number is valid.

**Where the request and response shapes come from.** The Commission does not
publish the REST contract: the `#/technical-information` page is a
JavaScript application with no readable text,
`.../vies/rest-api/v3/api-docs` and `.../swagger-ui.html` are both 404, and
`.../vies/rest-api/ms` does not exist. The only officially documented
contract is the SOAP WSDL
(https://ec.europa.eu/taxation_customs/vies/services/checkVatService.wsdl).

So the REST shapes below were **observed, not guessed**: two probe requests
on 2026-10-23, recorded in the P1b report. Deliberately not mapped over from
the WSDL, because the SOAP names differ between its two operations
(`name`/`address` on `checkVat`, `traderName`/`traderAddress` on
`checkVatApprox`) and picking the wrong pair would have been silent.

    POST https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number
    {"countryCode": "AT", "vatNumber": "U00000000"}

    200 {"countryCode", "vatNumber", "requestDate", "valid",
         "requestIdentifier", "name", "address", "traderName",
         "traderStreet", "traderPostalCode", "traderCity",
         "traderCompanyType", "traderNameMatch", "traderStreetMatch",
         "traderPostalCodeMatch", "traderCityMatch",
         "traderCompanyTypeMatch"}

**The trap, and the reason this module is careful.** An error comes back with
**HTTP 200** as well:

    200 {"actionSucceed": false, "errorWrappers": [{"error": "INVALID_INPUT"}]}

A client that keyed on the status code would read that as an answer, find no
`valid` field, treat the absence as false — and report a perfectly good VAT
number as **invalid**. That is the single most expensive mistake available
here: it turns a reverse-charge invoice into a domestic one because a
government service was busy. So `actionSucceed` is checked first, `valid` is
only ever read when it is genuinely present as a boolean, and every other
outcome is `unavailable`.

**`unavailable` is never `invalid`** (D56). `invalid` means VIES positively
said the number is not valid. Everything else — a timeout, a 5xx, a member
state that is not replying, a concurrency limit, a body we cannot parse — is
a failure to obtain an answer, and a failure to obtain an answer is not
evidence about a number.

**What this is not.** It is a VIES lookup of record under Article 31 of
Council Regulation (EC) No 904/2010. The WSDL's own documentation says the
result "does not in itself give a right to exempt intra-Community supplies
from Value Added Tax", and says nothing at all about a national qualified
confirmation procedure (the German *qualifizierte Bestätigungsabfrage* via
the BZSt, or FinanzOnline in Austria). FilmBill therefore does not claim this
replaces one — see SCOPE §17's question.

No authentication and no API key: the probe requests needed none.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..billing.parties import vat as vat_ids
from ..models.party import (
    VAT_INVALID,
    VAT_NOT_APPLICABLE,
    VAT_UNAVAILABLE,
    VAT_VALID,
)

logger = logging.getLogger(__name__)

#: Observed 2026-10-23. See the module docstring for why it is observed.
VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
#: The one REST endpoint that IS documented by being readable: member-state
#: availability. Not called on the hot path — a lookup that failed already
#: says which country it was asking about — but recorded here because a
#: future "is DE up?" diagnostic belongs with its sibling.
VIES_STATUS_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-status"

#: About eight seconds. Long enough for a member state having a slow day,
#: short enough that a user does not think the screen has died. VIES
#: publishes no recommended timeout at all.
TIMEOUT_SECONDS = 8.0
#: One retry, not three. The documented failure modes are concurrency
#: ceilings and a member state not replying; hammering either makes it worse,
#: and the answer is already "try again later".
RETRIES = 1

#: The fault values the WSDL's own documentation lists, plus the one observed
#: in the REST error body. **None of them means the number is invalid** — the
#: WSDL's text is explicit that they are processing failures.
VIES_ERRORS = (
    "INVALID_INPUT",
    "GLOBAL_MAX_CONCURRENT_REQ",
    "MS_MAX_CONCURRENT_REQ",
    "SERVICE_UNAVAILABLE",
    "MS_UNAVAILABLE",
    "TIMEOUT",
)

#: A sentence per fault, because "MS_UNAVAILABLE" on a screen is not an
#: explanation. German and English are the document languages; these are
#: English, like the engine's warnings, because they appear on an internal
#: screen (see `billing/tax/engine`'s note on the same split).
_ERROR_TEXTS = {
    "INVALID_INPUT": (
        "VIES rejected the request as malformed. The number was checked "
        "against its country's format first, so this usually means that "
        "country's format has changed."
    ),
    "GLOBAL_MAX_CONCURRENT_REQ": (
        "VIES is busy across all member states and did not process the "
        "request. Nothing is known about this number yet; try again shortly."
    ),
    "MS_MAX_CONCURRENT_REQ": (
        "The member state's own service is handling too many requests and "
        "did not process this one. Nothing is known about this number yet."
    ),
    "SERVICE_UNAVAILABLE": (
        "VIES itself could not be reached. Nothing is known about this "
        "number yet."
    ),
    "MS_UNAVAILABLE": (
        "The member state's VAT database is not replying. Nothing is known "
        "about this number yet — this is their service being down, not a "
        "verdict on the number."
    ),
    "TIMEOUT": (
        "The member state did not answer in time. Nothing is known about "
        "this number yet."
    ),
}

_TRANSPORT_FAILED = (
    "FilmBill could not reach the VIES service. On a self-hosted "
    "installation with no outbound internet this is expected — the party is "
    "still usable and no check is ever required to save one."
)


@dataclass(frozen=True)
class ViesResult:
    """One lookup. `status` is the only thing anything should branch on."""

    status: str
    vat_id: str
    country: str
    #: A sentence for the screen. Always set, including on success.
    detail: str = ""
    trader_name: Optional[str] = None
    trader_address: Optional[str] = None
    #: VIES's `requestIdentifier`. Empty string in the ordinary lookup, so
    #: normalised to None rather than stored as `""`.
    consultation_ref: Optional[str] = None
    request_date: Optional[str] = None
    #: Everything VIES sent, verbatim, for the audit trail and for the
    #: "differs" comparison on the screen. Never logged.
    raw: dict = field(default_factory=dict)
    #: The fault value, when there was one.
    error: Optional[str] = None


#: `(url, payload, timeout) -> (status_code, parsed body or None)`.
#:
#: Injectable because the tests must never call the real service: a suite
#: that reached out would be slow, flaky, dependent on somebody else's
#: uptime, and — for a service with concurrency ceilings — rude.
Transport = Callable[[str, dict, float], tuple[int, Any]]


def default_transport(url: str, payload: dict, timeout: float):
    """`requests`, with no secrets to leak.

    Nothing in the payload is a secret — a VAT ID is printed on invoices —
    but the response is not logged either: it carries a trader's name and
    address, and a log is a place that outlives the reason for having it.
    """
    import requests

    response = requests.post(
        url,
        json=payload,
        timeout=timeout,
        headers={"Accept": "application/json"},
    )
    try:
        return response.status_code, response.json()
    except (ValueError, json.JSONDecodeError):
        return response.status_code, None


def check_vat(
    vat_id: str,
    *,
    default_country: Optional[str] = None,
    transport: Optional[Transport] = None,
    url: str = VIES_URL,
    timeout: float = TIMEOUT_SECONDS,
    retries: int = RETRIES,
    extra: Optional[dict] = None,
) -> ViesResult:
    """Look `vat_id` up. Never raises for a network or service problem.

    Raises only `VatFormatError`, and only before anything leaves the
    machine: a malformed number costs a sentence rather than a timeout.

    Everything else comes back as a `ViesResult`, because a caller that has
    to catch three exception types to find out "we do not know" will
    eventually catch two of them.
    """
    normalised = vat_ids.normalise_and_validate(
        vat_id, default_country=default_country
    )

    if not vat_ids.is_checkable(normalised.country):
        return ViesResult(
            status=VAT_NOT_APPLICABLE,
            vat_id=normalised.full,
            country=normalised.country,
            detail=(
                f"VIES only covers EU member states, and {normalised.country} "
                "is not one, so this number cannot be checked there. It is "
                "stored as given."
            ),
        )

    send = transport or default_transport
    payload = {
        "countryCode": normalised.vies_country,
        "vatNumber": normalised.number,
    }
    if extra:
        # P1b-2: the requester and trader fields for the approximate check.
        # Merged here rather than built here, because WHICH fields and under
        # which spelling is the provider's business (and the spelling is a
        # trap — see `services/verification/vies_provider.py`). The two core
        # fields win on a collision: a caller must not be able to redirect a
        # lookup to a different number through `extra`.
        payload = {**{k: v for k, v in extra.items() if v is not None}, **payload}

    attempts = max(1, retries + 1)
    last_failure: Optional[str] = None
    for attempt in range(attempts):
        try:
            code, body = send(url, dict(payload), timeout)
        except Exception as error:  # noqa: BLE001 - every transport failure
            # Deliberately broad: a timeout, a DNS failure, a TLS error and a
            # proxy refusing all mean the same thing here, and listing the
            # exception types of whatever transport somebody injects is a
            # list that goes stale. The class of the error is logged; its
            # message is not, because a proxy error can carry a URL with
            # credentials in it.
            logger.warning(
                "VIES lookup failed at the transport layer (%s), attempt %s of %s",
                type(error).__name__,
                attempt + 1,
                attempts,
            )
            last_failure = _TRANSPORT_FAILED
            continue

        result = _interpret(normalised, code, body)
        if result.status != VAT_UNAVAILABLE or attempt == attempts - 1:
            return result
        last_failure = result.detail

    return ViesResult(
        status=VAT_UNAVAILABLE,
        vat_id=normalised.full,
        country=normalised.country,
        detail=last_failure or _TRANSPORT_FAILED,
    )


def _interpret(normalised, code: int, body: Any) -> ViesResult:
    """One HTTP exchange → a status. Where the care lives.

    The order of the checks is the whole function:

      1. a non-200 is `unavailable`;
      2. a body that is not a mapping is `unavailable`;
      3. **`actionSucceed: false` is `unavailable`** — this arrives with
         HTTP 200, which is the trap;
      4. `valid` must be genuinely a boolean. A missing or non-boolean
         `valid` is `unavailable`, never False. "The field we needed was not
         there" and "the number is bad" are different answers, and conflating
         them is how a reverse charge becomes domestic VAT.
    """
    base = dict(
        vat_id=normalised.full,
        country=normalised.country,
        raw=body if isinstance(body, dict) else {},
    )

    if code != 200:
        return ViesResult(
            status=VAT_UNAVAILABLE,
            detail=(
                f"VIES answered with HTTP {code}, so nothing is known about "
                "this number yet. Try again shortly."
            ),
            **base,
        )

    if not isinstance(body, dict):
        return ViesResult(
            status=VAT_UNAVAILABLE,
            detail=(
                "VIES answered with something this version cannot read, so "
                "nothing is known about this number yet. If it keeps "
                "happening the service's response format has changed."
            ),
            **base,
        )

    # The trap: an error arrives with HTTP 200.
    if body.get("actionSucceed") is False or body.get("errorWrappers"):
        error = _first_error(body)
        return ViesResult(
            status=VAT_UNAVAILABLE,
            error=error,
            detail=_ERROR_TEXTS.get(
                error,
                "VIES reported a problem it does not name, so nothing is "
                "known about this number yet.",
            ),
            **base,
        )

    valid = body.get("valid")
    if not isinstance(valid, bool):
        return ViesResult(
            status=VAT_UNAVAILABLE,
            detail=(
                "VIES answered without saying whether the number is valid, "
                "so nothing is known about it yet."
            ),
            **base,
        )

    trader_name = _clean(body.get("name")) or _clean(body.get("traderName"))
    trader_address = _clean(body.get("address")) or _join_address(body)
    reference = _clean(body.get("requestIdentifier"))

    if valid:
        return ViesResult(
            status=VAT_VALID,
            detail=(
                f"VIES confirmed this number on "
                f"{_clean(body.get('requestDate')) or 'the date shown'}."
            ),
            trader_name=trader_name,
            trader_address=trader_address,
            consultation_ref=reference,
            request_date=_clean(body.get("requestDate")),
            **base,
        )

    return ViesResult(
        status=VAT_INVALID,
        detail=(
            "VIES says this number is not valid. That is the member state's "
            "own answer, not a connection problem — check the number with "
            "the customer."
        ),
        consultation_ref=reference,
        request_date=_clean(body.get("requestDate")),
        **base,
    )


def _first_error(body: dict) -> Optional[str]:
    wrappers = body.get("errorWrappers")
    if isinstance(wrappers, list):
        for entry in wrappers:
            if isinstance(entry, dict) and entry.get("error"):
                return str(entry["error"])
    error = body.get("error")
    return str(error) if error else None


def _clean(value: Any) -> Optional[str]:
    """A VIES string, or None.

    VIES fills unknown fields with `"---"` — observed, not documented — and
    `""` for an absent request identifier. Both mean "nothing", and storing
    either would put three hyphens on a screen beside a party's real name.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or set(text) == {"-"}:
        return None
    return text


def _join_address(body: dict) -> Optional[str]:
    parts = [
        _clean(body.get(key))
        for key in ("traderStreet", "traderPostalCode", "traderCity")
    ]
    joined = ", ".join(part for part in parts if part)
    return joined or None
