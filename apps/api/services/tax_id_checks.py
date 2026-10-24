"""Running a tax-identifier check, recording it, and noticing what changed.

One function writes a check row — `record_check` — and everything else calls
it: the manual button, the initial series, the scheduler, a retry. That is
deliberate. The rules about what counts as an answer, what advances the
schedule and what deserves a human's attention are the same whoever asked,
and five callers each applying them would be five chances for one of them to
drift.

**`unavailable` is not a completed check** (D62). It does not displace an
earlier verdict, does not advance the initial series, and does not clear the
retry counter — it schedules a retry and leaves the previous state alone.
Treating it as completed would un-verify every customer at once the first
time a government service had a bad afternoon, and then re-verify them an
hour later, writing a change event each way.

**Change detection compares completed checks only**, for the same reason: a
`valid` followed by an `unavailable` followed by a `valid` is not two
changes, it is one unreachable registry.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..billing.parties.verification import (
    COMPLETED_STATUSES,
    NOT_CHECKABLE,
    UNAVAILABLE,
    VALID,
    PartyFacts,
    Requester,
    VerificationResult,
)
from ..models.company import Company
from ..models.party import (
    PROVIDER_MANUAL,
    TRIGGER_INITIAL,
    TRIGGER_MANUAL,
    TRIGGER_RETRY,
    TRIGGER_SCHEDULED,
    Party,
    PartyAddress,
    PartyTaxId,
    PartyVatCheck,
)
from . import audit
from .tax_id_policy import TaxIdCheckPolicy, load_policy
from .verification import verify

logger = logging.getLogger(__name__)

#: How long to wait after a failure to REACH the registry. Two attempts
#: close together, then back to the ordinary schedule: a registry that is
#: down for six hours is usually down for a day, and hammering it is both
#: rude and — per VIES's published concurrency limits — counter-productive.
RETRY_HOURS = (6, 24)

#: Spread, in seconds, added to every scheduled time. Without it every
#: identifier created in one import comes due in the same second for ever,
#: and the hourly task either does all of them or none. ±10 % of a day.
JITTER_SECONDS = 8640

#: How many identifiers one scheduler run may check. Small on purpose: an
#: installation with 2000 customers should spread them over hours rather
#: than open 2000 connections to the Commission and get its address blocked
#: (`IP_BLOCKED` is a documented VIES fault).
DEFAULT_BATCH = 25

#: What a change in these means, in words a person can act on. Closed,
#: because each one is rendered as a sentence and a reason nothing renders
#: is a reason nobody sees.
CHANGE_STATUS = "status_changed"
CHANGE_NAME = "registered_name_changed"
CHANGE_NAME_MATCH = "name_match_worsened"
CHANGE_REGISTRATION = "registration_lost"
CHANGE_KINDS = (
    CHANGE_STATUS,
    CHANGE_NAME,
    CHANGE_NAME_MATCH,
    CHANGE_REGISTRATION,
)


def party_facts(db: Session, party: Party) -> PartyFacts:
    """What the registries that compare are told about the party.

    The default billing address, because that is the one a document uses and
    therefore the one whose agreement with a register is worth knowing.
    """
    address = (
        db.query(PartyAddress)
        .filter(
            PartyAddress.party_id == party.id,
            PartyAddress.kind == "billing",
        )
        .order_by(PartyAddress.is_default.desc(), PartyAddress.created_at)
        .first()
    )
    text = None
    if address is not None:
        text = ", ".join(
            part
            for part in (
                address.street,
                " ".join(p for p in (address.zip, address.city) if p),
                address.country,
            )
            if part
        ) or None
    return PartyFacts(
        name=party.name or "",
        street=getattr(address, "street", None),
        postcode=getattr(address, "zip", None),
        city=getattr(address, "city", None),
        country=getattr(address, "country", None) or party.tax_country,
        address_text=text,
    )


def requester_for(db: Session, company_id: uuid.UUID) -> Requester:
    """Our own VAT ID, for the EU's approximate check.

    Empty when the company has not saved one, and the consequence is visible
    rather than silent: the lookup still happens, the result is marked
    `simple`, and the sentence says where to add it.
    """
    company = db.get(Company, company_id)
    vat_id = (getattr(company, "vat_id", None) or "").strip()
    if not vat_id:
        return Requester()
    country = vat_id[:2].upper() if vat_id[:2].isalpha() else (
        company.address_country or ""
    ).upper()
    return Requester(country=country or None, vat_number=vat_id)


def run_check(
    db: Session,
    *,
    tax_id: PartyTaxId,
    party: Party,
    transports: Optional[dict] = None,
) -> VerificationResult:
    """Ask the right registry about this identifier. Writes nothing."""
    transports = transports or {}
    return verify(
        tax_id.value,
        kind=tax_id.kind,
        country=tax_id.country,
        party=party_facts(db, party),
        requester=requester_for(db, party.company_id),
        vies_transport=transports.get("vies"),
        abn_transport=transports.get("abn"),
        abn_guid=transports.get("abn_guid"),
    )


def record_check(
    db: Session,
    *,
    tax_id: PartyTaxId,
    party: Party,
    result: VerificationResult,
    trigger_kind: str = TRIGGER_MANUAL,
    actor=None,
    checked_by: Optional[uuid.UUID] = None,
    policy: Optional[TaxIdCheckPolicy] = None,
    manual_note: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    valid_until: Optional[datetime] = None,
) -> PartyVatCheck:
    """Write the check row, detect a change, and set the next due time.

    The caller commits. Audit goes in the same transaction as the row, which
    is the point of not committing here: a check that happened with no audit
    event, or an event for a check that rolled back, are both worse than
    either alone.

    **The manual-confirmation fields are arguments, not something a caller
    sets afterwards.** The table is append-only at the database level, so
    `row.manual_note = ...` after the flush is an UPDATE and the trigger
    refuses it — which is exactly what happened on the first attempt, and
    exactly what the trigger is for. Everything a check row will ever say
    has to be said before it is inserted.
    """
    policy = policy or load_policy(db, company_id=party.company_id)
    previous = _latest_completed(db, tax_id)

    row = PartyVatCheck(
        company_id=party.company_id,
        party_id=party.id,
        tax_id_id=tax_id.id,
        vat_id=result.value or tax_id.value,
        country=result.country or tax_id.country,
        kind=tax_id.kind,
        provider=result.provider,
        trigger_kind=trigger_kind,
        status=result.status,
        qualified=bool(result.qualified),
        registered_name=result.registered_name,
        registered_address=result.registered_address,
        name_match=result.name_match,
        address_match=result.address_match,
        registration_active=result.registration_active,
        gst_registered=result.gst_registered,
        consultation_ref=result.consultation_ref,
        response=_stored_response(result),
        checked_by=checked_by,
        manual_note=manual_note,
        evidence_reference=evidence_reference,
        valid_until=valid_until,
    )

    completed = result.status in COMPLETED_STATUSES
    changes = detect_changes(previous, row) if completed else {}
    if changes:
        row.change_detected = changes

    db.add(row)
    db.flush()

    _advance_schedule(tax_id, result=result, policy=policy, completed=completed)

    if changes:
        tax_id.needs_attention = True
        tax_id.needs_attention_reason = _change_sentence(changes)
        if actor is not None:
            audit.record(
                db,
                actor,
                party,
                "party.tax_id_change_detected",
                None,
                {
                    "tax_id": tax_id.value,
                    "kind": tax_id.kind,
                    "changes": changes,
                },
                fields=["tax_id", "kind", "changes"],
                reason=tax_id.needs_attention_reason,
            )

    if actor is not None:
        audit.record(
            db,
            actor,
            party,
            "party.tax_id_checked",
            None,
            {
                "tax_id": tax_id.value,
                "kind": tax_id.kind,
                "status": result.status,
                "provider": result.provider,
                "qualified": bool(result.qualified),
                "trigger_kind": trigger_kind,
            },
            fields=["tax_id", "kind", "status", "provider", "qualified",
                    "trigger_kind"],
            reason=result.detail,
        )
    return row


def _stored_response(result: VerificationResult) -> dict:
    """The registry's body, with the lifted fields beside it.

    Beside, not instead of: a reviewer in two years wants the body, and the
    underscore-prefixed keys are how P1b already distinguishes our reading
    from theirs.
    """
    stored = dict(result.raw or {})
    stored["_detail"] = result.detail
    stored["_trader_name"] = result.registered_name
    stored["_trader_address"] = result.registered_address
    if result.error:
        stored["_error"] = result.error
    return stored


def _latest_completed(db: Session, tax_id: PartyTaxId) -> Optional[PartyVatCheck]:
    return (
        db.query(PartyVatCheck)
        .filter(
            PartyVatCheck.tax_id_id == tax_id.id,
            PartyVatCheck.status.in_(COMPLETED_STATUSES),
        )
        .order_by(PartyVatCheck.checked_at.desc())
        .first()
    )


def detect_changes(
    previous: Optional[PartyVatCheck], current: PartyVatCheck
) -> dict:
    """What differs between two completed checks, as a diff.

    **The first check of an identifier is never a change.** There is nothing
    to compare it with, and reporting "status changed from nothing to valid"
    on every new customer would make the attention list useless on day one.

    Four things matter (D63), and each is here because of what it means:

      * `status_changed` — valid ↔ invalid. The registration ended, or the
        number was mistyped and the register has caught up.
      * `registered_name_changed` — the company was renamed, or sold, or the
        number now belongs to somebody else.
      * `name_match_worsened` — specifically TO `mismatch`. An improvement
        needs nobody's attention.
      * `registration_lost` — an ABN cancelled, or GST registration ended.
    """
    if previous is None:
        return {}

    changes: dict = {}

    if previous.status != current.status:
        changes[CHANGE_STATUS] = [previous.status, current.status]

    before_name = (previous.registered_name or "").strip()
    after_name = (current.registered_name or "").strip()
    if before_name and after_name and before_name != after_name:
        # Both sides must have a name. A register that stopped disclosing
        # names — or started — is a change in the register's policy, not in
        # the company, and reporting it would fire on every customer of that
        # member state on the same day.
        changes[CHANGE_NAME] = [before_name, after_name]

    if (
        current.name_match == "mismatch"
        and previous.name_match != "mismatch"
        and previous.name_match is not None
    ):
        changes[CHANGE_NAME_MATCH] = [previous.name_match, current.name_match]

    for field in ("registration_active", "gst_registered"):
        before = getattr(previous, field)
        after = getattr(current, field)
        if before is True and after is False:
            # One direction only: gaining a registration is good news and
            # needs no intervention.
            changes.setdefault(CHANGE_REGISTRATION, {})[field] = [before, after]

    return changes


def _change_sentence(changes: dict) -> str:
    """The attention reason, in words.

    Assembled from the diff rather than stored per case, so a change that
    combines two of them reads as one sentence instead of overwriting
    itself.
    """
    parts = []
    if CHANGE_STATUS in changes:
        before, after = changes[CHANGE_STATUS]
        parts.append(
            f"the registry's answer changed from “{before}” to “{after}”"
        )
    if CHANGE_NAME in changes:
        before, after = changes[CHANGE_NAME]
        parts.append(
            f"the name on file at the registry changed from “{before}” to "
            f"“{after}”"
        )
    if CHANGE_NAME_MATCH in changes:
        parts.append(
            "the registry's name no longer looks like this party's name"
        )
    if CHANGE_REGISTRATION in changes:
        lost = ", ".join(
            {
                "registration_active": "the registration is no longer active",
                "gst_registered": "the GST registration has ended",
            }[field]
            for field in changes[CHANGE_REGISTRATION]
        )
        parts.append(lost)
    if not parts:  # pragma: no cover - defensive
        return "Something about this identifier changed."
    joined = parts[0] if len(parts) == 1 else (
        ", and ".join([", ".join(parts[:-1]), parts[-1]])
        if len(parts) > 2
        else " and ".join(parts)
    )
    return (
        f"Since the last check, {joined}. Acknowledge this with a note once "
        "you have looked at it."
    )


def _advance_schedule(
    tax_id: PartyTaxId,
    *,
    result: VerificationResult,
    policy: TaxIdCheckPolicy,
    completed: bool,
) -> None:
    """Set `next_check_at` and the counters.

    **An `unavailable` does not advance anything.** It increments the retry
    streak and schedules a near retry; the initial series and the ordinary
    interval are untouched, so a registry being down cannot walk an
    identifier through its initial series without ever having reached it.
    """
    now = datetime.now(timezone.utc)

    if not completed:
        tax_id.unavailable_streak = (tax_id.unavailable_streak or 0) + 1
        index = min(tax_id.unavailable_streak, len(RETRY_HOURS)) - 1
        if tax_id.unavailable_streak <= len(RETRY_HOURS):
            tax_id.next_check_at = _jittered(
                now + timedelta(hours=RETRY_HOURS[index])
            )
        else:
            # Out of retries: back to the ordinary schedule, so a registry
            # that is down for a week does not accumulate an attempt every
            # six hours for ever.
            tax_id.next_check_at = _next_regular(now, tax_id, policy)
        return

    tax_id.unavailable_streak = 0

    if result.status == NOT_CHECKABLE:
        # A settled fact about the world. Nothing to schedule — otherwise a
        # US EIN would generate an attempt every quarter that can only ever
        # return the same answer.
        tax_id.initial_checks_done = len(policy.initial_check_offsets)
        tax_id.next_check_at = None
        return

    offsets = policy.initial_check_offsets
    done = (tax_id.initial_checks_done or 0) + 1
    tax_id.initial_checks_done = done

    if done < len(offsets):
        # Still in the initial series: the next offset, measured from the
        # identifier's creation so the series does not stretch when a check
        # runs late.
        created = tax_id.created_at or now
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        target = created + timedelta(days=offsets[done])
        tax_id.next_check_at = _jittered(max(target, now + timedelta(minutes=1)))
        return

    tax_id.next_check_at = _next_regular(now, tax_id, policy)


def _next_regular(
    now: datetime, tax_id: PartyTaxId, policy: TaxIdCheckPolicy
) -> Optional[datetime]:
    interval = policy.interval_for(
        getattr(tax_id.party, "recheck_interval_days", None)
        if tax_id.party is not None
        else None
    )
    if not interval:
        # Re-checking turned off. A legitimate choice for an installation
        # with no outbound internet.
        return None
    return _jittered(now + timedelta(days=interval))


def _jittered(when: datetime) -> datetime:
    """Spread, so an import does not create a thundering herd.

    Without this every identifier created in one batch comes due in the same
    second for ever, and the hourly task either does all of them or none.
    """
    return when + timedelta(seconds=random.uniform(-JITTER_SECONDS, JITTER_SECONDS))


def schedule_initial(
    tax_id: PartyTaxId, *, policy: TaxIdCheckPolicy, now: Optional[datetime] = None
) -> None:
    """Put a new or changed identifier at the start of the initial series.

    Called on create and whenever the value or country changes (D63): a
    different number has no history, and an old check must not look like
    evidence for it.
    """
    now = now or datetime.now(timezone.utc)
    tax_id.initial_checks_done = 0
    tax_id.unavailable_streak = 0
    offsets = policy.initial_check_offsets or (0,)
    from ..billing.parties.tax_ids import is_verifiable

    if not is_verifiable(tax_id.kind):
        # Nothing will ever answer, so nothing is scheduled. The screen says
        # "not checkable" and offers a manual confirmation instead.
        tax_id.next_check_at = None
        return
    tax_id.next_check_at = now + timedelta(days=offsets[0])


def due_identifiers(
    db: Session,
    *,
    limit: int = DEFAULT_BATCH,
    now: Optional[datetime] = None,
    company_id: Optional[uuid.UUID] = None,
) -> list[PartyTaxId]:
    """The identifiers due for a check, locked for this worker.

    `FOR UPDATE SKIP LOCKED` is what makes two workers safe: the second one
    steps over the rows the first has taken instead of waiting for them or —
    worse — checking them again. Without `SKIP LOCKED` a second beat process
    (and there will be one, during a deploy) would double every check,
    doubling the load on a government register that publishes concurrency
    limits.

    Oldest due first, so a backlog drains in order rather than starving the
    identifiers that have waited longest.
    """
    now = now or datetime.now(timezone.utc)
    query = (
        select(PartyTaxId)
        .where(
            PartyTaxId.next_check_at.is_not(None),
            PartyTaxId.next_check_at <= now,
            PartyTaxId.is_active.is_(True),
        )
        .order_by(PartyTaxId.next_check_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if company_id is not None:
        query = query.where(PartyTaxId.company_id == company_id)
    return list(db.execute(query).scalars())


def acknowledge(
    db: Session,
    *,
    tax_id: PartyTaxId,
    party: Party,
    check: PartyVatCheck,
    note: str,
    actor=None,
) -> None:
    """Clear `needs_attention`, recording who looked and what they said.

    Only an explicit acknowledgement clears it — never a later check. A
    change that resolved itself still happened, and somebody should be able
    to see that it did.

    The note goes in the audit event rather than on the append-only check
    row, because that row cannot be updated: it is the evidence, and
    evidence that can be annotated afterwards is not evidence.
    """
    tax_id.needs_attention = False
    tax_id.needs_attention_reason = None
    if actor is not None:
        audit.record(
            db,
            actor,
            party,
            "party.tax_id_change_acknowledged",
            None,
            {
                "tax_id": tax_id.value,
                "kind": tax_id.kind,
                "check_id": str(check.id),
                "note": note,
            },
            fields=["tax_id", "kind", "check_id", "note"],
            reason=note,
        )


def needs_attention(
    db: Session, *, company_id: uuid.UUID
) -> list[PartyTaxId]:
    """Every flagged identifier in this company, oldest first."""
    return (
        db.query(PartyTaxId)
        .filter(
            PartyTaxId.company_id == company_id,
            PartyTaxId.needs_attention.is_(True),
        )
        .order_by(PartyTaxId.updated_at)
        .all()
    )
