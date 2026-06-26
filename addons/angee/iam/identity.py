"""Identity helpers: user-reference display without exposing the user object.

The OIDC login/link resolution that used to live here moved to the
``iam_integrate_oidc`` addon (it composes the ``integrate`` connection substrate
with this user). What remains is the pure user-reference display other addons use
to label a grant/principal without pulling a guarded user row into their scope.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from rebac import app_settings, system_context

from angee.base.models import instance_from_public_id, public_id_for


def user_label(user: Any) -> str:
    """Return any user model's human label from the Django auth contract."""

    return str(user.get_full_name() or user.username)


def user_public_id(user_id: Any) -> str | None:
    """Return a user's opaque public id without fetching the user row."""

    if user_id is None:
        return None
    return public_id_for(get_user_model(), user_id)


def user_display_label(user_id: Any) -> str | None:
    """Return a user's display label (name) without exposing the user object.

    Resolved under ``system_context`` (IAM's elevation for server-side
    reads) so an actor-scoped caller never pulls a guarded User row into
    its own queryset — REBAC rejects that; only a display string leaves
    the helper. Intended for the single-record form — not selected as a
    list column.
    """

    if user_id is None:
        return None
    user_model = get_user_model()
    with system_context(reason="iam.identity.user_label"):
        try:
            user = user_model.objects.filter(pk=user_id).only("first_name", "last_name", "username").first()
        except TypeError, ValueError:
            user = None
    if user is None:
        try:
            user = user_principal(str(user_id))
        except ValueError:
            return None
    return user_label(user)


def user_principal(principal_id: str, *, graphql_type_name: str = "UserType") -> Any:
    """Return the user addressed by a role-grant principal id."""

    user_model = get_user_model()
    resolved_id = _user_principal_node_id(principal_id, graphql_type_name=graphql_type_name)
    lookups: list[dict[str, Any]] = []
    subject_id_attr = str(getattr(user_model._meta, "rebac_id_attr", None) or app_settings.REBAC_USER_ID_ATTR)
    lookups.append({subject_id_attr: resolved_id})
    public_lookup = getattr(user_model, "public_id_lookup", None)
    if callable(public_lookup):
        lookups.append(public_lookup(resolved_id))
    else:
        pk = user_model._meta.pk
        if pk is not None:
            lookups.append({pk.name: resolved_id})

    tried: set[tuple[tuple[str, Any], ...]] = set()
    with system_context(reason="iam.identity.principal"):
        for lookup in lookups:
            key = tuple(sorted(lookup.items()))
            if key in tried:
                continue
            tried.add(key)
            try:
                user = user_model._default_manager.filter(**lookup).first()
            except TypeError, ValueError:
                continue
            if user is not None:
                return user
    raise ValueError(f"User principal {principal_id!r} was not found.")


def user_from_public_id(user_id: Any) -> Any:
    """Return the user addressed by one GraphQL public id, or raise."""

    user_model = get_user_model()
    with system_context(reason="iam.identity.user.lookup"):
        user = instance_from_public_id(user_model, str(user_id), queryset=user_model._default_manager.all())
    if user is None:
        raise ValueError(f"User {user_id!s} was not found.")
    return user


def _user_principal_node_id(principal_id: str, *, graphql_type_name: str) -> str:
    """Return the public id from a user principal id."""

    del graphql_type_name
    return principal_id
