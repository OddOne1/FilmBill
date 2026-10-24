"""Normalising and format-checking a typed tax identifier.

Pure: no database, no network, no Flask/FastAPI. The subprocess
import-boundary test enforces that, same as the rest of `billing/`.

**What this module is for.** A wrong format must be refused *before* a
network call, for two reasons that both come from the registries themselves:
VIES's own terms limit use to confirming real customers' numbers, so firing
typos at it is both rude and slow; and a registry asked about a malformed
number answers "invalid", which we would then store as a finding about the
customer when it is a finding about the typing.

**What it is NOT for.** It never decides that a number is real. A number can
be perfectly well-formed and belong to nobody. `valid` comes only from a
registry, and the only thing this module can say is "this could not possibly
be one" — which is why every refusal names the rule it applied and what it
saw, so a user whose real number we reject can tell us the rule is wrong.

Both check-digit algorithms below are implemented from the issuing
authority's own published document, cited in
`region_packs/tax_id_formats.yaml` next to each one, and each is tested
against that authority's own worked example rather than against examples of
our own devising — our own example could be wrong in the same way the
implementation is.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

#: The data file. Alongside `vat_formats.yaml`, which stays per-country for
#: EU VAT; this one is per kind.
FORMATS_PATH = (
    Path(__file__).resolve().parents[2] / "region_packs" / "tax_id_formats.yaml"
)

#: Characters people put in tax IDs that carry no information: spaces,
#: dots, hyphens, slashes and the non-breaking space a copy-paste from a
#: PDF leaves behind (which is invisible and has cost somebody an afternoon
#: in every project that did not strip it).
_SEPARATORS = re.compile(r"[\s.\-/ ‐-―]+")


class TaxIdFormatError(ValueError):
    """The value cannot be a tax ID of this kind.

    Carries a sentence meant for a person: which rule was applied and what
    the value looked like after normalisation, so the refusal is arguable
    rather than merely final.
    """

    def __init__(self, message: str, *, kind: str, value: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.value = value


@dataclass(frozen=True)
class NormalisedTaxId:
    """A tax ID as it will be stored, with what was typed kept beside it."""

    kind: str
    value: str
    as_entered: str
    country: Optional[str]
    #: The Swiss VAT-register suffix (`MWST`/`TVA`/`IVA`) if the user typed
    #: one. Stripped from `value` because it is not part of the identifier,
    #: and kept here because it is the user telling us the company is
    #: VAT-registered — worth recording, never worth validating.
    vat_suffix: Optional[str] = None
    #: True when this kind can be confirmed against a register we may query.
    verifiable: bool = False


@lru_cache(maxsize=1)
def load_formats() -> dict:
    """The format data, read once."""
    with FORMATS_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def kinds() -> tuple[str, ...]:
    return tuple(load_formats()["kinds"])


def vat_like_kinds() -> tuple[str, ...]:
    return tuple(load_formats()["vat_like_kinds"])


def is_verifiable(kind: str) -> bool:
    """Whether any provider can answer about this kind.

    A `uid_ch` is `false` here and that is a sourced decision, not an
    omission: no official programmatic interface to the Swiss UID register
    could be found and no terms for automated querying are published (D61).
    """
    entry = load_formats()["kinds"].get(kind)
    return bool(entry and entry.get("verifiable"))


def strip_separators(value: str) -> str:
    """Upper case, separators gone, Unicode normalised.

    NFKC first, because a full-width digit pasted from a Japanese locale
    looks like a digit to a human and is not one to `isdigit`-style checks
    everywhere downstream.
    """
    cleaned = unicodedata.normalize("NFKC", value or "")
    return _SEPARATORS.sub("", cleaned).upper()


# ── Check digits ─────────────────────────────────────────────────────────


def ch_uid_check_digit(payload: str) -> Optional[int]:
    """The eCH-0097 check digit for eight payload digits, or None.

    eCH-0097 V5.2.0 section 2.4.2, page 8, verbatim: "Die Prüfziffer wird
    nach dem Modulo 11 berechnet", multipliers by position 5, 4, 3, 2, 7, 6,
    5, 4; the products are summed; "Der verbleibende Rest (Modulo 11) wird
    von 11 subtrahiert und die daraus resultierende Zahl ist die Prüfziffer."

    Two special cases the standard states explicitly, and both matter:

    * a remainder of 0 means the check digit is 0 (not 11);
    * a computed check digit of 10 means **the number is never issued** —
      the standard's words are "so wird diese Nummer nicht vergeben". So
      `None` here means no valid UID can end in anything for this payload,
      and the caller must refuse rather than pick a digit.
    """
    entry = load_formats()["kinds"]["uid_ch"]
    weights = entry["check_weights"]
    total = sum(int(d) * w for d, w in zip(payload, weights))
    remainder = total % entry["check_modulus"]
    if remainder == 0:
        return 0
    digit = entry["check_modulus"] - remainder
    if digit == 10:
        return None
    return digit


def abn_is_valid(value: str) -> bool:
    """The ABR's modulus-89 check for an 11-digit ABN.

    https://abr.business.gov.au/Help/AbnFormat, verbatim steps: "Subtract 1
    from the first (left-most) digit of the ABN to give a new 11 digit
    number", multiply each digit by its positional weight, sum the products,
    divide by 89, "If the remainder is zero the number is a valid ABN".
    """
    entry = load_formats()["kinds"]["abn"]
    if len(value) != 11 or not value.isdigit():
        return False
    digits = [int(c) for c in value]
    if entry.get("check_subtract_one_from_first"):
        digits[0] -= 1
        # A leading zero would go to -1. The ABR's step says subtract 1 from
        # the first digit; an ABN beginning 0 is not issued, and -1 * 10
        # cannot reach a zero remainder by accident, so no special case is
        # needed — but it is worth saying that out loud, because a reader
        # checking for an off-by-one here deserves an answer.
    total = sum(d * w for d, w in zip(digits, entry["check_weights"]))
    return total % entry["check_modulus"] == 0


# ── Normalisation per kind ───────────────────────────────────────────────


def _normalise_uid_ch(raw: str, as_entered: str) -> NormalisedTaxId:
    entry = load_formats()["kinds"]["uid_ch"]
    value = strip_separators(raw)

    suffix = None
    for candidate in entry["vat_suffixes"]:
        if value.endswith(candidate):
            suffix = candidate
            value = value[: -len(candidate)]
            break

    if not re.match(entry["pattern"], value):
        raise TaxIdFormatError(
            f"{as_entered!r} does not look like a Swiss UID. A UID is CHE or "
            "ADM followed by nine digits, usually written "
            "CHE-123.456.789 — optionally with MWST, TVA or IVA after it.",
            kind="uid_ch",
            value=value,
        )

    digits = value[3:]
    expected = ch_uid_check_digit(digits[:8])
    if expected is None or expected != int(digits[8]):
        raise TaxIdFormatError(
            f"{as_entered!r} has the right shape but its check digit does "
            "not add up, so it cannot be a real UID. The rule is the "
            "modulo-11 calculation in eCH-0097 section 2.4.2; if you are "
            "sure the number is right, the format data may need correcting.",
            kind="uid_ch",
            value=value,
        )

    return NormalisedTaxId(
        kind="uid_ch",
        value=value,
        as_entered=as_entered,
        country="CH",
        vat_suffix=suffix,
        verifiable=False,
    )


def _normalise_abn(raw: str, as_entered: str) -> NormalisedTaxId:
    value = strip_separators(raw)
    if value.startswith("ABN"):
        value = value[3:]

    if not re.match(load_formats()["kinds"]["abn"]["pattern"], value):
        raise TaxIdFormatError(
            f"{as_entered!r} does not look like an ABN. An ABN is eleven "
            "digits, usually written 51 824 753 556.",
            kind="abn",
            value=value,
        )
    if not abn_is_valid(value):
        raise TaxIdFormatError(
            f"{as_entered!r} is eleven digits but fails the ABN check "
            "calculation, so it cannot be a real ABN. The rule is the "
            "modulus-89 calculation the Australian Business Register "
            "publishes at abr.business.gov.au/Help/AbnFormat.",
            kind="abn",
            value=value,
        )
    return NormalisedTaxId(
        kind="abn",
        value=value,
        as_entered=as_entered,
        country="AU",
        verifiable=True,
    )


def _normalise_free_text(kind: str, raw: str, as_entered: str,
                         country: Optional[str]) -> NormalisedTaxId:
    """A kind with no published check: shape bound only.

    `ein` gets its digits stripped of separators because NN-NNNNNNN is a
    display convention; the others keep what was typed, minus outer
    whitespace, because a Firmenbuchnummer like `FN 123456a` means exactly
    that and mangling it would lose information.
    """
    formats = load_formats()
    entry = formats["kinds"][kind]
    limit = formats["free_text_max_length"]

    if kind == "ein":
        value = strip_separators(raw)
        if not re.match(entry["pattern"], value):
            raise TaxIdFormatError(
                f"{as_entered!r} does not look like an EIN. An EIN is nine "
                "digits, usually written 12-3456789. There is no public "
                "register to check it against, so the number is taken as "
                "you type it.",
                kind=kind,
                value=value,
            )
        return NormalisedTaxId(
            kind=kind,
            value=value,
            as_entered=as_entered,
            country=country or "US",
            verifiable=False,
        )

    value = " ".join((raw or "").split())
    if not value:
        raise TaxIdFormatError(
            "An identifier cannot be empty.", kind=kind, value=""
        )
    if len(value) > limit:
        raise TaxIdFormatError(
            f"That is {len(value)} characters long, and an identifier of "
            f"this kind is limited to {limit}. If you meant to record an "
            "explanation rather than a number, the notes field beside it "
            "takes any length.",
            kind=kind,
            value=value[:limit],
        )
    return NormalisedTaxId(
        kind=kind,
        value=value,
        as_entered=as_entered,
        country=country,
        verifiable=False,
    )


def normalise(kind: str, raw: str, *, country: Optional[str] = None) -> NormalisedTaxId:
    """Normalise and format-check one identifier.

    `country` is used only by `vat` (where the prefix usually supplies it)
    and by the free-text kinds, which have no way to know it.

    Raises `TaxIdFormatError` with a readable sentence. Never consults a
    network, never decides a number is real.
    """
    if kind not in kinds():
        raise TaxIdFormatError(
            f"{kind!r} is not a kind of tax identifier this knows about. "
            f"The kinds are: {', '.join(kinds())}.",
            kind=kind,
            value=raw or "",
        )

    as_entered = (raw or "").strip()
    if not as_entered:
        raise TaxIdFormatError(
            "An identifier cannot be empty.", kind=kind, value=""
        )

    if kind == "vat":
        # Delegated whole to P1b's module, which owns the per-country
        # patterns and the EL/GR mapping. One implementation of the EU rules,
        # not two that could disagree.
        #
        # **Its exception is translated, not propagated.** This module's
        # contract is that it raises `TaxIdFormatError` and nothing else; a
        # second type leaking out means every caller has to catch two, and
        # one of them will catch one. That is not hypothetical — the first
        # version of this did propagate `VatFormatError`, the router caught
        # only `TaxIdFormatError`, and the escaping exception left the
        # request's transaction aborted, which showed up as the test suite
        # HANGING rather than as a 500.
        from .vat import VatFormatError, normalise_and_validate

        try:
            normalised = normalise_and_validate(
                as_entered, default_country=country
            )
        except VatFormatError as error:
            raise TaxIdFormatError(
                str(error), kind="vat", value=as_entered
            ) from None
        return NormalisedTaxId(
            kind="vat",
            value=normalised.full,
            as_entered=as_entered,
            # `.country` is the ISO form — GR, where `.vies_country` is EL.
            # Storing ISO because that is what the party's `tax_country` and
            # every region pack use; the provider converts on the way out.
            country=normalised.country,
            verifiable=True,
        )
    if kind == "uid_ch":
        return _normalise_uid_ch(as_entered, as_entered)
    if kind == "abn":
        return _normalise_abn(as_entered, as_entered)
    return _normalise_free_text(kind, as_entered, as_entered, country)
