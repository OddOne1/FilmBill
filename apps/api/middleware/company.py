"""The company a request is acting in, and whether it may.

Two dependencies and one small context object. Every business endpoint in
FilmBill from here on takes `Depends(require("some.permission"))` and gets
back a `Membership`; nothing else establishes company context, and no router
filters by `company_id` by hand (CLAUDE.md rule 6).

**The header, and only the header.** The active company travels in
`X-Company-Id`. It is deliberately not in the path: a company id repeated in
`/companies/{id}/invoices/{id}` alongside a header would be two sources for
one fact, and the interesting bug is exactly the request where they disagree.
So the company-scoped routes are spelled `/company/...` — singular, "the one
I am acting in" — and `/companies` (plural) is only the two operations that
have no company context by definition: listing the ones you belong to, and
creating a new one.

**404, never 403.** A 403 means "this exists and you may not"; that sentence
is itself information, and for a company it is enough to confirm a client
relationship that neither party published. Every failure to establish
membership answers 404 with the same body — no row, revoked, expired, an
archived company, a company that does not exist, or a superadmin who is
simply not a member. A caller cannot distinguish them, which is the point.

The one exception is a request that names no company at all, or names
something that is not a UUID. That is a broken client rather than an
authorisation question, it reveals nothing about which companies exist, and
answering it 404 would send whoever is debugging it looking for a missing
row. It gets a 400 that says what is wrong.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.company import Company, CompanyMembership, CompanyRole
from ..models.user import User
from ..services.permissions import active_membership_in, has_permission
from .auth import get_current_user

#: The one header. Named here so the tests and the router can spell it the
#: same way as the dependency.
COMPANY_HEADER = "X-Company-Id"

#: The single body every scoping failure answers with. One string, so a
#: client cannot tell an expired membership from a company that was never
#: created, and so a future edit cannot make one of them chattier by accident.
NOT_FOUND_DETAIL = "Not found"


@dataclass(frozen=True)
class Membership:
    """Resolved company context for one request.

    Carries the ORM rows rather than ids, because every caller that has one
    of these immediately needs the company (its currency, its settings) or
    the role (to decide a second permission), and re-fetching would be a
    second query for a row already in the session.

    `company_id` is a property rather than a field so that `scoped()` can
    duck-type on it without this module and `services/permissions.py`
    importing each other.
    """

    user: User
    company: Company
    record: CompanyMembership

    @property
    def company_id(self) -> uuid.UUID:
        return self.company.id

    @property
    def role(self) -> CompanyRole:
        return self.record.role

    def can(self, permission: str) -> bool:
        """Whether this membership holds `permission` — for a second check
        inside a handler that has already passed its first one."""
        return has_permission(permission, self.role, self.company)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)


def current_membership(
    x_company_id: Optional[str] = Header(default=None, alias=COMPANY_HEADER),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Membership:
    """Resolve user + company + role, or 404.

    Nothing here consults `User.role`. An installation superadmin who is not
    a member of this company is refused exactly like anyone else — see
    `services/permissions.py` for why the two ladders stay apart.
    """
    if not x_company_id or not x_company_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{COMPANY_HEADER} header is required",
        )

    try:
        company_id = uuid.UUID(x_company_id.strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{COMPANY_HEADER} is not a valid id",
        )

    record = active_membership_in(db, current_user, company_id)
    if record is None:
        raise _not_found()

    company = db.query(Company).filter(Company.id == company_id).first()
    # Membership without a company should be impossible (the FK cascades),
    # and an archived company is closed to everyone including its owner —
    # both answer the same 404 as never having had access.
    if company is None or company.archived_at is not None:
        raise _not_found()

    return Membership(user=current_user, company=company, record=record)


def require(permission: str):
    """Dependency factory: this endpoint needs `permission` in the active company.

    Returns the `Membership` on success, so an endpoint declares its
    authorisation and obtains its company context in one parameter — there is
    no way to take the context while forgetting the check, because taking the
    context IS the check.

    A refusal is the same 404 as having no membership at all. A member of the
    company who lacks the permission learns nothing they did not already
    know (they know the company exists — they are in it), and keeping one
    answer means no endpoint can leak the difference by being written
    slightly differently from its neighbour.
    """

    def _dependency(membership: Membership = Depends(current_membership)) -> Membership:
        if not membership.can(permission):
            raise _not_found()
        return membership

    # Named for the permission so that FastAPI's dependency cache and any
    # traceback point at which check failed, rather than at six identical
    # `_dependency` frames.
    _dependency.__name__ = f"require_{permission.replace('.', '_')}"
    return _dependency
