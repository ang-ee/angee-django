"""GraphQL access control for Angee IAM.

iam owns "who is a platform admin", so the platform-admin GraphQL gate lives here
— not buried in ``iam.schema`` — and downstream addons (e.g. ``integrate``) import
it without pulling in iam's whole schema module. Also the home of the small
request/auth context helpers shared between the permission and iam's resolvers.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from rebac import PermissionDenied
from rebac.managers import RebacManager
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


class PlatformAdminPermission(BasePermission):
    """Allow only actors that reach IAM's const-backed platform admin role."""

    message = "Platform admin permission required."
    error_extensions = {"code": "PERMISSION_DENIED"}

    def has_permission(
        self,
        source: Any,
        info: strawberry.Info,
        **kwargs: Any,
    ) -> bool:
        """Return whether the request user has platform-admin reach."""

        del source, kwargs
        user = getattr(request_from_info(info), "user", None)
        if not is_authenticated(user):
            return False
        user_model = get_user_model()
        if isinstance(user_model._default_manager, RebacManager):
            return cast(bool, user_model.objects.filter(pk=cast(Any, user).pk).exists())
        return bool(getattr(user, "is_superuser", False))


ADMIN_PERMISSION_CLASSES: list[type[BasePermission]] = [PlatformAdminPermission]
