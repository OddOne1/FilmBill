"""Normalising a VAT identification number, and checking its shape.

Pure, like everything else under `billing/`: no network, no session. The
network part is `services/vies.py`, and the split is load-bearing — a
malformed number must be refused **before** any call goes out, so that a
typo produces a sentence in a tenth of a second instead of a timeout in
eight seconds.

**Three things worth knowing before editing this file.**

1. **Greece is `EL` to VIES and `GR` in ISO 3166.** The live member-state
   listing has an `EL` entry and no `GR` one at all, so a lookup sent as
   `GR` would simply not be answered. `vies_country` translates one way and
   `iso_country` the other, and both are tested.
2. **The format table is data and is not official.** The Commission
   publishes no per-country formats; the only official constraint is the
   WSDL's `[0-9A-Za-z+*.]{2,12}`, which is also applied. See the header of
   `region_packs/vat_formats.yaml`.
3. **A country with no entry is accepted, not refused.** A Swiss UID or a US
   EIN is free text with status `not_applicable` — VIES does not cover them,
   and refusing a number because this file has no pattern for it would make
   a Swiss supplier unsaveable.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

#: Beside the tax packs, because it is the same kind of thing: per-country
#: data a user may have to correct.
FORMATS_FILE = Path(__file__).resolve().parents[2] / "region_packs" / "vat_formats.yaml"

#: VIES's spelling of Greece. ISO 3166 says `GR`.
VIES_GREECE = "EL"
ISO_GREECE = "GR"

#: Everything that is punctuation in a VAT number as people write it.
#: Stripped rather than refused: `ATU 123 456 78` and `AT-U12345678` are the
#: same number, and a refusal would be pedantry about a form of words.
_NOISE = re.compile(r"[\s.\-/]")


class VatFormatError(ValueError):
    """The number cannot be a VAT ID for that country. A 422 at the router.

    Raised before any network call, which is the point: a typo should cost a
    sentence, not a timeout.
    """


@dataclass(frozen=True)
class NormalisedVatId:
    """A VAT ID split into the two parts every lookup needs.

    `country` is the ISO spelling (what the rest of FilmBill uses) and
    `vies_country` the one VIES answers to. Both, because carrying only one
    means translating at every call site and getting it wrong at one of them.
    """

    #: The whole thing, compact and upper case: `ATU12345678`.
    full: str
    #: ISO 3166 alpha-2 — `GR` for Greece.
    country: str
    #: What VIES is asked — `EL` for Greece.
    vies_country: str
    #: The part after the country prefix: `U12345678`.
    number: str
    #: Whether a country prefix was present at all. A number typed without
    #: one is still usable: the party's own country supplies it.
    had_prefix: bool


@lru_cache(maxsize=1)
def _formats() -> tuple[dict, str]:
    """`({country: pattern}, wsdl pattern)`, read once."""
    raw = yaml.safe_load(FORMATS_FILE.read_text()) or {}
    countries = raw.get("countries") or {}
    if not isinstance(countries, dict) or not countries:
        raise VatFormatError(
            f"{FORMATS_FILE.name} has no `countries` table, so no VAT number "
            "could be checked."
        )
    wsdl = raw.get("_wsdl") or ".*"
    return (
        {str(key).upper(): str(value) for key, value in countries.items()},
        str(wsdl),
    )


def known_countries() -> tuple[str, ...]:
    """Every country the format table has an entry for, sorted."""
    return tuple(sorted(_formats()[0]))


def vies_country(country: Optional[str]) -> Optional[str]:
    """ISO → the spelling VIES answers to. `GR` becomes `EL`."""
    if not country:
        return None
    upper = country.strip().upper()
    return VIES_GREECE if upper == ISO_GREECE else upper


def iso_country(country: Optional[str]) -> Optional[str]:
    """VIES → ISO. `EL` becomes `GR`."""
    if not country:
        return None
    upper = country.strip().upper()
    return ISO_GREECE if upper == VIES_GREECE else upper


def is_checkable(country: Optional[str]) -> bool:
    """Whether VIES covers this country at all.

    False for Switzerland, the United States, the United Kingdom proper —
    their numbers are recorded as `not_applicable` rather than checked.
    """
    if not country:
        return False
    return vies_country(country) in _formats()[0]


def normalise(
    value: str, *, default_country: Optional[str] = None
) -> NormalisedVatId:
    """`" at-u 123 456 78 "` → `ATU12345678`, split and ready to look up.

    `default_country` fills in a prefix the user did not type, from the
    party's own country. A number with no prefix and no default is refused:
    `12345678` is not a VAT ID until somebody says whose.

    Does NOT check the per-country format — `validate_format` does, so a
    caller that only wants the parts does not pay for a refusal it has no
    message for.
    """
    compact = _NOISE.sub("", (value or "")).upper()
    if not compact:
        raise VatFormatError("A VAT ID cannot be empty.")

    prefix = compact[:2]
    if prefix.isalpha() and (
        prefix in _formats()[0] or prefix in ("EL", "GR", "XI")
    ):
        country = iso_country(prefix)
        number = compact[2:]
        had_prefix = True
    elif prefix.isalpha() and len(compact) > 2 and not compact[2:].isalpha():
        # An unknown two-letter prefix, e.g. `CHE123456789`. Taken as a
        # country rather than as part of the number: a Swiss UID really does
        # start with its country, and treating it as digits would make the
        # whole thing unparseable.
        country = prefix
        number = compact[2:]
        had_prefix = True
    else:
        if not default_country:
            raise VatFormatError(
                f"{value!r} has no country in it, so there is no way to tell "
                "whose VAT ID it is. Write it with its country prefix — "
                "`ATU12345678` — or set the party's country first."
            )
        country = default_country.strip().upper()
        number = compact
        had_prefix = False

    return NormalisedVatId(
        full=f"{country}{number}",
        country=country,
        vies_country=vies_country(country) or country,
        number=number,
        had_prefix=had_prefix,
    )


def validate_format(normalised: NormalisedVatId) -> None:
    """Refuse a number that cannot be that country's. Before any network call.

    Two checks, in this order:

      1. the WSDL's own constraint — 2 to 12 characters from
         `[0-9A-Za-z+*.]`, the only officially published rule — which applies
         to every country including the ones with no entry;
      2. the per-country pattern, when the table has one.

    A country the table does not know passes the second check by definition.
    That is the Swiss case and it is deliberate: VIES does not cover it, the
    number is recorded as `not_applicable`, and refusing it here would make
    the supplier unsaveable.
    """
    patterns, wsdl = _formats()

    if not re.fullmatch(wsdl, normalised.number):
        raise VatFormatError(
            f"{normalised.full!r} is not a usable VAT ID. The number after "
            f"the country has to be 2 to 12 characters long and may contain "
            f"only letters, digits and the symbols + * . — this one is "
            f"{len(normalised.number)} character(s): {normalised.number!r}."
        )

    pattern = patterns.get(normalised.vies_country)
    if pattern is None:
        return
    if not re.fullmatch(pattern, normalised.number):
        raise VatFormatError(
            f"{normalised.full!r} does not look like a "
            f"{normalised.country} VAT ID. {normalised.country} numbers "
            f"match {pattern} after the country prefix — for example "
            f"{_example(normalised.vies_country)}. Check the number, or "
            "correct the pattern in region_packs/vat_formats.yaml if it is "
            "the pattern that is wrong."
        )


#: One example per country we have a decent one for, so the refusal shows a
#: shape rather than only a regular expression. Deliberately not exhaustive:
#: a missing example degrades to the pattern, which is still readable.
_EXAMPLES = {
    "AT": "ATU12345678",
    "DE": "DE123456789",
    "EL": "EL123456789",
    "FR": "FRXX123456789",
    "IT": "IT12345678901",
    "NL": "NL123456789B01",
    "XI": "XI123456789",
}


def _example(country: str) -> str:
    return _EXAMPLES.get(country, "the published format for that country")


def normalise_and_validate(
    value: str, *, default_country: Optional[str] = None
) -> NormalisedVatId:
    """The two steps together, which is what every caller wants."""
    normalised = normalise(value, default_country=default_country)
    validate_format(normalised)
    return normalised
