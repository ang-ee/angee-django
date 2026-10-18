"""Shared GraphQL write-target helpers."""

from __future__ import annotations

from typing import TypeVar

from django.db import models

from angee.graphql.ids import PublicID, instance_for_id

_ModelT = TypeVar("_ModelT", bound=models.Model)


def instance_for_write(model: type[_ModelT], id: PublicID) -> _ModelT | None:
    """Return the row addressed by ``id`` through the write-scoped queryset, or None.

    The write scope is the isolation gate: a member of another scope never finds
    the row (it is filtered out), so an authored action needs no separate scope
    check before its per-row ``write`` preflight. ``None`` means the actor cannot
    reach the row — surface it as a plain not-found, never as an existence oracle.
    """

    return instance_for_id(model, id, queryset=model.objects.for_write())
