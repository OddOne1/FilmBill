"""Admin endpoints for user and site management."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import uuid
from datetime import datetime, timezone

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.user import User, UserStatus, UserGlobalRole
from ..models.activity import ActivityLog
from ..schemas.auth import (
    UserResponse, UpdateUserRoleRequest, AdminUserResponse,
)
from ..services.auth_service import bump_token_version

router = APIRouter(prefix="/admin", tags=["admin"])

def _require_superadmin(current_user: User) -> None:
    if current_user.role != UserGlobalRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can access this endpoint"
        )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[AdminUserResponse])
def list_all_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every user in the system. Superadmin only.

    FreeFrame enriched each row with a per-project role summary; projects
    are gone. P0b puts per-company role memberships in their place
    (CLAUDE.md rule 6), which is why AdminUserResponse stays its own schema.
    """
    _require_superadmin(current_user)

    users = db.query(User).filter(User.deleted_at.is_(None)).all()
    return [AdminUserResponse.model_validate(u) for u in users]

@router.patch("/users/{user_id}/deactivate", response_model=UserResponse)
def deactivate_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deactivate a user. Admins cannot deactivate themselves."""
    if current_user.role != UserGlobalRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can deactivate users"
        )

    # Prevent admin from deactivating themselves
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate yourself"
        )

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.status = UserStatus.deactivated
    db.commit()
    db.refresh(user)
    return user

@router.patch("/users/{user_id}/reactivate", response_model=UserResponse)
def reactivate_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reactivate a deactivated user. Only accessible by admins."""
    if current_user.role != UserGlobalRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can reactivate users"
        )

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.status = UserStatus.active
    db.commit()
    db.refresh(user)
    return user

@router.patch("/users/{user_id}/disable-2fa", response_model=UserResponse)
def admin_disable_two_factor(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Strip a user's 2FA when they have lost every factor.

    No code from that user, because the situation this exists for is
    precisely the one where they cannot produce one: authenticator gone,
    email inaccessible, backup codes lost. Self-service is impossible by
    definition there, so requiring proof would make this endpoint useless
    for its only purpose.

    NOT usable on yourself, matching `deactivate_user` right above. That is
    a real gate, not symmetry for its own sake: without it a superadmin's
    stolen session could strip that superadmin's OWN 2FA with no code, and
    the re-auth on /auth/2fa/disable would be a formality the highest
    privileged accounts could always walk around. A superadmin disabling
    their own 2FA goes through self-service and proves possession like
    everyone else; if they have genuinely lost every factor, another
    superadmin does it for them.

    Logged to ActivityLog, because an unlogged way to remove someone else's
    second factor is indistinguishable after the fact from an attacker
    having done it.
    """
    _require_superadmin(current_user)

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use your own security settings to disable your 2FA",
        )

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    had_2fa = bool(user.two_factor_enabled)
    # The same three fields the self-service path clears — a secret or a
    # stale code set left behind would outlive the enrolment it belonged to.
    user.two_factor_enabled = False
    user.totp_secret_encrypted = None
    user.backup_codes_hashed = None
    user.two_factor_method = None  # FreeFrame §194 — cleared with the rest.
    # the TARGET's sessions, not the acting admin's. An admin reset
    # happens because the user has lost every factor, which is exactly the
    # situation where somebody else may be holding a live session on that
    # account. Leaving those running would make the reset a formality. The
    # admin's own token_version is untouched, so their session is unaffected.
    bump_token_version(user)

    db.add(
        ActivityLog(
            user_id=current_user.id,  # the ACTOR, per the column's own comment
            action="admin_disabled_2fa",
            payload={
                "target_user_id": str(user.id),
                "target_email": user.email,
                # Distinguishes "an admin removed a live second factor" from
                # "an admin clicked it on an account that had none".
                "was_enabled": had_2fa,
            },
        )
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/clear-account-gate", response_model=UserResponse)
def clear_account_gate(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Let one user past the FreeFrame §200 onboarding gate.

    The gate is computed from stored data and cannot be dismissed from the
    browser, which is the property that makes it worth having — and is also
    exactly how somebody ends up locked out of the entire app because the
    backup address they typed has a typo in it and the confirmation code can
    never arrive. This is the way back in.

    A waiver of the BLOCK, not of the requirement: `account_setup_required`
    still reads True for this user, the settings screen still asks them to
    finish, and verifying a backup address clears the waiver again. So this
    cannot quietly become a permanent exemption that nobody remembers
    granting.

    **Usable on yourself**, unlike `admin_disable_two_factor` above — and the
    difference is deliberate. That endpoint refuses self-use because a stolen
    superadmin session could otherwise strip its own second factor with no
    code. This one removes no protection: a gated superadmin can already
    reach /auth/* and finish setup normally, so the worst a stolen session
    achieves here is skipping a screen it could have completed anyway. The
    last superadmin on an instance with nobody else to ask is the whole
    reason it has to work on yourself.

    Logged to ActivityLog. An unlogged way to bypass an account requirement
    is indistinguishable after the fact from an attacker having used it.
    """
    _require_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    was_outstanding = bool(user.account_setup_required)
    user.account_gate_waived_at = datetime.now(timezone.utc)

    db.add(
        ActivityLog(
            user_id=current_user.id,  # the ACTOR, per the column's own comment
            action="admin_cleared_account_gate",
            payload={
                "target_user_id": str(user.id),
                "target_email": user.email,
                # Distinguishes a real rescue from a click on an account that
                # was never gated — and records WHAT was outstanding, which is
                # the part worth having months later.
                "was_outstanding": was_outstanding,
                "must_set_password": bool(user.must_set_password),
                "backup_email_state": user.backup_email_state,
            },
        )
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def update_user_role(
    user_id: uuid.UUID,
    body: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Promote or demote a user to/from admin role. Only accessible by admins."""
    if current_user.role != UserGlobalRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can change user roles"
        )

    # Prevent admin from removing their own admin role
    if user_id == current_user.id and not body.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own admin role"
        )

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Binary toggle -- "Remove Admin" lands on superuser, not user, so it
    # never silently strips someone's existing ability to do their work.
    # Demoting all the way to 'user' has no UI yet; not this endpoint's job.
    # The real per-company role model arrives in P0b (CLAUDE.md rule 6) and
    # will replace this global toggle.
    user.role = UserGlobalRole.superadmin if body.is_admin else UserGlobalRole.superuser
    db.commit()
    db.refresh(user)
    return user
