from .user import User
from .activity import ActivityLog, Notification, NotificationType
from .site_settings import SiteSettings
from .email_settings import EmailSettings
# Imported here and not only where they are used: alembic/env.py does
# `import models` and reads `Base.metadata`, so a model that is not re-exported
# from this file is invisible to `alembic check` — which is the job that would
# otherwise not notice a table with no migration behind it (CLAUDE.md 17d).
from .company import (
    Company,
    CompanyBankAccount,
    CompanyMembership,
    CompanyRole,
    CompanyScoped,
)
from .number_series import NumberAllocation, NumberSeries
from .audit import AuditEvent
from .tax import TaxClass, TaxCode, TaxRule
from .catalog import (
    Item,
    ItemCategory,
    ItemRate,
    RentalFactor,
    RentalFactorGroup,
    Unit,
)
from .project import (
    Budget,
    BudgetActual,
    BudgetAdjustment,
    BudgetCommitment,
    BudgetGlobal,
    BudgetLine,
    BudgetSection,
    Project,
    ProjectBlock,
    ProjectDay,
)
from .crew import PartyCrewRole
from .device import Device, DeviceAlternative, DeviceAssociation, DeviceUnit
from .party import (
    Party,
    PartyAddress,
    PartyBankAccount,
    PartyContactPoint,
    PartyTaxId,
    PartyVatCheck,
)

__all__ = [
    "PartyCrewRole",
    "Device",
    "DeviceAlternative",
    "DeviceAssociation",
    "DeviceUnit",
    "User",
    "ActivityLog",
    "Notification",
    "NotificationType",
    "SiteSettings",
    "EmailSettings",
    "Company",
    "CompanyBankAccount",
    "CompanyMembership",
    "CompanyRole",
    "CompanyScoped",
    "NumberSeries",
    "NumberAllocation",
    "AuditEvent",
    "TaxCode",
    "TaxClass",
    "TaxRule",
    "Party",
    "PartyAddress",
    "PartyBankAccount",
    "PartyContactPoint",
    "PartyTaxId",
    "PartyVatCheck",
    "Project",
    "ProjectBlock",
    "ProjectDay",
    "Budget",
    "BudgetSection",
    "BudgetLine",
    "BudgetGlobal",
    "BudgetAdjustment",
    "BudgetCommitment",
    "BudgetActual",
    "Item",
    "ItemCategory",
    "ItemRate",
    "RentalFactor",
    "RentalFactorGroup",
    "Unit",
]
