"""Projects and production days: the rules, away from the HTTP layer.

What is here rather than in `routers/projects.py`: the number allocation and
its transaction boundary, the day-range expansion, the list of fields an
audit row records, and the party archive guard's project source. The router
does lookups, permissions and response shapes.

**The number is allocated inside the caller's transaction** (CLAUDE.md
rule 5). `allocate_number` holds `SELECT … FOR UPDATE` on the series row and
does not commit, so a project whose INSERT fails consumes no number and a
project that commits has both the number and the row that records it — or
neither. `create_project` therefore flushes and never commits: the router
owns the commit, and `test_projects_api.py` proves the rollback case by
failing the insert after the allocation and asserting the counter did not
move.
"""

import uuid
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

from sqlalchemy.orm import Session

from ..models.party import Party
from ..models.project import (
    PROJECT_DAY_TYPES,
    PROJECT_SERIES_TYPE,
    PROJECT_STATUSES,
    Project,
    ProjectDay,
)
from . import audit
from .number_series import (
    NumberSeriesMissing,
    allocate_number,
)
from .party_service import register_archive_blocker
from .permissions import scoped


class ProjectError(ValueError):
    """A refusal a user can act on. The router answers 409 with the sentence."""


# ─── What an audit row records ──────────────────────────────────────────────
#
# Named fields rather than the whole row, for `audit.snapshot`'s reason:
# adding a column must not silently start logging it.

PROJECT_AUDITED: tuple[str, ...] = (
    "project_number",
    "name",
    "client_party_id",
    "description",
    "status",
    "start_date",
    "end_date",
    "notes",
)

PROJECT_DAY_AUDITED: tuple[str, ...] = (
    "date",
    "type",
    "location",
    "call_time",
    "wrap_time",
    "notes",
    "is_active",
)

PROJECT_CREATED = "project.created"
PROJECT_UPDATED = "project.updated"
PROJECT_STATUS_CHANGED = "project.status_changed"
DAY_CREATED = "project_day.created"
DAY_UPDATED = "project_day.updated"
DAY_REMOVED = "project_day.removed"


# ─── Vocabulary checks ──────────────────────────────────────────────────────


def check_status(value: str) -> str:
    if value not in PROJECT_STATUSES:
        raise ProjectError(
            f"{value!r} is not a project status. The four are "
            f"{', '.join(PROJECT_STATUSES)}."
        )
    return value


def check_day_type(value: str) -> str:
    if value not in PROJECT_DAY_TYPES:
        raise ProjectError(
            f"{value!r} is not a production-day type. SCOPE §11.3 lists "
            f"{', '.join(PROJECT_DAY_TYPES)}."
        )
    return value


# ─── Creating a project ─────────────────────────────────────────────────────


def create_project(
    db: Session,
    membership,
    *,
    name: str,
    start_date: date,
    end_date: Optional[date] = None,
    client_party_id: Optional[uuid.UUID] = None,
    description: Optional[str] = None,
    status: str = "planning",
    notes: Optional[str] = None,
    actor: Optional[audit.Actor] = None,
) -> Project:
    """A project with its number, in the CALLER's transaction. No commit.

    The number comes from the `project` series through the same
    `allocate_number` that will number invoices, so the one promise about
    numbers has one implementation. A company with no active `project`
    series — somebody switched it off — is a `ProjectError` and so a 409,
    not a 500: it names the screen that fixes it.
    """
    check_status(status)
    if end_date is not None and end_date < start_date:
        raise ProjectError(
            f"the end date ({end_date}) is before the start date "
            f"({start_date}). Leave it empty if the wrap is not booked yet."
        )

    project = Project(
        company_id=membership.company_id,
        name=name.strip(),
        client_party_id=client_party_id,
        description=description,
        status=status,
        start_date=start_date,
        end_date=end_date,
        notes=notes,
        # Filled in two statements down. Not nullable in the database, so the
        # flush below is the first point at which it has to be right.
        project_number="",
    )

    try:
        plan = allocate_number(
            db,
            company_id=membership.company_id,
            document_type=PROJECT_SERIES_TYPE,
            # The project's own start date decides the period, so a project
            # starting in January 2027 planned in December 2026 is numbered
            # in the year it runs. Out-of-order dates are allowed with a
            # warning (D45) and the warning is the series' business.
            document_date=start_date,
            entity_type="projects",
            entity_id=project.id,
            actor=actor,
        )
    except NumberSeriesMissing as error:
        raise ProjectError(str(error)) from None

    project.project_number = plan.number
    db.add(project)
    db.flush()
    return project


# ─── Days ───────────────────────────────────────────────────────────────────


def active_days(db: Session, project: Project) -> list[ProjectDay]:
    return (
        db.query(ProjectDay)
        .filter(
            ProjectDay.project_id == project.id,
            ProjectDay.is_active.is_(True),
        )
        .order_by(ProjectDay.date, ProjectDay.type)
        .all()
    )


def count_days_of_type(db: Session, project_id: uuid.UUID, day_type: str) -> int:
    """How many ACTIVE days of this type the project has.

    What a `days:<type>` global's value is. A count of active rows and not of
    every row ever created: a shoot day that was taken out of the plan is not
    a shoot day, and a budget that still billed for it would be wrong in the
    direction nobody checks.
    """
    return (
        db.query(ProjectDay)
        .filter(
            ProjectDay.project_id == project_id,
            ProjectDay.type == day_type,
            ProjectDay.is_active.is_(True),
        )
        .count()
    )


def dates_in_range(
    start: date, end: date, *, skip_weekends: bool = False
) -> list[date]:
    """Every date from `start` to `end` inclusive, weekends optionally out.

    Pure, so the weekend rule is testable without a project. Saturday and
    Sunday are `weekday()` 5 and 6; nothing here knows about public
    holidays, because a holiday calendar is region-pack data that P7's
    overtime engine brings (SCOPE §11.4) and guessing one now would put
    Austrian holidays on a German shoot.
    """
    if end < start:
        raise ProjectError(
            f"the range ends ({end}) before it starts ({start})."
        )
    span = (end - start).days
    if span > 730:
        raise ProjectError(
            f"that range is {span + 1} days. A production day range is a "
            f"block of a shoot, not two years of calendar — add them in "
            f"shorter runs."
        )
    found = []
    for offset in range(span + 1):
        day = start + timedelta(days=offset)
        if skip_weekends and day.weekday() >= 5:
            continue
        found.append(day)
    return found


def create_day_range(
    db: Session,
    membership,
    project: Project,
    *,
    start: date,
    end: date,
    day_type: str,
    skip_weekends: bool = False,
) -> tuple[list[ProjectDay], list[date], list[date]]:
    """Create the days in the range. Returns `(created, existing, skipped)`.

    One transaction, and it reports what it did rather than failing on the
    first collision: adding "the 5th to the 12th as shoot days" over a block
    that already has three of them is a normal act, and refusing the whole
    range because of them would make somebody enter the gaps by hand.

    `existing` is the dates that already had an active day of this type;
    `skipped` is the weekends left out, so the screen can say "8 days added,
    2 weekend days skipped" instead of silently producing a different
    number of days than the person asked for (rule 17c — a silent screen is
    the failure mode).
    """
    check_day_type(day_type)
    wanted = dates_in_range(start, end, skip_weekends=skip_weekends)
    all_dates = dates_in_range(start, end)
    skipped = [day for day in all_dates if day not in set(wanted)]

    taken = {
        row.date
        for row in db.query(ProjectDay)
        .filter(
            ProjectDay.project_id == project.id,
            ProjectDay.type == day_type,
            ProjectDay.is_active.is_(True),
        )
        .all()
    }

    created: list[ProjectDay] = []
    existing: list[date] = []
    for day in wanted:
        if day in taken:
            existing.append(day)
            continue
        row = ProjectDay(
            company_id=membership.company_id,
            project_id=project.id,
            date=day,
            type=day_type,
            is_active=True,
        )
        db.add(row)
        created.append(row)
    db.flush()
    return created, existing, skipped


# ─── Projects as a reason a party cannot be archived (A2) ───────────────────
#
# Registered HERE, at the bottom of the module that owns the referring table
# — which is what `party_service`'s registration point is for, and the same
# shape `catalog_service` and `party_service` use for the tax registry.
#
# An archived client on a live project would leave that project resolving its
# recipient from a row the party list no longer shows. The refusal names the
# project, because "the party is in use" is not something anybody can act on.


def _projects_using_party(db: Session, party: Party) -> list:
    """Non-archived projects whose client is this party."""
    from .tax_references import Referrer

    rows = (
        db.query(Project)
        .filter(
            Project.company_id == party.company_id,
            Project.client_party_id == party.id,
            Project.status != "archived",
        )
        .order_by(Project.project_number)
        .all()
    )
    return [
        Referrer(kind="project", name=f"{row.project_number} {row.name}")
        for row in rows
    ]


def register_referrers() -> None:
    """Tell the party archive registry about projects.

    A function as well as the call below, matching `party_service`'s own
    shape: `main.py` can call it explicitly, and a test that imports this
    module without going through `main` still gets the guard. The registry
    is idempotent, so both happening is harmless.
    """
    register_archive_blocker(_projects_using_party)


register_referrers()
