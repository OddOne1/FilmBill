"""Recording what changed — in the caller's transaction, with secrets removed.

One helper, `record`, and the two properties that make it worth having:

**It writes in the caller's transaction and never commits.** A change that is
rolled back leaves no audit row; a change that is committed always has one.
Those are the same sentence, and they are only true if the audit write shares
the transaction with the change it describes. An audit log written by a
separate connection — or committed eagerly "so it cannot be lost" — records
things that did not happen, which is worse than recording nothing.

**Nothing secret reaches the table.** `audit_events` cannot be updated or
deleted (see `models/audit.py`), so a secret written into it is a secret that
cannot be taken out again. Passwords, TOTP secrets, backup codes, tokens and
magic codes are replaced with a marker; the FACT that the field changed is
still recorded, because that is the audit-worthy part.

**IBANs are masked, and that is a permission decision, not a privacy
gesture.** Reading a bank account needs `company.settings.edit` (owner/admin),
deliberately — an IBAN is the detail a convincing invoice fraud needs. If the
audit log stored IBANs in full, `audit.view` would be a way round that gate,
and `audit.view` is held by the tax advisor. So the log records
`AT…3201`: enough to tell which account an event is about, not enough to be
paid as somebody else.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from ..models.audit import AuditEvent

# ─── Redaction ──────────────────────────────────────────────────────────────

#: What a redacted value is replaced with. A constant, so a test can assert
#: the marker rather than the absence of a secret — "the password is not in
#: this row" passes for a row that is empty for any reason at all, which is
#: the vacuous-assertion trap in CLAUDE.md's harness rule.
REDACTED = "[redacted]"

#: Field names whose VALUE never appears in an audit row, in any form.
#:
#: Matched on the field name, case-insensitively, as a whole name — not as a
#: substring, which would redact `password_changed_at` (a timestamp worth
#: having) while missing nothing. The suffix list below covers the shapes
#: that do want substring matching.
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "new_password",
        "current_password",
        "totp_secret",
        "totp_secret_encrypted",
        "backup_codes",
        "backup_codes_hashed",
        "invite_token",
        "refresh_token",
        "access_token",
        "magic_code",
        "code",
        "jwt_secret",
        "smtp_password",
        "secret_key",
        "s3_secret_key",
        "api_key",
    }
)

#: Any field whose name ENDS in one of these is redacted too.
#:
#: Belt and braces for the fields nobody has thought of yet: a column added
#: in P4 called `signing_secret` or `webhook_token` is redacted the day it
#: arrives rather than the day somebody notices. Over-redacting a field is a
#: readable audit row with one value missing; under-redacting one is a secret
#: in a table that cannot be deleted from.
SECRET_SUFFIXES: tuple[str, ...] = (
    "_password",
    "_secret",
    "_secret_encrypted",
    "_token",
    "_codes",
    "_codes_hashed",
    "_api_key",
)

#: Fields stored MASKED rather than redacted: the value is reduced to the
#: part that identifies it without being usable.
MASKED_FIELDS: frozenset[str] = frozenset({"iban"})


def mask_iban(value: Any) -> Any:
    """`AT611904300234573201` → `AT…3201`. Country code plus the last four.

    Enough to tell two of a company's accounts apart in a log, which is what
    the log is for, and not enough to be paid instead of them. Short or
    non-string values are returned redacted rather than partially exposed —
    a value this function does not recognise is a value it should not be
    guessing about.
    """
    if not isinstance(value, str):
        return REDACTED if value is not None else None
    compact = value.replace(" ", "").upper()
    if len(compact) < 8:
        return REDACTED
    return f"{compact[:2]}…{compact[-4:]}"


def is_secret_field(name: str) -> bool:
    lowered = (name or "").lower()
    return lowered in SECRET_FIELDS or lowered.endswith(SECRET_SUFFIXES)


def redact(name: str, value: Any) -> Any:
    """The value as it may be stored, for the field called `name`."""
    lowered = (name or "").lower()
    if is_secret_field(lowered):
        return REDACTED
    if lowered in MASKED_FIELDS:
        return mask_iban(value)
    return _jsonable(value)


def _jsonable(value: Any) -> Any:
    """A value JSONB can hold, without losing what it was.

    Dates and timestamps become ISO strings, `Decimal` becomes a STRING and
    not a float (CLAUDE.md rule 1 — a money amount that goes through a float
    on its way into the audit log is an audit log that disagrees with the
    document), enums become their value, UUIDs their text. Anything else
    unrecognised becomes `repr`, because an audit row that fails to serialise
    is a change that cannot be recorded, and refusing the change over the
    log's formatting would be the wrong way round.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # Floats should not be reaching here at all for money; recorded as a
        # string so the row at least cannot pretend to a precision it lost.
        return repr(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, dict):
        return {str(key): redact(str(key), inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(inner) for inner in value]
    return repr(value)


# ─── Who did it ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Actor:
    """Who, where from, and in which company.

    A small value rather than the `Membership` or the `Request`, so that
    `record` can be called from a service that has neither — a Celery task, a
    migration, a CLI — and so a test can state the actor in one line.
    """

    user_id: Optional[uuid.UUID] = None
    company_id: Optional[uuid.UUID] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None

    @classmethod
    def system(cls, company_id: Optional[uuid.UUID] = None) -> "Actor":
        """Nobody did it — a sweep, an expiry, a scheduled job."""
        return cls(company_id=company_id)


def actor_from(request=None, *, membership=None, user=None) -> Actor:
    """Build an `Actor` from whatever a handler happens to have.

    `membership` gives the company and the user; `user` alone covers the
    install-level endpoints, which have no company. The request supplies the
    address and the client string, truncated to the columns' widths rather
    than left to fail the INSERT — a 900-character user agent must not be the
    reason a company edit is refused.
    """
    company_id = getattr(membership, "company_id", None)
    acting_user = user or getattr(membership, "user", None)
    user_id = getattr(acting_user, "id", None)

    ip = None
    user_agent = None
    if request is not None:
        client = getattr(request, "client", None)
        ip = getattr(client, "host", None)
        headers = getattr(request, "headers", None)
        if headers is not None:
            user_agent = headers.get("user-agent")

    return Actor(
        user_id=user_id,
        company_id=company_id,
        ip=(ip or None) and str(ip)[:64],
        user_agent=(user_agent or None) and str(user_agent)[:512],
    )


# ─── The diff ───────────────────────────────────────────────────────────────


def diff(
    before: Optional[dict], after: Optional[dict], *, fields: Optional[Iterable[str]] = None
) -> dict[str, list]:
    """`{field: [old, new]}` for the fields that changed, already redacted.

    Only the fields that changed, because an audit row listing forty
    unchanged columns is a row nobody reads. A field present in one dict and
    absent from the other counts as a change to or from `None`, so a create
    (`before=None`) records what it was created with and a delete records
    what was lost.

    Comparison happens on the RAW values and redaction afterwards. The other
    way round, two different secrets would both redact to the marker and
    compare equal — and a password change would record nothing at all.
    """
    before = before or {}
    after = after or {}
    names = list(fields) if fields is not None else sorted(set(before) | set(after))

    changed: dict[str, list] = {}
    for name in names:
        old = before.get(name)
        new = after.get(name)
        if old == new:
            continue
        changed[name] = [redact(name, old), redact(name, new)]
    return changed


def snapshot(entity, fields: Iterable[str]) -> dict:
    """`{field: value}` read off an ORM object. For the `before`/`after` pair.

    Reading named fields rather than the whole `__dict__`, so that adding a
    column does not silently start logging it — including, one day, a column
    holding something that should not be logged at all.
    """
    return {name: getattr(entity, name, None) for name in fields}


# ─── Recording ──────────────────────────────────────────────────────────────


def _entity_type_of(entity) -> str:
    table = getattr(entity, "__tablename__", None)
    if table:
        return str(table)[:64]
    return type(entity).__name__.lower()[:64]


def record(
    db: Session,
    actor: Actor,
    entity,
    action: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    reason: Optional[str] = None,
    *,
    fields: Optional[Iterable[str]] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    company_id: Optional[uuid.UUID] = None,
) -> AuditEvent:
    """Write one audit row. In the caller's transaction. No commit.

    `entity` may be an ORM object — its table name and id are read off it —
    or None, for an event about something with no row of its own, in which
    case `entity_type` is given explicitly.

    `company_id` falls back to the entity's own, then to the actor's. An
    install-level event (site settings, a user account) has none and shows up
    on nobody's company audit screen, which is deliberate: a company owner has
    no business reading what the installation administrator does.
    """
    resolved_company = company_id
    if resolved_company is None and entity is not None:
        resolved_company = getattr(entity, "company_id", None)
        # A `Company` itself is the company, not a row inside one.
        if resolved_company is None and _entity_type_of(entity) == "companies":
            resolved_company = getattr(entity, "id", None)
    if resolved_company is None:
        resolved_company = actor.company_id

    event = AuditEvent(
        company_id=resolved_company,
        user_id=actor.user_id,
        entity_type=entity_type or _entity_type_of(entity),
        entity_id=entity_id if entity_id is not None else getattr(entity, "id", None),
        action=action,
        changes=diff(before, after, fields=fields),
        reason=reason,
        ip=actor.ip,
        user_agent=actor.user_agent,
    )
    db.add(event)
    # Flushed, not committed: the row has to exist for the rest of this
    # transaction to see, and has to vanish with it if the caller rolls back.
    db.flush()
    return event


# ─── Reading ────────────────────────────────────────────────────────────────


def company_events(db: Session, membership):
    """The base query for one company's audit log. The ONLY way to read it.

    `audit_events.company_id` is nullable — an install-level event belongs to
    no company — so this table cannot carry the `CompanyScoped` mixin and
    `services/permissions.scoped()` refuses it by design. That makes this
    function the single place the company filter lives, so that the filter
    cannot be the thing a new endpoint forgets (CLAUDE.md rule 6, honoured by
    a different mechanism and said out loud rather than quietly skipped).

    The `IS NOT NULL` is implied by the equality, and stated anyway: it is
    what keeps install-level rows off a company's screen even if
    `membership.company_id` were ever None.
    """
    return db.query(AuditEvent).filter(
        AuditEvent.company_id.is_not(None),
        AuditEvent.company_id == membership.company_id,
    )
