"""Crew on the calendar: assignments to strips, their days, clashes (B2c-1).

**An assignment belongs to a strip and carries days.** When made, it gets
every day of the strip that is not `off` (the strip's active day rows); the
days can then be unticked one by one — somebody only on days 3 to 5, or
arriving on the travel day. A day outside the strip's range is refused.

**The status path**, nothing else:

    scheduled → booked      (marked by hand, with a note on how)
    scheduled → declined
    booked    → cancelled
    booked    → scheduled   (reopen, a note is required)

and "remove" sets `cancelled` from any status that is not already
cancelled. Every change is audited in the caller's transaction.

**Clashes warn and never block** (decided). A person's `scheduled` and
`booked` assignments on overlapping dates — any project, any strip — and
their own unavailability periods produce a warning list; `declined` and
`cancelled` assignments never count, nor does an assignment on a strip that
was removed, and an assignment never clashes with itself. Nothing here
refuses a save because of one.

No commit anywhere in this module.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..models.catalog import Item
from ..models.crew import (
    ASSIGNMENT_BOOKED,
    ASSIGNMENT_CANCELLED,
    ASSIGNMENT_DECLINED,
    ASSIGNMENT_SCHEDULED,
    OCCUPYING_STATUSES,
    CrewAssignment,
    CrewAssignmentDay,
    CrewUnavailability,
)
from ..models.party import Party
from ..models.project import Project, ProjectBlock, ProjectDay
from . import audit, crew_roles_service
from .calendar_service import say

#: The status changes a person may ask for. Remove is its own action.
TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (ASSIGNMENT_SCHEDULED, ASSIGNMENT_BOOKED),
        (ASSIGNMENT_SCHEDULED, ASSIGNMENT_DECLINED),
        (ASSIGNMENT_BOOKED, ASSIGNMENT_CANCELLED),
        (ASSIGNMENT_BOOKED, ASSIGNMENT_SCHEDULED),
    }
)

#: An availability grid wider than this is a different question.
MAX_RANGE_DAYS = 92

ASSIGNMENT_FIELDS = (
    "project_id",
    "block_id",
    "party_id",
    "role_item_id",
    "status",
    "day_rate",
    "status_note",
    "booked_on",
    "booked_by",
    "days",
)
UNAVAILABILITY_FIELDS = ("party_id", "start_date", "end_date", "reason", "note", "is_active")


class AssignmentError(ValueError):
    """A sentence for the person; surfaces as a 422."""


class NotCrew(AssignmentError):
    """The party is not flagged crew; the screen offers "Mark as crew"."""


class AlreadyOnStrip(AssignmentError):
    """Surfaces as a 409."""


# ─── Words ──────────────────────────────────────────────────────────────────


def strip_name(block: ProjectBlock) -> str:
    return block.label or block.type.capitalize()


def say_dates(dates: Iterable[date]) -> str:
    """`[6, 7, 8, 10 July]` → `6 July 2026 – 8 July 2026, 10 July 2026`."""
    ordered = sorted(set(dates))
    if not ordered:
        return ""
    runs: list[tuple[date, date]] = []
    start = end = ordered[0]
    for day in ordered[1:]:
        if day == end + timedelta(days=1):
            end = day
        else:
            runs.append((start, end))
            start = end = day
    runs.append((start, end))
    return ", ".join(say(a) if a == b else f"{say(a)} – {say(b)}" for a, b in runs)


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


# ─── Clashes ────────────────────────────────────────────────────────────────


@dataclass
class Clash:
    """One thing a person's days run into."""

    kind: str  # "assignment" | "unavailable"
    party_id: uuid.UUID
    party_name: str
    dates: list[date] = field(default_factory=list)
    assignment_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    project_name: Optional[str] = None
    project_number: Optional[str] = None
    block_id: Optional[uuid.UUID] = None
    strip_name: Optional[str] = None
    status: Optional[str] = None
    unavailability_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    @property
    def message(self) -> str:
        """B1.2: names the subject, says what is wrong and what to do."""
        when = say_dates(self.dates)
        if self.kind == "unavailable":
            why = f" ({self.reason})" if self.reason else ""
            return (
                f"{self.party_name} is not available on {when}{why}. Check with "
                "them before booking, or untick those days."
            )
        state = "booked" if self.status == ASSIGNMENT_BOOKED else "scheduled"
        return (
            f"{self.party_name} is already {state} on {self.project_name}, strip "
            f"“{self.strip_name}”, on {when}. Check with them, or untick those days."
        )


@dataclass(frozen=True)
class Occupation:
    """What one person has on one date: an assignment day or unavailability."""

    kind: str
    party_id: uuid.UUID
    date: date
    status: Optional[str] = None
    assignment_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    project_name: Optional[str] = None
    project_number: Optional[str] = None
    block_id: Optional[uuid.UUID] = None
    strip_name: Optional[str] = None
    unavailability_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


def occupations(
    db: Session,
    company_id: uuid.UUID,
    party_ids: Iterable[uuid.UUID],
    start: date,
    end: date,
) -> dict[uuid.UUID, list[Occupation]]:
    """`party id → everything occupying them between start and end`.

    Assignment days count only when the assignment is `scheduled` or
    `booked`, the day is active and the strip is active. Two queries for any
    number of people.
    """
    ids = list(set(party_ids))
    out: dict[uuid.UUID, list[Occupation]] = {pid: [] for pid in ids}
    if not ids:
        return out
    rows = (
        db.query(CrewAssignmentDay.date, CrewAssignment, Project, ProjectBlock)
        .join(CrewAssignment, CrewAssignment.id == CrewAssignmentDay.assignment_id)
        .join(Project, Project.id == CrewAssignment.project_id)
        .join(ProjectBlock, ProjectBlock.id == CrewAssignment.block_id)
        .filter(
            CrewAssignmentDay.company_id == company_id,
            CrewAssignment.company_id == company_id,
            CrewAssignment.party_id.in_(ids),
            CrewAssignment.status.in_(OCCUPYING_STATUSES),
            CrewAssignmentDay.is_active.is_(True),
            ProjectBlock.is_active.is_(True),
            CrewAssignmentDay.date >= start,
            CrewAssignmentDay.date <= end,
        )
        .order_by(CrewAssignmentDay.date, CrewAssignment.created_at)
        .all()
    )
    for day, assignment, project, block in rows:
        out[assignment.party_id].append(
            Occupation(
                kind="assignment",
                party_id=assignment.party_id,
                date=day,
                status=assignment.status,
                assignment_id=assignment.id,
                project_id=project.id,
                project_name=project.name,
                project_number=project.project_number,
                block_id=block.id,
                strip_name=strip_name(block),
            )
        )
    periods = (
        db.query(CrewUnavailability)
        .filter(
            CrewUnavailability.company_id == company_id,
            CrewUnavailability.party_id.in_(ids),
            CrewUnavailability.is_active.is_(True),
            CrewUnavailability.start_date <= end,
            CrewUnavailability.end_date >= start,
        )
        .order_by(CrewUnavailability.start_date)
        .all()
    )
    for period in periods:
        for day in _dates(max(period.start_date, start), min(period.end_date, end)):
            out[period.party_id].append(
                Occupation(
                    kind="unavailable",
                    party_id=period.party_id,
                    date=day,
                    unavailability_id=period.id,
                    reason=period.reason,
                    start_date=period.start_date,
                    end_date=period.end_date,
                )
            )
    return out


def clashes_for(
    db: Session,
    company_id: uuid.UUID,
    wanted: list[tuple[Optional[uuid.UUID], uuid.UUID, str, list[date]]],
) -> dict[Optional[uuid.UUID], list[Clash]]:
    """For each `(assignment id or None, party id, party name, dates)`: what
    those dates run into, grouped by the other assignment or period.

    The assignment's own days never count (`exclude itself`). Keyed by the
    assignment id given (None for a check before anything exists)."""
    out: dict[Optional[uuid.UUID], list[Clash]] = {}
    all_dates = [d for _, _, _, dates in wanted for d in dates]
    if not all_dates:
        return {key: [] for key, _, _, _ in wanted}
    busy = occupations(db, company_id, [pid for _, pid, _, _ in wanted], min(all_dates), max(all_dates))
    for key, party_id, party_name, dates in wanted:
        mine = set(dates)
        grouped: dict[tuple, Clash] = {}
        for occ in busy.get(party_id, []):
            if occ.date not in mine:
                continue
            if occ.kind == "assignment":
                if key is not None and occ.assignment_id == key:
                    continue
                group = ("a", occ.assignment_id)
                clash = grouped.get(group) or Clash(
                    kind="assignment",
                    party_id=party_id,
                    party_name=party_name,
                    assignment_id=occ.assignment_id,
                    project_id=occ.project_id,
                    project_name=occ.project_name,
                    project_number=occ.project_number,
                    block_id=occ.block_id,
                    strip_name=occ.strip_name,
                    status=occ.status,
                )
            else:
                group = ("u", occ.unavailability_id)
                clash = grouped.get(group) or Clash(
                    kind="unavailable",
                    party_id=party_id,
                    party_name=party_name,
                    unavailability_id=occ.unavailability_id,
                    reason=occ.reason,
                    start_date=occ.start_date,
                    end_date=occ.end_date,
                )
            clash.dates.append(occ.date)
            grouped[group] = clash
        out[key] = list(grouped.values())
    return out


# ─── Reading ────────────────────────────────────────────────────────────────


def strip_dates(db: Session, block: ProjectBlock) -> list[ProjectDay]:
    return (
        db.query(ProjectDay)
        .filter(
            ProjectDay.company_id == block.company_id,
            ProjectDay.block_id == block.id,
            ProjectDay.is_active.is_(True),
        )
        .order_by(ProjectDay.date)
        .all()
    )


def default_dates(db: Session, block: ProjectBlock) -> list[date]:
    """Every day of the strip that is not `off`."""
    return [day.date for day in strip_dates(db, block) if day.type != "off"]


def active_days(db: Session, assignment_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[CrewAssignmentDay]]:
    ids = list(assignment_ids)
    out: dict[uuid.UUID, list[CrewAssignmentDay]] = {aid: [] for aid in ids}
    if not ids:
        return out
    for row in (
        db.query(CrewAssignmentDay)
        .filter(CrewAssignmentDay.assignment_id.in_(ids), CrewAssignmentDay.is_active.is_(True))
        .order_by(CrewAssignmentDay.date)
    ):
        out[row.assignment_id].append(row)
    return out


def _days_snapshot(days: list[CrewAssignmentDay]) -> list[dict]:
    return [
        {
            "date": day.date.isoformat(),
            "call_time": day.call_time.isoformat() if day.call_time else None,
            "wrap_time": day.wrap_time.isoformat() if day.wrap_time else None,
        }
        for day in sorted(days, key=lambda d: d.date)
    ]


def _snapshot(db: Session, row: CrewAssignment, *, with_days: bool = True) -> dict:
    out = {
        "project_id": row.project_id,
        "block_id": row.block_id,
        "party_id": row.party_id,
        "role_item_id": row.role_item_id,
        "status": row.status,
        "day_rate": row.day_rate,
        "status_note": row.status_note,
        "booked_on": row.booked_on,
        "booked_by": row.booked_by,
    }
    if with_days:
        out["days"] = _days_snapshot(active_days(db, [row.id])[row.id])
    return out


# ─── Writing: days ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DayWish:
    date: date
    call_time: Optional[time] = None
    wrap_time: Optional[time] = None


def check_days(block: ProjectBlock, wishes: list[DayWish]) -> None:
    seen: set[date] = set()
    for wish in wishes:
        if wish.date < block.start_date or wish.date > block.end_date:
            raise AssignmentError(
                f"{say(wish.date)} is outside the strip “{strip_name(block)}” "
                f"({say(block.start_date)} – {say(block.end_date)}). Choose days "
                "inside the strip, or change the strip first."
            )
        if wish.date in seen:
            raise AssignmentError(
                f"{say(wish.date)} is listed twice for this person. List each day once."
            )
        seen.add(wish.date)


def _write_days(db: Session, row: CrewAssignment, wishes: list[DayWish]) -> None:
    """Make the active days exactly `wishes`: untick the rest, reactivate or
    add the wished ones, set their call and wrap."""
    existing = (
        db.query(CrewAssignmentDay)
        .filter(CrewAssignmentDay.assignment_id == row.id)
        .order_by(CrewAssignmentDay.is_active.desc(), CrewAssignmentDay.created_at)
        .all()
    )
    by_date: dict[date, CrewAssignmentDay] = {}
    for day in existing:
        by_date.setdefault(day.date, day)
    wanted = {wish.date: wish for wish in wishes}
    for day in existing:
        if day.is_active and day.date not in wanted:
            day.is_active = False
    db.flush()
    for when, wish in wanted.items():
        day = by_date.get(when)
        if day is None:
            db.add(
                CrewAssignmentDay(
                    company_id=row.company_id,
                    assignment_id=row.id,
                    date=when,
                    call_time=wish.call_time,
                    wrap_time=wish.wrap_time,
                    is_active=True,
                )
            )
        else:
            day.is_active = True
            day.call_time = wish.call_time
            day.wrap_time = wish.wrap_time
    db.flush()


# ─── Writing: assignments ───────────────────────────────────────────────────


@dataclass
class NewAssignment:
    party: Party
    role: Optional[Item]
    days: Optional[list[DayWish]] = None


def _check_role(role: Optional[Item]) -> None:
    if role is not None:
        try:
            crew_roles_service.check_labor_item(role)
        except crew_roles_service.CrewRoleError as error:
            raise AssignmentError(str(error)) from None


def _rate_for(db: Session, party: Party, role: Optional[Item]) -> Optional[Decimal]:
    """The person's effective rate in the role at this moment, or None."""
    if role is None:
        return None
    views = crew_roles_service.roles_of(db, party.company_id, [party.id])[party.id]
    for view in views:
        if view.row.item_id == role.id:
            return view.rate.effective
    return crew_roles_service.typical_rates(db, party.company_id, [role.id])[role.id]


def create_assignments(
    db: Session,
    *,
    project: Project,
    block: ProjectBlock,
    people: list[NewAssignment],
    actor: audit.Actor,
) -> list[CrewAssignment]:
    """Several people on one strip at once, all or none. No commit."""
    not_crew = [entry.party for entry in people if not entry.party.is_crew]
    if not_crew:
        names = ", ".join(party.name for party in not_crew)
        verb = "is" if len(not_crew) == 1 else "are"
        raise NotCrew(
            f"{names} {verb} not marked as crew. Mark them as crew, then add "
            "them to the strip."
        )
    ids = [entry.party.id for entry in people]
    if len(set(ids)) != len(ids):
        raise AssignmentError("The same person is listed twice. Add each person once.")
    taken = (
        db.query(CrewAssignment, Party.name)
        .join(Party, Party.id == CrewAssignment.party_id)
        .filter(
            CrewAssignment.company_id == project.company_id,
            CrewAssignment.block_id == block.id,
            CrewAssignment.party_id.in_(ids),
            CrewAssignment.status != ASSIGNMENT_CANCELLED,
        )
        .all()
    )
    if taken:
        names = ", ".join(name for _, name in taken)
        raise AlreadyOnStrip(
            f"{names} {'is' if len(taken) == 1 else 'are'} already on the strip "
            f"“{strip_name(block)}”. Change the days there instead, or remove the "
            "entry first to add them again."
        )
    defaults = [DayWish(date=d) for d in default_dates(db, block)]
    made = []
    for entry in people:
        _check_role(entry.role)
        wishes = entry.days if entry.days is not None else defaults
        check_days(block, wishes)
        row = CrewAssignment(
            company_id=project.company_id,
            project_id=project.id,
            block_id=block.id,
            party_id=entry.party.id,
            role_item_id=entry.role.id if entry.role else None,
            status=ASSIGNMENT_SCHEDULED,
            day_rate=_rate_for(db, entry.party, entry.role),
        )
        db.add(row)
        db.flush()
        _write_days(db, row, wishes)
        audit.record(
            db, actor, row, "crew_assignment.created", None, _snapshot(db, row), fields=ASSIGNMENT_FIELDS
        )
        made.append(row)
    return made


def update_assignment(
    db: Session,
    *,
    row: CrewAssignment,
    block: ProjectBlock,
    changes: dict,
    role: Optional[Item],
    actor: audit.Actor,
) -> None:
    """Role, rate and days (with call and wrap). `crew_assignment.updated`
    for role and rate, `crew_assignment.days_set` for the days."""
    if row.status == ASSIGNMENT_CANCELLED:
        raise AssignmentError(
            "This person was removed from the strip. Add them again to plan with them."
        )
    before = _snapshot(db, row, with_days=False)
    if "role_item_id" in changes:
        _check_role(role)
        row.role_item_id = role.id if role else None
    if "day_rate" in changes:
        row.day_rate = changes["day_rate"]
    db.flush()
    after = _snapshot(db, row, with_days=False)
    if before != after:
        audit.record(db, actor, row, "crew_assignment.updated", before, after, fields=ASSIGNMENT_FIELDS)
    if "days" in changes:
        wishes = changes["days"]
        check_days(block, wishes)
        days_before = _days_snapshot(active_days(db, [row.id])[row.id])
        _write_days(db, row, wishes)
        days_after = _days_snapshot(active_days(db, [row.id])[row.id])
        if days_before != days_after:
            audit.record(
                db,
                actor,
                row,
                "crew_assignment.days_set",
                {"days": days_before},
                {"days": days_after},
                fields=("days",),
            )


def transition_error(name: str, current: str, wanted: str) -> Optional[str]:
    """None when the change is allowed, otherwise the sentence why not."""
    if (current, wanted) in TRANSITIONS:
        return None
    if current == wanted:
        return f"{name} is already {current} on this strip."
    if wanted == ASSIGNMENT_BOOKED:
        return (
            f"{name} is {current} on this strip and cannot be marked booked. "
            "Only somebody scheduled can be booked; add them again to plan with them."
        )
    return f"{name} is {current} on this strip, so it cannot be changed to {wanted}."


def _apply_status(row: CrewAssignment, wanted: str, note: Optional[str], user_id) -> None:
    row.status = wanted
    row.status_note = note
    if wanted == ASSIGNMENT_BOOKED:
        row.booked_on = datetime.now(timezone.utc)
        row.booked_by = user_id
    elif wanted == ASSIGNMENT_SCHEDULED:
        row.booked_on = None
        row.booked_by = None


def set_status(
    db: Session,
    *,
    row: CrewAssignment,
    name: str,
    wanted: str,
    note: Optional[str],
    actor: audit.Actor,
) -> None:
    note = (note or "").strip() or None
    problem = transition_error(name, row.status, wanted)
    if problem:
        raise AssignmentError(problem)
    if row.status == ASSIGNMENT_BOOKED and wanted == ASSIGNMENT_SCHEDULED and not note:
        raise AssignmentError(
            f"Reopening {name}'s booking needs a note saying why. Add one, then reopen."
        )
    before = _snapshot(db, row, with_days=False)
    _apply_status(row, wanted, note, actor.user_id)
    db.flush()
    audit.record(
        db, actor, row, "crew_assignment.status_changed", before, _snapshot(db, row, with_days=False),
        fields=ASSIGNMENT_FIELDS,
    )


@dataclass
class BulkResult:
    changed: list[CrewAssignment]
    skipped: list[tuple[CrewAssignment, str]]


def set_status_bulk(
    db: Session,
    *,
    rows: list[tuple[CrewAssignment, str]],
    wanted: str,
    note: Optional[str],
    actor: audit.Actor,
    project: Project,
) -> BulkResult:
    """"Mark selected as booked": the allowed ones change, the others are
    listed with the reason and left as they are. ONE audit event with the
    counts."""
    note = (note or "").strip() or None
    result = BulkResult(changed=[], skipped=[])
    for row, name in rows:
        problem = transition_error(name, row.status, wanted)
        if problem is None and row.status == ASSIGNMENT_BOOKED and wanted == ASSIGNMENT_SCHEDULED and not note:
            problem = f"Reopening {name}'s booking needs a note saying why."
        if problem:
            result.skipped.append((row, problem))
            continue
        _apply_status(row, wanted, note, actor.user_id)
        result.changed.append(row)
    db.flush()
    audit.record(
        db,
        actor,
        None,
        "crew_assignment.status_changed",
        None,
        {
            "status": wanted,
            "status_note": note,
            "changed": len(result.changed),
            "skipped": len(result.skipped),
            "assignment_ids": [row.id for row in result.changed],
            "project_id": project.id,
        },
        fields=("status", "status_note", "changed", "skipped", "assignment_ids", "project_id"),
        entity_type="projects",
        entity_id=project.id,
        company_id=project.company_id,
    )
    return result


def remove_assignment(db: Session, *, row: CrewAssignment, actor: audit.Actor) -> None:
    """Remove = `cancelled`; the row and its days are kept."""
    if row.status == ASSIGNMENT_CANCELLED:
        return
    before = _snapshot(db, row, with_days=False)
    row.status = ASSIGNMENT_CANCELLED
    db.flush()
    audit.record(
        db, actor, row, "crew_assignment.removed", before, _snapshot(db, row, with_days=False),
        fields=ASSIGNMENT_FIELDS,
    )


# ─── Unavailability ─────────────────────────────────────────────────────────


def _period_snapshot(row: CrewUnavailability) -> dict:
    return {
        "party_id": row.party_id,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "reason": row.reason,
        "note": row.note,
        "is_active": row.is_active,
    }


def check_period(start: date, end: date) -> None:
    if start > end:
        raise AssignmentError(
            f"The period ends on {say(end)}, before it starts on {say(start)}. "
            "Swap the two dates."
        )


def add_unavailability(
    db: Session, *, party: Party, start: date, end: date, reason, note, actor: audit.Actor
) -> CrewUnavailability:
    check_period(start, end)
    row = CrewUnavailability(
        company_id=party.company_id,
        party_id=party.id,
        start_date=start,
        end_date=end,
        reason=(reason or "").strip() or None,
        note=(note or "").strip() or None,
        is_active=True,
    )
    db.add(row)
    db.flush()
    audit.record(
        db, actor, row, "crew_unavailability.created", None, _period_snapshot(row),
        fields=UNAVAILABILITY_FIELDS,
    )
    return row


def update_unavailability(db: Session, *, row: CrewUnavailability, changes: dict, actor: audit.Actor) -> None:
    before = _period_snapshot(row)
    for name in ("start_date", "end_date"):
        if name in changes and changes[name] is not None:
            setattr(row, name, changes[name])
    for name in ("reason", "note"):
        if name in changes:
            setattr(row, name, (changes[name] or "").strip() or None)
    check_period(row.start_date, row.end_date)
    db.flush()
    after = _period_snapshot(row)
    if before != after:
        audit.record(
            db, actor, row, "crew_unavailability.updated", before, after, fields=UNAVAILABILITY_FIELDS
        )


def remove_unavailability(db: Session, *, row: CrewUnavailability, actor: audit.Actor) -> None:
    if not row.is_active:
        return
    before = _period_snapshot(row)
    row.is_active = False
    db.flush()
    audit.record(
        db, actor, row, "crew_unavailability.removed", before, _period_snapshot(row),
        fields=UNAVAILABILITY_FIELDS,
    )


# ─── Availability ───────────────────────────────────────────────────────────


def check_range(start: date, end: date) -> None:
    if start > end:
        raise AssignmentError(
            f"The range ends on {say(end)}, before it starts on {say(start)}. Swap the two dates."
        )
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise AssignmentError(
            f"The range is {(end - start).days + 1} days long; the availability view "
            f"shows at most {MAX_RANGE_DAYS}. Choose a shorter range."
        )


def day_state(entries: list[Occupation]) -> str:
    """booked beats scheduled beats unavailable beats free."""
    statuses = {e.status for e in entries if e.kind == "assignment"}
    if ASSIGNMENT_BOOKED in statuses:
        return "booked"
    if ASSIGNMENT_SCHEDULED in statuses:
        return "scheduled"
    if any(e.kind == "unavailable" for e in entries):
        return "unavailable"
    return "free"


def grid(
    db: Session, company_id: uuid.UUID, party_ids: list[uuid.UUID], start: date, end: date
) -> dict[uuid.UUID, dict[date, list[Occupation]]]:
    busy = occupations(db, company_id, party_ids, start, end)
    out: dict[uuid.UUID, dict[date, list[Occupation]]] = {}
    for pid in party_ids:
        by_day: dict[date, list[Occupation]] = defaultdict(list)
        for occ in busy.get(pid, []):
            by_day[occ.date].append(occ)
        out[pid] = by_day
    return out
