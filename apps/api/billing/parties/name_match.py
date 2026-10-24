"""Comparing a party's name with the one a tax register holds.

Pure, and deliberately dull. The thresholds and the legal-form list are in
`region_packs/name_match.yaml` because they grow with every country a user
invoices.

**Why compare at all.** A VAT number can be perfectly valid and belong to
somebody else — a typo that lands on a real company, or a number copied from
the wrong invoice. The registry's name is the cheap check against that, and
it is the only one available: nothing else in a VIES answer says *whose*
number this is.

**Why it never corrects anything.** FilmBill does not write a registry's
name onto a party. Registers hold abbreviated, stale and differently
transliterated forms; the party's name is what goes on an invoice and it
belongs to the company, not to us. So the output is a judgement with four
values and no side effects, and `unknown` is one of them.

**`unknown` is not a soft `mismatch`.** Germany returns `---` for every
VIES lookup — it does not disclose names at all — and several other member
states return placeholders. Reporting that as a mismatch would have users
chasing data that was never wrong, every single time, which is how a warning
becomes something people click past. So a placeholder is `unknown` and says
so in words.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

MATCH = "match"
PARTIAL = "partial"
MISMATCH = "mismatch"
UNKNOWN = "unknown"
RESULTS = (MATCH, PARTIAL, MISMATCH, UNKNOWN)

#: Address comparison has no `partial`: an address agrees, differs, or was
#: not returned. It is informational, and a three-way answer that nobody
#: acts on differently is three ways of saying "look at it yourself".
ADDRESS_MATCH = "match"
ADDRESS_DIFFERS = "differs"
ADDRESS_UNKNOWN = "unknown"

DATA_PATH = (
    Path(__file__).resolve().parents[2] / "region_packs" / "name_match.yaml"
)

#: `ß` → `ss` before the accent-stripping pass, because NFKD leaves `ß`
#: alone — it has no decomposition — and a German company spelled
#: "Straßen GmbH" in one register and "Strassen GmbH" in another is one
#: company. The other three are the ones that would otherwise lose a letter.
_TRANSLITERATE = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "ø": "oe",
        "đ": "d",
        "ł": "l",
        "ħ": "h",
        "ı": "i",
    }
)


@dataclass(frozen=True)
class NameComparison:
    """The verdict, the score, and a sentence a person can read."""

    result: str
    score: int
    detail: str
    #: Both names as compared, so a reviewer can see what the rules did to
    #: them. Without this, "mismatch" on two names that look identical on
    #: screen is unarguable — and the legal-form stripping is exactly the
    #: kind of step that goes wrong invisibly.
    normalised_party: str = ""
    normalised_registry: str = ""


@lru_cache(maxsize=1)
def _data() -> dict:
    with DATA_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@lru_cache(maxsize=1)
def _legal_forms() -> tuple[str, ...]:
    """Longest first.

    Not a style choice: "gesellschaft mit beschrankter haftung" must be
    removed before "gesellschaft", or the remainder leaves
    "mit beschrankter haftung" behind as if it were part of the name.
    """
    return tuple(
        sorted(_data()["legal_forms"], key=len, reverse=True)
    )


def _fold(value: str) -> str:
    """Lower case, accents gone, punctuation to spaces, spacing collapsed."""
    text = (value or "").strip().lower().translate(_TRANSLITERATE)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # `&` is "and" in half the registers and `+` in a few: spell it out so
    # "Meier & Sohn" and "Meier und Sohn" fold together.
    text = text.replace("&", " and ").replace("+", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Dotted initialisms: "A.B.C. Media" folds to "a b c media", which shares
    # only one word with "ABC Media" and would score as a partial. Runs of
    # two or more single LETTERS are joined back up. Letters only, and only
    # runs — "A 1 Media" keeps its parts, and a genuine one-letter word
    # (there are companies called "O") is left alone.
    return re.sub(
        r"\b(?:[a-z] ){1,}[a-z]\b",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )


def is_placeholder(value: Optional[str]) -> bool:
    """Whether a registry returned nothing usable.

    Checked on the FOLDED value, so `"---"`, `" - - - "` and `"N/A"` all
    land here rather than three of them being compared as if they were
    names.
    """
    if not value or not value.strip():
        return True
    folded = _fold(value)
    if not folded:
        # Punctuation only — `---` folds to the empty string.
        return True
    return folded in {_fold(entry) for entry in _data()["placeholders"]}


def normalise_name(value: str) -> str:
    """A name reduced to what identifies it.

    Legal form stripped, noise words dropped, tokens sorted. Sorting is the
    one aggressive step and it earns its place: registers routinely invert
    the parts of a name ("Rundfunk Österreichischer" appears in real data),
    and word order is not identity.
    """
    folded = _fold(value)
    if not folded:
        return ""

    # Legal forms, longest first, bounded by word edges so "ag" does not
    # eat the "ag" in "Hagenberg".
    for form in _legal_forms():
        folded = re.sub(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", " ", folded)
    folded = re.sub(r"\s+", " ", folded).strip()

    noise = set(_data()["noise_words"])
    tokens = [token for token in folded.split() if token not in noise]
    if not tokens:
        # The whole name was legal form and noise. Fall back to the folded
        # form rather than to nothing, so two such names can still be
        # compared — "Die Firma GmbH" is a real thing to be called.
        return folded
    return " ".join(sorted(tokens))


def compare_names(party_name: str, registry_name: Optional[str]) -> NameComparison:
    """How the registry's name compares with the party's.

    Never raises, never writes, and never returns a verdict without a
    sentence: a chip that says "mismatch" with nothing beside it is a chip
    people learn to ignore.
    """
    if is_placeholder(registry_name):
        return NameComparison(
            result=UNKNOWN,
            score=0,
            detail=(
                "The register did not return a name, so there is nothing to "
                "compare. Several countries never do — Germany answers every "
                "lookup this way — and it says nothing about the number."
            ),
        )
    if is_placeholder(party_name):
        return NameComparison(
            result=UNKNOWN,
            score=0,
            detail="This party has no name to compare.",
        )

    left = normalise_name(party_name)
    right = normalise_name(registry_name or "")
    if not left or not right:
        return NameComparison(
            result=UNKNOWN,
            score=0,
            detail=(
                "One of the two names is nothing but a legal form, so there "
                "is no name left to compare."
            ),
            normalised_party=left,
            normalised_registry=right,
        )

    score = _score(left, right)
    thresholds = _data()["thresholds"]

    if score >= thresholds["match"]:
        result = MATCH
        detail = f"The register's name agrees with this party's ({score} / 100)."
    elif score >= thresholds["partial"]:
        result = PARTIAL
        detail = (
            f"The register's name is close but not the same ({score} / 100). "
            "Often an abbreviation or a trading name — worth a look, not "
            "necessarily wrong."
        )
    else:
        result = MISMATCH
        detail = (
            f"The register's name does not look like this party's "
            f"({score} / 100). A valid number belonging to somebody else is "
            "exactly what this check is for."
        )
    return NameComparison(
        result=result,
        score=score,
        detail=detail,
        normalised_party=left,
        normalised_registry=right,
    )


def _score(left: str, right: str) -> int:
    """0..100. Token containment first, then character similarity.

    Containment matters more than distance here because the commonest real
    difference is one name being a prefix of the other — "ORF" against
    "ORF Österreichischer Rundfunk" — and character similarity scores that
    pair terribly while a human has no doubt at all.
    """
    if left == right:
        return 100

    left_tokens = set(left.split())
    right_tokens = set(right.split())
    shared = left_tokens & right_tokens
    if shared:
        # How much of the SHORTER name is accounted for. Using the shorter
        # side is the point: a register that holds the full legal name of a
        # company we call "ORF" should match, and dividing by the longer
        # side would punish it for being more complete.
        smaller = min(len(left_tokens), len(right_tokens))
        containment = len(shared) / smaller
        if containment == 1.0:
            # Every word of the shorter name appears in the longer one.
            extra = abs(len(left_tokens) - len(right_tokens))
            if extra == 0:
                return 100
            # Each additional word costs a little: "Alpha Film" against
            # "Alpha Film Produktion" is close; against "Alpha Film Beta
            # Gamma Delta Epsilon" it is not.
            return max(_data()["thresholds"]["partial"], 96 - 6 * extra)

    ratio = SequenceMatcher(None, left, right).ratio()
    token_ratio = (
        len(shared) / max(len(left_tokens | right_tokens), 1) if shared else 0.0
    )
    # The better of the two readings, because they fail in different ways:
    # character similarity is blind to word order and token overlap is blind
    # to spelling.
    return int(round(100 * max(ratio, token_ratio)))


def compare_addresses(
    party_address: Optional[str], registry_address: Optional[str]
) -> str:
    """`match` | `differs` | `unknown`. Informational only.

    Deliberately crude. Addresses are formatted differently by every
    register, and a careful comparison would produce confident answers that
    are wrong — so this answers only when the two clearly agree or clearly
    do not, and `unknown` whenever either side is missing.
    """
    if is_placeholder(registry_address) or is_placeholder(party_address):
        return ADDRESS_UNKNOWN

    left = _fold(party_address or "")
    right = _fold(registry_address or "")
    if not left or not right:
        return ADDRESS_UNKNOWN
    if left == right:
        return ADDRESS_MATCH

    # Agreement on the parts that identify a place: the digits (house number
    # and postcode) and the longest word. A register that writes
    # "Wuerzburggasse 30, 1136 Wien" against our "Würzburggasse 30, 1136
    # Wien" agrees, and should not be reported as differing because of a
    # transliteration.
    left_digits = set(re.findall(r"\d+", left))
    right_digits = set(re.findall(r"\d+", right))
    if left_digits and left_digits == right_digits:
        shared_words = set(left.split()) & set(right.split())
        if shared_words:
            return ADDRESS_MATCH

    ratio = SequenceMatcher(None, left, right).ratio()
    return ADDRESS_MATCH if ratio >= 0.9 else ADDRESS_DIFFERS
