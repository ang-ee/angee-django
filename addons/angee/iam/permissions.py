"""GraphQL access control for Angee IAM.

iam owns "who is a platform admin", so the platform-admin GraphQL gate lives here
— not buried in ``iam.schema`` — and downstream addons (e.g. ``integrate``) import
it without pulling in iam's whole schema module. Also the home of the small
request/auth context helpers shared between the permission and iam's resolvers.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

import strawberry
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from rebac import ObjectRef, PermissionDenied, app_settings, current_actor
from rebac import backend as rebac_backend
from strawberry.permission import BasePermission


def request_from_info(info: strawberry.Info) -> HttpRequest:
    """Return the Django request from Strawberry's context."""

    return cast(HttpRequest, info.context.request)


def is_authenticated(user: Any) -> bool:
    """Return whether ``user`` is a real authenticated session user."""

    return not isinstance(user, AnonymousUser) and bool(getattr(user, "is_authenticated", False))


def session_user(info: strawberry.Info) -> Any:
    """Return the authenticated session user or raise a REBAC denial.

    The shared "this resolver requires a signed-in user" gate; iam's resolvers
    and downstream self-service mutations (e.g. ``integrate``) use it so the
    anonymous-deny check lives in exactly one place.
    """

    user = getattr(request_from_info(info), "user", None)
    if not is_authenticated(user):
        raise PermissionDenied("Authentication required.")
    return user


def is_platform_admin(user: Any) -> bool:
    """Return whether ``user`` reaches IAM's platform-admin role."""

    if not is_authenticated(user):
        return False
    role = _platform_admin_role()
    if role is None:
        return bool(getattr(user, "is_superuser", False))
    return current_actor_has_role(role)


def current_actor_has_role(role: ObjectRef) -> bool:
    """Return whether the ambient REBAC actor is an effective member of ``role``."""

    actor = current_actor()
    if actor is None:
        return False
    result = rebac_backend().check_access(
        subject=actor,
        action="effective_member",
        resource=role,
    )
    return bool(result.allowed)


def _platform_admin_role() -> ObjectRef | None:
    """Return the configured platform-admin role object, if any."""

    role = app_settings.REBAC_UNIVERSAL_ADMIN_ROLE
    return ObjectRef.parse(role) if role else None


def require_platform_admin(info: strawberry.Info) -> Any:
    """Return the session user or raise when it lacks platform-admin reach."""

    user = getattr(request_from_info(info), "user", None)
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
        return is_platform_admin(getattr(request_from_info(info), "user", None))


ADMIN_PERMISSION_CLASSES: list[type[BasePermission]] = [PlatformAdminPermission]
