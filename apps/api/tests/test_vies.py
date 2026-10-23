"""VAT ID normalisation and the VIES client, with a fake transport.

**No test here ever reaches the real service.** A suite that did would be
slow, flaky, dependent on somebody else's uptime, and — against a service
whose documented failure modes are concurrency ceilings — rude. The transport
is a parameter for exactly this reason.

The one thing in this file to read before changing anything: **`unavailable`
is never `invalid`.** `invalid` means VIES positively said the number is not
valid. A timeout, a 5xx, a member state that is not replying, a body we
cannot parse — all of those are a failure to OBTAIN an answer, and a failure
to obtain an answer is not evidence about a number. Getting that backwards
turns a reverse-charge invoice into a domestic one because a government
service was busy.

The real responses this file imitates were observed on 2026-10-23 (see
`services/vies.py`), including the one that matters most: **an error comes
back with HTTP 200**.
"""

import pytest

from apps.api.billing.parties import (
    VatFormatError,
    is_checkable,
    iso_country,
    normalise,
    normalise_and_validate,
    vies_country,
)
from apps.api.services import vies

#: The success body, exactly as the live service shaped it.
VALID_BODY = {
    "countryCode": "AT",
    "vatNumber": "U12345678",
    "requestDate": "2026-10-23T13:52:58.891Z",
    "valid": True,
    "requestIdentifier": "",
    "name": "YON Studio OG",
    "address": "Praterstraße 1, 1020 Wien",
    "traderName": "---",
    "traderStreet": "---",
    "traderPostalCode": "---",
    "traderCity": "---",
    "traderCompanyType": "---",
    "traderNameMatch": "NOT_PROCESSED",
    "traderStreetMatch": "NOT_PROCESSED",
    "traderPostalCodeMatch": "NOT_PROCESSED",
    "traderCityMatch": "NOT_PROCESSED",
    "traderCompanyTypeMatch": "NOT_PROCESSED",
}

#: The "not allocated" body, from the probe that observed the shape.
INVALID_BODY = {
    **VALID_BODY,
    "valid": False,
    "name": "---",
    "address": "---",
}


def transport_returning(code, body, *, record=None):
    """A fake transport. Records what it was asked, so the tests can check it."""

    def _send(url, payload, timeout):
        if record is not None:
            record.append({"url": url, "payload": payload, "timeout": timeout})
        return code, body

    return _send


def transport_raising(error: Exception, *, record=None):
    def _send(url, payload, timeout):
        if record is not None:
            record.append(payload)
        raise error

    return _send


# ─── Normalisation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ATU12345678", "ATU12345678"),
        ("atu12345678", "ATU12345678"),
        ("  ATU 123 456 78  ", "ATU12345678"),
        ("AT-U12345678", "ATU12345678"),
        ("AT.U12345678", "ATU12345678"),
        ("DE 123 456 789", "DE123456789"),
        ("NL123456789B01", "NL123456789B01"),
    ],
)
def test_spaces_dots_dashes_and_case_are_normalised(raw, expected):
    assert normalise_and_validate(raw).full == expected


def test_greece_is_el_to_vies_and_gr_in_iso():
    """The live member-state listing has an `EL` entry and no `GR` one, so a
    lookup sent as `GR` would simply not be answered."""
    assert vies_country("GR") == "EL"
    assert vies_country("EL") == "EL"
    assert iso_country("EL") == "GR"
    assert iso_country("GR") == "GR"

    # Both spellings accepted on the way in, both stored as ISO, both asked
    # as EL.
    for raw in ("EL123456789", "GR123456789"):
        normalised = normalise_and_validate(raw)
        assert normalised.country == "GR"
        assert normalised.vies_country == "EL"
        assert normalised.full == "GR123456789"


def test_greece_is_asked_as_el_on_the_wire():
    """The end of that chain: what actually goes into the request body."""
    sent = []
    vies.check_vat(
        "GR123456789", transport=transport_returning(200, VALID_BODY, record=sent)
    )

    assert sent[0]["payload"] == {"countryCode": "EL", "vatNumber": "123456789"}


def test_northern_ireland_is_xi():
    """A real VIES country code — confirmed in the live listing, which has no
    `GB` entry at all."""
    assert is_checkable("XI") is True
    assert normalise_and_validate("XI123456789").vies_country == "XI"


def test_a_number_with_no_prefix_takes_the_partys_country():
    assert normalise_and_validate("123456789", default_country="DE").full == (
        "DE123456789"
    )


def test_a_number_with_no_prefix_and_no_country_is_refused():
    """`12345678` is not a VAT ID until somebody says whose."""
    with pytest.raises(VatFormatError) as caught:
        normalise("12345678")
    assert "no country in it" in str(caught.value)


@pytest.mark.parametrize(
    "raw",
    [
        "ATU1234567",
        "ATU123456789",
        "ATX12345678",
        "DE12345678",
        "DE1234567890",
        "NL123456789X01",
    ],
)
def test_a_wrong_format_is_refused_with_the_pattern_and_an_example(raw):
    with pytest.raises(VatFormatError) as caught:
        normalise_and_validate(raw)
    message = str(caught.value)
    assert "does not look like" in message
    # The refusal says where to correct the rule if the rule is what is
    # wrong — the formats are not official and the file says so.
    assert "vat_formats.yaml" in message


def test_the_wsdl_length_constraint_applies_to_every_country():
    """The only officially published rule: 2 to 12 characters.

    Applied even to a country the table has no pattern for, which is the
    Swiss case — so a Swiss "number" of forty characters is still refused.
    """
    with pytest.raises(VatFormatError) as caught:
        normalise_and_validate("CHE1234567890123456789")
    assert "2 to 12 characters" in str(caught.value)


def test_a_non_eu_number_is_accepted_as_free_text():
    """Refusing it would make a Swiss supplier unsaveable."""
    normalised = normalise_and_validate("CHE-123.456.789")

    assert normalised.full == "CHE123456789"
    assert normalised.country == "CH"
    assert is_checkable("CH") is False


def test_an_empty_vat_id_is_refused():
    with pytest.raises(VatFormatError):
        normalise("   ")


# ─── The lookup: the answers ────────────────────────────────────────────────


def test_a_valid_number_comes_back_valid_with_what_vies_returned():
    result = vies.check_vat(
        "ATU12345678", transport=transport_returning(200, VALID_BODY)
    )

    assert result.status == "valid"
    assert result.trader_name == "YON Studio OG"
    assert result.trader_address == "Praterstraße 1, 1020 Wien"
    assert result.request_date == "2026-10-23T13:52:58.891Z"
    assert result.error is None
    # Stored verbatim, for the audit trail and the "differs" comparison.
    assert result.raw["traderNameMatch"] == "NOT_PROCESSED"


def test_vies_filling_a_field_with_hyphens_is_read_as_nothing():
    """Observed, not documented: VIES writes `---` for an unknown field.

    Storing it would put three hyphens on a screen beside a party's real
    name, and a "differs" marker would fire on every single check.
    """
    result = vies.check_vat(
        "ATU12345678", transport=transport_returning(200, INVALID_BODY)
    )

    assert result.trader_name is None
    assert result.trader_address is None


def test_an_empty_request_identifier_becomes_none_rather_than_a_blank():
    result = vies.check_vat(
        "ATU12345678", transport=transport_returning(200, VALID_BODY)
    )
    assert result.consultation_ref is None


def test_a_consultation_reference_is_kept_when_there_is_one():
    body = {**VALID_BODY, "requestIdentifier": "WAPIAAAAX123456789"}
    result = vies.check_vat(
        "ATU12345678", transport=transport_returning(200, body)
    )
    assert result.consultation_ref == "WAPIAAAAX123456789"


def test_vies_saying_not_valid_is_the_only_thing_that_means_invalid():
    result = vies.check_vat(
        "ATU12345678", transport=transport_returning(200, INVALID_BODY)
    )

    assert result.status == "invalid"
    # And the sentence makes clear it is a verdict rather than an outage,
    # because that distinction is the whole point of this module.
    assert "not a connection problem" in result.detail


def test_a_non_eu_country_is_not_applicable_and_never_leaves_the_machine():
    sent = []
    result = vies.check_vat(
        "CHE123456789",
        transport=transport_returning(200, VALID_BODY, record=sent),
    )

    assert result.status == "not_applicable"
    assert "only covers EU member states" in result.detail
    assert sent == [], "a non-EU number was sent to VIES anyway"


def test_a_malformed_number_is_refused_before_any_network_call():
    """A typo should cost a sentence in a tenth of a second, not a timeout in
    eight seconds."""
    sent = []
    with pytest.raises(VatFormatError):
        vies.check_vat(
            "ATU123",
            transport=transport_returning(200, VALID_BODY, record=sent),
        )
    assert sent == []


# ─── The lookup: every way of not getting an answer ─────────────────────────


@pytest.mark.parametrize("error", vies.VIES_ERRORS)
def test_every_documented_fault_is_unavailable_and_never_invalid(error):
    """The WSDL's own text: these are processing failures, not verdicts.

    Including `INVALID_INPUT`, whose NAME is the trap — it means the request
    was malformed, not that the number is bad.
    """
    body = {"actionSucceed": False, "errorWrappers": [{"error": error}]}
    result = vies.check_vat(
        "ATU12345678", transport=transport_returning(200, body)
    )

    assert result.status == "unavailable", error
    assert result.status != "invalid"
    assert result.error == error
    # Every fault has a sentence; a code on a screen is not an explanation.
    assert len(result.detail) > 40, error


def test_an_error_arriving_with_http_200_is_still_an_error():
    """**The trap this module exists to avoid.**

    VIES returns faults with HTTP 200. A client keyed on the status code
    would read this as an answer, find no `valid` field, treat the absence as
    False and report a perfectly good number as invalid.
    """
    body = {"actionSucceed": False, "errorWrappers": [{"error": "MS_UNAVAILABLE"}]}
    code, parsed = 200, body

    result = vies._interpret(
        normalise_and_validate("ATU12345678"), code, parsed
    )

    assert result.status == "unavailable"
    assert result.status != "invalid"


def test_a_missing_valid_field_is_unavailable_not_invalid():
    """"The field we needed was not there" and "the number is bad" are
    different answers."""
    for body in ({}, {"countryCode": "AT"}, {"valid": None}, {"valid": "false"}):
        result = vies.check_vat(
            "ATU12345678", transport=transport_returning(200, body)
        )
        assert result.status == "unavailable", body
        assert result.status != "invalid", body


@pytest.mark.parametrize("code", [400, 401, 429, 500, 502, 503])
def test_an_http_error_is_unavailable(code):
    """Including 429: a rate limit is not a verdict on a number."""
    result = vies.check_vat(
        "ATU12345678", transport=transport_returning(code, None)
    )

    assert result.status == "unavailable"
    assert str(code) in result.detail


def test_a_body_that_is_not_json_is_unavailable():
    result = vies.check_vat(
        "ATU12345678", transport=transport_returning(200, "<html>oops</html>")
    )

    assert result.status == "unavailable"
    assert "cannot read" in result.detail


def test_a_timeout_is_unavailable_and_says_so_in_plain_words():
    """The mutation behind this one is the dangerous one: mapping a timeout
    to `invalid` would charge domestic VAT because a service was slow."""
    result = vies.check_vat(
        "ATU12345678",
        transport=transport_raising(TimeoutError("timed out")),
    )

    assert result.status == "unavailable"
    assert result.status != "invalid"
    assert "could not reach" in result.detail


def test_a_connection_failure_is_unavailable_and_mentions_self_hosting():
    """An installation with no outbound internet is a supported arrangement,
    so the message says so rather than reading as a bug."""
    result = vies.check_vat(
        "ATU12345678", transport=transport_raising(OSError("no route to host"))
    )

    assert result.status == "unavailable"
    assert "self-hosted" in result.detail


def test_a_transport_failure_is_retried_once_and_then_given_up_on():
    attempts = []
    result = vies.check_vat(
        "ATU12345678",
        transport=transport_raising(OSError("flaky"), record=attempts),
    )

    assert result.status == "unavailable"
    assert len(attempts) == 2, "one retry, not none and not three"


def test_a_fault_is_retried_once_and_the_second_answer_is_used():
    """A concurrency ceiling is exactly the case a single retry is for."""
    answers = [
        (200, {"actionSucceed": False, "errorWrappers": [{"error": "MS_MAX_CONCURRENT_REQ"}]}),
        (200, VALID_BODY),
    ]

    def _send(url, payload, timeout):
        return answers.pop(0)

    result = vies.check_vat("ATU12345678", transport=_send)

    assert result.status == "valid"
    assert answers == []


def test_a_verdict_is_not_retried():
    """`invalid` is an answer. Asking again would be asking a settled
    question, and VIES publishes concurrency ceilings."""
    calls = []
    result = vies.check_vat(
        "ATU12345678",
        transport=transport_returning(200, INVALID_BODY, record=calls),
    )

    assert result.status == "invalid"
    assert len(calls) == 1


def test_the_request_goes_to_the_observed_endpoint_with_the_observed_shape():
    """Pinned, because the shape was OBSERVED rather than documented — so a
    change to it is a thing somebody has to notice deliberately."""
    sent = []
    vies.check_vat(
        "ATU12345678", transport=transport_returning(200, VALID_BODY, record=sent)
    )

    assert sent[0]["url"] == (
        "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
    )
    assert sent[0]["payload"] == {"countryCode": "AT", "vatNumber": "U12345678"}
    assert sent[0]["timeout"] == pytest.approx(8.0, abs=0.5)


def test_the_response_never_reaches_the_log(caplog):
    """It carries a trader's name and address, and a log is a place that
    outlives the reason for having it."""
    import logging

    with caplog.at_level(logging.DEBUG):
        vies.check_vat(
            "ATU12345678", transport=transport_returning(200, VALID_BODY)
        )
        vies.check_vat(
            "ATU12345678", transport=transport_raising(OSError("boom"))
        )

    text = " ".join(record.getMessage() for record in caplog.records)
    assert "YON Studio OG" not in text
    assert "Praterstraße" not in text
    # The class of a transport failure IS logged — that is a diagnostic with
    # no personal data in it.
    assert "OSError" in text


# ─── The format table is data, and says it is unverified ───────────────────


def test_the_format_file_says_it_is_not_official_and_not_verified():
    """Asserted on the FILE, because the file is what somebody edits.

    The Commission publishes no per-country formats, so every pattern in
    there is "the format in ordinary published use" and a wrong one rejects a
    real number. The header has to say so, and has to point at the one line
    that does have an official source.
    """
    from apps.api.billing.parties import FORMATS_FILE

    header = FORMATS_FILE.read_text()[:3000]

    assert "NOT VERIFIED" in header
    assert "checkVatService.wsdl" in header, "no official source cited for `_wsdl`"
    assert "EL" in header and "GR" in header, "the Greece mapping is not explained"


def test_the_format_file_is_not_mistaken_for_a_tax_pack():
    """It lives beside the tax packs, which glob `*.yaml`.

    A bare glob made the tax loader try to read it as a pack and fail — so
    `available_packs` discriminates on the `pack:` key, and this is the test
    that keeps the two kinds of region-pack data apart.
    """
    from apps.api.billing.tax import available_packs

    assert "vat_formats" not in available_packs()
    assert set(available_packs()) == {"at", "de", "eu_generic"}


def test_every_eu_member_state_the_live_service_lists_has_a_format():
    """The 28 country codes the live `check-status` endpoint returned on
    2026-10-23 — 27 member states plus `XI`.

    Written out rather than fetched: a test that called the service would be
    the thing this file exists not to do, and a country going missing from
    the table is a number that cannot be checked.
    """
    from apps.api.billing.parties import known_countries

    live = {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI",
        "FR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL",
        "PT", "RO", "SE", "SI", "SK", "XI",
    }
    missing = live - set(known_countries())

    assert not missing, f"no VAT format for {sorted(missing)}"
    # `GR` is the ISO spelling of `EL` and is in the table as well, so the
    # table is one entry wider than the live listing.
    assert "GR" in known_countries()
