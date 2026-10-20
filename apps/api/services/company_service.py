"""Creating companies, and the entitlement changes that end sessions.

Two things live here rather than in the router, for two different reasons.

`create_company` is here because `routers/setup.py` needs it as well as
`routers/companies.py`, and "the first company on an install" must be built
the same way as the tenth — including the Owner membership, which is the part
an inline version in setup would be most likely to get subtly different.

The `*_and_bump` helpers are here because CLAUDE.md's rule about sessions is
easy to state and easy to forget: a membership change alters what an
ALREADY-SIGNED-IN session is entitled to, so it has to bump the target user's
`token_version`. Writing the grant and the bump as one function means a caller
cannot do the first without the second. There is no code path that changes a
membership without going through one of these.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.iban import format_iban, normalise_iban
from ..models.company import (
    Company,
    CompanyBankAccount,
    CompanyMembership,
    CompanyRole,
)
from ..models.user import User
from .auth_service import bump_token_version
from .permissions import active_memberships, roles_requiring_2fa


def create_company(
    db: Session,
    *,
    owner: User,
    legal_name: str,
    **fields,
) -> Company:
    """A company plus the Owner membership that makes it usable. No commit.

    The two rows are created together and never apart. A company with no
    owner is unreachable — nobody holds `company.members.manage` on it, so
    nobody can add the first member — which would be a row that can only be
    fixed with SQL.

    `granted_by` is left NULL here. The grantor of the first membership is
    the person creating the company, i.e. the grantee, and recording someone
    as having granted themselves authority reads as an audit finding rather
    than as the ordinary event it is.

    The eight default number series are seeded in the same transaction. A
    company whose first invoice cannot be numbered is a company that has to
    be configured before it can be used, and "configure the numbering" is not
    a decision anybody wants to make under time pressure on the day they are
    issuing their first invoice. They are user-editable from the moment they
    exist (SCOPE §16, and `services/number_series.DEFAULT_SERIES`).
    """
    company = Company(legal_name=legal_name, **fields)
    db.add(company)
    db.flush()

    db.add(
        CompanyMembership(
            company_id=company.id,
            user_id=owner.id,
            role=CompanyRole.owner,
        )
    )
    db.flush()

    # Imported here rather than at module scope: `number_series` imports
    # `models.company`, and a top-level import would close a cycle through
    # this module's own imports.
    from .number_series import seed_number_series

    seed_number_series(db, company_id=company.id)
    return company


def grant_membership_and_bump(
    db: Session,
    *,
    company: Company,
    user: User,
    role: CompanyRole,
    granted_by: User,
    expires_at: Optional[datetime] = None,
) -> CompanyMembership:
    """Give `user` `role` in `company`, and end their current sessions. No commit.

    The bump is not optional and not conditional. Gaining a role can turn 2FA
    from optional into required for this person (§4), and a session minted
    before that change would carry on without ever passing the forced
    enrolment — the exact hole `token_version` exists to close. It is also
    the simpler rule to state: entitlements changed, so re-authenticate.
    """
    record = CompanyMembership(
        company_id=company.id,
        user_id=user.id,
        role=role,
        granted_by=granted_by.id,
        expires_at=expires_at,
    )
    db.add(record)
    bump_token_version(user)
    db.flush()
    return record


def update_membership_and_bump(
    db: Session,
    *,
    record: CompanyMembership,
    user: User,
    role: Optional[CompanyRole] = None,
    expires_at: Optional[datetime] = None,
    set_expiry: bool = False,
) -> CompanyMembership:
    """Change a role and/or an expiry, and end that user's sessions. No commit.

    `set_expiry` distinguishes "leave the expiry alone" from "clear it".
    Without it, `expires_at=None` would have to mean both, and the one it
    would silently mean is the dangerous one — an expiring tax-advisor grant
    quietly becoming permanent because someone edited the role.
    """
    if role is not None:
        record.role = role
    if set_expiry:
        record.expires_at = expires_at
    bump_token_version(user)
    db.flush()
    return record


def revoke_membership_and_bump(
    db: Session,
    *,
    record: CompanyMembership,
    user: User,
) -> CompanyMembership:
    """Take the membership away, now, including from open sessions. No commit.

    `revoked_at` is set rather than the row deleted: "who had access to this
    company's books last March" is the question an audit asks, and a DELETE
    is the one answer that cannot be given afterwards.
    """
    record.revoked_at = datetime.now(timezone.utc)
    bump_token_version(user)
    db.flush()
    return record


def active_owner_count(db: Session, company_id: uuid.UUID) -> int:
    """How many people currently hold Owner in this company.

    Used to refuse the edit that would leave none. That state is not
    recoverable through the API — `company.members.manage` is held by owners
    and admins, and an admin cannot promote anyone to owner — so it has to be
    prevented rather than repaired.
    """
    rows = (
        db.query(CompanyMembership)
        .filter(
            CompanyMembership.company_id == company_id,
            CompanyMembership.role == CompanyRole.owner,
            CompanyMembership.revoked_at.is_(None),
        )
        .all()
    )
    return sum(1 for row in rows if row.is_active())


def two_factor_required_by_membership(db: Session, user: User) -> bool:
    """Whether any company role this user holds requires them to have 2FA.

    DERIVED, every time, from `company_memberships` joined to the companies'
    own settings — never from a column on `users`. A denormalised flag would
    be wrong the moment a membership was revoked or expired, and wrong in the
    direction that keeps demanding a second factor from someone whose reason
    for needing one has gone away.

    One query for the memberships plus one for the companies, once per login.
    That is the cost, stated: this runs on the password path and the
    magic-code path, both of which already do a bcrypt verify.
    """
    memberships = active_memberships(db, user)
    if not memberships:
        return False

    company_ids = {record.company_id for record in memberships}
    companies = db.query(Company).filter(Company.id.in_(company_ids)).all()
    required = roles_requiring_2fa(companies)

    return any(record.role in required for record in memberships)


# ─── Bank accounts ──────────────────────────────────────────────────────────


def set_default_bank_account(
    db: Session,
    *,
    company_id: uuid.UUID,
    account: CompanyBankAccount,
    is_default: bool,
) -> None:
    """Make `account` the company's default, demoting whichever held it. No commit.

    Server-side and in one transaction, because the client cannot do this: it
    would have to demote the old default and promote the new one as two
    requests, and between them the company either has two defaults or none.
    Both are states a document renderer has no answer for.

    The demotion is a single UPDATE over the other rows rather than a read
    followed by writes. Same reason — a read-then-write leaves a window, and
    the window is exactly where a second person saving the same form lands.

    Called for EVERY create and update, not only when `is_default` is true,
    so the "first account is automatically the default" rule and the "you
    cannot un-default the only one" rule both live here rather than being
    re-derived at each call site.
    """
    if not is_default:
        return

    db.query(CompanyBankAccount).filter(
        CompanyBankAccount.company_id == company_id,
        CompanyBankAccount.id != account.id,
        CompanyBankAccount.is_default.is_(True),
    ).update({"is_default": False}, synchronize_session="fetch")
    account.is_default = True
    db.flush()


#: The name of the unique constraint on `(company_id, iban)`, spelled once.
#:
#: Both the migration and the IntegrityError mapping below need it, and a
#: second spelling of it is a mapping that silently stops recognising its own
#: constraint after a rename — which would turn the race's loser back into a
#: 500.
IBAN_UNIQUE_CONSTRAINT = "uq_company_bank_accounts_company_iban"


def find_bank_account_with_iban(
    db: Session,
    *,
    company_id: uuid.UUID,
    iban: str,
    exclude_id: Optional[uuid.UUID] = None,
) -> Optional[CompanyBankAccount]:
    """The account in THIS company already holding `iban`, or None.

    `iban` is normalised here rather than trusted, so that a caller which
    skipped the schema (a script, a future importer) still compares like with
    like. Normalising on both sides of the comparison would be wrong — the
    column is normalised by construction, and normalising it in SQL would
    throw the index away.

    `exclude_id` is how an edit distinguishes "somebody else already has this"
    from "this is the account I am editing, unchanged".
    """
    query = db.query(CompanyBankAccount).filter(
        CompanyBankAccount.company_id == company_id,
        CompanyBankAccount.iban == normalise_iban(iban),
    )
    if exclude_id is not None:
        query = query.filter(CompanyBankAccount.id != exclude_id)
    return query.first()


def duplicate_iban_detail(account: Optional[CompanyBankAccount]) -> str:
    """The sentence a 409 carries, naming the account that already has it.

    A label is optional on this model and is in fact usually empty — the dev
    database has two unlabelled accounts — so "already saved as 'Hauptkonto'"
    cannot be the only wording. Without a label the sentence says so and
    gives the grouped IBAN instead, which is what the person is looking at on
    the screen.

    `account` may be None: the race's loser learns about the conflict from the
    database, after the row that caused it was committed by somebody else, and
    re-reading it to decorate the message would be a third query for a
    sentence. The generic form is still specific about what to do.
    """
    if account is None:
        return (
            "That IBAN is already saved on this company. Reload the list — "
            "somebody added it a moment ago."
        )
    if account.label:
        return f'This IBAN is already saved as "{account.label}".'
    return (
        "This IBAN is already saved on this company. That account has no "
        f"label — look for {format_iban(account.iban)} in the list."
    )


def is_duplicate_iban_error(error: IntegrityError) -> bool:
    """Whether `error` is the `(company_id, iban)` unique violation.

    Narrow on purpose. Mapping every IntegrityError on this table to a 409
    would hide a foreign-key or NOT NULL failure behind a message about
    duplicate IBANs, which is worse than the 500 it replaced.

    psycopg2 names the constraint in `diag.constraint_name`; the substring
    fallback covers a driver that does not, and costs nothing because the
    constraint name is unique in this schema.
    """
    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    named = getattr(diagnostic, "constraint_name", None)
    if named:
        return named == IBAN_UNIQUE_CONSTRAINT
    return IBAN_UNIQUE_CONSTRAINT in str(getattr(error, "orig", error))


def has_other_bank_accounts(
    db: Session, *, company_id: uuid.UUID, exclude_id: uuid.UUID
) -> bool:
    """Whether this company has a bank account other than `exclude_id`.

    Used to answer "may this one stop being the default" and "may it be
    deleted": a company with accounts must have exactly one default, so the
    last one cannot give the flag up and the default cannot be removed while
    another account could inherit it.
    """
    return (
        db.query(CompanyBankAccount)
        .filter(
            CompanyBankAccount.company_id == company_id,
            CompanyBankAccount.id != exclude_id,
        )
        .first()
        is not None
    )
