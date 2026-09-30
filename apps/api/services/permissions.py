"""Who may do what — as data, in one table.

This file's previous version said it would be rewritten in P0b and what it
would become. This is that rewrite: the global role ladder is still here
because the installation still has one, and everything else is new.

**Two ladders, not one.**

  * `User.role` — superadmin / superuser / user — runs the *installation*.
    Users, mail, site settings. It grants nothing inside any company.
  * `CompanyMembership.role` — owner / admin / accountant / producer / staff
    / tax_advisor — is authority inside ONE company, and lives nowhere but
    that table.

A superadmin who is not a member of a company gets the same 404 for its data
that a stranger gets. That is not an oversight to be smoothed over later: an
installation administrator is frequently a hosting provider, and "can restart
the server" is not "can read the invoices".

**Why a dict and not `if role == ...`.**

`PERMISSIONS` is the whole authorisation model in one screen. A reviewer can
answer "what can an accountant do" by reading one literal, and adding a
capability is a line here rather than a condition somewhere in a router. The
map is deliberately seeded with keys no endpoint uses yet — every phase from
P4 on will need `documents.finalize` and `ledger.view`, and writing them down
now is what stops each of those phases inventing its own spelling and its own
role list. An unused key costs nothing and is the point.

**Why 404 and not 403** — see `middleware/company.py`. It is enforced there,
at the dependency, so no endpoint has to remember it.
"""

from typing import Iterable, Optional

from ..models.company import Company, CompanyMembership, CompanyRole, CompanyScoped
from ..models.user import User, UserGlobalRole

# ─── The installation ladder ────────────────────────────────────────────────


def is_superadmin(user: User | None) -> bool:
    """The instance administrator: user management, site and mail settings."""
    return user is not None and user.role == UserGlobalRole.superadmin


def is_superuser_or_above(user: User | None) -> bool:
    """Looser than is_superadmin — anyone except a plain `user`.

    The only gate between the two today is who may invite people
    (routers/users.py::invite_user).
    """
    return user is not None and user.role in (
        UserGlobalRole.superadmin,
        UserGlobalRole.superuser,
    )


# ─── The company ladder ─────────────────────────────────────────────────────

_ALL: set[CompanyRole] = set(CompanyRole)
_OWNER_ADMIN: set[CompanyRole] = {CompanyRole.owner, CompanyRole.admin}
_BOOKS: set[CompanyRole] = {CompanyRole.owner, CompanyRole.admin, CompanyRole.accountant}
_PRODUCTION: set[CompanyRole] = {
    CompanyRole.owner,
    CompanyRole.admin,
    CompanyRole.accountant,
    CompanyRole.producer,
}

#: Every permission this system knows about, and which roles hold it.
#:
#: Keys are dotted and read noun-first (`company.settings.edit`, not
#: `edit_company_settings`) so that related capabilities sort together and a
#: whole area is greppable by prefix.
#:
#: A key that is absent from this map is held by NOBODY — `has_permission`
#: reads a missing key as an empty set rather than as "unrestricted". A typo
#: in a `require()` call therefore locks an endpoint down instead of opening
#: it, which is the direction a typo should fail in.
PERMISSIONS: dict[str, set[CompanyRole]] = {
    # ── Used by an endpoint in this prompt ──────────────────────────
    #: See the company at all: its name, its settings as read-only, its
    #: existence in the switcher. Every member has it, including the tax
    #: advisor, who otherwise could not tell whose archive they are looking
    #: at.
    "company.view": _ALL,
    "company.settings.edit": _OWNER_ADMIN,
    "company.members.manage": _OWNER_ADMIN,
    # ── Seeded for later phases, deliberately unused today ──────────
    #: P0b-2. Who may change how documents are numbered. Owner/admin only:
    #: a number series is the thing CLAUDE.md rule 5 makes gap-free, and
    #: re-pointing one is not an accounting task, it is a structural one.
    "number_series.manage": _OWNER_ADMIN,
    #: P0b-2. The audit trail. Accountant included — reconstructing what
    #: happened to a document is exactly their job.
    "audit.view": _BOOKS,
    #: P5. The archive of finalized documents. The tax advisor's entire
    #: reason for existing in this system.
    "archive.view": _BOOKS | {CompanyRole.tax_advisor},
    "archive.download": _BOOKS | {CompanyRole.tax_advisor},
    #: P4/P5. Turning a draft into a document with a number on it, and
    #: superseding one that already has (CLAUDE.md rule 3). A producer can,
    #: because in a production company the producer is who sends the
    #: invoice; staff cannot.
    "documents.finalize": _PRODUCTION,
    "documents.revise": _PRODUCTION,
    #: P6. The books themselves.
    "ledger.view": _BOOKS,
    #: P6. The company's own analysis of itself. The tax advisor is NOT in
    #: these sets — see TAX_ADVISOR_OPTIONAL below, which can add them per
    #: company.
    "reports.view": _BOOKS,
    "exports.download": _BOOKS,
}

#: Permissions a `tax_advisor` holds only where the company has opted in, via
#: `Company.tax_advisor_reports`.
#:
#: Kept OUT of `PERMISSIONS` rather than added to it with a condition
#: attached, so the literal above stays readable as an unconditional
#: statement. The conditional grant is one branch, in one function, named.
TAX_ADVISOR_OPTIONAL: frozenset[str] = frozenset({"reports.view", "exports.download"})


def roles_with(permission: str) -> set[CompanyRole]:
    """Which roles hold `permission` unconditionally. Empty for unknown keys."""
    return PERMISSIONS.get(permission, set())


def has_permission(
    permission: str, role: CompanyRole, company: Optional[Company] = None
) -> bool:
    """Whether `role` in `company` may do `permission`.

    `company` is optional only so that a caller reasoning about roles in the
    abstract (a test matrix, a docs page) need not invent one. Pass it
    wherever a real request is being decided — without it the conditional
    tax-advisor grant cannot be evaluated and reads as denied, which is the
    right way for a missing argument to fail.
    """
    if role in roles_with(permission):
        return True
    if (
        role == CompanyRole.tax_advisor
        and permission in TAX_ADVISOR_OPTIONAL
        and company is not None
        and bool(company.tax_advisor_reports)
    ):
        return True
    return False


def permissions_for(role: CompanyRole, company: Optional[Company] = None) -> list[str]:
    """Every permission `role` holds here, sorted.

    Served to the web app so navigation can render only what the person can
    reach (§3). Presentation only: each endpoint enforces its own
    `require()`, so a client that ignored this list would simply be a client
    whose extra menu entries all 404.
    """
    return sorted(
        key for key in PERMISSIONS if has_permission(key, role, company)
    )


# ─── Company scoping ────────────────────────────────────────────────────────


class ScopeError(RuntimeError):
    """`scoped()` was handed a query it cannot make safe.

    A programming error, not a request error, and it is raised rather than
    silently passed through: a query that cannot be scoped must not run at
    all. Surfacing as a 500 is correct — a leak that returns another
    company's rows with a 200 is the outcome this whole module exists to
    prevent, and a crash in development is how it gets found.
    """


def scoped(query, membership):
    """Constrain `query` to one company. The only way a business query runs.

    Takes the `Membership` context from `middleware/company.py` (duck-typed
    on `.company_id` rather than imported, to keep this module free of the
    FastAPI layer) and applies the filter to every entity the query selects.

    Refuses anything that is not `CompanyScoped`. That refusal is the load-
    bearing part: it means a router cannot route a query through here, pass
    review because the call is present, and still be unscoped because the
    model never had a `company_id`.
    """
    descriptions = getattr(query, "column_descriptions", None) or []
    entities = []
    for description in descriptions:
        entity = description.get("entity")
        if entity is not None and entity not in entities:
            entities.append(entity)

    if not entities:
        raise ScopeError(
            "scoped() needs a query over mapped entities; got a query with "
            "no entity to constrain."
        )

    for entity in entities:
        if not issubclass(entity, CompanyScoped):
            raise ScopeError(
                f"{entity.__name__} is not CompanyScoped, so it cannot be "
                f"company-scoped. Add the mixin (and a migration) or do not "
                f"route this query through scoped()."
            )
        query = query.filter(entity.company_id == membership.company_id)

    return query


def active_memberships(db, user: User) -> list[CompanyMembership]:
    """Every membership `user` currently holds, across all companies.

    ONE query — this runs on every login (see
    `site_settings_service.two_factor_required_for`) as well as behind the
    company switcher. The expiry test is applied in Python rather than in SQL
    so that `CompanyMembership.is_active` stays the single definition of
    "active"; the index does the work that matters, and a person is in a
    handful of companies, not thousands.
    """
    rows = (
        db.query(CompanyMembership)
        .filter(
            CompanyMembership.user_id == user.id,
            CompanyMembership.revoked_at.is_(None),
        )
        .all()
    )
    return [row for row in rows if row.is_active()]


def active_membership_in(db, user: User, company_id) -> Optional[CompanyMembership]:
    """`user`'s active membership in one company, or None.

    None covers every way of not having access — no row, revoked, expired,
    or a company that does not exist — because the caller must answer all
    four identically (404) and a richer return type would invite it not to.
    """
    row = (
        db.query(CompanyMembership)
        .filter(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company_id,
            CompanyMembership.revoked_at.is_(None),
        )
        .first()
    )
    return row if row is not None and row.is_active() else None


def roles_requiring_2fa(companies: Iterable[Company]) -> set[CompanyRole]:
    """The union of every company's `require_2fa_roles`, plus tax_advisor.

    `tax_advisor` is added unconditionally and cannot be configured away:
    the role exists to hand a company's books to someone who does not work
    there, and every install should be safe by default rather than safe once
    an admin has found the setting.
    """
    required: set[CompanyRole] = {CompanyRole.tax_advisor}
    for company in companies:
        for value in company.require_2fa_roles or []:
            try:
                required.add(CompanyRole(value))
            except ValueError:
                # A role name that no longer exists — left over from a
                # renamed enum value, say. Ignored rather than raised: a
                # stale entry in a settings list must not make logins fail.
                continue
    return required
