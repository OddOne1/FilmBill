"""Authorisation helpers.

FreeFrame's version answered project-membership questions (owner / manager /
member / viewer on a Project, plus share-link sessions). None of those objects
exist in FilmBill, so what is left here is the part that is genuinely about
the platform: the global role ladder.

**This file is rewritten in P0b.** That is where the real model lands:
per-company roles and the `company_id`-scoped query helpers CLAUDE.md rule 6
requires, including the rule that cross-company access returns 404 rather
than 403. Nothing here should grow a business concept in the meantime.
"""

from ..models.user import User, UserGlobalRole


def is_superadmin(user: User | None) -> bool:
    """The instance administrator: user management, site and mail settings."""
    return user is not None and user.role == UserGlobalRole.superadmin


def is_superuser_or_above(user: User | None) -> bool:
    """Looser than is_superadmin — anyone except a plain `user`.

    The only gate between the two today is who may invite people
    (routers/users.py::invite_user).
    """
    return user is not None and user.role in (
        UserGlobalRole.superadmin,
        UserGlobalRole.superuser,
    )
