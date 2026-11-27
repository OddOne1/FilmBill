"""Team members as crew: a login linked to a person contact (R2).

A login is a `User` with a `CompanyMembership`; a crew person is a person
`Party` with `is_crew`. `Party.user_id` says they are the same person. This
module is the only writer of that column.

**Never merges.** Linking sets `user_id` (and `is_crew`), nothing else: the
contact's roles, assignments, tags and tax data stay exactly as they were.
Unlinking clears `user_id`, nothing else.

**The email suggestion is a suggestion.** When no contact is linked and
exactly one unlinked, active person contact has the login's email as its
primary email (case-insensitive), the admin is OFFERED it; the link is made
only when the admin sends that contact's id back (`mode = link`). Two or
more matches: no suggestion, the admin picks.

**Self-service** never takes a party id from the request: the contact is
always the one linked to the caller's own login in the caller's company. A
member switching themselves on when a contact already has their email gets
a sentence and nothing is created or linked; the company's owners and
admins get one notification each.

**Members set roles, never rates.** A role a member sets is stored
`confirmed = false, set_by = self` until an admin confirms it.

No commit anywhere: the caller owns the transaction, and every audit row
lands or vanishes with its write.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..billing.resources.crew_csv import CrewValues, display_name
from ..models.activity import Notification, NotificationType
from ..models.company import CompanyMembership, CompanyRole
from ..models.crew import (
    OCCUPYING_STATUSES,
    ROLE_SET_BY_ADMIN,
    ROLE_SET_BY_SELF,
    CrewAssignment,
    CrewAssignmentDay,
    PartyCrewRole,
)
from ..models.party import Party, PartyContactPoint
from ..models.project import Project, ProjectBlock
from ..models.user import User, UserStatus
from . import audit, crew_roles_service, crew_service
from .crew_assignment_service import strip_name

#: The tag a contact created from a login carries.
TEAM_TAG = "Team"
SOURCE_TEAM = "team"

STATE_NOT_CREW = "not_crew"
STATE_CREW = "crew"
STATE_NEEDS_LINK = "needs_link"

#: Who gets the "wants to work as crew" notification, and who may use the
#: admin routes (`company.members.manage` is the same two roles).
ADMIN_ROLES = (CompanyRole.owner, CompanyRole.admin)

#: The sentence a member gets when a contact already has their email.
NEEDS_LINK_SENTENCE = (
    "A contact with your email already exists. An admin has to connect it to your login."
)

LINK_FIELDS = ("user_id", "is_crew", "party_id", "email", "self_service", "future_bookings")


class TeamCrewError(ValueError):
    """A sentence for the person; surfaces as a 422."""


class LinkConflict(TeamCrewError):
    """The contact or the login is linked already; surfaces as a 409."""


class NeedsLink(TeamCrewError):
    """Self-service: a contact has the caller's email; surfaces as a 409."""


# ─── Reading ────────────────────────────────────────────────────────────────


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def active_membership(
    db: Session, company_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[CompanyMembership]:
    """`user_id`'s membership in this company if it grants anything right
    now, else None."""
    rows = (
        db.query(CompanyMembership)
        .filter(
            CompanyMembership.company_id == company_id,
            CompanyMembership.user_id == user_id,
            CompanyMembership.revoked_at.is_(None),
        )
        .all()
    )
    return next((row for row in rows if row.is_active()), None)


def login_is_active(user: Optional[User]) -> bool:
    return user is not None and user.deleted_at is None and user.status != UserStatus.deactivated


def linked_contact(db: Session, company_id: uuid.UUID, user_id: uuid.UUID) -> Optional[Party]:
    return (
        db.query(Party)
        .filter(Party.company_id == company_id, Party.user_id == user_id)
        .first()
    )


def email_matches(db: Session, company_id: uuid.UUID, email: Optional[str]) -> list[Party]:
    """Active, unlinked PERSON contacts of this company whose primary email
    is `email`, ignoring case; oldest first."""
    wanted = (email or "").strip().lower()
    if not wanted:
        return []
    return (
        db.query(Party)
        .join(PartyContactPoint, PartyContactPoint.party_id == Party.id)
        .filter(
            Party.company_id == company_id,
            Party.type == "person",
            Party.is_active.is_(True),
            Party.user_id.is_(None),
            PartyContactPoint.company_id == company_id,
            PartyContactPoint.kind == "email",
            PartyContactPoint.is_primary.is_(True),
            func.lower(func.trim(PartyContactPoint.value)) == wanted,
        )
        .distinct()
        .order_by(Party.created_at, Party.id)
        .all()
    )


def crew_state(party: Optional[Party], matches: list[Party]) -> str:
    if party is not None:
        return STATE_CREW if party.is_crew else STATE_NOT_CREW
    return STATE_NEEDS_LINK if matches else STATE_NOT_CREW


@dataclass
class FutureBooking:
    assignment_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    strip_name: str
    status: str
    first_date: date
    last_date: date


def future_bookings(
    db: Session, company_id: uuid.UUID, party_id: uuid.UUID, *, today: Optional[date] = None
) -> list[FutureBooking]:
    """The person's scheduled and booked assignments with at least one day
    from today on, soonest first. Read only — nothing is cancelled."""
    today = today or date.today()
    rows = (
        db.query(
            CrewAssignment,
            Project.name,
            ProjectBlock,
            func.min(CrewAssignmentDay.date),
            func.max(CrewAssignmentDay.date),
        )
        .join(Project, Project.id == CrewAssignment.project_id)
        .join(ProjectBlock, ProjectBlock.id == CrewAssignment.block_id)
        .join(CrewAssignmentDay, CrewAssignmentDay.assignment_id == CrewAssignment.id)
        .filter(
            CrewAssignment.company_id == company_id,
            CrewAssignment.party_id == party_id,
            CrewAssignment.status.in_(OCCUPYING_STATUSES),
            CrewAssignmentDay.is_active.is_(True),
            ProjectBlock.is_active.is_(True),
        )
        .group_by(CrewAssignment.id, Project.name, ProjectBlock.id)
        .having(func.max(CrewAssignmentDay.date) >= today)
        .all()
    )
    out = [
        FutureBooking(
            assignment_id=row.id,
            project_id=row.project_id,
            project_name=name,
            strip_name=strip_name(block),
            status=row.status,
            first_date=first,
            last_date=last,
        )
        for row, name, block, first, last in rows
    ]
    out.sort(key=lambda b: (b.first_date, b.project_name, str(b.assignment_id)))
    return out


@dataclass
class TeamRow:
    user: User
    membership: Optional[CompanyMembership]
    contact: Optional[Party]
    contact_email: Optional[str]
    matches: list[Party] = field(default_factory=list)

    @property
    def state(self) -> str:
        return crew_state(self.contact, self.matches)

    @property
    def has_active_login(self) -> bool:
        return self.membership is not None and login_is_active(self.user)

    @property
    def suggestion(self) -> Optional[Party]:
        """Exactly one match is a suggestion; two or more is a choice."""
        if self.contact is None and len(self.matches) == 1:
            return self.matches[0]
        return None


def team(db: Session, company_id: uuid.UUID) -> list[TeamRow]:
    """Every active member, plus every linked contact whose login no longer
    has access here ("no active login": the link is history). Members in
    the order they were granted, the others after them by name."""
    memberships = (
        db.query(CompanyMembership)
        .filter(
            CompanyMembership.company_id == company_id,
            CompanyMembership.revoked_at.is_(None),
        )
        .order_by(CompanyMembership.granted_at, CompanyMembership.id)
        .all()
    )
    active = [m for m in memberships if m.is_active()]
    linked = {
        party.user_id: party
        for party in db.query(Party).filter(
            Party.company_id == company_id, Party.user_id.isnot(None)
        )
    }
    user_ids = {m.user_id for m in active} | set(linked)
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids))} if user_ids else {}
    contacts = crew_service.primary_contacts(db, company_id, [p.id for p in linked.values()])

    rows: list[TeamRow] = []
    seen: set[uuid.UUID] = set()
    for membership in active:
        user = users.get(membership.user_id)
        if user is None or membership.user_id in seen:
            continue
        seen.add(user.id)
        contact = linked.get(user.id)
        rows.append(
            TeamRow(
                user=user,
                membership=membership,
                contact=contact,
                contact_email=contacts.get(contact.id, {}).get("email") if contact else None,
                matches=[] if contact is not None else email_matches(db, company_id, user.email),
            )
        )
    others = [
        TeamRow(
            user=users[user_id],
            membership=None,
            contact=party,
            contact_email=contacts.get(party.id, {}).get("email"),
        )
        for user_id, party in linked.items()
        if user_id not in seen and user_id in users
    ]
    rows.extend(sorted(others, key=lambda row: (row.contact.name.casefold(), str(row.contact.id))))
    return rows


# ─── Writing: the admin side ────────────────────────────────────────────────


def _link_snapshot(party: Party, **extra) -> dict:
    return {
        "user_id": party.user_id,
        "is_crew": party.is_crew,
        "party_id": party.id,
        **extra,
    }


def _check_member(db: Session, company_id: uuid.UUID, user: User) -> CompanyMembership:
    membership = active_membership(db, company_id, user.id)
    if membership is None or not login_is_active(user):
        raise TeamCrewError(
            f"{user.name} has no active login in your company. Only a team member "
            "with access can be connected to a contact; give them access first."
        )
    return membership


def link(
    db: Session,
    *,
    company_id: uuid.UUID,
    user: User,
    party: Party,
    actor: audit.Actor,
    self_service: bool = False,
) -> Party:
    """Connect `user`'s login to `party` and mark the contact crew. The
    admin chose this contact (its id came back from the dialog). No commit.
    """
    _check_member(db, company_id, user)
    if party.type != "person":
        raise TeamCrewError(
            f"{party.name} is an organisation, not a person. A login can only be "
            "connected to a person contact; choose the person, or create a new crew contact."
        )
    if not party.is_active:
        raise TeamCrewError(
            f"{party.name} is archived. Reactivate the contact first, or create a "
            "new crew contact instead."
        )
    if party.user_id is not None and party.user_id != user.id:
        raise LinkConflict(
            f"{party.name} is already connected to another login. Unlink it there "
            "first, or choose another contact."
        )
    current = linked_contact(db, company_id, user.id)
    if current is not None and current.id != party.id:
        raise LinkConflict(
            f"{user.name} is already connected to the contact {current.name}. "
            "Unlink that contact first, or keep it."
        )
    before = _link_snapshot(party)
    party.user_id = user.id
    party.is_crew = True
    db.flush()
    after = _link_snapshot(party, email=user.email, **({"self_service": True} if self_service else {}))
    if before != _link_snapshot(party):
        audit.record(db, actor, party, "team.crew_linked", before, after, fields=LINK_FIELDS)
    return party


def create_contact(
    db: Session,
    *,
    company_id: uuid.UUID,
    user: User,
    actor: audit.Actor,
    self_service: bool = False,
) -> Party:
    """A new person contact from the login: first and last name, the login
    email as primary email, crew, tagged "Team", linked. No commit."""
    _check_member(db, company_id, user)
    current = linked_contact(db, company_id, user.id)
    if current is not None:
        raise LinkConflict(
            f"{user.name} is already connected to the contact {current.name}. "
            "Unlink that contact first, or keep it."
        )
    first = (user.first_name or "").strip() or None
    last = (user.last_name or "").strip() or None
    name = display_name(first, None, last) or user.email
    party = crew_service.create_member(
        db,
        company_id=company_id,
        values=CrewValues(
            name=name,
            roles=(TEAM_TAG,),
            email=user.email,
            first_name=first,
            last_name=last,
        ),
        actor=actor,
        source=SOURCE_TEAM,
    )
    party.user_id = user.id
    db.flush()
    audit.record(
        db, actor, party, "team.crew_created", None,
        _link_snapshot(party, email=user.email, **({"self_service": True} if self_service else {})),
        fields=LINK_FIELDS,
    )
    return party


def stop(
    db: Session,
    *,
    company_id: uuid.UUID,
    party: Party,
    actor: audit.Actor,
    self_service: bool = False,
) -> list[FutureBooking]:
    """"Stops working as crew": `is_crew = false`, the link stays. Returns
    the person's future bookings — which stay exactly as they are."""
    bookings = future_bookings(db, company_id, party.id)
    if party.is_crew:
        before = _link_snapshot(party)
        party.is_crew = False
        db.flush()
        audit.record(
            db, actor, party, "team.crew_stopped", before,
            _link_snapshot(
                party,
                future_bookings=len(bookings),
                **({"self_service": True} if self_service else {}),
            ),
            fields=LINK_FIELDS,
        )
    return bookings


def unlink(db: Session, *, party: Party, actor: audit.Actor) -> Party:
    """Removes the link. The contact stays as it is, crew or not."""
    if party.user_id is None:
        return party
    before = _link_snapshot(party)
    party.user_id = None
    db.flush()
    audit.record(db, actor, party, "team.unlinked", before, _link_snapshot(party), fields=LINK_FIELDS)
    return party


def _active_roles(db: Session, party: Party) -> dict[uuid.UUID, PartyCrewRole]:
    return {
        row.item_id: row
        for row in db.query(PartyCrewRole).filter(
            PartyCrewRole.company_id == party.company_id,
            PartyCrewRole.party_id == party.id,
            PartyCrewRole.is_active.is_(True),
        )
    }


def _labor_items(db: Session, company_id: uuid.UUID, item_ids: Iterable[uuid.UUID]):
    items = []
    for item_id in dict.fromkeys(item_ids):
        item = crew_roles_service.labor_item(db, company_id, item_id)
        if item is None:
            raise LookupError("item")
        items.append(crew_roles_service.check_labor_item(item))
    return items


def set_roles(
    db: Session,
    *,
    party: Party,
    item_ids: list[uuid.UUID],
    actor: audit.Actor,
    set_by: str,
) -> None:
    """The person's roles become exactly `item_ids`. No commit.

    `set_by = admin` (the Team dialog's Save): missing roles are added
    confirmed, held self-reported ones are confirmed, the rest retired.
    `set_by = self`: missing roles are added unconfirmed, held ones are left
    as they are (an admin's confirmation is not undone by the member saving
    again), the rest retired. Never a rate, either way."""
    crew_roles_service.check_crew(party)
    items = _labor_items(db, party.company_id, item_ids)
    held = _active_roles(db, party)
    wanted = {item.id for item in items}
    for item in items:
        row = held.get(item.id)
        if row is None:
            crew_roles_service.add_role(
                db,
                party=party,
                item=item,
                own_day_rate=None,
                notes=None,
                actor=actor,
                set_by=set_by,
                action="crew_role.self_set" if set_by == ROLE_SET_BY_SELF else "party_crew_role.added",
                source=SOURCE_TEAM,
            )
        elif set_by == ROLE_SET_BY_ADMIN:
            crew_roles_service.confirm_role(db, row=row, name=item.name_de, actor=actor)
    for item_id, row in held.items():
        if item_id not in wanted:
            item = crew_roles_service.labor_item(db, party.company_id, item_id)
            crew_roles_service.remove_role(
                db,
                row=row,
                name=item.name_de if item else "",
                actor=actor,
                self_service=set_by == ROLE_SET_BY_SELF,
            )


# ─── Writing: the member's own ──────────────────────────────────────────────


def notify_admins(db: Session, *, company_id: uuid.UUID, company_name: str, user: User) -> int:
    """One notification per owner and admin of the company: "<name> wants to
    work as crew". Not twice while an earlier one is still unread."""
    title = f"{user.name} wants to work as crew"
    admins = (
        db.query(User)
        .join(CompanyMembership, CompanyMembership.user_id == User.id)
        .filter(
            CompanyMembership.company_id == company_id,
            CompanyMembership.revoked_at.is_(None),
            CompanyMembership.role.in_(ADMIN_ROLES),
            User.id != user.id,
        )
        .all()
    )
    made = 0
    for admin in admins:
        if not login_is_active(admin) or active_membership(db, company_id, admin.id) is None:
            continue
        unread = (
            db.query(Notification)
            .filter(
                Notification.user_id == admin.id,
                Notification.type == NotificationType.team,
                Notification.title == title,
                Notification.read.is_(False),
            )
            .first()
        )
        if unread is not None:
            continue
        db.add(
            Notification(
                user_id=admin.id,
                type=NotificationType.team,
                title=title,
                body=(
                    f"A contact in {company_name} already has their email. "
                    "Connect their contact in Settings → Team."
                ),
                link="/settings/company/members",
                read=False,
            )
        )
        made += 1
    db.flush()
    return made


def switch_on_myself(
    db: Session,
    *,
    company_id: uuid.UUID,
    company_name: str,
    user: User,
    actor: audit.Actor,
) -> Party:
    """"I also work as crew", from the member's own login. Linked already:
    the contact becomes crew again. No link and no contact has the email: a
    new crew contact, exactly as an admin's "create". A contact has the
    email: nothing is created or linked; the admins are told."""
    party = linked_contact(db, company_id, user.id)
    if party is not None:
        return link(db, company_id=company_id, user=user, party=party, actor=actor, self_service=True)
    if email_matches(db, company_id, user.email):
        notify_admins(db, company_id=company_id, company_name=company_name, user=user)
        raise NeedsLink(NEEDS_LINK_SENTENCE)
    return create_contact(db, company_id=company_id, user=user, actor=actor, self_service=True)


# ─── My schedule ────────────────────────────────────────────────────────────


@dataclass
class ScheduleDay:
    date: date
    call_time: Optional[object]
    wrap_time: Optional[object]
    location: Optional[str]


@dataclass
class ScheduleEntry:
    assignment: CrewAssignment
    project_name: str
    block: ProjectBlock
    role_name: Optional[str]
    days: list[ScheduleDay]


def my_schedule(db: Session, company_id: uuid.UUID, party_id: uuid.UUID) -> list[ScheduleEntry]:
    """The scheduled and booked assignments of ONE contact — the caller's
    own, resolved from their login, never from the request. Each day's call
    and wrap: the person's own, else the day's, else the strip's; the
    location: the day's, else the strip's. Retired strips are left out."""
    from ..models.catalog import Item
    from ..models.project import ProjectDay

    rows = (
        db.query(CrewAssignment, Project.name, ProjectBlock)
        .join(Project, Project.id == CrewAssignment.project_id)
        .join(ProjectBlock, ProjectBlock.id == CrewAssignment.block_id)
        .filter(
            CrewAssignment.company_id == company_id,
            CrewAssignment.party_id == party_id,
            CrewAssignment.status.in_(OCCUPYING_STATUSES),
            ProjectBlock.is_active.is_(True),
        )
        .all()
    )
    if not rows:
        return []
    ids = [row.id for row, _, _ in rows]
    days: dict[uuid.UUID, list[CrewAssignmentDay]] = {i: [] for i in ids}
    for day in (
        db.query(CrewAssignmentDay)
        .filter(
            CrewAssignmentDay.company_id == company_id,
            CrewAssignmentDay.assignment_id.in_(ids),
            CrewAssignmentDay.is_active.is_(True),
        )
        .order_by(CrewAssignmentDay.date)
    ):
        days[day.assignment_id].append(day)
    block_ids = {block.id for _, _, block in rows}
    project_days = {
        (d.block_id, d.date): d
        for d in db.query(ProjectDay).filter(
            ProjectDay.company_id == company_id,
            ProjectDay.block_id.in_(block_ids),
            ProjectDay.is_active.is_(True),
        )
    }
    role_ids = {row.role_item_id for row, _, _ in rows if row.role_item_id}
    role_names = (
        dict(db.query(Item.id, Item.name_de).filter(Item.company_id == company_id, Item.id.in_(role_ids)).all())
        if role_ids
        else {}
    )
    out = []
    for row, name, block in rows:
        if not days[row.id]:
            continue
        entry_days = []
        for day in days[row.id]:
            pday = project_days.get((block.id, day.date))
            entry_days.append(
                ScheduleDay(
                    date=day.date,
                    call_time=day.call_time or (pday.call_time if pday else None) or block.default_call_time,
                    wrap_time=day.wrap_time or (pday.wrap_time if pday else None) or block.default_wrap_time,
                    location=(pday.location if pday else None) or block.location,
                )
            )
        out.append(
            ScheduleEntry(
                assignment=row,
                project_name=name,
                block=block,
                role_name=role_names.get(row.role_item_id) if row.role_item_id else None,
                days=entry_days,
            )
        )
    return out
