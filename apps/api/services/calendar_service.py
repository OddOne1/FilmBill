"""The project calendar: strips over days (B2a), away from the HTTP layer.

A strip (`ProjectBlock`) groups a run of a project's days so they can be
created, moved, resized and edited together. **Days remain the source of
truth for every count** — `project_service.count_days_of_type` counts
`project_days`, never a strip's range — so each function here is, in the
end, a statement about which day rows exist and what type each has.

Rules every function keeps:

* one active day row per date per strip (`uq_project_days_active`); a date
  inside the range with no row is a gap and counts as nothing;
* nothing is deleted: a day leaving a strip is `is_active = False`, a strip
  leaving the plan is too;
* one audit event per action, with counts, never one per day (a fifteen-row
  log entry for one drag is how a log stops being read). Recorded in the
  CALLER's transaction; nothing here commits.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..models.project import PROJECT_DAY_TYPES, Project, ProjectBlock, ProjectDay
from . import audit
from .project_service import ProjectError, day_counts_by_type

#: The longest strip there is. The same limit as A2's day range, for the same
#: reason: a strip is a phase of a production, not two years of calendar.
MAX_STRIP_DAYS = 731

BLOCK_AUDITED: tuple[str, ...] = (
    "type",
    "label",
    "start_date",
    "end_date",
    "location",
    "location_id",
    "default_call_time",
    "default_wrap_time",
    "notes",
    "position",
    "is_active",
)

BLOCK_CREATED = "project_block.created"
BLOCK_UPDATED = "project_block.updated"
BLOCK_MOVED = "project_block.moved"
BLOCK_RESIZED = "project_block.resized"
BLOCK_REMOVED = "project_block.removed"
BLOCK_DAYS_SET = "project_block.days_set"

#: The fields that make a day "carry data" — what a resize asks about.
DAY_DATA_FIELDS: tuple[str, ...] = ("location", "location_id", "call_time", "wrap_time", "notes")


def say(day: date) -> str:
    """`2027-03-04` → `4 March 2027`. How a date reads in a sentence."""
    return f"{day.day} {day.strftime('%B %Y')}"


def has_own_data(day: ProjectDay) -> bool:
    return any(getattr(day, name) not in (None, "") for name in DAY_DATA_FIELDS)


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def check_range(start: date, end: date) -> None:
    if end < start:
        raise ProjectError(
            f"This strip would end on {say(end)}, before it starts on "
            f"{say(start)}. Pick an end date on or after the start."
        )
    span = (end - start).days + 1
    if span > MAX_STRIP_DAYS:
        raise ProjectError(
            f"This strip would be {span} days long. Split it into shorter "
            f"strips — one phase of a production at a time."
        )


def check_type(value: str) -> str:
    if value not in PROJECT_DAY_TYPES:
        raise ProjectError(
            f"{value!r} is not a day type. Use one of "
            f"{', '.join(PROJECT_DAY_TYPES)}."
        )
    return value


# ─── Reading ────────────────────────────────────────────────────────────────


def block_days(db: Session, block: ProjectBlock) -> list[ProjectDay]:
    return (
        db.query(ProjectDay)
        .filter(ProjectDay.block_id == block.id, ProjectDay.is_active.is_(True))
        .order_by(ProjectDay.date)
        .all()
    )


def block_counts(days: Iterable[ProjectDay]) -> dict[str, int]:
    """`type → days` inside one strip. One row per date per strip, so rows
    and dates are the same number here."""
    counts: dict[str, int] = {}
    for day in days:
        counts[day.type] = counts.get(day.type, 0) + 1
    return counts


def project_counts(db: Session, project: Project) -> dict[str, int]:
    return day_counts_by_type(db, [project.id]).get(project.id, {})


@dataclass
class CalendarView:
    blocks: list[tuple[ProjectBlock, list[ProjectDay]]]
    loose: list[ProjectDay]
    counts: dict[str, int]


def calendar(
    db: Session,
    project: Project,
    *,
    range_start: Optional[date] = None,
    range_end: Optional[date] = None,
) -> CalendarView:
    """The strips that touch the range, each with its days, and the loose
    days in it. Counts are for the whole project, whatever the range."""
    if range_start and range_end and range_end < range_start:
        raise ProjectError(
            f"The calendar range ends on {say(range_end)}, before it starts on "
            f"{say(range_start)}."
        )
    query = db.query(ProjectBlock).filter(
        ProjectBlock.project_id == project.id, ProjectBlock.is_active.is_(True)
    )
    if range_end is not None:
        query = query.filter(ProjectBlock.start_date <= range_end)
    if range_start is not None:
        query = query.filter(ProjectBlock.end_date >= range_start)
    blocks = query.order_by(ProjectBlock.start_date, ProjectBlock.created_at).all()

    days_by_block: dict[uuid.UUID, list[ProjectDay]] = {block.id: [] for block in blocks}
    if blocks:
        for day in (
            db.query(ProjectDay)
            .filter(
                ProjectDay.block_id.in_(list(days_by_block)),
                ProjectDay.is_active.is_(True),
            )
            .order_by(ProjectDay.date)
        ):
            days_by_block[day.block_id].append(day)

    loose_query = db.query(ProjectDay).filter(
        ProjectDay.project_id == project.id,
        ProjectDay.block_id.is_(None),
        ProjectDay.is_active.is_(True),
    )
    if range_start is not None:
        loose_query = loose_query.filter(ProjectDay.date >= range_start)
    if range_end is not None:
        loose_query = loose_query.filter(ProjectDay.date <= range_end)
    loose = loose_query.order_by(ProjectDay.date, ProjectDay.type).all()

    return CalendarView(
        blocks=[(block, days_by_block[block.id]) for block in blocks],
        loose=loose,
        counts=project_counts(db, project),
    )


# ─── Writing ────────────────────────────────────────────────────────────────


@dataclass
class Change:
    """What an action did, for the response and the audit row."""

    detail: str
    applied: bool = True
    needs_confirm: bool = False
    at_risk: list[ProjectDay] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    changed: int = 0


def _new_day(block: ProjectBlock, day: date, day_type: str) -> ProjectDay:
    return ProjectDay(
        company_id=block.company_id,
        project_id=block.project_id,
        block_id=block.id,
        date=day,
        type=day_type,
        is_active=True,
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def create_block(
    db: Session,
    membership,
    project: Project,
    *,
    actor: audit.Actor,
    day_type: str,
    start: date,
    end: date,
    label: Optional[str] = None,
    location: Optional[str] = None,
    default_call_time=None,
    default_wrap_time=None,
    notes: Optional[str] = None,
    weekends: str = "same",
    reason: Optional[str] = None,
) -> tuple[ProjectBlock, Change]:
    """A strip and one day per date; weekends `off` or of the main type."""
    check_type(day_type)
    check_range(start, end)
    block = ProjectBlock(
        company_id=membership.company_id,
        project_id=project.id,
        type=day_type,
        label=(label or "").strip() or None,
        start_date=start,
        end_date=end,
        location=location,
        default_call_time=default_call_time,
        default_wrap_time=default_wrap_time,
        notes=notes,
        is_active=True,
    )
    db.add(block)
    db.flush()
    off = 0
    for day in _dates(start, end):
        weekend = day.weekday() >= 5
        if weekend and weekends == "off":
            off += 1
            db.add(_new_day(block, day, "off"))
        else:
            db.add(_new_day(block, day, day_type))
    db.flush()
    total = (end - start).days + 1
    detail = f"{_plural(total, 'day')} added"
    if off:
        detail += f", {off} of them weekend days off"
    detail += "."
    audit.record(
        db,
        actor,
        block,
        BLOCK_CREATED,
        None,
        {**audit.snapshot(block, BLOCK_AUDITED), "days": total, "weekend_days_off": off},
        reason=reason or detail,
    )
    return block, Change(detail=detail, added=total)


def update_block(
    db: Session, block: ProjectBlock, changes: dict, *, actor: audit.Actor
) -> Change:
    """A strip's own fields. A new main type carries the days of the old one
    with it; overrides (travel, off, …) stay what they are."""
    before = audit.snapshot(block, BLOCK_AUDITED)
    moved = 0
    if "type" in changes and changes["type"] is not None and changes["type"] != block.type:
        check_type(changes["type"])
        for day in block_days(db, block):
            if day.type == block.type:
                day.type = changes["type"]
                moved += 1
    for name, value in changes.items():
        if name == "type" and value is None:
            continue
        if name == "label" and value is not None:
            value = value.strip() or None
        setattr(block, name, value)
    db.flush()
    audit.record(
        db,
        actor,
        block,
        BLOCK_UPDATED,
        before,
        audit.snapshot(block, BLOCK_AUDITED),
        fields=BLOCK_AUDITED,
        reason=f"{_plural(moved, 'day')} followed the new type." if moved else None,
    )
    return Change(detail="Strip saved.", changed=moved)


def _shift(db: Session, days: list[ProjectDay], offset: int) -> None:
    """Move every day by `offset` without tripping the per-strip index.

    A unique index is checked row by row, so moving 3–7 March forward one
    day in date order would put the 3rd onto the 4th while the 4th is still
    there. Moving the LAST day first (or the first day first when moving
    back) never collides.
    """
    ordered = sorted(days, key=lambda day: day.date, reverse=offset > 0)
    for day in ordered:
        day.date = day.date + timedelta(days=offset)
        db.flush()


def move_block(
    db: Session, block: ProjectBlock, new_start: date, *, actor: audit.Actor
) -> Change:
    """The whole strip to a new start; every day, override and detail with it."""
    offset = (new_start - block.start_date).days
    if offset == 0:
        return Change(detail="The strip already starts there.")
    before = audit.snapshot(block, BLOCK_AUDITED)
    days = block_days(db, block)
    _shift(db, days, offset)
    block.start_date = block.start_date + timedelta(days=offset)
    block.end_date = block.end_date + timedelta(days=offset)
    db.flush()
    direction = "later" if offset > 0 else "earlier"
    detail = (
        f"Moved {_plural(abs(offset), 'day')} {direction}, to "
        f"{say(block.start_date)} – {say(block.end_date)}."
    )
    audit.record(
        db,
        actor,
        block,
        BLOCK_MOVED,
        before,
        audit.snapshot(block, BLOCK_AUDITED),
        fields=BLOCK_AUDITED,
        reason=f"{detail} {_plural(len(days), 'day')} moved with it.",
    )
    return Change(detail=detail, changed=len(days))


def resize_block(
    db: Session,
    block: ProjectBlock,
    *,
    new_start: Optional[date],
    new_end: Optional[date],
    confirm: bool,
    actor: audit.Actor,
) -> Change:
    """New start and/or end.

    Growing adds days of the main type on the new dates. Shrinking takes the
    days outside the new range out of the plan (`is_active = False`) — and
    if any of them has notes, a location or a time of its own, nothing
    happens until the caller confirms, because those are the days somebody
    typed something into and would not expect a drag to throw away.
    """
    start = new_start or block.start_date
    end = new_end or block.end_date
    check_range(start, end)
    days = block_days(db, block)
    leaving = [day for day in days if day.date < start or day.date > end]
    at_risk = [day for day in leaving if has_own_data(day)]
    if at_risk and not confirm:
        listed = ", ".join(say(day.date) for day in at_risk)
        return Change(
            applied=False,
            needs_confirm=True,
            at_risk=at_risk,
            detail=(
                f"{_plural(len(at_risk), 'day')} that would be taken out "
                f"{'has' if len(at_risk) == 1 else 'have'} notes, a location or "
                f"times of {'its' if len(at_risk) == 1 else 'their'} own: "
                f"{listed}. Confirm to take {'it' if len(at_risk) == 1 else 'them'} "
                f"out anyway."
            ),
        )

    before = audit.snapshot(block, BLOCK_AUDITED)
    for day in leaving:
        day.is_active = False
    db.flush()
    added = 0
    for day in _dates(start, end):
        if block.start_date <= day <= block.end_date:
            continue
        db.add(_new_day(block, day, block.type))
        added += 1
    block.start_date = start
    block.end_date = end
    db.flush()
    parts = []
    if added:
        parts.append(f"{_plural(added, 'day')} added")
    if leaving:
        parts.append(f"{_plural(len(leaving), 'day')} taken out")
    detail = (", ".join(parts) or "No days changed") + (
        f"; the strip now runs {say(start)} – {say(end)}."
    )
    audit.record(
        db,
        actor,
        block,
        BLOCK_RESIZED,
        before,
        audit.snapshot(block, BLOCK_AUDITED),
        fields=BLOCK_AUDITED,
        reason=detail,
    )
    return Change(detail=detail, added=added, removed=len(leaving))


def set_days(
    db: Session,
    block: ProjectBlock,
    entries: list[tuple[date, Optional[str]]],
    *,
    actor: audit.Actor,
) -> Change:
    """Set the type of some of the strip's days; `None` = back to main type.

    A date inside the range that has no row (a gap) gets one. A date outside
    the range is refused — the strip is resized first, on purpose, so that
    "mark the 9th as travel" can never silently make a strip longer.
    """
    seen: set[date] = set()
    for day, day_type in entries:
        if day in seen:
            raise ProjectError(
                f"{say(day)} is in the list twice. Send each day once."
            )
        seen.add(day)
        if day < block.start_date or day > block.end_date:
            raise ProjectError(
                f"{say(day)} is outside this strip ({say(block.start_date)} – "
                f"{say(block.end_date)}). Resize the strip first."
            )
        if day_type is not None:
            check_type(day_type)

    by_date = {day.date: day for day in block_days(db, block)}
    changed = created = 0
    types: dict[str, int] = {}
    for day, day_type in entries:
        wanted = day_type or block.type
        row = by_date.get(day)
        if row is None:
            db.add(_new_day(block, day, wanted))
            created += 1
        elif row.type != wanted:
            row.type = wanted
            changed += 1
        else:
            continue
        types[wanted] = types.get(wanted, 0) + 1
    db.flush()
    detail = (
        f"{_plural(changed + created, 'day')} changed."
        if changed + created
        else "Nothing changed — the days already had those types."
    )
    audit.record(
        db,
        actor,
        block,
        BLOCK_DAYS_SET,
        None,
        {
            "requested": len(entries),
            "changed": changed,
            "created": created,
            "unchanged": len(entries) - changed - created,
            "types": types,
        },
        reason=detail,
    )
    return Change(detail=detail, changed=changed, added=created)


def duplicate_block(
    db: Session,
    membership,
    project: Project,
    block: ProjectBlock,
    new_start: date,
    *,
    actor: audit.Actor,
) -> tuple[ProjectBlock, Change]:
    """A copy of the strip starting on `new_start`: same fields, same days
    — types, location, times and notes — at the same offsets."""
    offset = (new_start - block.start_date).days
    copy = ProjectBlock(
        company_id=block.company_id,
        project_id=block.project_id,
        type=block.type,
        label=block.label,
        start_date=block.start_date + timedelta(days=offset),
        end_date=block.end_date + timedelta(days=offset),
        location=block.location,
        location_id=block.location_id,
        default_call_time=block.default_call_time,
        default_wrap_time=block.default_wrap_time,
        notes=block.notes,
        position=block.position,
        is_active=True,
    )
    db.add(copy)
    db.flush()
    days = block_days(db, block)
    for day in days:
        row = _new_day(copy, day.date + timedelta(days=offset), day.type)
        for name in DAY_DATA_FIELDS:
            setattr(row, name, getattr(day, name))
        db.add(row)
    db.flush()
    detail = (
        f"Duplicated to {say(copy.start_date)} – {say(copy.end_date)}, "
        f"{_plural(len(days), 'day')}."
    )
    audit.record(
        db,
        actor,
        copy,
        BLOCK_CREATED,
        None,
        {**audit.snapshot(copy, BLOCK_AUDITED), "days": len(days), "duplicated_from": str(block.id)},
        reason=detail,
    )
    return copy, Change(detail=detail, added=len(days))


def remove_block(
    db: Session, block: ProjectBlock, *, actor: audit.Actor
) -> Change:
    """The strip and its days out of the plan. Nothing is deleted."""
    if not block.is_active:
        return Change(detail="This strip was already removed.")
    before = audit.snapshot(block, BLOCK_AUDITED)
    days = block_days(db, block)
    for day in days:
        day.is_active = False
    block.is_active = False
    db.flush()
    detail = f"Strip removed with its {_plural(len(days), 'day')}."
    audit.record(
        db,
        actor,
        block,
        BLOCK_REMOVED,
        before,
        audit.snapshot(block, BLOCK_AUDITED),
        fields=BLOCK_AUDITED,
        reason=detail,
    )
    return Change(detail=detail, removed=len(days))
