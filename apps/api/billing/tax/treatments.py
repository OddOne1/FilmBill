"""The tax treatments, as a system enum in code.

**Why these are code and the rates are data.** A treatment is not a number
somebody edits — it decides BEHAVIOUR. `eu_reverse_charge` means the invoice
carries a note and the recipient accounts for the tax; `intra_community_supply`
means the line belongs in the EC Sales List (ZM); `small_business_exemption`
means no tax is shown anywhere. Code elsewhere in the system will branch on
these, and a treatment invented in a YAML file would be a branch nothing
takes — a silent no-op on a real invoice.

Rates, categories, legal notes and the rules that choose between treatments
are all data in the region packs (CLAUDE.md rule 7). The list of things a
treatment can BE is not.

The labels are here, in both languages, because a document template and a
settings screen must not each spell "Innergemeinschaftliche Lieferung"
their own way.
"""

from typing import Mapping

#: Domestic tax at the rate of the item's tax class. The ordinary case.
DOMESTIC = "domestic"
#: B2B services to a verified VAT ID in another EU country: the recipient
#: accounts for the tax, and the invoice says so.
EU_REVERSE_CHARGE = "eu_reverse_charge"
#: Goods to a verified VAT ID in another EU country.
INTRA_COMMUNITY_SUPPLY = "intra_community_supply"
#: Goods leaving the EU.
EXPORT = "export"
#: The place of supply is abroad, so there is nothing to tax here.
NOT_TAXABLE_IN_COUNTRY = "not_taxable_in_country"
#: The seller is below the small-business threshold and charges no tax.
SMALL_BUSINESS_EXEMPTION = "small_business_exemption"
#: Intra-group recharge. In SCOPE §6.2 and reachable only by configuration —
#: no shipped rule proposes it, because whether an internal recharge is
#: taxable is exactly the kind of question FilmBill does not answer (D19).
INTERNAL_RECHARGE = "internal_recharge"
#: A treatment the user defined by pointing a rule at a code of their own.
CUSTOM = "custom"

#: Every treatment there is. Ordered as SCOPE §6.2 lists them, which is also
#: the order the settings screen shows.
TREATMENTS: tuple[str, ...] = (
    DOMESTIC,
    EU_REVERSE_CHARGE,
    INTRA_COMMUNITY_SUPPLY,
    EXPORT,
    NOT_TAXABLE_IN_COUNTRY,
    SMALL_BUSINESS_EXEMPTION,
    INTERNAL_RECHARGE,
    CUSTOM,
)

#: `{treatment: {lang: label}}`. German first because the product's first
#: users are Austrian; English because the code and the tests are.
TREATMENT_LABELS: Mapping[str, Mapping[str, str]] = {
    DOMESTIC: {"de": "Inland", "en": "Domestic"},
    EU_REVERSE_CHARGE: {
        "de": "Reverse Charge (EU)",
        "en": "Reverse charge (EU)",
    },
    INTRA_COMMUNITY_SUPPLY: {
        "de": "Innergemeinschaftliche Lieferung",
        "en": "Intra-community supply",
    },
    EXPORT: {"de": "Ausfuhrlieferung", "en": "Export"},
    NOT_TAXABLE_IN_COUNTRY: {
        "de": "Nicht im Inland steuerbar",
        "en": "Not taxable in this country",
    },
    SMALL_BUSINESS_EXEMPTION: {
        "de": "Kleinunternehmerregelung",
        "en": "Small business exemption",
    },
    INTERNAL_RECHARGE: {
        "de": "Interne Weiterverrechnung",
        "en": "Internal recharge",
    },
    CUSTOM: {"de": "Benutzerdefiniert", "en": "Custom"},
}

#: What an unknown language falls back to. Not the first key of whatever dict
#: happens to be there: a label that changes when somebody reorders a literal
#: is a label that will differ between two screens.
DEFAULT_LANGUAGE = "en"


def treatment_label(treatment: str, lang: str) -> str:
    """The label for `treatment` in `lang`.

    Falls back to English and then to the treatment's own key, so a treatment
    added here without a label still renders as something a reader can match
    to this file instead of as an empty string.
    """
    labels = TREATMENT_LABELS.get(treatment)
    if labels is None:
        return treatment
    return labels.get(lang) or labels.get(DEFAULT_LANGUAGE) or treatment
