"""Shared GraphQL result type for console domain actions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar, cast

import strawberry
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import models
from rebac import system_context
from strawberry.scalars import JSON
from strawberry.utils.str_converters import to_camel_case

from angee.graphql.ids import PublicID, instance_for_id, public_id_value

_ActionTarget = TypeVar("_ActionTarget", bound=models.Model)


@strawberry.type
class ActionResult:
    """Outcome of a console domain action: a success flag and a human message.

    Returned by non-CRUD action mutations (sync, test, discover, register-payment,
    …) so the client can surface a toast and refresh the affected record.

    On a *domain* failure the action returns ``ok=False`` and may populate
    ``validation_errors`` — a field → messages map keyed by the argument names the
    form binds to (its arg descriptor ``name``s) — which a typed-args action form
    binds to its inputs, keeping the dialog open until ``ok=True``. Keys that match
    no argument surface at form level. This is the in-band path; a *non-domain*
    failure still raises a GraphQL error carrying the ``validationErrors``
    extension instead.
    """

    ok: bool
    message: str
    validation_errors: JSON | None = None

    @classmethod
    def from_error(cls, error: Exception, summary: str) -> ActionResult:
        """Return a failed result from a caught exception.

        A Django ``ValidationError`` carrying per-field messages (``error_dict``)
        becomes the in-band ``validation_errors`` map a typed-args action form binds
        to its inputs: field names are camel-cased to match the GraphQL argument
        names the form binds to, and ``NON_FIELD_ERRORS`` (or any key that matches
        no argument) surfaces at form level. Any other exception — or a
        ``ValidationError`` with only non-field messages — yields a message-only
        failure. ``summary`` is the human banner shown either way; the raw exception
        text is never leaked into it.
        """

        if isinstance(error, ValidationError) and hasattr(error, "error_dict"):
            field_errors: dict[str, list[str]] = {}
            for field, messages in error.message_dict.items():
                key = field if field == NON_FIELD_ERRORS else to_camel_case(field)
                field_errors[key] = list(messages)
            if field_errors:
                return cls(ok=False, message=summary, validation_errors=cast(JSON, field_errors))
        return cls(ok=False, message=summary)


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
