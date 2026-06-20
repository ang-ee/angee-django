"""Shared GraphQL result type for console domain actions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar, cast

import strawberry
from django.db import models
from rebac import system_context

from angee.graphql.ids import PublicID, instance_for_id, public_id_value

_ActionTarget = TypeVar("_ActionTarget", bound=models.Model)


@strawberry.type
class ActionResult:
    """Outcome of a console domain action: a success flag and a human message.

    Returned by non-CRUD action mutations (sync, test, discover, …) so the client
    can surface a toast and refresh the affected record.
    """

    ok: bool
    message: str


def resolve_action_target(
    model: type[_ActionTarget],
    id: PublicID,
    *,
    reason: str,
    queryset: models.QuerySet[_ActionTarget] | None = None,
    select_related: tuple[str, ...] = (),
) -> _ActionTarget:
    """Return an elevated action target addressed by one GraphQL public id.

    The caller owns actor authorization, usually with field ``permission_classes``.
    This helper owns the repeated action-write lookup shape: build the requested
    queryset, enter ``system_context`` for the row read, and raise a stable
    not-found error instead of leaking ``None`` into the action body.
    """

    active_queryset = queryset if queryset is not None else model._default_manager.all()
    if select_related:
        active_queryset = active_queryset.select_related(*select_related)
    with system_context(reason=reason):
        instance = instance_for_id(model, id, queryset=active_queryset)
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {public_id_value(id)!r} was not found.")
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
