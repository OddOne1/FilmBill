"""Where a buyer is, relative to the seller.

**This is a membership fact, not a tax rule.** Which states are in the EU is
published, identical for every company, and changes when the EU changes — not
when an advisor's opinion changes. That is why it is a constant here and not
a region pack entry: CLAUDE.md rule 7 forbids `if country == "AT"` deciding a
tax question, and this decides none. It answers "is DE in the EU", which has
one answer for everybody.

`propose_tax` still takes the set as a keyword argument defaulting to this
constant, so the day a pack wants to supply its own — a non-EU installation,
or the morning after an accession — it can, without changing any caller.
"""

from typing import Optional

#: The 27 member states, ISO 3166-1 alpha-2, as of 2026-10. The UK is not in
#: it; Northern Ireland's goods protocol is a special case this engine does
#: not model and would be a region pack's business if it ever did.
EU_MEMBER_STATES: frozenset[str] = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
        "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL",
        "PT", "RO", "SE", "SI", "SK",
    }
)

SAME_COUNTRY = "same_country"
EU_OTHER = "eu_other"
NON_EU = "non_eu"


def region_of(
    seller_country: Optional[str],
    buyer_country: Optional[str],
    *,
    eu_member_states: frozenset[str] = EU_MEMBER_STATES,
) -> Optional[str]:
    """Which `BUYER_REGIONS` value describes this pair, or None.

    None when either country is unknown — and the caller must treat that as
    blocking rather than as a default. A buyer with no country is not
    "probably domestic": guessing that is how a reverse-charge invoice gets
    20 % Austrian VAT on it.

    Case-insensitive on the way in, because the company form upper-cases its
    country and a party imported from a CSV might not.
    """
    if not seller_country or not buyer_country:
        return None

    seller = seller_country.strip().upper()
    buyer = buyer_country.strip().upper()
    if not seller or not buyer:
        return None

    if seller == buyer:
        return SAME_COUNTRY
    if buyer in eu_member_states and seller in eu_member_states:
        return EU_OTHER
    return NON_EU
