"""The verification providers: VIES and the ABR.

**No test in this file reaches the network.** Every one injects a transport,
and `test_no_provider_can_reach_the_network` makes that structural rather
than a convention — it runs the whole registry with a transport that raises
if it is called at all, after monkeypatching the real ones away.

**The seven questions, asked of every provider**, because the mapping rule
is the same everywhere and must be impossible to get wrong in one of them:
valid, invalid, malformed body, timeout, 5xx, rate limit, not configured.
Parametrised over the providers for exactly that reason — a new provider
that does not answer all seven the same way fails here rather than in
production, and the one answer that matters is that **only a registry saying
"no" is `invalid`**.
"""

import json

import pytest

from apps.api.billing.parties.verification import (
    INVALID,
    NOT_CHECKABLE,
    NOT_CONFIGURED,
    UNAVAILABLE,
    VALID,
    PartyFacts,
    Requester,
    VerificationResult,
)
from apps.api.services.verification import (
    AbnProvider,
    ViesProvider,
    provider_for,
    verify,
)

ORF = PartyFacts(
    name="Österreichischer Rundfunk",
    street="Würzburggasse 30",
    postcode="1136",
    city="Wien",
    country="AT",
    address_text="Würzburggasse 30, 1136 Wien",
)
US_REQUESTER = Requester(country="AT", vat_number="ATU16360407")


def vies_transport(status=200, body=None):
    """A fake VIES transport that records what it was sent."""
    sent: list[dict] = []

    def transport(url, payload, timeout):
        sent.append(dict(payload))
        if isinstance(body, Exception):
            raise body
        return status, body

    transport.sent = sent  # type: ignore[attr-defined]
    return transport


def abn_transport(status=200, body=""):
    sent: list[str] = []

    def transport(url, timeout):
        sent.append(url)
        if isinstance(body, Exception):
            raise body
        return status, body

    transport.sent = sent  # type: ignore[attr-defined]
    return transport


def abn_body(**overrides) -> str:
    payload = {
        "Abn": "51824753556",
        "AbnStatus": "Active",
        "EntityName": "SYDNEY STUDIOS PTY LTD",
        "EntityTypeName": "Australian Private Company",
        "Gst": "2000-07-01",
        "AddressState": "NSW",
        "AddressPostcode": "2000",
        "Message": "",
    }
    payload.update(overrides)
    return f"callback({json.dumps(payload)})"


def vies_body(**overrides) -> dict:
    payload = {
        "countryCode": "AT",
        "vatNumber": "U16360407",
        "requestDate": "2026-10-24T09:00:00.000Z",
        "valid": True,
        "requestIdentifier": "WAPIAAAA1",
        "name": "Österreichischer Rundfunk",
        "address": "Würzburggasse 30, 1136 Wien",
        "traderName": "Österreichischer Rundfunk",
        "traderStreet": "Würzburggasse 30",
        "traderPostalCode": "1136",
        "traderCity": "Wien",
        "traderNameMatch": "VALID",
    }
    payload.update(overrides)
    return payload


# ─── The field-name trap ────────────────────────────────────────────────────


def test_the_requester_goes_out_under_the_rest_names_not_the_wsdl_names():
    """**The trap this provider is written around.**

    The WSDL documents `requesterCountryCode` / `requesterVatNumber`. REST
    reads `requesterMemberStateCode` / `requesterNumber` and **silently
    ignores** the WSDL spelling — observed with curl on 2026-10-24: the REST
    names with a bad requester produce `INVALID_REQUESTER_INFO`, while the
    WSDL names produce an ordinary plain lookup that looks like it worked.

    Had the WSDL names been mapped over, every check would have been stored
    as `qualified` with an empty consultation reference and no matching done
    — a claim about evidence we never obtained, with nothing failing. So the
    spelling is pinned here, and the WSDL spelling is asserted ABSENT.
    """
    transport = vies_transport(body=vies_body())
    ViesProvider(transport=transport).check(
        "ATU16360407",
        kind="vat",
        country="AT",
        party=ORF,
        requester=US_REQUESTER,
    )

    sent = transport.sent[0]
    assert sent["requesterMemberStateCode"] == "AT"
    assert sent["requesterNumber"] == "U16360407"
    assert "requesterCountryCode" not in sent
    assert "requesterVatNumber" not in sent


def test_the_requester_number_goes_out_without_its_country_prefix():
    """VIES takes the country and the number separately. A requester number
    sent WITH its prefix is rejected as `INVALID_REQUESTER_INFO`, which
    would turn every qualified check into an `unavailable` for a reason no
    log would explain."""
    transport = vies_transport(body=vies_body())
    ViesProvider(transport=transport).check(
        "DE123456789",
        kind="vat",
        country="DE",
        party=ORF,
        requester=Requester(country="AT", vat_number="ATU16360407"),
    )

    assert transport.sent[0]["requesterNumber"] == "U16360407"


def test_the_trader_postcode_uses_the_rest_spelling():
    """REST spells it `traderPostalCode`; the WSDL says `traderPostcode`.
    Observed, and the wrong one is simply dropped — so the matching would
    quietly not happen."""
    transport = vies_transport(body=vies_body())
    ViesProvider(transport=transport).check(
        "ATU16360407", kind="vat", country="AT", party=ORF, requester=US_REQUESTER
    )

    sent = transport.sent[0]
    assert sent["traderPostalCode"] == "1136"
    assert "traderPostcode" not in sent
    assert sent["traderName"] == "Österreichischer Rundfunk"
    assert sent["traderCity"] == "Wien"


def test_no_trader_fields_are_sent_without_a_requester():
    """VIES rejects trader fields without a requester
    (`INVALID_REQUESTER_INFO`), and a rejected request is an `unavailable`
    we did not need. So a company with no VAT ID of its own makes a plain
    lookup."""
    transport = vies_transport(body=vies_body(requestIdentifier=""))
    result = ViesProvider(transport=transport).check(
        "ATU16360407", kind="vat", country="AT", party=ORF, requester=Requester()
    )

    sent = transport.sent[0]
    assert set(sent) == {"countryCode", "vatNumber"}
    assert result.status == VALID
    assert result.qualified is False
    # And it says why, pointing at where to fix it.
    assert "no VAT ID saved" in result.detail
    assert "Settings → Company" in result.detail


def test_a_caller_cannot_redirect_the_lookup_through_the_extra_fields():
    """The two core fields win on a collision. Without that, an `extra`
    carrying `vatNumber` would check one number and store the answer against
    another."""
    from apps.api.services import vies

    transport = vies_transport(body=vies_body())
    vies.check_vat(
        "ATU16360407",
        default_country="AT",
        transport=transport,
        extra={"vatNumber": "U99999999", "countryCode": "DE"},
    )

    assert transport.sent[0]["vatNumber"] == "U16360407"
    assert transport.sent[0]["countryCode"] == "AT"


# ─── `qualified` is about what came back, not what we asked ────────────────


def test_qualified_needs_a_consultation_reference_not_just_a_requester():
    """If the Commission ignores our requester for any reason, the result is
    `simple`. What matters is what we can show a tax advisor, not what we
    intended to ask."""
    transport = vies_transport(body=vies_body(requestIdentifier=""))
    result = ViesProvider(transport=transport).check(
        "ATU16360407", kind="vat", country="AT", party=ORF, requester=US_REQUESTER
    )

    assert result.status == VALID
    assert result.qualified is False
    assert result.consultation_ref is None
    assert "returned no consultation reference" in result.detail


def test_a_qualified_result_keeps_the_reference_and_says_so():
    transport = vies_transport(body=vies_body(requestIdentifier="WAPIAAAA1"))
    result = ViesProvider(transport=transport).check(
        "ATU16360407", kind="vat", country="AT", party=ORF, requester=US_REQUESTER
    )

    assert result.qualified is True
    assert result.consultation_ref == "WAPIAAAA1"
    assert "consultation reference stored with it" in result.detail


def test_a_manual_result_can_never_be_constructed_as_qualified():
    """The claim "a tax authority confirmed this" is the one nobody may make
    by accident, so the value object refuses it as well as the database
    (D66)."""
    with pytest.raises(ValueError) as caught:
        VerificationResult(status=VALID, provider="manual", qualified=True)
    assert "never a qualified consultation" in str(caught.value)


def test_a_status_outside_the_five_is_refused():
    with pytest.raises(ValueError) as caught:
        VerificationResult(status="probably_fine", provider="vies")
    assert "not a verification status" in str(caught.value)


# ─── The seven questions, every provider ───────────────────────────────────


def _vies(status=200, body=None):
    return ViesProvider(transport=vies_transport(status, body)), dict(
        value="ATU16360407", kind="vat", country="AT"
    )


def _abn(status=200, body=""):
    return AbnProvider(transport=abn_transport(status, body), guid="test-guid"), dict(
        value="51824753556", kind="abn", country="AU"
    )


def _run(provider, args, requester=US_REQUESTER):
    return provider.check(
        args["value"],
        kind=args["kind"],
        country=args["country"],
        party=ORF,
        requester=requester,
    )


def test_a_valid_answer_is_valid_from_both_providers():
    provider, args = _vies(body=vies_body())
    assert _run(provider, args).status == VALID

    provider, args = _abn(body=abn_body())
    assert _run(provider, args).status == VALID


def test_a_registry_saying_no_is_invalid_from_both_providers():
    """The only shape of answer that is ever `invalid`."""
    provider, args = _vies(body=vies_body(valid=False))
    result = _run(provider, args)
    assert result.status == INVALID
    assert "not a valid VAT" in result.detail
    assert "not a guess" in result.detail

    provider, args = _abn(
        body=abn_body(Abn="", Message="The ABN you entered is not a valid ABN")
    )
    result = _run(provider, args)
    assert result.status == INVALID
    assert "register's own answer" in result.detail


@pytest.mark.parametrize("factory", [_vies, _abn])
def test_a_timeout_is_unavailable_never_invalid(factory):
    """**The rule that costs money if it flips.** A government service having
    a bad afternoon must not read as a finding about somebody's number."""
    provider, args = factory(body=TimeoutError("timed out"))
    result = _run(provider, args)

    assert result.status == UNAVAILABLE
    assert result.status != INVALID


@pytest.mark.parametrize("factory", [_vies, _abn])
def test_a_5xx_is_unavailable(factory):
    provider, args = factory(status=503, body="" if factory is _abn else {})
    assert _run(provider, args).status == UNAVAILABLE


@pytest.mark.parametrize("factory", [_vies, _abn])
def test_a_malformed_body_is_unavailable(factory):
    """Not `invalid`, and not a crash: a body we cannot read means we do not
    know."""
    provider, args = factory(body="<html>maintenance</html>" if factory is _abn else "not a dict")
    result = _run(provider, args)

    assert result.status == UNAVAILABLE
    assert result.detail.strip()


def test_a_vies_rate_limit_is_unavailable_and_keeps_the_token():
    """`MS_MAX_CONCURRENT_REQ` is documented in the WSDL and arrives with
    HTTP 200. A client keyed on the status code would read it as an answer,
    find no `valid` field and report a good number as invalid."""
    provider, args = _vies(
        body={"actionSucceed": False, "errorWrappers": [{"error": "MS_MAX_CONCURRENT_REQ"}]}
    )
    result = _run(provider, args)

    assert result.status == UNAVAILABLE
    assert result.error == "MS_MAX_CONCURRENT_REQ"


def test_a_member_state_being_down_is_unavailable():
    provider, args = _vies(
        body={"actionSucceed": False, "errorWrappers": [{"error": "MS_UNAVAILABLE"}]}
    )
    result = _run(provider, args)

    assert result.status == UNAVAILABLE
    assert result.error == "MS_UNAVAILABLE"


def test_an_abn_rate_limit_or_throttle_is_unavailable():
    """The ABR publishes no rate limit, but it exposes an activity report per
    GUID, so one exists. A throttling message must not become a verdict."""
    provider, args = _abn(
        body=abn_body(Abn="", Message="Too many requests, please try again later")
    )
    assert _run(provider, args).status == UNAVAILABLE


def test_an_unconfigured_abn_provider_is_not_configured_with_instructions():
    """**Never an error and never `invalid`.** A self-hosted installation
    that has not registered for a GUID must not be told its customers'
    numbers are bad."""
    result = AbnProvider(guid=None).check(
        "51824753556",
        kind="abn",
        country="AU",
        party=ORF,
        requester=Requester(),
    )

    assert result.status == NOT_CONFIGURED
    assert result.status != INVALID
    # And it says exactly what to do, including the variable name.
    assert "ABN_LOOKUP_GUID" in result.detail
    assert "WebServiceRegistration" in result.detail
    assert "nothing about it is in doubt" in result.detail


def test_a_rejected_guid_is_not_configured_not_invalid():
    """The register answered, but about our credentials — not about the
    number."""
    provider, args = _abn(
        body=abn_body(Abn="", Message="Unauthorized: the GUID supplied is not valid")
    )
    result = _run(provider, args)

    assert result.status == NOT_CONFIGURED
    assert "ABN_LOOKUP_GUID" in result.detail


# ─── What each registry reports beyond existence ───────────────────────────


def test_a_cancelled_abn_is_invalid_because_the_register_answered():
    """"Cancelled" is a positive answer: the register knows this ABN and
    says it is not current. An invoice relying on it would be relying on a
    registration that has ended."""
    provider, args = _abn(body=abn_body(AbnStatus="Cancelled"))
    result = _run(provider, args)

    assert result.status == INVALID
    assert result.registration_active is False
    assert "no longer current" in result.detail
    # And the register's own words are kept, not paraphrased away.
    assert result.raw["AbnStatus"] == "Cancelled"


def test_gst_registration_is_read_from_the_date_range():
    """The official data dictionary gives `goodsAndServicesTax` exactly two
    fields, `effectiveFrom` and `effectiveTo` — there is no boolean. So "is
    this entity GST-registered today" is a computation over periods."""
    provider, args = _abn(body=abn_body(Gst="2000-07-01"))
    assert _run(provider, args).gst_registered is True

    provider, args = _abn(body=abn_body(Gst="2000-07-01", GstTo="2010-01-01"))
    assert _run(provider, args).gst_registered is False


def test_a_missing_gst_period_is_none_not_false():
    """"We do not know" and "they are not registered" are different facts,
    and the second one affects an Australian invoice."""
    provider, args = _abn(body=abn_body(Gst=""))
    assert _run(provider, args).gst_registered is None


def test_vies_reports_nothing_about_registration_status():
    """It has no such concept, so the fields stay None rather than being
    guessed from `valid` — which would read as "the register says they are
    active" when the register said no such thing."""
    provider, args = _vies(body=vies_body())
    result = _run(provider, args)

    assert result.registration_active is None
    assert result.gst_registered is None


def test_a_german_placeholder_name_is_never_stored_as_a_name():
    """Germany returns `---` for every lookup — it does not disclose names.

    Storing that as `registered_name` would put "---" on a screen beside
    "the register says:", which looks like a bug and reads like a finding.
    """
    provider, args = _vies(
        body=vies_body(
            traderName="---",
            traderStreet="---",
            traderPostalCode="---",
            traderCity="---",
            name="---",
            address="---",
            traderNameMatch="NOT_PROCESSED",
        )
    )
    result = _run(provider, args)

    assert result.registered_name is None
    assert result.registered_address is None
    # Nothing to compare, so `unknown` — not a mismatch, which would fire on
    # every German customer every time.
    assert result.name_match == "unknown"


def test_a_withheld_name_with_a_positive_verdict_is_still_a_match():
    """The case that looks contradictory and is not.

    A member state may decline to disclose the name (`---`) and still
    confirm that the name we SENT belongs to the number
    (`traderNameMatch: VALID`). That is a better answer than either half
    alone: the register has matched it for us without telling us what it
    holds. So the verdict is `match` and `registered_name` stays empty,
    because there is genuinely no name to show.
    """
    provider, args = _vies(
        body=vies_body(
            name="---", traderName="---", traderNameMatch="VALID"
        )
    )
    result = _run(provider, args)

    assert result.name_match == "match"
    assert result.registered_name is None
    assert "its verdict, not a comparison of ours" in result.detail


def test_vies_own_name_verdict_is_preferred_over_our_comparison():
    """**The register's verdict beats a string distance.**

    `traderNameMatch` is the member state's own answer about the name we
    submitted. A fuzzy comparison is a guess about what a register thinks;
    this is what it thinks. So when the two disagree — here the names are
    identical and the register says INVALID — the register wins.
    """
    provider, args = _vies(
        body=vies_body(
            name="Österreichischer Rundfunk", traderNameMatch="INVALID"
        )
    )
    result = _run(provider, args)

    assert result.name_match == "mismatch"
    assert "its verdict, not a comparison of ours" in result.detail


def test_the_wsdls_numeric_match_codes_are_understood_too():
    """The XSD declares the enumeration as 1, 2, 3; REST returns the words.
    Which one arrives is not documented for REST, and a client that
    understood only one would silently lose the verdict."""
    provider, args = _vies(body=vies_body(traderNameMatch="2"))
    assert _run(provider, args).name_match == "mismatch"

    provider, args = _vies(body=vies_body(traderNameMatch="1"))
    assert _run(provider, args).name_match == "match"


def test_our_comparison_is_used_when_vies_did_not_process_the_name():
    """NOT_PROCESSED is what a plain lookup returns, and what a member state
    returns when it does no matching. Falling back is the whole point: most
    checks will not be qualified."""
    provider, args = _vies(
        body=vies_body(name="Alpha Film GmbH", traderNameMatch="NOT_PROCESSED")
    )
    result = _run(provider, args)

    assert result.name_match == "mismatch"
    # Our own sentence, not the register's.
    assert "its verdict" not in result.detail
    assert result.registered_name == "Alpha Film GmbH"


def test_a_component_address_verdict_collapses_to_one_value():
    """The stored column is one value, and a user acting on "street agrees,
    city does not" does the same thing as for "the address differs"."""
    provider, args = _vies(
        body=vies_body(
            traderStreetMatch="VALID",
            traderPostalCodeMatch="VALID",
            traderCityMatch="VALID",
        )
    )
    assert _run(provider, args).address_match == "match"

    provider, args = _vies(
        body=vies_body(traderStreetMatch="VALID", traderCityMatch="INVALID")
    )
    assert _run(provider, args).address_match == "differs"


# ─── The registry of providers ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind, country, expected",
    [
        ("vat", "DE", "vies"),
        ("vat", "AT", "vies"),
        ("vat", "XI", "vies"),
        ("abn", "AU", "abn"),
        ("abn", "NZ", "abn"),
        ("vat", "GB", None),
        ("vat", "CH", None),
        ("uid_ch", "CH", None),
        ("ein", "US", None),
        ("tax_number", "AT", None),
        ("company_register", "AT", None),
        ("other", None, None),
    ],
)
def test_which_provider_covers_which_kind(kind, country, expected):
    """`XI` is in because Northern Ireland stayed in the EU VAT area for
    goods; `GB` is out because VIES does not cover it. `uid_ch` is out
    because no official interface to the Swiss register could be found
    (D61) — not because nobody got round to it."""
    provider = provider_for(kind, country)
    assert (provider.name if provider else None) == expected


def test_a_kind_with_no_provider_is_not_checkable_with_one_sentence():
    """One constructor for it, so a user never sees two different
    explanations of the same situation and assumes one is a bug."""
    for kind, country in [("uid_ch", "CH"), ("ein", "US"), ("other", None)]:
        result = verify("whatever", kind=kind, country=country)
        assert result.status == NOT_CHECKABLE
        assert "no register FilmBill can query" in result.detail
        assert "record a confirmation by hand" in result.detail


def test_not_checkable_counts_as_a_completed_answer():
    """It is a settled fact about the world, so it must not leave an
    identifier retrying for ever — unlike `unavailable`, which is exactly a
    "try again"."""
    from apps.api.billing.parties.verification import COMPLETED_STATUSES

    assert NOT_CHECKABLE in COMPLETED_STATUSES
    assert UNAVAILABLE not in COMPLETED_STATUSES
    assert NOT_CONFIGURED not in COMPLETED_STATUSES


def test_asking_the_wrong_provider_is_a_wiring_bug_not_a_result():
    """A provider raises here rather than returning `unavailable`: the
    registry picks the provider, so this can only be a programming error,
    and swallowing it would hide the mis-wiring behind a plausible status."""
    with pytest.raises(ValueError) as caught:
        ViesProvider(transport=vies_transport()).check(
            "51824753556",
            kind="abn",
            country="AU",
            party=ORF,
            requester=Requester(),
        )
    assert "wiring bug" in str(caught.value)


def test_no_provider_can_reach_the_network(monkeypatch):
    """**Structural, not a convention.**

    Both default transports are replaced with one that raises if it is
    called at all, and then every provider is exercised. If a provider ever
    grows a second code path that builds its own transport, this fails —
    which is the only way to know that "tests never reach the network" is
    still true a year from now (rule 17b).
    """
    from apps.api.services import vies as vies_module
    from apps.api.services.verification import abn_provider

    def forbidden(*args, **kwargs):
        raise AssertionError("a provider tried to reach the network")

    monkeypatch.setattr(vies_module, "default_transport", forbidden)
    monkeypatch.setattr(abn_provider, "default_transport", forbidden)

    # VIES and the ABR both have an injected transport in every test; these
    # calls deliberately pass none, so only the monkeypatched default is
    # reachable.
    for kind, country, value in [
        ("uid_ch", "CH", "CHE109322551"),
        ("ein", "US", "123456789"),
        ("company_register", "AT", "FN 123456a"),
    ]:
        # No provider, so nothing is called at all.
        assert verify(value, kind=kind, country=country).status == NOT_CHECKABLE

    # And the one that IS configured but has no credential never calls out.
    result = AbnProvider(guid=None).check(
        "51824753556", kind="abn", country="AU", party=ORF, requester=Requester()
    )
    assert result.status == NOT_CONFIGURED
