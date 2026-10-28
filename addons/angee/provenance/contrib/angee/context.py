"""Angee management and merge lifecycle attribution defaults."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import models, router

from angee.provenance.context import _ChangesetScope, changeset, changeset_model, current_changeset


def management_context(command: str) -> _ChangesetScope:
    """Attribute one non-server management command through the host's native hook."""

    return changeset(fresh=True, activity="system", change_reason=f"manage.{command}", process=True)


@contextmanager
def merge_context(survivor: models.Model, *, join: bool = False) -> Iterator[None]:
    """Preserve a whole fold's attribution, including an unchanged survivor."""

    context = current_changeset() if join else None
    if context is not None:
        # Attribution is lazy: an uncaptured owner can fold before its ambient
        # changeset has been inserted. The native write router owns that future
        # destination just as it owns current_changeset(record=True).
        context_alias = context._state.db or router.db_for_write(changeset_model(), instance=context)
        if context_alias != survivor._state.db:
            raise ValidationError("A fold must share the database of its changeset.")
    with changeset(
        fresh=context is None,
        activity="merge" if context is None else None,
        origin=survivor if context is None else None,
    ):
        yield
        if (context := current_changeset(required=True, record=True)) is not None:
            context.touched.append(type(survivor)._meta.label_lower)
