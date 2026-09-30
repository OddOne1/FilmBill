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
from ..models.company import Company, CompanyMembership, CompanyRole
from ..models.user import User, UserStatus
from ..schemas.company import (
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
