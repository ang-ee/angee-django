"""Shared GraphQL write-target helpers."""

from __future__ import annotations

from typing import TypeVar

from django.db import models

from angee.graphql.data.extensions import resource_extensions
from angee.graphql.ids import PublicID, instance_for_id

_ModelT = TypeVar("_ModelT", bound=models.Model)


def write_queryset(model: type[_ModelT]) -> models.QuerySet[_ModelT]:
    """Keep native row authorization while disabling field redaction for writes."""

    source = resource_extensions(model).write_queryset
    return source(model) if source is not None else model._default_manager.all()


def instance_for_write(model: type[_ModelT], id: PublicID) -> _ModelT | None:
    """Return the row addressed by ``id`` through the write-scoped queryset, or None.

    The write scope is the isolation gate: a member of another scope never finds
    the row (it is filtered out), so an authored action needs no separate scope
    check before its per-row ``write`` preflight. ``None`` means the actor cannot
    reach the row — surface it as a plain not-found, never as an existence oracle.
    """

    return instance_for_id(model, id, queryset=write_queryset(model))
