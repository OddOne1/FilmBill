"""Companies, and who is in them.

Two path shapes, and the split is the whole scoping design (see
`middleware/company.py`):

  * `/companies` — plural, no company context. Listing the ones you belong to
    and creating a new one are the only two operations that cannot name an
    active company, because in the first the answer IS the list and in the
    second it does not exist yet.
  * `/company/...` — singular, "the one I am acting in". The company comes
    from the `X-Company-Id` header and from nowhere else. No id is repeated
    in the path, so there is no request in which a path and a header can
    disagree.

Every handler below takes its company context from `require(...)`, which
returns the resolved `Membership` only if the permission is held. There is no
way to get the context without the check, and no `filter(company_id == ...)`
written by hand — the members listing goes through `scoped()`.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.company import Membership, current_membership, require
from ..middleware.auth import get_current_user
from ..core.iban import format_iban
from ..models.company import (
    Company,
    CompanyBankAccount,
    CompanyMembership,
    CompanyRole,
)
from ..models.user import User, UserStatus
from ..schemas.company import (
    BankAccountCreate,
    BankAccountResponse,
    BankAccountUpdate,
    CompanyCreate,
    CompanyResponse,
    CompanySummary,
    CompanyUpdate,
    MemberCreate,
    MemberResponse,
    MemberUpdate,
)
from ..services import company_service, invite_service
from ..services.auth_service import get_user_by_email
from ..services.permissions import (
    active_memberships,
    is_superadmin,
    permissions_for,
    scoped,
)

router = APIRouter(tags=["companies"])


def _member_response(record: CompanyMembership, user: User) -> MemberResponse:
    return MemberResponse(
        id=record.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=record.role,
        status=user.status.value if hasattr(user.status, "value") else str(user.status),
        granted_at=record.granted_at,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
        is_active=record.is_active(),
        two_factor_enabled=bool(user.two_factor_enabled),
    )


# ─── No company context ─────────────────────────────────────────────────────


@router.get("/companies", response_model=list[CompanySummary])
def list_my_companies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The companies the caller may act in. The switcher's source.

    Memberships only — a superadmin sees the companies they are a member of
    and no others, exactly like everyone else. An installation administrator
    who wants into a company's data adds themselves to it, which leaves a row
    saying so; that is the difference between administration and a back door.

    Not company-scoped and cannot be: the answer to "which companies" is what
    a company context would be selecting from.
    """
    memberships = active_memberships(db, current_user)
    if not memberships:
        return []

    companies = {
        company.id: company
        for company in db.query(Company)
        .filter(
            Company.id.in_({record.company_id for record in memberships}),
            Company.archived_at.is_(None),
        )
        .all()
    }

    summaries = []
    for record in memberships:
        company = companies.get(record.company_id)
        if company is None:
            continue
        summaries.append(
            CompanySummary(
                id=company.id,
                legal_name=company.legal_name,
                trading_name=company.trading_name,
                display_name=company.display_name,
                default_currency=company.default_currency,
                role=record.role,
                permissions=permissions_for(record.role, company),
                expires_at=record.expires_at,
            )
        )
    return sorted(summaries, key=lambda s: s.display_name.lower())


@router.post(
    "/companies", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED
)
def create_company(
    body: CompanyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a company and become its Owner.

    The one place where installation authority reaches into the company
    ladder, and it is narrow by construction: a superadmin may create a
    company, and doing so grants them Owner of THAT company and nothing else.
    An install where nobody could make the first company would be an install
    nobody could use.

    403 rather than 404 for a non-superadmin, unlike everywhere else in this
    router. There is no company here to be coy about — the refusal is about
    the caller's own installation role, which they can already read off
    `/auth/me`.
    """
    if not is_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an installation administrator can create a company.",
        )

    company = company_service.create_company(
        db,
        owner=current_user,
        **body.model_dump(),
    )
    db.commit()
    db.refresh(company)
    return company


# ─── The active company ─────────────────────────────────────────────────────


@router.get("/company", response_model=CompanyResponse)
def get_active_company(
    membership: Membership = Depends(require("company.view")),
):
    """The company named by `X-Company-Id`, in full.

    No database call of its own: `current_membership` already loaded the row
    in order to decide whether this caller may see it.
    """
    return membership.company


@router.patch("/company", response_model=CompanyResponse)
def update_active_company(
    body: CompanyUpdate,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """Change the active company's details.

    `exclude_unset` rather than `exclude_none`: a PATCH that sends
    `trading_name: null` means "we do not have one", and that has to be
    distinguishable from not mentioning the field.
    """
    changes = body.model_dump(exclude_unset=True)
    if "require_2fa_roles" in changes and changes["require_2fa_roles"] is not None:
        # Stored as plain strings so the column stays readable JSON and does
        # not depend on how a particular SQLAlchemy version serialises an
        # Enum inside JSONB.
        changes["require_2fa_roles"] = [
            role.value if isinstance(role, CompanyRole) else str(role)
            for role in changes["require_2fa_roles"]
        ]
    for field, value in changes.items():
        setattr(membership.company, field, value)

    db.commit()
    db.refresh(membership.company)
    return membership.company


# ─── Members of the active company ──────────────────────────────────────────


@router.get("/company/members", response_model=list[MemberResponse])
def list_members(
    include_revoked: bool = False,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.members.manage")),
):
    """Everyone who is, or was, in this company.

    The query goes through `scoped()` rather than filtering by hand. That is
    not ceremony: this is the listing whose unscoped version would return
    every membership on the installation, complete with email addresses, to
    anyone who is an admin of any one company.
    """
    query = scoped(db.query(CompanyMembership), membership)
    if not include_revoked:
        query = query.filter(CompanyMembership.revoked_at.is_(None))

    records = query.order_by(CompanyMembership.granted_at).all()
    if not records:
        return []

    users = {
        user.id: user
        for user in db.query(User)
        .filter(User.id.in_({record.user_id for record in records}))
        .all()
    }
    return [
        _member_response(record, users[record.user_id])
        for record in records
        if record.user_id in users
    ]


@router.post(
    "/company/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    body: MemberCreate,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.members.manage")),
):
    """Add someone to this company, inviting them if they have no account.

    Both halves land in ONE transaction, so an invited account cannot exist
    without the membership that was the reason to invite it. The mail is
    queued after the commit — see `invite_service.send_invite` for why the
    order matters.

    Only an Owner may create another Owner. An admin promoting themselves is
    otherwise a one-request path from "can manage members" to "can do
    everything", which is not what the two roles are meant to differ by.
    """
    if body.role == CompanyRole.owner and membership.role != CompanyRole.owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can make someone else an owner.",
        )

    email = body.email.strip()
    user = get_user_by_email(db, email)
    invited = user is None

    if user is None:
        user = invite_service.create_invited_user(db, email=email, name=body.name)
    else:
        existing = (
            db.query(CompanyMembership)
            .filter(
                CompanyMembership.company_id == membership.company_id,
                CompanyMembership.user_id == user.id,
                CompanyMembership.revoked_at.is_(None),
            )
            .first()
        )
        if existing is not None and existing.is_active():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That person is already a member of this company.",
            )

    record = company_service.grant_membership_and_bump(
        db,
        company=membership.company,
        user=user,
        role=body.role,
        granted_by=membership.user,
        expires_at=body.expires_at,
    )
    db.commit()
    db.refresh(record)
    db.refresh(user)

    if invited:
        invite_service.send_invite(db, user, membership.user)

    return _member_response(record, user)


def _member_or_404(
    db: Session, membership: Membership, membership_id: uuid.UUID
) -> CompanyMembership:
    """Look a membership up INSIDE the active company.

    Through `scoped()`, so a membership id belonging to another company is
    not found rather than found-and-refused. That distinction is the whole
    cross-company rule: a 403 here would confirm that the id names something.
    """
    record = (
        scoped(db.query(CompanyMembership), membership)
        .filter(CompanyMembership.id == membership_id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return record


def _target_user(db: Session, record: CompanyMembership) -> User:
    user = db.query(User).filter(User.id == record.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return user


def _refuse_last_owner(
    db: Session, membership: Membership, record: CompanyMembership
) -> None:
    """Stop the edit that would leave the company with no active owner.

    Checked here rather than repaired later, because the resulting state has
    no way out through the API: `company.members.manage` belongs to owners and
    admins, and an admin cannot promote anyone to owner. Someone would need
    SQL.
    """
    if record.role != CompanyRole.owner or not record.is_active():
        return
    if company_service.active_owner_count(db, membership.company_id) > 1:
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="A company must keep at least one owner.",
    )


@router.patch("/company/members/{membership_id}", response_model=MemberResponse)
def update_member(
    membership_id: uuid.UUID,
    body: MemberUpdate,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.members.manage")),
):
    """Change someone's role or the date their access lapses.

    `model_fields_set` rather than a None check, so that sending
    `expires_at: null` clears the expiry while omitting the field leaves it
    alone. Getting that backwards would turn a role change into a silent
    promotion of a time-limited tax-advisor grant into a permanent one.
    """
    record = _member_or_404(db, membership, membership_id)
    user = _target_user(db, record)

    if body.role is not None and body.role != record.role:
        if body.role == CompanyRole.owner and membership.role != CompanyRole.owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an owner can make someone else an owner.",
            )
        _refuse_last_owner(db, membership, record)

    set_expiry = "expires_at" in body.model_fields_set

    company_service.update_membership_and_bump(
        db,
        record=record,
        user=user,
        role=body.role,
        expires_at=body.expires_at,
        set_expiry=set_expiry,
    )
    db.commit()
    db.refresh(record)
    db.refresh(user)
    return _member_response(record, user)


@router.delete("/company/members/{membership_id}", response_model=MemberResponse)
def revoke_member(
    membership_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.members.manage")),
):
    """Take someone's access away, now.

    Returns the revoked row rather than 204, so the Members screen can show
    the revocation with its timestamp without a refetch — and so the audit
    answer ("until when did they have it") is in the response that removed it.
    """
    record = _member_or_404(db, membership, membership_id)
    if record.revoked_at is not None:
        # Already gone. Idempotent rather than an error: two clicks on a
        # slow connection are not a conflict to report.
        return _member_response(record, _target_user(db, record))

    _refuse_last_owner(db, membership, record)
    user = _target_user(db, record)

    company_service.revoke_membership_and_bump(db, record=record, user=user)
    db.commit()
    db.refresh(record)
    db.refresh(user)
    return _member_response(record, user)


# ─── Bank accounts of the active company ────────────────────────────────────
#
# Gated on `company.settings.edit` for READS as well as writes, which is
# stricter than `company.view` and deliberate: an IBAN is the detail a
# convincing invoice fraud needs, and there is no reason for every member of a
# production company to be able to read one out of the settings screen. No new
# permission key was invented for it — this is the company's settings screen,
# and `company.settings.edit` is the key that already means "may work on this
# company's own configuration".


def _bank_account_response(account: CompanyBankAccount) -> BankAccountResponse:
    return BankAccountResponse(
        id=account.id,
        label=account.label,
        iban=account.iban,
        iban_formatted=format_iban(account.iban),
        bic=account.bic,
        bank_name=account.bank_name,
        is_default=account.is_default,
        created_at=account.created_at,
    )


def _bank_account_or_404(
    db: Session, membership: Membership, account_id: uuid.UUID
) -> CompanyBankAccount:
    """Look an account up INSIDE the active company, through `scoped()`.

    Same rule as `_member_or_404` above: an id belonging to another company is
    not found rather than found-and-refused, because a 403 would confirm that
    the id names a real bank account somewhere on this installation.
    """
    account = (
        scoped(db.query(CompanyBankAccount), membership)
        .filter(CompanyBankAccount.id == account_id)
        .first()
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return account


@router.get("/company/bank-accounts", response_model=list[BankAccountResponse])
def list_bank_accounts(
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """This company's bank accounts, default first."""
    accounts = (
        scoped(db.query(CompanyBankAccount), membership)
        .order_by(CompanyBankAccount.is_default.desc(), CompanyBankAccount.created_at)
        .all()
    )
    return [_bank_account_response(account) for account in accounts]


@router.post(
    "/company/bank-accounts",
    response_model=BankAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_bank_account(
    body: BankAccountCreate,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """Add an account. The first one is the default whether it asked to be.

    A company with accounts but no default is a company whose next invoice has
    no account number on it, and nothing downstream has a sensible answer for
    that — so the state is made unreachable here rather than handled
    everywhere else.
    """
    existing = scoped(db.query(CompanyBankAccount), membership).first()

    account = CompanyBankAccount(
        company_id=membership.company_id,
        label=body.label,
        iban=body.iban,
        bic=body.bic,
        bank_name=body.bank_name,
        is_default=False,
    )
    db.add(account)
    db.flush()

    company_service.set_default_bank_account(
        db,
        company_id=membership.company_id,
        account=account,
        is_default=body.is_default or existing is None,
    )
    db.commit()
    db.refresh(account)
    return _bank_account_response(account)


@router.patch(
    "/company/bank-accounts/{bank_account_id}", response_model=BankAccountResponse
)
def update_bank_account(
    bank_account_id: uuid.UUID,
    body: BankAccountUpdate,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """Edit an account, or make it the default.

    `is_default: false` is refused rather than obeyed. Demoting the default
    without naming a replacement is the "no default" state again; promoting
    the intended account instead is one request and demotes this one as a
    side effect, atomically.
    """
    account = _bank_account_or_404(db, membership, bank_account_id)
    changes = body.model_dump(exclude_unset=True)
    promote = changes.pop("is_default", None)

    if promote is False and account.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A company keeps one default account. Make another account "
                "the default instead — this one is demoted automatically."
            ),
        )

    for field, value in changes.items():
        setattr(account, field, value)

    if promote:
        company_service.set_default_bank_account(
            db,
            company_id=membership.company_id,
            account=account,
            is_default=True,
        )

    db.commit()
    db.refresh(account)
    return _bank_account_response(account)


@router.delete(
    "/company/bank-accounts/{bank_account_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_bank_account(
    bank_account_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """Remove an account.

    Deleted outright, unlike a membership: a bank account carries no history
    of its own, and the documents that were paid into it keep their own
    snapshot of it (CLAUDE.md rule 4) rather than pointing at this row.

    The default may only go when it is the last one left — otherwise the
    company would be left with accounts and no default. Promote another first.
    """
    account = _bank_account_or_404(db, membership, bank_account_id)

    if account.is_default and company_service.has_other_bank_accounts(
        db, company_id=membership.company_id, exclude_id=account.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "That is the default account. Make another account the "
                "default first, then remove this one."
            ),
        )

    db.delete(account)
    db.commit()
    return None
