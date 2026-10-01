"""IBAN structure and check digits. No network, no service, no country table.

ISO 13616 / ISO 7064 MOD-97-10. Everything needed to reject a typo is in the
number itself, which is the entire point of the check digits, so this calls
nothing and can never be the reason a form hangs.

**What this does NOT claim.** A valid IBAN is a well-formed one, not an
existing one. Mod-97 catches every single-digit error and almost every
transposition — which is what a person entering a bank account off a
letterhead actually gets wrong — and says nothing about whether the account
is open, or whose it is. Anything stronger needs a bank, and FilmBill is not
going to call one to let somebody save a settings form.

**Length is checked per country where the country is known, and only then.**
The registry below covers SEPA plus the countries a Vienna production company
plausibly invoices. An unknown country code passes on structure and check
digits alone rather than being refused: the registry gains entries over time
and a self-hosted install in a country this file has not heard of must not be
unable to enter its own bank account.
"""

import re
import string

#: IBAN lengths by country, ISO 13616. SEPA in full, plus the non-SEPA
#: countries most likely to appear on a production's invoices.
#:
#: Deliberately data rather than logic (CLAUDE.md rule 7) — adding a country
#: is a line here, never a branch.
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24,
    "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18,
    "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IS": 26,
    "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21,
    "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19,
    "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15, "PK": 24, "PL": 28,
    "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SE": 24,
    "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24,
    "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}

#: Two letters, two digits, then up to 30 alphanumerics. Case-insensitive
#: here; `normalise_iban` upper-cases before this is applied.
_IBAN_SHAPE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}$")

#: A → 10, B → 11, … Z → 35, per ISO 13616's conversion step.
_LETTER_VALUES = {
    letter: str(index + 10) for index, letter in enumerate(string.ascii_uppercase)
}


class InvalidIBAN(ValueError):
    """A readable reason, meant to be shown to whoever typed it.

    Prose rather than a code, because there is exactly one consumer — the bank
    accounts form — and "the check digits do not match; one character is
    probably wrong" tells a person what to do, while `IBAN_CHECKSUM_FAILED`
    tells them to ask someone.
    """


def normalise_iban(value: str) -> str:
    """Strip spaces, upper-case. The form people are used to typing, in.

    Printed IBANs are grouped in fours and people type them that way. Storing
    the compact form means a comparison never has to care about spacing, and
    `format_iban` puts the groups back for display.
    """
    return re.sub(r"\s+", "", value or "").upper()


def _mod_97(iban: str) -> int:
    """ISO 7064 MOD-97-10 over the rearranged, digit-expanded IBAN.

    Chunked rather than one enormous `int(...) % 97`. A 34-character IBAN
    expands to ~40 digits, which Python handles fine — but Python also caps
    `int()` on long strings by default since 3.11 (`sys.set_int_max_str_digits`
    is 4300, so this particular length is safe), and a running remainder is
    both the textbook formulation and obviously bounded.
    """
    rearranged = iban[4:] + iban[:4]
    expanded = "".join(_LETTER_VALUES.get(char, char) for char in rearranged)

    remainder = 0
    for index in range(0, len(expanded), 9):
        remainder = int(str(remainder) + expanded[index : index + 9]) % 97
    return remainder


def validate_iban(value: str) -> str:
    """Return the normalised IBAN, or raise `InvalidIBAN` saying what is wrong.

    Returns the cleaned value rather than a bool, so a caller cannot validate
    one string and then store a different one — which is how a database ends
    up holding the spaced version for some rows and the compact version for
    others, and how an equality check starts missing.
    """
    iban = normalise_iban(value)

    if not iban:
        raise InvalidIBAN("An IBAN is required.")

    if not _IBAN_SHAPE.match(iban):
        raise InvalidIBAN(
            "That does not look like an IBAN. It starts with two letters for "
            "the country, then two digits, then the account — for example "
            "AT61 1904 3002 3457 3201."
        )

    country = iban[:2]
    expected = IBAN_LENGTHS.get(country)
    if expected is not None and len(iban) != expected:
        raise InvalidIBAN(
            f"An {country} IBAN has {expected} characters; this one has "
            f"{len(iban)}."
        )

    if _mod_97(iban) != 1:
        # The most common outcome by far, and the message says what to do
        # about it. Deliberately does not say WHICH character — mod-97 knows
        # that something is wrong, not where.
        raise InvalidIBAN(
            "The check digits do not match. One character is probably wrong — "
            "compare it against your bank statement."
        )

    return iban


def format_iban(value: str) -> str:
    """Groups of four, for display. `AT61 1904 3002 3457 3201`."""
    iban = normalise_iban(value)
    return " ".join(iban[index : index + 4] for index in range(0, len(iban), 4))
