"""Creating an invited account and mailing the link — the one implementation.

Extracted from `routers/users.py::invite_user` unchanged in behaviour, because
P0b-1 adds a second caller: adding a member to a company by an address that
has no account yet has to invite that person, and writing a second invite here
would give FilmBill two token formats, two expiry rules and two emails to keep
in step. There is one, and both callers use it.

What this deliberately does NOT do is decide anything about companies. It
creates a platform account and sends the link. The caller grants whatever
membership it wanted to grant, in its own transaction — see
`routers/companies.py::add_member`, which does both and commits once.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models.user import User, UserStatus
from ..tasks.celery_app import send_task_safe
from ..tasks.email_tasks import send_invite_email
from .auth_service import split_full_name
from .site_settings_service import instance_org_name

#: How long an invite link stays good. Seven days, as it has been.
INVITE_EXPIRY_DAYS = 7


def create_invited_user(
    db: Session,
    *,
    email: str,
    name: Optional[str] = None,
) -> User:
    """Add a `pending_invite` account with a fresh token. Does NOT commit.

    Not committing is the point of this signature: the company-members caller
    needs the user row and the membership row to land together, so that a
    failure cannot leave an invited account with no reason to have been
    invited. `db.flush()` is called so the caller has `user.id` to build a
    membership from.
    """
    first_name, last_name = split_full_name(name or email.split("@")[0])
    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        status=UserStatus.pending_invite,
        invite_token=secrets.token_urlsafe(48),
        invite_token_expires_at=datetime.now(timezone.utc)
        + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    db.add(user)
    db.flush()
    return user


def send_invite(db: Session, user: User, inviter: Optional[User]) -> None:
    """Queue the invite mail for `user`. Call AFTER the commit.

    After, not before, and that ordering is the whole reason this is a
    separate function from the one above: the email worker is a different
    process reading a different session. A task queued inside the
    transaction can be picked up before the commit lands, and the invitee
    then follows a link to a token the database does not have yet.
    """
    invite_url = f"{settings.frontend_url}/invite/{user.invite_token}"
    send_task_safe(
        send_invite_email,
        user.email,
        (inviter.name if inviter else None) or "Admin",
        instance_org_name(db),
        invite_url,
        INVITE_EXPIRY_DAYS,
    )
