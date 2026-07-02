"""Identity helpers: user-reference display without exposing the user object.

The OIDC login/link resolution that used to live here moved to the
``iam_integrate_oidc`` addon (it composes the ``integrate`` connection substrate
with this user). What remains is the pure user-reference display other addons use
to label a grant/principal without pulling a guarded user row into their scope.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpRequest
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


def _label_memo(request: HttpRequest | None) -> dict[Any, str | None] | None:
    """Return the per-request user-label memo, creating it on first use.

    Hung on the Django request — the per-request seam IAM already uses for
    request-scoped state — so a list of rows sharing an author resolves the
    label (and queries the user) once instead of per row. Absent outside a
    request, where resolution falls back to the uncached path.
    """

    if request is None:
        return None
    memo: dict[Any, str | None] | None = getattr(request, "_iam_user_label_memo", None)
    if memo is None:
        memo = {}
        setattr(request, "_iam_user_label_memo", memo)
    return memo


def user_display_labels(
    user_ids: Iterable[Any],
    *,
    request: HttpRequest | None = None,
) -> dict[Any, str | None]:
    """Return user display labels for ``user_ids`` with one elevated read.

    Resolved under ``system_context`` (IAM's elevation for server-side
    reads) so an actor-scoped caller never pulls a guarded User row into
    its own queryset — REBAC rejects that; only a display string leaves
    the helper.

    Pass the Django ``request`` to prime and reuse the request-scoped label memo:
    list resolvers can ask for all distinct ids up front and pay one query.
    """

    ids = [user_id for user_id in dict.fromkeys(user_ids) if user_id is not None]
    if not ids:
        return {}
    memo = _label_memo(request)
    labels: dict[Any, str | None] = {}
    missing: list[Any] = []
    for user_id in ids:
        if memo is not None and user_id in memo:
            labels[user_id] = memo[user_id]
        else:
            missing.append(user_id)
    if not missing:
        return labels

    user_model = get_user_model()
    pk = user_model._meta.pk
    public_lookup = getattr(user_model, "public_id_lookup", None)
    lookup = Q()
    if pk is not None:
        pk_values = []
        for user_id in missing:
            try:
                pk_values.append(pk.to_python(user_id))
            except (TypeError, ValueError, ValidationError):
                continue
        if pk_values:
            lookup |= Q(**{f"{pk.name}__in": pk_values})
    if callable(public_lookup):
        for user_id in missing:
            try:
                lookup |= Q(**public_lookup(str(user_id)))
            except (TypeError, ValueError):
                continue

    fetched: dict[str, str] = {}
    with system_context(reason="iam.identity.user_label"):
        users = user_model.objects.filter(lookup).only("first_name", "last_name", "username") if lookup else []
        for user in users:
            label = user_label(user)
            fetched[str(user.pk)] = label
            public_id = getattr(user, "sqid", None)
            if public_id:
                fetched[str(public_id)] = label

    for user_id in missing:
        fetched_label = fetched.get(str(user_id))
        labels[user_id] = fetched_label
        if memo is not None:
            memo[user_id] = fetched_label
    return labels


def user_display_label(user_id: Any, *, request: HttpRequest | None = None) -> str | None:
    """Return one user's display label without exposing the user object."""

    if user_id is None:
        return None
    memo = _label_memo(request)
    if memo is not None and user_id in memo:
        return memo[user_id]
    label = user_display_labels((user_id,), request=request).get(user_id)
    if label is not None:
        return label
    try:
        user = user_principal(str(user_id))
    except ValueError:
        return None
    label = user_label(user)
    if memo is not None:
        memo[user_id] = label
    return label


def user_principal(principal_id: str) -> Any:
    """Return the user addressed by a role-grant principal id."""

    user_model = get_user_model()
    resolved_id = _user_principal_node_id(principal_id)
    lookups: list[dict[str, Any]] = []
    subject_id_attr = str(getattr(user_model._meta, "rebac_id_attr", None) or app_settings.REBAC_USER_ID_ATTR)
    lookups.append({subject_id_attr: resolved_id})
    public_lookup = getattr(user_model, "public_id_lookup", None)
    if callable(public_lookup):
        lookups.append(public_lookup(resolved_id))
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


def _user_principal_node_id(principal_id: str) -> str:
    """Return the public id from a user principal id."""

    return principal_id
