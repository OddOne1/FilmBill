from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
from jose import JWTError, jwt
import bcrypt
from sqlalchemy import func
from sqlalchemy.orm import Session
from ..config import settings
from ..models.user import User, UserStatus

def hash_password(password: str) -> str:
    # bcrypt has a 72 byte limit, truncate to avoid errors
    pwd_bytes = password[:72].encode('utf-8')
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(pwd_bytes, salt)
    return hashed_bytes.decode('utf-8')

def verify_password(plain: str, hashed: str) -> bool:
    try:
        plain_bytes = plain[:72].encode('utf-8')
        hashed_bytes = hashed.encode('utf-8')
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except ValueError:
        return False

#: The `tv` claim's value for a token that does not carry one.
#:
#: Load-bearing, and the whole reason token_version can ship without logging
#: anyone out: every session alive at deploy time holds a token minted before
#: this claim existed, and every row the migration touches starts at 0. Read
#: the absent claim as anything else and the deploy becomes a forced
#: sign-out of every user at once.
LEGACY_TOKEN_VERSION = 0


def token_version_of(payload: dict) -> int:
    """The `tv` this token was minted under. See LEGACY_TOKEN_VERSION."""
    return payload.get("tv", LEGACY_TOKEN_VERSION)


def create_access_token(user_id: str, token_version: int) -> str:
    """FreeFrame §199 — `token_version` is REQUIRED, not defaulted.

    Every caller has the User row in hand and can pass `user.token_version`;
    a default here would let a forgotten argument quietly mint a token that
    is stale the moment it is issued, and the symptom of that — a user
    logged straight back out — would be nowhere near the call site that
    caused it. A missing argument failing loudly is the cheaper outcome.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "type": "access", "tv": token_version, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

def create_refresh_token(user_id: str, token_version: int) -> str:
    """Same contract as create_access_token; see its docstring for `tv`."""
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload = {"sub": str(user_id), "type": "refresh", "tv": token_version, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def bump_token_version(user: User) -> None:
    """End every session this user currently holds.

    Called for changes that alter what an already-signed-in session is
    entitled to — 2FA enabled, disabled, admin-reset, backup codes
    regenerated, a password set or changed — none of which used to end a
    session at all.

    Does NOT commit: the bump belongs in the same transaction as whatever
    change justified it, or a crash between the two would end every session
    for a change that never landed.

    `or 0` covers a row that predates the column's default in some test
    fixture or a partially-migrated database; the column itself is NOT NULL.
    """
    user.token_version = (user.token_version or 0) + 1

#: How long a half-finished login stays valid. Long enough to open an
#: authenticator app or wait for an email, short enough that a token lifted
#: off a screen is not a standing invitation.
TWOFA_PENDING_EXPIRY_MINUTES = 10

#: The `type` claim that marks a token as "password accepted, second factor
#: outstanding".
#:
#: This is the whole security model of the 2FA flow and it costs no new
#: code: middleware/auth.py's get_current_user already rejects anything
#: whose type is not exactly "access", so a pending token cannot reach a
#: single authenticated endpoint. Verified by reading that middleware, and
#: asserted in tests/test_two_factor.py rather than assumed.
TWOFA_PENDING_TOKEN_TYPE = "2fa_pending"

#: Which primary credential got the caller this far. Carried in the pending
#: token's `via` claim.
#:
#: This exists because the two primary credentials are not interchangeable
#: once the SECOND factor is chosen. A password proves something the user
#: knows; a magic code proves control of their mailbox. If the second factor
#: is also emailed, then a magic-code login backed by an emailed fallback is
#: one factor twice over: whoever reads the inbox gets both codes.
#:
#: FreeFrame's pending token carried no such claim, so
#: /auth/2fa/send-email-fallback mailed a second code to the very mailbox
#: that had just been used as the first factor. Found by running the flow
#: end to end against Mailpit.
VIA_PASSWORD = "password"
VIA_MAGIC_CODE = "magic_code"


def create_2fa_pending_token(user_id: str, *, via: str = VIA_PASSWORD) -> str:
    """A token that proves ONE primary credential passed and nothing more.

    `via` records which one, so the second-factor step can refuse a factor
    that would re-prove the same thing. Defaults to VIA_PASSWORD so an older
    token — or a caller written before this claim existed — is treated as
    the stricter of the two rather than the looser.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=TWOFA_PENDING_EXPIRY_MINUTES)
    payload = {
        "sub": str(user_id),
        "type": TWOFA_PENDING_TOKEN_TYPE,
        "via": via,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_2fa_pending_token(token: str) -> Optional[str]:
    """The user id inside a valid pending token, or None.

    None covers every failure the caller treats identically — expired,
    tampered, or an access/refresh token being passed where a pending one
    belongs. That last case matters: without the type check, a real access
    token would satisfy the 2FA step and complete a login it was never
    issued for.
    """
    claims = decode_2fa_pending_claims(token)
    return claims.get("sub") if claims else None


def decode_2fa_pending_claims(token: str) -> Optional[dict]:
    """The whole validated payload, for callers that need `via` as well."""
    # Guarded before decode_token, not inside the try: python-jose raises
    # AttributeError (not JWTError) on a None/empty token, which escapes
    # decode_token's except and surfaces as a 500 instead of a 401. Reached
    # by a caller that has neither a session nor a pending token — the
    # authenticated fallback path makes that combination possible.
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != TWOFA_PENDING_TOKEN_TYPE:
        return None
    return payload


def pending_token_via(token: str) -> str:
    """Which primary credential a pending token represents.

    VIA_PASSWORD for a token that does not say. That default is the
    permissive one — it is magic-code origin that restricts what may serve
    as the second factor — and it is correct here for the same reason the
    claim exists: only /auth/verify-magic-code ever mints the restricted
    kind, and it always sets the claim. A token without it is either an old
    one from a password login or not a pending token at all, and
    decode_2fa_pending_claims has already rejected the second case.
    """
    claims = decode_2fa_pending_claims(token) or {}
    return claims.get("via") or VIA_PASSWORD


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Look a user up by email, case-insensitively.

    `users.email` is a plain case-sensitive unique column, so
    `Mathias@yon.studio` and `mathias@yon.studio` were two different
    accounts as far as this lookup was concerned — which is exactly how
    one person ended up with two, holding different project grants (see
    CLAUDE.md FreeFrame and scripts/merge_user_accounts.sql).

    **The comparison is normalised, not the stored value.** Existing rows
    legitimately contain uppercase, so lowercasing the *input* against a
    `==` on the raw column would stop matching them and lock those people
    out on their next login. Nothing here rewrites what is stored; new
    signups keep whatever capitalisation the person typed.

    The `deleted_at` filter is load-bearing now in a way it wasn't before:
    the retired half of a merged pair keeps its lowercase address forever,
    and without this filter the case-insensitive comparison would match
    both rows and could authenticate someone into the dead account.

    This is the single choke point for login, magic-code, the register and
    invite duplicate-checks, and share access — eight call sites across
    auth.py, users.py and share.py — so they all become case-insensitive
    together. `routers/setup.py` has its own inline copy of this query and
    is fixed alongside.
    """
    return db.query(User).filter(
        func.lower(User.email) == email.strip().lower(),
        User.deleted_at.is_(None),
    ).first()

def get_user_by_id(db: Session, user_id: uuid.UUID) -> Optional[User]:
    return db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()

def split_full_name(full_name: str) -> tuple[Optional[str], str]:
    """Split a single "full name" string into (first_name, last_name) by
    splitting on the first space -- everything before the first space
    becomes first_name, everything after becomes last_name (so multi-word
    last names like "Mary Jane Watson" stay together as one last_name).
    Single-word names have no first_name. Used by the legacy /auth/register
    endpoint and the admin invite flow, which still collect one "name"
    field, to populate the split first_name/last_name columns on User.
    """
    stripped = full_name.strip()
    if " " in stripped:
        first, rest = stripped.split(" ", 1)
        return first, rest
    return None, stripped
