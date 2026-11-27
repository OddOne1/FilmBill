"""Real rows for the real-database company tests.

Not fixtures, because every test here needs a different *shape* of world —
two companies and one member, or one company and six roles — and a fixture
per shape would be more code than the shapes. These are plain functions over
the `pg_db` session.

Everything committed, always. The middleware that runs before a route opens
its OWN session (see `conftest.pg_db`), so a row left uncommitted in the test's
session is a row the account gate and the setup guard cannot see, and the
request fails for a reason that has nothing to do with what is being tested.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from apps.api.models.company import (
    Company,
    CompanyBankAccount,
    CompanyMembership,
    CompanyRole,
)
from apps.api.models.user import User, UserGlobalRole, UserStatus
from apps.api.services.auth_service import (
    create_access_token,
    create_refresh_token,
    hash_password,
)

#: One bcrypt hash, computed once per process rather than per user. bcrypt is
#: deliberately slow and the permission matrix creates six users; hashing the
#: same password six times per test is ten seconds of nothing.
_PASSWORD = "Rehearsal-Dinner-Truck-42"
_PASSWORD_HASH: Optional[str] = None


def password() -> str:
    return _PASSWORD


def _hash() -> str:
    global _PASSWORD_HASH
    if _PASSWORD_HASH is None:
        _PASSWORD_HASH = hash_password(_PASSWORD)
    return _PASSWORD_HASH


def make_user(
    db,
    email: str,
    *,
    global_role: UserGlobalRole = UserGlobalRole.user,
    with_password: bool = True,
    two_factor_enabled: bool = False,
    status: UserStatus = UserStatus.active,
) -> User:
    """An account that can actually make a request.

    `backup_email_verified_at` is set on every one of them. The account gate
    (middleware/account_gate.py) answers 403 to every non-/auth route while a
    backup address is outstanding, so a user without it would 403 on every
    company endpoint and each of these tests would pass for entirely the wrong
    reason.
    """
    user = User(
        email=email,
        first_name="Test",
        last_name=email.split("@")[0],
        password_hash=_hash() if with_password else None,
        status=status,
        role=global_role,
        email_verified=True,
        two_factor_enabled=two_factor_enabled,
        two_factor_method="totp" if two_factor_enabled else None,
        totp_secret_encrypted="not-a-real-secret" if two_factor_enabled else None,
        backup_email=f"backup+{email}",
        backup_email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_superadmin(db, email: str = "installation-root@example.com") -> User:
    """The installation administrator.

    Most of these tests need one to exist even when it is not the caller,
    because `SetupGuardMiddleware` answers 503 to everything until there is
    a superadmin — which would make every assertion below read "503".
    """
    return make_user(db, email, global_role=UserGlobalRole.superadmin)


def make_company(db, legal_name: str, **fields) -> Company:
    company = Company(legal_name=legal_name, **fields)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def grant(
    db,
    company: Company,
    user: User,
    role: CompanyRole,
    *,
    expires_at: Optional[datetime] = None,
    revoked_at: Optional[datetime] = None,
) -> CompanyMembership:
    """A membership row, written directly.

    Directly rather than through `POST /company/members`, deliberately: these
    tests are about what a membership DOES, and building the world through
    the endpoint under test would mean a bug in the endpoint could hide a bug
    in the scoping. `test_company_members_endpoint` covers the endpoint
    itself.

    Note that this does NOT bump `token_version` — the service function does
    that, and `test_company_session_invalidation.py` checks it there.
    """
    record = CompanyMembership(
        company_id=company.id,
        user_id=user.id,
        role=role,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def auth_headers(user: User, company: Optional[Company] = None) -> dict:
    """Bearer token for `user`, plus the active company header when given.

    The token carries the user's CURRENT `token_version`. A test that changes
    entitlements and then expects the old token to be rejected must therefore
    take its headers BEFORE the change — which is exactly the sequence
    `test_company_session_invalidation.py` asserts.
    """
    headers = {
        "Authorization": f"Bearer {create_access_token(str(user.id), user.token_version or 0)}"
    }
    if company is not None:
        headers["X-Company-Id"] = str(company.id)
    return headers


def refresh_token_for(user: User) -> str:
    return create_refresh_token(str(user.id), user.token_version or 0)


def unrelated_company_id() -> str:
    """An id that names nothing. For the "does not exist" half of 404."""
    return str(uuid.uuid4())


def make_bank_account(
    db,
    company: Company,
    *,
    iban: str = "AT61 1904 3002 3457 3201",
    label: Optional[str] = None,
    is_default: bool = False,
) -> CompanyBankAccount:
    """A bank account row, written directly.

    Directly rather than through `POST /company/bank-accounts`, for the same
    reason `grant` does not use the members endpoint: these tests are about
    what the row DOES, and building the world through the endpoint under test
    would let a bug in the endpoint hide a bug in the scoping. The IBAN is
    stored normalised, exactly as the schema's validator would leave it, so a
    row built here is indistinguishable from one the API wrote.
    """
    from apps.api.core.iban import normalise_iban

    account = CompanyBankAccount(
        company_id=company.id,
        iban=normalise_iban(iban),
        label=label,
        is_default=is_default,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def make_number_series(
    db,
    company: Company,
    *,
    document_type: str = "invoice",
    name: str = "Ausgangsrechnungen",
    pattern: str = "AR-{YYYY}-{SEQ}",
    reset: str = "yearly",
    next_value: int = 1,
    current_period: Optional[str] = None,
    padding: int = 4,
    is_active: bool = True,
):
    """One number series, written directly.

    Directly rather than through `POST /company/number-series`, for the same
    reason `grant` does not use the members endpoint: a bug in the endpoint
    must not be able to hide a bug in the scoping.

    `current_period` defaults to None — a series that has never allocated
    anything — because that is the state a seeded series is in, and a test
    that wants a counter mid-year says so.
    """
    from apps.api.models.number_series import NumberSeries

    series = NumberSeries(
        company_id=company.id,
        document_type=document_type,
        name=name,
        pattern=pattern,
        reset=reset,
        next_value=next_value,
        current_period=current_period,
        padding=padding,
        is_active=is_active,
    )
    db.add(series)
    db.commit()
    db.refresh(series)
    return series


def make_tax_code(
    db,
    company: Company,
    *,
    code: str = "AT-20",
    name: str = "20 % USt",
    rate: str = "20",
    category: str = "S",
    legal_notes: Optional[dict] = None,
    notes_reviewed: bool = False,
    is_active: bool = True,
    origin_pack: Optional[str] = "at",
):
    """One tax code, written directly.

    Directly rather than through the region-pack seeder, for the same reason
    `grant` does not use the members endpoint: a test about scoping must not
    be able to pass because the seeder happened to put the right rows in the
    right company.

    `rate` is a string and converted here, because a test that writes
    `rate=20.5` would be writing a float into a NUMERIC column and quietly
    testing rule 1 in reverse.
    """
    from decimal import Decimal

    from apps.api.models.tax import TaxCode

    row = TaxCode(
        company_id=company.id,
        code=code,
        name=name,
        rate=Decimal(rate),
        category=category,
        legal_notes=legal_notes or {},
        notes_reviewed=notes_reviewed,
        reporting_mapping={},
        is_active=is_active,
        origin_pack=origin_pack,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_tax_class(
    db,
    company: Company,
    *,
    key: str = "standard",
    label: str = "Normalsteuersatz",
    code=None,
    position: int = 10,
    origin_pack: Optional[str] = "at",
):
    """One tax class, pointing at `code` (created if not given)."""
    from apps.api.models.tax import TaxClass

    if code is None:
        code = make_tax_code(db, company)
    row = TaxClass(
        company_id=company.id,
        key=key,
        label=label,
        code_id=code.id,
        position=position,
        origin_pack=origin_pack,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_party(
    db,
    company: Company,
    *,
    type: str = "organisation",
    name: str = "ORF",
    parent=None,
    is_customer: bool = False,
    is_supplier: bool = False,
    vat_id: Optional[str] = None,
    tax_id_kind: str = "vat",
    tax_country: Optional[str] = None,
    business_status: str = "auto",
    tax_override: Optional[dict] = None,
    tax_status_note: Optional[str] = None,
    party_number: Optional[str] = None,
    is_active: bool = True,
    is_crew: bool = False,
    tags: Optional[list] = None,
    email: Optional[str] = None,
):
    """One party, written directly.

    Directly rather than through `POST /parties`, for the same reason `grant`
    does not use the members endpoint: a test about scoping or inheritance
    must not be able to pass because the endpoint happened to behave.

    `vat_id` keeps its name through P1b-2 although the column is gone: it
    creates a real primary `party_tax_ids` row, so the parameter still means
    what it says — "a party with this number" — and every test that used it
    still exercises the live path rather than a column that no longer
    exists. `tax_id_kind` picks which kind it is, for the Swiss and
    Australian cases.
    """
    from apps.api.models.party import Party, PartyTaxId

    row = Party(
        company_id=company.id,
        type=type,
        name=name,
        parent_id=parent.id if parent is not None else None,
        is_customer=is_customer,
        is_supplier=is_supplier,
        tax_country=tax_country,
        business_status=business_status,
        tax_override=tax_override,
        tax_status_note=tax_status_note,
        party_number=party_number,
        is_active=is_active,
        is_crew=is_crew,
        tags=list(tags or []),
        einvoice_routing={},
    )
    db.add(row)
    db.flush()

    if email:
        from apps.api.models.party import PartyContactPoint

        db.add(
            PartyContactPoint(
                company_id=company.id,
                party_id=row.id,
                kind="email",
                value=email,
                purpose="general",
                is_primary=True,
            )
        )

    if vat_id:
        db.add(
            PartyTaxId(
                company_id=company.id,
                party_id=row.id,
                kind=tax_id_kind,
                # The country as the number declares it, which is what a
                # check must use — not the party's `tax_country`, which can
                # differ (a German company VAT-registered in Austria).
                country=(
                    "CH"
                    if tax_id_kind == "uid_ch"
                    else "AU"
                    if tax_id_kind == "abn"
                    else vat_id[:2]
                    if len(vat_id) >= 2 and vat_id[:2].isalpha()
                    else tax_country
                ),
                value=vat_id,
                value_as_entered=vat_id,
                is_primary=True,
                is_active=True,
            )
        )
    db.commit()
    db.refresh(row)
    return row


def make_party_tax_id(
    db,
    company: Company,
    party,
    *,
    kind: str = "vat",
    value: str = "ATU16360407",
    country: Optional[str] = None,
    is_primary: bool = False,
    is_active: bool = True,
    notes: Optional[str] = None,
):
    """A second (or third) identifier on a party, written directly."""
    from apps.api.models.party import PartyTaxId

    row = PartyTaxId(
        company_id=company.id,
        party_id=party.id,
        kind=kind,
        value=value,
        value_as_entered=value,
        country=country,
        is_primary=is_primary,
        is_active=is_active,
        notes=notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_party_address(
    db,
    company: Company,
    party,
    *,
    kind: str = "billing",
    street: Optional[str] = "Würzburggasse 30",
    zip: Optional[str] = "1136",
    city: Optional[str] = "Wien",
    country: Optional[str] = "AT",
    is_default: bool = True,
):
    from apps.api.models.party import PartyAddress

    row = PartyAddress(
        company_id=company.id,
        party_id=party.id,
        kind=kind,
        street=street,
        zip=zip,
        city=city,
        country=country,
        is_default=is_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ─── Catalog (A1) ───────────────────────────────────────────────────────────
#
# Written directly rather than through `seed_catalog`, for the same reason
# `grant` writes a membership directly: these build the WORLD a test acts in,
# and building it through the code under test would let one bug hide another.
# `test_catalog_api.py` covers the seeding itself.


def make_unit(
    db,
    company: Company,
    *,
    key: str = "day",
    label_de: str = "Tag",
    label_en: Optional[str] = "Day",
    period_unit: str = "day",
    is_system: bool = True,
    position: int = 10,
    is_active: bool = True,
):
    from apps.api.models.catalog import Unit

    row = Unit(
        company_id=company.id,
        key=key,
        label_de=label_de,
        label_en=label_en,
        period_unit=period_unit,
        is_system=is_system,
        position=position,
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_item_category(
    db,
    company: Company,
    *,
    name: str = "Kamera",
    kind: str = "equipment_rental",
    parent=None,
    position: int = 10,
    is_active: bool = True,
):
    from apps.api.models.catalog import ItemCategory

    row = ItemCategory(
        company_id=company.id,
        name=name,
        kind=kind,
        parent_id=parent.id if parent is not None else None,
        position=position,
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_rental_factor_group(
    db,
    company: Company,
    *,
    name: str = "Standard-Mietfaktoren",
    factors=((1, "1.0"), (2, "1.5"), (3, "2.0"), (5, "2.8"), (7, "3.5")),
    is_active: bool = True,
):
    """A factor group carrying the A1 prompt's own table by default."""
    from decimal import Decimal

    from apps.api.models.catalog import RentalFactor, RentalFactorGroup

    group = RentalFactorGroup(
        company_id=company.id, name=name, is_active=is_active
    )
    db.add(group)
    db.flush()
    for days, factor in factors:
        db.add(
            RentalFactor(
                company_id=company.id,
                group_id=group.id,
                days=days,
                factor=Decimal(factor),
            )
        )
    db.commit()
    db.refresh(group)
    return group


def make_item(
    db,
    company: Company,
    *,
    sku: Optional[str] = "CAM-001",
    name_de: str = "Arri Alexa 35",
    name_en: Optional[str] = "Arri Alexa 35",
    kind: str = "equipment_rental",
    category=None,
    unit=None,
    default_tax_class: Optional[str] = None,
    rental_factor_group=None,
    is_active: bool = True,
    bill_rate_day: Optional[str] = "1200.00",
    cost_rate_day: Optional[str] = "600.00",
    currency: str = "EUR",
):
    """One item, with the day rates most tests want it to have."""
    from decimal import Decimal

    from apps.api.models.catalog import Item, ItemRate

    item = Item(
        company_id=company.id,
        sku=sku,
        name_de=name_de,
        name_en=name_en,
        kind=kind,
        category_id=category.id if category is not None else None,
        unit_id=unit.id if unit is not None else None,
        default_tax_class=default_tax_class,
        rental_factor_group_id=(
            rental_factor_group.id if rental_factor_group is not None else None
        ),
        is_active=is_active,
    )
    db.add(item)
    db.flush()
    for rate_type, amount in (("bill", bill_rate_day), ("cost", cost_rate_day)):
        if amount is None:
            continue
        db.add(
            ItemRate(
                company_id=company.id,
                item_id=item.id,
                rate_type=rate_type,
                period_unit="day",
                amount=Decimal(amount),
                currency=currency,
            )
        )
    db.commit()
    db.refresh(item)
    return item


def make_project(
    db,
    company: Company,
    *,
    name: str = "Spot Sommer 2026",
    project_number: str = "PRJ-2026-001",
    client=None,
    status: str = "active",
    start_date=None,
    end_date=None,
    description: Optional[str] = None,
    notes: Optional[str] = None,
):
    """A project row, written directly.

    The number is a literal rather than an allocation: `make_company` writes
    a company row without seeding its number series, and a factory that
    allocated would couple every test that wants a project to the series
    machinery. `test_projects_api.py` covers the allocation through the
    endpoint, which is where it matters.
    """
    from datetime import date as _date

    from apps.api.models.project import Project

    row = Project(
        company_id=company.id,
        project_number=project_number,
        name=name,
        client_party_id=client.id if client is not None else None,
        description=description,
        status=status,
        start_date=start_date or _date(2026, 6, 1),
        end_date=end_date,
        notes=notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_project_day(
    db,
    company: Company,
    project,
    *,
    date=None,
    type: str = "shoot",
    location: Optional[str] = None,
    notes: Optional[str] = None,
    is_active: bool = True,
):
    from datetime import date as _date

    from apps.api.models.project import ProjectDay

    row = ProjectDay(
        company_id=company.id,
        project_id=project.id,
        date=date or _date(2026, 6, 1),
        type=type,
        location=location,
        notes=notes,
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_project_block(
    db,
    company: Company,
    project,
    *,
    start_date=None,
    end_date=None,
    type: str = "shoot",
    label: Optional[str] = None,
):
    """A strip (B2a) with one active day of its own type per date."""
    from datetime import date as _date, timedelta as _timedelta

    from apps.api.models.project import ProjectBlock, ProjectDay

    start = start_date or _date(2026, 7, 6)
    end = end_date or start
    block = ProjectBlock(
        company_id=company.id,
        project_id=project.id,
        type=type,
        label=label,
        start_date=start,
        end_date=end,
        is_active=True,
    )
    db.add(block)
    db.flush()
    day = start
    while day <= end:
        db.add(
            ProjectDay(
                company_id=company.id,
                project_id=project.id,
                block_id=block.id,
                date=day,
                type=type,
                is_active=True,
            )
        )
        day += _timedelta(days=1)
    db.flush()
    return block


def make_budget(
    db,
    company: Company,
    project,
    *,
    name: str = "Estimate",
    currency: str = "EUR",
    notes: Optional[str] = None,
    is_primary: bool = False,
    locked_at=None,
    is_active: bool = True,
):
    from apps.api.models.project import Budget

    row = Budget(
        company_id=company.id,
        project_id=project.id,
        name=name,
        currency=currency,
        notes=notes,
        is_primary=is_primary,
        locked_at=locked_at,
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_budget_section(
    db,
    company: Company,
    budget,
    *,
    code: Optional[str] = "1100",
    name: str = "Crew",
    parent=None,
    position: int = 10,
    is_active: bool = True,
):
    from apps.api.models.project import BudgetSection

    row = BudgetSection(
        company_id=company.id,
        budget_id=budget.id,
        code=code,
        name=name,
        parent_id=parent.id if parent is not None else None,
        position=position,
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_budget_line(
    db,
    company: Company,
    section,
    *,
    description: str = "DoP",
    kind: str = "labor",
    quantity: str = "1",
    period_value: str = "3",
    period_unit: str = "day",
    factor: str = "1",
    unit_price: str = "850.00",
    unit_cost: str = "600.00",
    item=None,
    tax_ref: str = "",
    position: int = 10,
    is_active: bool = True,
):
    from decimal import Decimal

    from apps.api.models.project import BudgetLine

    row = BudgetLine(
        company_id=company.id,
        section_id=section.id,
        position=position,
        item_id=item.id if item is not None else None,
        description=description,
        kind=kind,
        quantity=Decimal(quantity),
        period_value=Decimal(period_value),
        period_unit=period_unit,
        factor=Decimal(factor),
        unit_price=Decimal(unit_price),
        unit_cost=Decimal(unit_cost),
        tax_ref=tax_ref,
        tags=[],
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_budget_global(
    db,
    company: Company,
    budget,
    *,
    key: str = "shoot_days",
    label: str = "Shoot days",
    value: str = "3",
    unit: Optional[str] = "days",
    source: str = "manual",
    is_active: bool = True,
):
    from decimal import Decimal

    from apps.api.models.project import BudgetGlobal

    row = BudgetGlobal(
        company_id=company.id,
        budget_id=budget.id,
        key=key,
        label=label,
        value=Decimal(value),
        unit=unit,
        source=source,
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_budget_adjustment(
    db,
    company: Company,
    budget,
    *,
    section=None,
    label: str = "Production fee",
    type: str = "surcharge",
    mode: str = "percent",
    value: str = "8",
    base: str = "subtotal",
    kinds=(),
    position: int = 10,
    is_active: bool = True,
):
    from decimal import Decimal

    from apps.api.models.project import BudgetAdjustment

    row = BudgetAdjustment(
        company_id=company.id,
        budget_id=budget.id,
        section_id=section.id if section is not None else None,
        position=position,
        label=label,
        type=type,
        mode=mode,
        value=Decimal(value),
        base=base,
        kinds=list(kinds),
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_budget_commitment(
    db,
    company: Company,
    budget,
    section,
    *,
    line=None,
    amount: str = "500.00",
    status: str = "open",
    reference: Optional[str] = "PO-1",
):
    """One commitment, written directly — for tests that need a row of
    another company to exist rather than tests about creating one."""
    from datetime import date
    from decimal import Decimal

    from apps.api.models.project import BudgetCommitment

    row = BudgetCommitment(
        company_id=company.id,
        budget_id=budget.id,
        section_id=section.id,
        line_id=None if line is None else line.id,
        amount=Decimal(amount),
        status=status,
        reference=reference,
        commitment_date=date(2026, 6, 1),
    )
    db.add(row)
    db.flush()
    return row


def make_budget_actual(
    db,
    company: Company,
    budget,
    section,
    *,
    line=None,
    commitment=None,
    amount: str = "100.00",
    reference: Optional[str] = "RE-1",
):
    """One actual, written directly. See `make_budget_commitment`."""
    from datetime import date
    from decimal import Decimal

    from apps.api.models.project import BudgetActual

    row = BudgetActual(
        company_id=company.id,
        budget_id=budget.id,
        section_id=section.id,
        line_id=None if line is None else line.id,
        commitment_id=None if commitment is None else commitment.id,
        amount=Decimal(amount),
        reference=reference,
        incurred_on=date(2026, 6, 2),
    )
    db.add(row)
    db.flush()
    return row


def make_device(
    db,
    company: Company,
    *,
    name: str = "Alexa 35",
    quantity_owned: int = 3,
    purchase_price: Optional[str] = "65000.00",
    category: Optional[str] = "Camera",
    is_active: bool = True,
):
    """One device, written directly (B2b)."""
    from decimal import Decimal

    from apps.api.models.device import Device

    row = Device(
        company_id=company.id,
        name=name,
        name_key=" ".join(name.split()).casefold(),
        category=category,
        quantity_owned=quantity_owned,
        purchase_price=Decimal(purchase_price) if purchase_price is not None else None,
        is_active=is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_device_unit(
    db,
    company: Company,
    device,
    *,
    serial_number: str = "AX35-0001",
    status: str = "active",
    removed_reason: Optional[str] = None,
    purchase_price: Optional[str] = None,
    seller_name: Optional[str] = None,
):
    """One unit of a device, written directly (B2b)."""
    from datetime import date
    from decimal import Decimal

    from apps.api.models.device import DeviceUnit

    row = DeviceUnit(
        company_id=company.id,
        device_id=device.id,
        serial_number=serial_number,
        serial_key=serial_number.strip().casefold(),
        status=status,
        removed_reason=(removed_reason or "Written off in a test")
        if status in ("lost", "retired", "sold")
        else None,
        removed_on=date(2026, 11, 1) if status in ("lost", "retired", "sold") else None,
        purchase_price=Decimal(purchase_price) if purchase_price is not None else None,
        seller_name=seller_name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_device_association(db, company: Company, device, associated, *, quantity_per_parent: int = 1):
    """`device` needs `associated` (B2b-2), written directly."""
    from apps.api.models.device import DeviceAssociation

    row = DeviceAssociation(
        company_id=company.id,
        device_id=device.id,
        associated_device_id=associated.id,
        quantity_per_parent=quantity_per_parent,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_device_alternative(db, company: Company, device, alternative, *, position: int = 0):
    """`alternative` can stand in for `device` (B2b-2), written directly."""
    from apps.api.models.device import DeviceAlternative

    row = DeviceAlternative(
        company_id=company.id,
        device_id=device.id,
        alternative_device_id=alternative.id,
        position=position,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_crew_role(db, company: Company, party, item, *, own_day_rate: Optional[str] = None):
    """R1: a role (a labor item) on a person, written directly."""
    from decimal import Decimal

    from apps.api.models.crew import PartyCrewRole

    row = PartyCrewRole(
        company_id=company.id,
        party_id=party.id,
        item_id=item.id,
        own_day_rate=Decimal(own_day_rate) if own_day_rate is not None else None,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
