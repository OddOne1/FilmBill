"""Team members as crew (R2).

Two halves:

* `/team…` — the admin side, on the Team page. `company.members.manage`
  (owner, admin), the key the Members screen already uses; anybody else
  gets the same 404 as a company they are not in.
* `/me/crew…`, `/me/schedule` — the member's own. Any active member of the
  company except a tax advisor (an external person whose access is the
  archive, D23/D43), and no `parties.view` needed. **The contact is always
  the one linked to the caller's own login**: no route here takes a party
  id, a user id or a rate from the request.

There is no DELETE: "stops working as crew" is `POST /team/{user_id}/crew/stop`
(like crew's own `…/remove`), which sets `is_crew` false and keeps the link,
the contact and every booking.
"""

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.company import NOT_FOUND_DETAIL, Membership, current_membership, require
from ..models.company import CompanyMembership, CompanyRole
from ..models.crew import ROLE_SET_BY_ADMIN, ROLE_SET_BY_SELF
from ..models.party import Party
from ..models.user import User
from ..schemas.team import (
    FutureBookingResponse,
    LaborItemChoice,
    MyCrewResponse,
    MyCrewRoles,
    MyCrewSwitch,
    MyCrewSwitchResponse,
    MyScheduleDay,
    MyScheduleEntry,
    MyScheduleResponse,
    TeamContact,
    TeamCrewRequest,
    TeamMemberResponse,
    TeamResponse,
    TeamRole,
    TeamStopResponse,
)
from ..services import audit, crew_roles_service, crew_service
from ..services import team_crew_service as service

router = APIRouter(tags=["team"])

MANAGE = "company.members.manage"


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)


def _unprocessable(error: Exception):
    code = (
        status.HTTP_409_CONFLICT
        if isinstance(error, (service.LinkConflict, service.NeedsLink, crew_roles_service.RoleExists))
        else status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    raise HTTPException(status_code=code, detail=str(error)) from None


# ─── Shapes ─────────────────────────────────────────────────────────────────


def _roles(db: Session, company_id: uuid.UUID, party_ids) -> dict[uuid.UUID, list[TeamRole]]:
    views = crew_roles_service.roles_of(db, company_id, party_ids)
    return {
        party_id: [
            TeamRole(
                id=view.row.id,
                item_id=view.row.item_id,
                name=view.name,
                confirmed=view.row.confirmed,
                set_by=view.row.set_by,
            )
            for view in rows
        ]
        for party_id, rows in views.items()
    }


def _emails(db: Session, company_id: uuid.UUID, parties: list[Party]) -> dict[uuid.UUID, Optional[str]]:
    contacts = crew_service.primary_contacts(db, company_id, [p.id for p in parties])
    return {pid: values.get("email") for pid, values in contacts.items()}


def _member_responses(db: Session, company_id: uuid.UUID, rows: list[service.TeamRow]) -> list[TeamMemberResponse]:
    linked = [row.contact for row in rows if row.contact is not None]
    roles = _roles(db, company_id, [p.id for p in linked])
    matched = [p for row in rows for p in row.matches]
    emails = _emails(db, company_id, matched)
    out = []
    for row in rows:
        contact = row.contact
        out.append(
            TeamMemberResponse(
                user_id=row.user.id,
                name=row.user.name,
                email=row.user.email,
                role=row.membership.role if row.membership is not None else None,
                has_active_login=row.has_active_login,
                crew_state=row.state,
                contact=TeamContact(id=contact.id, name=contact.name, email=row.contact_email)
                if contact is not None
                else None,
                roles=roles.get(contact.id, []) if contact is not None else [],
                suggestion=TeamContact(
                    id=row.suggestion.id, name=row.suggestion.name, email=emails.get(row.suggestion.id)
                )
                if row.suggestion is not None
                else None,
                matches=[TeamContact(id=p.id, name=p.name, email=emails.get(p.id)) for p in row.matches],
            )
        )
    return out


def _team_row(db: Session, membership: Membership, user_id: uuid.UUID) -> service.TeamRow:
    """The Team page's row for `user_id`, or 404: a user is reachable here
    only while they have ANY membership row in this company (a revoked one
    too, so it can be refused with a sentence) or a linked contact."""
    company_id = membership.company_id
    known = (
        db.query(CompanyMembership.id)
        .filter(CompanyMembership.company_id == company_id, CompanyMembership.user_id == user_id)
        .first()
        is not None
        or service.linked_contact(db, company_id, user_id) is not None
    )
    user = db.get(User, user_id) if known else None
    if user is None:
        _not_found()
    for row in service.team(db, company_id):
        if row.user.id == user_id:
            return row
    # A member whose access lapsed and who has no linked contact.
    return service.TeamRow(user=user, membership=None, contact=None, contact_email=None)


def _member(db: Session, membership: Membership, user_id: uuid.UUID) -> TeamMemberResponse:
    db.expire_all()
    return _member_responses(db, membership.company_id, [_team_row(db, membership, user_id)])[0]


def _bookings(rows: list[service.FutureBooking]) -> list[FutureBookingResponse]:
    return [
        FutureBookingResponse(
            assignment_id=b.assignment_id,
            project_id=b.project_id,
            project_name=b.project_name,
            strip_name=b.strip_name,
            status=b.status,
            first_date=b.first_date,
            last_date=b.last_date,
        )
        for b in rows
    ]


# ─── The admin side ─────────────────────────────────────────────────────────


@router.get("/team", response_model=TeamResponse)
def read_team(
    db: Session = Depends(get_db),
    membership: Membership = Depends(require(MANAGE)),
):
    """Active members with their crew state, linked contact, roles (no
    rates) and, with no link, the email suggestion; then linked contacts
    whose login has no access any more."""
    return TeamResponse(members=_member_responses(db, membership.company_id, service.team(db, membership.company_id)))


@router.post("/team/{user_id}/crew", response_model=TeamMemberResponse)
def make_crew(
    user_id: uuid.UUID,
    body: TeamCrewRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require(MANAGE)),
):
    """"Works as crew". `link` connects the contact the admin confirmed
    (`party_id` is required — a suggestion is never followed by itself);
    `create` makes a new person contact from the login. Either way the
    contact is crew and linked; `role_item_ids`, when sent, become its
    roles, confirmed."""
    row = _team_row(db, membership, user_id)
    actor = audit.actor_from(request, membership=membership)
    try:
        if body.mode == "link":
            if body.party_id is None:
                raise service.TeamCrewError(
                    "Choose the contact to connect, or create a new crew contact instead."
                )
            party = (
                db.query(Party)
                .filter(Party.company_id == membership.company_id, Party.id == body.party_id)
                .first()
            )
            if party is None:
                _not_found()
            party = service.link(db, company_id=membership.company_id, user=row.user, party=party, actor=actor)
        else:
            party = service.create_contact(db, company_id=membership.company_id, user=row.user, actor=actor)
        if body.role_item_ids is not None:
            service.set_roles(db, party=party, item_ids=body.role_item_ids, actor=actor, set_by=ROLE_SET_BY_ADMIN)
    except LookupError:
        db.rollback()
        _not_found()
    except (service.TeamCrewError, crew_roles_service.CrewRoleError) as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    return _member(db, membership, user_id)


@router.post("/team/{user_id}/crew/stop", response_model=TeamStopResponse)
def stop_crew(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require(MANAGE)),
):
    """"Stops working as crew": `is_crew = false`, the link kept. Lists the
    person's future bookings; none is cancelled, nothing is deleted."""
    row = _team_row(db, membership, user_id)
    if row.contact is None:
        _not_found()
    bookings = service.stop(
        db,
        company_id=membership.company_id,
        party=row.contact,
        actor=audit.actor_from(request, membership=membership),
    )
    db.commit()
    return TeamStopResponse(member=_member(db, membership, user_id), future_bookings=_bookings(bookings))


@router.post("/team/{user_id}/unlink", response_model=TeamMemberResponse)
def unlink(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require(MANAGE)),
):
    """Removes the link. The contact stays exactly as it is."""
    row = _team_row(db, membership, user_id)
    if row.contact is None:
        _not_found()
    service.unlink(db, party=row.contact, actor=audit.actor_from(request, membership=membership))
    db.commit()
    return _member(db, membership, user_id)


@router.post("/team/{user_id}/crew/roles/{role_id}/confirm", response_model=TeamMemberResponse)
def confirm_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require(MANAGE)),
):
    """An admin vouches for a role the member set for themselves."""
    row = _team_row(db, membership, user_id)
    if row.contact is None:
        _not_found()
    found = crew_roles_service.active_role(db, membership.company_id, row.contact.id, role_id)
    if found is None:
        _not_found()
    crew_roles_service.confirm_role(
        db, row=found[0], name=found[1], actor=audit.actor_from(request, membership=membership)
    )
    db.commit()
    return _member(db, membership, user_id)


# ─── The member's own ───────────────────────────────────────────────────────


def self_service(membership: Membership = Depends(current_membership)) -> Membership:
    """Any active member of the company, on their own login only. The tax
    advisor is refused with the same 404 as everything else they lack."""
    if membership.role == CompanyRole.tax_advisor:
        _not_found()
    return membership


def _labor_items(db: Session, company_id: uuid.UUID) -> list[LaborItemChoice]:
    by_name = crew_roles_service.labor_items_by_name(db, company_id)
    items = sorted({item_id: name for item_id, name in by_name.values()}.items(), key=lambda e: e[1].casefold())
    return [LaborItemChoice(item_id=item_id, name=name) for item_id, name in items]


def _my_profile(db: Session, membership: Membership) -> MyCrewResponse:
    db.expire_all()
    company_id = membership.company_id
    party = service.linked_contact(db, company_id, membership.user.id)
    matches = [] if party is not None else service.email_matches(db, company_id, membership.user.email)
    state = service.crew_state(party, matches)
    return MyCrewResponse(
        is_crew=bool(party is not None and party.is_crew),
        crew_state=state,
        contact_name=party.name if party is not None else None,
        roles=_roles(db, company_id, [party.id])[party.id] if party is not None else [],
        labor_items=_labor_items(db, company_id),
        message=service.NEEDS_LINK_SENTENCE if state == service.STATE_NEEDS_LINK else None,
    )


@router.get("/me/crew", response_model=MyCrewResponse)
def read_my_crew(
    db: Session = Depends(get_db),
    membership: Membership = Depends(self_service),
):
    """Am I crew, my roles (confirmed or self-reported), my contact's name.
    No rate."""
    return _my_profile(db, membership)


@router.put("/me/crew", response_model=MyCrewSwitchResponse)
def switch_my_crew(
    body: MyCrewSwitch,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(self_service),
):
    """"I also work as crew", on or off. On with no link: a new crew contact
    when no contact has my email; when one does, nothing is created or
    linked, the admins are notified, and the answer is a 409 with the
    sentence. Off: `is_crew = false`, the link and every booking stay."""
    company_id = membership.company_id
    actor = audit.actor_from(request, membership=membership)
    bookings: list[service.FutureBooking] = []
    if body.is_crew:
        try:
            service.switch_on_myself(
                db,
                company_id=company_id,
                company_name=membership.company.display_name,
                user=membership.user,
                actor=actor,
            )
        except service.NeedsLink as error:
            # The notification is the point of this answer: kept.
            db.commit()
            _unprocessable(error)
        except service.TeamCrewError as error:
            db.rollback()
            _unprocessable(error)
    else:
        party = service.linked_contact(db, company_id, membership.user.id)
        if party is not None:
            bookings = service.stop(db, company_id=company_id, party=party, actor=actor, self_service=True)
    db.commit()
    return MyCrewSwitchResponse(profile=_my_profile(db, membership), future_bookings=_bookings(bookings))


@router.put("/me/crew/roles", response_model=MyCrewResponse)
def set_my_roles(
    body: MyCrewRoles,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(self_service),
):
    """The labor items I can do, exactly. New ones are self-reported until
    an admin confirms them; ones an admin confirmed stay confirmed; leaving
    one out removes it. No rate in the request or the response."""
    party = service.linked_contact(db, membership.company_id, membership.user.id)
    if party is None or not party.is_crew:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You are not working as crew yet. Switch on “I also work as crew” first, then choose your roles.",
        )
    try:
        service.set_roles(
            db,
            party=party,
            item_ids=body.item_ids,
            actor=audit.actor_from(request, membership=membership),
            set_by=ROLE_SET_BY_SELF,
        )
    except LookupError:
        db.rollback()
        _not_found()
    except crew_roles_service.CrewRoleError as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    return _my_profile(db, membership)


@router.get("/me/schedule", response_model=MyScheduleResponse)
def read_my_schedule(
    db: Session = Depends(get_db),
    membership: Membership = Depends(self_service),
):
    """My own booked and scheduled days, upcoming first. Nothing about
    other crew, rates, budgets or gear."""
    party = service.linked_contact(db, membership.company_id, membership.user.id)
    if party is None or not party.is_crew:
        return MyScheduleResponse(available=False)
    today = date.today()
    entries = []
    for entry in service.my_schedule(db, membership.company_id, party.id):
        row = entry.assignment
        entries.append(
            MyScheduleEntry(
                assignment_id=row.id,
                project_id=row.project_id,
                project_name=entry.project_name,
                strip_name=service.strip_name(entry.block),
                role_name=entry.role_name,
                status=row.status,
                note=row.status_note,
                location=entry.block.location,
                first_date=entry.days[0].date,
                last_date=entry.days[-1].date,
                days=[
                    MyScheduleDay(date=d.date, call_time=d.call_time, wrap_time=d.wrap_time, location=d.location)
                    for d in entry.days
                ],
            )
        )
    upcoming = sorted(
        (e for e in entries if e.last_date >= today),
        key=lambda e: (e.first_date, e.project_name, e.strip_name, str(e.assignment_id)),
    )
    past = sorted(
        (e for e in entries if e.last_date < today),
        key=lambda e: (e.last_date, e.project_name, str(e.assignment_id)),
        reverse=True,
    )
    return MyScheduleResponse(available=True, upcoming=upcoming, past=past)
