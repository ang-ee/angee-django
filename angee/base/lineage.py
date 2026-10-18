"""Django multi-table-inheritance lineage helpers."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import models


def concrete_child_models(parent_model: type[models.Model]) -> tuple[type[models.Model], ...]:
    """Return direct, installed MTI children in stable model-label order."""

    return tuple(
        sorted(
            (
                model
                for model in parent_model._meta.apps.get_models()
                if model._meta.managed and not model._meta.proxy and tuple(model._meta.parents) == (parent_model,)
            ),
            key=lambda model: model._meta.label_lower,
        )
    )


def concrete_lineage(model: type[models.Model]) -> tuple[type[models.Model], ...]:
    """Installed concrete tables sharing a record's primary key, in stable order."""

    owners = {model, *model._meta.get_parent_list()}
    pending = list(owners)
    while pending:
        for child in concrete_child_models(pending.pop()):
            if child not in owners:
                owners.add(child)
                pending.append(child)
    return tuple(sorted(owners, key=lambda owner: owner._meta.label_lower))


def concrete_child_accessor(parent_model: type[models.Model], child_model: type[models.Model]) -> str:
    """Return Django's reverse accessor for a direct MTI parent link."""

    parent_link = child_model._meta.parents.get(parent_model)
    if parent_link is None:
        raise ImproperlyConfigured(f"{child_model._meta.label} is not a direct child of {parent_model._meta.label}.")
    return str(parent_link.remote_field.get_accessor_name())


def concrete_child(
    parent: models.Model,
    child_model: type[models.Model],
    *,
    queryset: models.QuerySet[Any] | None = None,
    cache_attr: str | None = None,
) -> models.Model | None:
    """Read one child through a prefetch cache or its supplied base/scoped queryset.

    The caller owns access policy. The default base manager reads structural MTI
    identity; an actor-scoped queryset can instead restrict the returned row.
    """

    if queryset is None and isinstance(parent, child_model):
        return parent
    parent_model = next((model for model in child_model._meta.parents if isinstance(parent, model)), type(parent))
    accessor = concrete_child_accessor(parent_model, child_model)
    if cache_attr is not None and hasattr(parent, cache_attr):
        cached = getattr(parent, cache_attr)
        if isinstance(cached, (list, tuple)):
            return cached[0] if cached else None
        return cached
    if queryset is None and accessor in parent._state.fields_cache:
        return parent._state.fields_cache[accessor]
    rows = queryset if queryset is not None else child_model._base_manager.all()
    return rows.filter(pk=parent.pk).first()


def most_derived_concrete(row: models.Model) -> models.Model:
    """Resolve ``row`` to the deepest installed MTI child sharing its key."""

    descendants = sorted(
        (model for model in concrete_lineage(type(row)) if issubclass(model, type(row))),
        key=lambda model: len(model._meta.get_parent_list()),
        reverse=True,
    )
    for model in descendants:
        if isinstance(row, model):
            return row
        found = model._base_manager.db_manager(hints={"instance": row}).filter(pk=row.pk).first()
        if found is not None:
            return found
    return row


