"""The EU VIES provider, with requester data when we have our own VAT ID.

Builds on `services/vies.py` (P1b), which owns the transport, the observed
REST shapes and the HTTP-200-error trap. This adds the three things P1b-2
needs: the requester fields, the trader fields for the approximate check,
and the name comparison.

**Where the request shape comes from, and the trap in it.** The Commission
still publishes no REST contract — `/rest-api/v3/api-docs` and
`/swagger-ui/index.html` are both 404 and the technical-information page is a
JavaScript application with no readable text. The only documented contract is
the SOAP WSDL, which names the approximate check's fields
`requesterCountryCode`, `requesterVatNumber`, `traderName`, `traderStreet`,
**`traderPostcode`**, `traderCity`.

Those names do not work over REST, and the way they fail is the dangerous
one. Observed with curl on 2026-10-24:

    {"countryCode":"DE","vatNumber":"115235681",
     "requesterMemberStateCode":"AT","requesterNumber":"U99999999", ...}
    → 200 {"actionSucceed": false,
           "errorWrappers": [{"error": "INVALID_REQUESTER_INFO"}]}

    the same request with the SOAP spelling
    (requesterCountryCode / requesterVatNumber)
    → 200 {..., "valid": true, "requestIdentifier": "",
           "traderNameMatch": "NOT_PROCESSED"}

So REST reads `requesterMemberStateCode` / `requesterNumber`, and **silently
ignores the SOAP spelling**: the request goes through as a plain lookup that
looks like it worked. Had we mapped the WSDL's names over, every check would
have been stored as `qualified` with an empty consultation reference and no
matching done — a claim about evidence we never obtained, and nothing would
have failed. The REST response also uses `traderPostalCode` where the WSDL
says `traderPostcode`. A test pins both spellings.

**`qualified` is not a synonym for "we sent a requester".** It is true only
when a consultation reference came back. If the Commission ignores our
requester for any reason, the result says `simple`, because what matters is
what we can show a tax advisor — not what we intended to ask.

**What this is not.** A VIES lookup of record under Article 31 of Regulation
(EC) 904/2010. The WSDL's own text says a result "does not in itself give a
right to exempt intra-Community supplies from Value Added Tax", and FilmBill
claims nothing more — see SCOPE §17 question 10(a).
"""

from __future__ import annotations

from typing import Optional

from ...billing.parties import name_match
from ...billing.parties.vat import is_checkable, vies_country
from ...billing.parties.verification import (
    INVALID,
    UNAVAILABLE,
    VALID,
    PartyFacts,
    Requester,
    VerificationResult,
)
from .. import vies

#: The countries VIES answers for. `XI` is Northern Ireland, which stayed in
#: the EU VAT area for goods; `GB` is deliberately absent.
PROVIDER_NAME = "vies"


class ViesProvider:
    """Asks VIES, and compares the name it returns."""

    name = PROVIDER_NAME

    def __init__(self, transport: Optional[vies.Transport] = None) -> None:
        #: Injected, so every test in the suite runs against a fake and
        #: none of them can reach the network. The default is only ever
        #: used in a running installation.
        self._transport = transport

    def supports(self, kind: str, country: Optional[str]) -> bool:
        if kind != "vat":
            return False
        if not country:
            # No country means we cannot tell whether VIES covers it. The
            # normaliser refuses a VAT ID with no prefix and no default, so
            # this is a defensive branch rather than a reachable one.
            return False
        return is_checkable(country)

    def check(
        self,
        value: str,
        *,
        kind: str,
        country: Optional[str],
        party: PartyFacts,
        requester: Requester,
    ) -> VerificationResult:
        if not self.supports(kind, country):
            raise ValueError(
                f"ViesProvider was asked about {kind!r} in {country!r}, "
                "which it does not support. The registry picks the provider; "
                "this is a wiring bug."
            )

        trader = {}
        requester_fields = {}
        if requester.is_complete:
            # The approximate check. Only sent together with the requester,
            # because VIES rejects trader fields without one
            # (INVALID_REQUESTER_INFO) and a rejected request is an
            # `unavailable` we did not need.
            requester_fields = {
                # The REST names, observed. NOT the WSDL's
                # requesterCountryCode/requesterVatNumber, which REST
                # ignores in silence — see the module docstring.
                "requesterMemberStateCode": vies_country(requester.country),
                "requesterNumber": _strip_prefix(
                    requester.vat_number, requester.country
                ),
            }
            trader = {
                "traderName": party.name or None,
                "traderStreet": party.street or None,
                # REST spells this `traderPostalCode`; the WSDL says
                # `traderPostcode`. Observed, and pinned by a test.
                "traderPostalCode": party.postcode or None,
                "traderCity": party.city or None,
            }
            trader = {k: v for k, v in trader.items() if v}

        answer = vies.check_vat(
            value,
            default_country=country,
            transport=self._transport,
            extra={**requester_fields, **trader} or None,
        )

        # **VIES does its own name matching, and its answer beats ours.**
        # On an approximate check the service returns `traderNameMatch` with
        # the member state's own verdict on the name we submitted — the WSDL
        # documents the values as 1 VALID, 2 INVALID, 3 NOT_PROCESSED, and
        # REST returns them as those words. A fuzzy string comparison is a
        # guess about what a register thinks; this IS what the register
        # thinks, so it is preferred whenever it is a verdict at all.
        #
        # Our own comparison remains for every other case: a plain lookup, a
        # member state that returns NOT_PROCESSED, and the countries that
        # return a name but do no matching.
        comparison = _name_comparison(party.name, answer)
        address_result = _address_comparison(party.address_text, answer)

        consultation_ref = answer.consultation_ref or None
        qualified = bool(requester.is_complete and consultation_ref)

        detail = answer.detail
        if answer.status == VALID:
            detail = _valid_sentence(qualified, requester, comparison)
        elif answer.status == INVALID:
            detail = (
                f"VIES says {answer.vat_id} is not a valid VAT "
                "identification number. That is the member state's own "
                "answer, not a guess."
            )

        return VerificationResult(
            status=answer.status,
            provider=self.name,
            value=answer.vat_id,
            kind="vat",
            country=country,
            detail=detail,
            registered_name=(
                answer.trader_name
                if not name_match.is_placeholder(answer.trader_name)
                else None
            ),
            registered_address=(
                answer.trader_address
                if not name_match.is_placeholder(answer.trader_address)
                else None
            ),
            consultation_ref=consultation_ref,
            qualified=qualified,
            name_match=comparison.result,
            address_match=address_result,
            raw=dict(answer.raw),
            error=answer.error,
        )


def _name_comparison(
    party_name: str, answer
) -> name_match.NameComparison:
    """VIES's own verdict if it gave one, otherwise our comparison.

    `traderNameMatch` is the member state's answer about the name WE sent,
    which is strictly better evidence than a string distance — and it is the
    only thing in the response that speaks to "is this number this
    company's" rather than "is this number real".
    """
    verdict = _match_code(answer.raw.get("traderNameMatch"))
    if verdict == "VALID":
        return name_match.NameComparison(
            result=name_match.MATCH,
            score=100,
            detail=(
                "The member state's own register confirms the name as well as "
                "the number — this is its verdict, not a comparison of ours."
            ),
        )
    if verdict == "INVALID":
        return name_match.NameComparison(
            result=name_match.MISMATCH,
            score=0,
            detail=(
                "The member state's own register says the name does not "
                "belong to this number. That is its verdict, not a "
                "comparison of ours — a valid number belonging to somebody "
                "else is exactly what it is for."
            ),
        )
    # NOT_PROCESSED, absent, or anything unrecognised: fall back to our own
    # reading of whatever name came back.
    return name_match.compare_names(party_name, answer.trader_name)


def _address_comparison(party_address: Optional[str], answer) -> str:
    """The same preference, for the address fields.

    VIES returns a verdict per address component. They are collapsed to one
    value here because the stored column is one value, and because a user
    acting on "the street agrees but the city does not" does the same thing
    they would do for "the address differs": look at it.
    """
    components = [
        _match_code(answer.raw.get(field))
        for field in ("traderStreetMatch", "traderPostalCodeMatch", "traderCityMatch")
    ]
    verdicts = [code for code in components if code in ("VALID", "INVALID")]
    if verdicts:
        return (
            name_match.ADDRESS_MATCH
            if all(code == "VALID" for code in verdicts)
            else name_match.ADDRESS_DIFFERS
        )
    return name_match.compare_addresses(party_address, answer.trader_address)


def _match_code(value) -> Optional[str]:
    """`traderNameMatch` as a word.

    REST returns the words; the WSDL's XSD declares the enumeration as the
    digits 1, 2 and 3 with 1 documented as VALID. Both are accepted, because
    which one arrives is not something the Commission documents for REST and
    a client that understood only one would silently lose the verdict.
    """
    if value is None:
        return None
    text = str(value).strip().upper()
    return {
        "1": "VALID",
        "2": "INVALID",
        "3": "NOT_PROCESSED",
        "VALID": "VALID",
        "INVALID": "INVALID",
        "NOT_PROCESSED": "NOT_PROCESSED",
    }.get(text)


def _strip_prefix(vat_number: Optional[str], country: Optional[str]) -> str:
    """`ATU12345678` → `U12345678`.

    VIES takes the country and the number separately in both its APIs, and a
    requester number sent WITH its prefix is rejected as
    `INVALID_REQUESTER_INFO` — which would turn every qualified check into
    an `unavailable`, for a reason no log would explain.
    """
    number = (vat_number or "").strip().upper().replace(" ", "")
    prefix = (country or "").strip().upper()
    if prefix and number.startswith(prefix):
        return number[len(prefix) :]
    if len(number) >= 2 and number[:2].isalpha():
        return number[2:]
    return number


def _valid_sentence(
    qualified: bool, requester: Requester, comparison: name_match.NameComparison
) -> str:
    """What to say when the number is good, which is not just "valid".

    The three things a user needs are whether the number is good, whether
    the NAME agrees, and whether what we have is a consultation reference or
    a plain lookup — in that order, because the first is the question and
    the other two are what a tax advisor will ask about afterwards.
    """
    head = "VIES confirms this number is valid."
    if qualified:
        middle = (
            " The lookup carried your own VAT ID as the requester, so the "
            "answer has a consultation reference stored with it."
        )
    elif requester.is_complete:
        middle = (
            " Your VAT ID was sent as the requester but VIES returned no "
            "consultation reference, so this is recorded as a simple lookup."
        )
    else:
        middle = (
            " This is a simple lookup: your company has no VAT ID saved, so "
            "there was no requester to send and no consultation reference to "
            "store. Add one under Settings → Company."
        )
    return f"{head}{middle} {comparison.detail}".strip()
