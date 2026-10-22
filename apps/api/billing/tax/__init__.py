"""Tax: the proposal engine and its closed vocabulary.

`propose_tax` is the whole public surface (SCOPE §6.3): given the seller, the
buyer, the supply and the company's own tables, it returns what tax FilmBill
*proposes*, why, and what it is unsure about. It never decides anything that
depends on a country's interpretation — those live in the region packs as
data (CLAUDE.md rule 7), and a user can edit every one of them.

**FilmBill proposes, the user decides.** That is why the result carries
`reasons`, `warnings` and `blocking` rather than only a rate: a proposal
nobody can audit is indistinguishable from a guess, and this engine must
never guess. A missing home country or an inactive code is `blocking`, not a
plausible-looking 20 %.
"""

from .engine import (
    OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS,
    OVERRIDE_FIXED_CODE,
    OVERRIDE_KINDS,
    Override,
    Proposal,
    TaxClassData,
    TaxCodeData,
    TaxInputs,
    TaxRuleData,
    TaxWarning,
    WouldHaveApplied,
    propose_tax,
    warning_text,
)
from .packs import (
    GENERIC_PACK,
    PACK_DIRECTORY,
    PackClass,
    PackCode,
    PackRule,
    RegionPack,
    available_packs,
    load_pack,
    pack_for_country,
    pack_name_for_country,
)
from .regions import EU_MEMBER_STATES, region_of
from .treatments import TREATMENT_LABELS, TREATMENTS, treatment_label
from .vocabulary import (
    BUYER_REGIONS,
    BUYER_TYPES,
    CATEGORIES,
    CONDITION_KEYS,
    ITEM_CLASS_SENTINEL,
    SUPPLY_KINDS,
    WARNING_CODES,
    ZERO_RATE_CATEGORIES,
    PackError,
    validate_conditions,
    validate_outcome,
    validate_rate_for_category,
)

__all__ = [
    "BUYER_REGIONS",
    "OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS",
    "OVERRIDE_FIXED_CODE",
    "OVERRIDE_KINDS",
    "Override",
    "Proposal",
    "TaxClassData",
    "TaxCodeData",
    "TaxInputs",
    "TaxRuleData",
    "TaxWarning",
    "WouldHaveApplied",
    "propose_tax",
    "warning_text",
    "GENERIC_PACK",
    "PACK_DIRECTORY",
    "PackClass",
    "PackCode",
    "PackRule",
    "RegionPack",
    "available_packs",
    "load_pack",
    "pack_for_country",
    "pack_name_for_country",
    "BUYER_TYPES",
    "CATEGORIES",
    "CONDITION_KEYS",
    "EU_MEMBER_STATES",
    "ITEM_CLASS_SENTINEL",
    "PackError",
    "SUPPLY_KINDS",
    "TREATMENTS",
    "TREATMENT_LABELS",
    "WARNING_CODES",
    "ZERO_RATE_CATEGORIES",
    "region_of",
    "treatment_label",
    "validate_conditions",
    "validate_outcome",
    "validate_rate_for_category",
]
