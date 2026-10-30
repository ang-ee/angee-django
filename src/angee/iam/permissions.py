"""GraphQL access control for Angee IAM.

iam owns "who is a platform admin", so the platform-admin GraphQL gate lives here
— not buried in ``iam.schema`` — and downstream addons (e.g. ``integrate``) import
it without pulling in iam's whole schema module. Session authentication checks
are shared by permission classes and self-service resolvers; Strawberry owns
request context.
"""

from __future__ import annotations

from typing import Any, ClassVar

import strawberry
from django.contrib.auth.models import AnonymousUser
from rebac import ObjectRef, PermissionDenied, current_actor
from strawberry.permission import BasePermission
from strawberry_django.auth.utils import get_current_user

from angee.iam.roles import platform_admin_role, subject_has_role


def is_authenticated(user: Any) -> bool:
    """Return whether ``user`` is a real authenticated session user."""

    return not isinstance(user, AnonymousUser) and bool(getattr(user, "is_authenticated", False))


def session_user(info: strawberry.Info) -> Any:
    """Return the authenticated session user or raise a REBAC denial.

    The shared "this resolver requires a signed-in user" gate; iam's resolvers
    and downstream self-service mutations (e.g. ``integrate``) use it so the
    anonymous-deny check lives in exactly one place.
    """

    user = get_current_user(info)
    if not is_authenticated(user):
        raise PermissionDenied("Authentication required.")
    return user


def is_platform_admin(user: Any) -> bool:
    """Return whether ``user`` reaches IAM's platform-admin role."""

    if not is_authenticated(user):
        return False
    role = platform_admin_role()
    return role is not None and current_actor_has_role(role)


def current_actor_has_role(role: ObjectRef) -> bool:
    """Return whether the ambient REBAC actor is an effective member of ``role``."""

    return subject_has_role(current_actor(), role)


def require_platform_admin(info: strawberry.Info) -> Any:
    """Return the session user or raise when it lacks platform-admin reach."""

    user = get_current_user(info)
    if not is_platform_admin(user):
        raise PermissionDenied("Platform admin permission required.")
    return user


class RolePermission(BasePermission):
    """Allow actors that reach ``role_ref`` through ``effective_member``."""

    role_ref: ClassVar[ObjectRef | None] = None

    message = "Role permission required."
    error_extensions = {"code": "PERMISSION_DENIED"}

    def has_permission(
        self,
        source: Any,
        info: strawberry.Info,
        **kwargs: Any,
    ) -> bool:
        """Return whether the current actor reaches the configured role."""

        del source, kwargs
        return self.role_ref is not None and current_actor_has_role(self.role_ref)


class PlatformAdminPermission(RolePermission):
    """Allow only actors that reach IAM's const-backed platform admin role."""

    message = "Platform admin permission required."

    def has_permission(
        self,
        source: Any,
        info: strawberry.Info,
        **kwargs: Any,
    ) -> bool:
        """Return whether the request user has platform-admin reach."""

        del source, kwargs
        return is_platform_admin(get_current_user(info))


ADMIN_PERMISSION_CLASSES: list[type[BasePermission]] = [PlatformAdminPermission]
