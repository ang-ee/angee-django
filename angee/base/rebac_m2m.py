"""REBAC glue for many-to-many fields backed by explicit relationship tuples."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any, TypeAlias

from django.db import models, transaction

M2MRelationPair: TypeAlias = tuple[models.Model, models.Model]
M2MRelationCallback: TypeAlias = Callable[[models.Model, models.Model], None]
ContextFactory: TypeAlias = Callable[[], AbstractContextManager[object]]

_SYNC_ACTIONS = {"post_add", "post_remove", "pre_clear"}


def m2m_changed_relation_pairs(
    *,
    instance: models.Model,
    action: str,
    reverse: bool,
    model: type[models.Model],
    pk_set: set[Any] | None,
    forward_field_name: str,
    reverse_field_name: str,
) -> list[M2MRelationPair]:
    """Return forward/reverse model pairs affected by one Django M2M signal."""

    if action not in _SYNC_ACTIONS:
        return []
    if action == "pre_clear":
        if reverse:
            return [
                (forward, instance)
                for forward in getattr(instance, reverse_field_name).all().order_by("pk")
            ]
        return [
            (instance, reverse_instance)
            for reverse_instance in getattr(instance, forward_field_name).all().order_by("pk")
        ]
    if reverse:
        return [
            (forward, instance)
            for forward in model._base_manager.filter(pk__in=pk_set or ()).order_by("pk")
        ]
    return [
        (instance, reverse_instance)
        for reverse_instance in model._base_manager.filter(pk__in=pk_set or ()).order_by("pk")
    ]


def reconcile_m2m_changed_relationships_on_commit(
    *,
    action: str,
    instance: models.Model,
    reverse: bool,
    model: type[models.Model],
    pk_set: set[Any] | None,
    forward_field_name: str,
    reverse_field_name: str,
    grant: M2MRelationCallback,
    revoke: M2MRelationCallback,
    using: str | None = None,
    context: ContextFactory = nullcontext,
) -> None:
    """Mirror one M2M signal into non-field-backed REBAC tuples after commit."""

    pairs = m2m_changed_relation_pairs(
        instance=instance,
        action=action,
        reverse=reverse,
        model=model,
        pk_set=pk_set,
        forward_field_name=forward_field_name,
        reverse_field_name=reverse_field_name,
    )
    if not pairs:
        return

    def reconcile() -> None:
        with context():
            for forward, reverse_instance in pairs:
                if action == "post_add":
                    grant(forward, reverse_instance)
                else:
                    revoke(forward, reverse_instance)

    transaction.on_commit(reconcile, using=using)
