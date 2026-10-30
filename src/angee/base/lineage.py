"""Django multi-table-inheritance lineage helpers."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import models


def concrete_child_models[T: models.Model](parent_model: type[T]) -> tuple[type[T], ...]:
    """Return direct, installed MTI children in stable model-label order."""

    children: list[type[T]] = []
    for relation in parent_model._meta.get_fields(include_hidden=True):
        if not isinstance(relation, models.OneToOneRel) or not relation.parent_link:
            continue
        model = relation.related_model
        if (
            model is not None
            and model._meta.managed
            and not model._meta.proxy
            and tuple(model._meta.parents) == (parent_model,)
        ):
            assert issubclass(model, parent_model)
            children.append(model)
    return tuple(sorted(children, key=lambda model: model._meta.label_lower))


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
    if parent_link is None or parent_link.remote_field is None:
        raise ImproperlyConfigured(f"{child_model._meta.label} is not a direct child of {parent_model._meta.label}.")
    return str(parent_link.remote_field.get_accessor_name())


def concrete_child[T: models.Model](
    parent: models.Model,
    child_model: type[T],
    *,
    queryset: models.QuerySet[T] | None = None,
    cache_attr: str | None = None,
) -> T | None:
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
            cached = cached[0] if cached else None
        if cached is not None and not isinstance(cached, child_model):
            raise TypeError("An MTI prefetch must contain rows of its child model.")
        return cached
    if queryset is None and accessor in parent._state.fields_cache:
        cached = parent._state.fields_cache[accessor]
        if cached is not None and not isinstance(cached, child_model):
            raise TypeError("An MTI relation cache must contain a row of its child model.")
        return cached
    rows = queryset if queryset is not None else child_model._base_manager.db_manager(hints={"instance": parent}).all()
    return rows.filter(pk=parent.pk).first()


def most_derived_concrete[T: models.Model](row: T) -> T:
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
            assert isinstance(found, type(row))
            return found
    return row
