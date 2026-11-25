"""Countries: ISO 3166-1, read from `apps/api/data/countries.json` (B1.2).

**Stored as two letters, shown as three.** Every country column holds the
ISO 3166-1 alpha-2 code, because that is what the region packs, the tax
rules and the VAT checks are keyed by. The screens show the English name
with the alpha-3 code ("Austria (AUT)") — that is presentation, and it lives
in the web app's `countryLabel`. Nothing here changes what is stored.

**One source of truth.** The data file is the list; `apps/web/lib/
countries.json` must be a byte-identical copy, and `tests/test_countries.py`
fails when it is not. The names are in the file rather than taken from
`Intl.DisplayNames`, so a test environment without full ICU data shows the
same names as a browser.

**Greece is `GR`.** VIES calls it `EL`, and that mapping stays where VIES is
called (`billing/parties/vat.py`) — never in a stored value. Northern
Ireland's `XI` is a VIES prefix and not a country, so it is not in the list;
a tax identifier's country is validated by the tax-id rules, not here.

A stored code that is not in the list is never silently changed: validation
applies to what a request WRITES, and the screens show an unknown stored
code as itself with "(unknown code)".
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "countries.json"


@lru_cache(maxsize=1)
def countries() -> tuple[dict, ...]:
    """Every country, as `{alpha2, alpha3, name}`, in the file's order."""
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return tuple(data["countries"])


@lru_cache(maxsize=1)
def country_codes() -> frozenset[str]:
    """The valid alpha-2 codes."""
    return frozenset(row["alpha2"] for row in countries())


def normalise_country(value: Optional[str]) -> Optional[str]:
    """A request's country as it is stored: trimmed, upper case, or None.

    Empty means none. Anything else must be a code in the list; the refusal
    says what was sent and what is wanted, in a sentence a form can show.
    """
    if value is None:
        return None
    code = value.strip().upper()
    if not code:
        return None
    if code not in country_codes():
        raise ValueError(
            f"“{value.strip()}” is not a country code. Pick the country from "
            f"the list, or send its two-letter ISO code, for example AT."
        )
    return code
