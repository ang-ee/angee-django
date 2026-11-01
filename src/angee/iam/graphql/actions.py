"""Shared GraphQL result type, guard, and target preflights for console domain actions.

In-band refusals preserve the error's declared ``DomainError`` / ``ValidationError``
code in nullable ``ActionResult.code``; errors without a code project ``None``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar, cast

import strawberry
from django.core.exceptions import NON_FIELD_ERRORS, ImproperlyConfigured
from django.db import models
from rebac import PermissionDenied, RebacMixin, system_context
from strawberry_django.auth.utils import get_current_user

from angee.graphql.ids import PublicID, instance_for_id, public_id_value
from angee.iam.graphql.writes import instance_for_write
from angee.iam.permissions import require_rebac_queryset
from graphql import GraphQLError

_ActionTarget = TypeVar("_ActionTarget", bound=models.Model)
_P = ParamSpec("_P")

from angee.graphql.actions import ActionTargetUnavailable


def session_user(info: strawberry.Info) -> Any:
    """Return a signed-in session user, or deny contexts without authentication.

    Strawberry owns HTTP and WebSocket request resolution. Its missing-user
    exceptions represent an unauthenticated context at this action boundary.
    IAM and self-service resolvers compose this same gate.
    """

    try:
        user = get_current_user(info)
    except (AttributeError, KeyError, ValueError) as error:
        raise PermissionDenied("Authentication required.") from error
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication required.")
    return user


def authorized_action_target(
    info: strawberry.Info,
    model: type[_ActionTarget],
    id: PublicID,
    permission: str,
) -> _ActionTarget:
    """Return the actor-authorized row a domain action targets.

    The one owner of the action preflight ceremony (session gate → actor-scoped
    lookup → per-row permission check) that every in-band ``ActionResult`` verb
    repeats:

    1. An unauthenticated session raises ``rebac.PermissionDenied`` — a GraphQL
       error through the shared ``session_user`` gate, so the client
       re-authenticates instead of toasting.
    2. The row resolves through the actor's write-scoped queryset: a row the actor cannot
       reach reads as plain not-found, never an existence oracle.
    3. The resolved row must grant the per-row REBAC ``permission`` (e.g.
       ``"write"``, ``"write__status"``).

    Not-found and denied raise the non-field ``ActionTargetUnavailable`` shape
    :func:`action_guard` maps to an in-band :class:`ActionResult`: the verb's
    guard ``summary`` banners the toast while the specific reason rides
    ``validation_errors[NON_FIELD_ERRORS]``. The returned row stays bound to the
    actor, so the model write that follows runs under REBAC — this is the
    *actor-scoped* sibling of :func:`resolve_action_target`, which is *elevated*
    and leaves authorization to the caller.
    """

    session_user(info)
    instance = instance_for_write(model, id)
    return _require_action_permission(instance, model, id, permission)


def authorized_permission_target(
    info: strawberry.Info,
    model: type[_ActionTarget],
    id: PublicID,
    permission: str,
    *,
    narrow: Callable[[models.QuerySet[_ActionTarget]], models.QuerySet[_ActionTarget]] | None = None,
) -> _ActionTarget:
    """Resolve a target through the exact requested permission scope.

    ``narrow`` lets the verb's owner keep rows another verb owns out of the
    lookup; a narrowed-away row reads as not found.
    """

    user = session_user(info)
    scoped: models.QuerySet[_ActionTarget] = require_rebac_queryset(model).with_actor(user).with_action(permission)
    if narrow is not None:
        scoped = narrow(scoped)
    instance = instance_for_id(model, id, queryset=scoped)
    return _require_action_permission(instance, model, id, permission)


def _require_action_permission(
    instance: _ActionTarget | None,
    model: type[_ActionTarget],
    id: PublicID,
    permission: str,
) -> _ActionTarget:
    """Preserve the shared not-found and row-permission result contract."""

    if instance is None:
        raise ActionTargetUnavailable(
            {
                NON_FIELD_ERRORS: [
                    f"{model._meta.object_name} {public_id_value(id)!r} was not found.",
                ]
            }
        )
    if not isinstance(instance, RebacMixin):
        raise ImproperlyConfigured(f"{model._meta.label} requires RebacMixin for an authorized action.")
    if not instance.has_access(permission):
        raise ActionTargetUnavailable(
            {
                NON_FIELD_ERRORS: [
                    f"You are not allowed to modify this {model._meta.verbose_name}.",
                ]
            }
        )
    return instance


def resolve_action_target(
    model: type[_ActionTarget],
    id: PublicID,
    *,
    reason: str,
    queryset: models.QuerySet[_ActionTarget] | None = None,
    select_related: tuple[str, ...] = (),
) -> _ActionTarget:
    """Return an elevated action target addressed by one GraphQL public id.

    The caller owns actor authorization, usually with field ``permission_classes``
    (an operator/admin verb where the actor's role, not the row, authorizes).
    This helper owns the repeated action-write lookup shape: build the requested
    queryset, enter ``system_context`` for the row read, and raise a stable
    not-found error instead of leaking ``None`` into the action body. A missing
    row raises a ``BAD_USER_INPUT`` GraphQL error, matching the role-gated surface.

    For a domain verb authorized by the row itself (the ceremony
    session gate → actor-scoped lookup → per-row check, returning in-band
    failures), use :func:`authorized_action_target` instead.
    """

    active_queryset = queryset if queryset is not None else model._default_manager.all()

    if select_related:
        active_queryset = active_queryset.select_related(*select_related)
    with system_context(reason=reason):
        instance = instance_for_id(model, id, queryset=active_queryset)
    if instance is None:
        raise GraphQLError(
            f"{model._meta.object_name} {public_id_value(id)!r} was not found.",
            extensions={"code": "BAD_USER_INPUT"},
        )
    return cast(_ActionTarget, instance)


@contextmanager
def action_target(
    model: type[_ActionTarget],
    id: PublicID,
    *,
    reason: str,
    queryset: models.QuerySet[_ActionTarget] | None = None,
    select_related: tuple[str, ...] = (),
) -> Iterator[_ActionTarget]:
    """Yield a resolved action target inside the matching elevated context."""

    target = resolve_action_target(
        model,
        id,
        reason=reason,
        queryset=queryset,
        select_related=select_related,
    )
    with system_context(reason=reason):
        yield target
