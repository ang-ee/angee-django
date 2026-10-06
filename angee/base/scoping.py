"""Transport-neutral queryset scoping adapters for Django models."""

from __future__ import annotations

from typing import Any, TypeVar, cast

from django.db import models
from rebac.resources import model_resource_type

_ModelT = TypeVar("_ModelT", bound=models.Model)
_QuerySetT = TypeVar("_QuerySetT", bound=models.QuerySet[Any])


def lock_if_supported(
    queryset: _QuerySetT,
    *,
    of: tuple[str, ...] = ("self",),
    skip_locked: bool = False,
    no_key: bool = False,
) -> _QuerySetT:
    """Declare row-lock intent; Django owns write routing and backend support.

    ``"self"`` means the whole row: for a multi-table child it also names every
    parent link, since Django's ``of`` locks only the tables it names.
    """

    if "self" in of:
        of = (*of, *(path for path in _parent_link_paths(queryset.model._meta.concrete_model) if path not in of))
    return queryset.select_for_update(of=of, skip_locked=skip_locked, no_key=no_key)


def _parent_link_paths(model: type[models.Model], prefix: str = "") -> tuple[str, ...]:
    """Return ``of`` paths reaching each concrete multi-table ancestor of ``model``."""

    paths: list[str] = []
    for parent, link in model._meta.parents.items():
        if link is None:
            continue
        path = f"{prefix}{link.name}"
        paths.append(path)
        paths.extend(_parent_link_paths(parent, f"{path}__"))
    return tuple(paths)


def bind_actor(instance: models.Model, actor: Any | None) -> None:
    """Bind ``actor`` to ``instance`` when the model owns REBAC row policy."""

    if actor is None:
        return
    if _is_angee_model(type(instance)):
        cast(Any, instance).with_actor(actor)
        return
    with_actor = getattr(instance, "with_actor", None)
    if callable(with_actor):
        with_actor(actor)


def aggregate_scoped_queryset(queryset: models.QuerySet[_ModelT]) -> models.QuerySet[_ModelT]:
    """Return the aggregate-safe scoped queryset for a REBAC model."""

    if queryset.query.is_empty():
        return queryset
    if requires_angee_rebac_contract(queryset.model):
        return cast(models.QuerySet[_ModelT], cast(Any, queryset).scoped_for_aggregate())
    if _is_angee_model(queryset.model):
        return queryset
    scoped = getattr(queryset, "scoped_for_aggregate", None)
    if callable(scoped):
        return cast(models.QuerySet[_ModelT], scoped())
    return queryset


def read_scoped_queryset(
    model: type[_ModelT],
    actor: Any | None,
    *,
    action: str = "read",
) -> models.QuerySet[_ModelT]:
    """Return readable rows; an absent actor cannot read protected models.

    Models without row policy retain their default queryset. Protected models
    without an actor or a scoping manager return an empty queryset.
    """

    manager = model._default_manager
    if not model_resource_type(model):
        return manager.all()
    if actor is None:
        return model._base_manager.none()
    if _is_angee_model(model):
        return cast(models.QuerySet[_ModelT], cast(Any, manager).with_actor(actor).with_action(action))
    with_actor = getattr(manager, "with_actor", None)
    if not callable(with_actor):
        return model._base_manager.none()
    queryset = with_actor(actor)
    with_action = getattr(queryset, "with_action", None)
    return cast(models.QuerySet[_ModelT], with_action(action) if callable(with_action) else queryset)


def gated_field_expression(queryset: models.QuerySet[Any], field_name: str) -> models.Expression:
    """Project a ``read__<field>``-gated column as a filter or sort operand that reveals nothing.

    The value is the column's on rows whose effective actor holds the gate and
    NULL elsewhere, so a predicate or an ordering over it discloses no value the
    actor cannot read. An unscoped (system) queryset projects the column itself.
    """

    field = queryset.model._meta.get_field(field_name)
    output_field = cast("models.Field[Any, Any]", field).clone()
    output_field.null = True
    effective_actor = getattr(queryset, "effective_actor", None)
    actor, unscoped = effective_actor() if callable(effective_actor) else (None, True)
    if unscoped:
        return models.ExpressionWrapper(models.F(field_name), output_field=output_field)
    readers = aggregate_scoped_queryset(read_scoped_queryset(queryset.model, actor, action=f"read__{field_name}"))
    return models.Case(
        models.When(models.Exists(readers.filter(pk=models.OuterRef("pk"))), then=models.F(field_name)),
        default=models.Value(None),
        output_field=output_field,
    )


def write_scoped_queryset(model: type[_ModelT]) -> models.QuerySet[_ModelT]:
    """Return a write-target queryset with REBAC row scope and unredacted fields."""

    manager = model._default_manager
    if _is_angee_model(model):
        if requires_angee_rebac_contract(model):
            return cast(models.QuerySet[_ModelT], cast(Any, manager).for_write())
        return manager.all()
    for_write = getattr(manager, "for_write", None)
    if callable(for_write):
        return cast(models.QuerySet[_ModelT], for_write())
    return manager.all()


def system_queryset(
    model: type[_ModelT],
    *,
    lock: tuple[str, ...] | None = None,
) -> models.QuerySet[_ModelT]:
    """Return the model's unscoped system queryset, with a third-party fallback."""

    owner = getattr(model, "system_queryset", None)
    if callable(owner):
        return cast(models.QuerySet[_ModelT], owner(lock=lock))
    queryset = model._base_manager.all()
    system_context = getattr(queryset, "system_context", None)
    if callable(system_context):
        queryset = system_context(reason=f"{model._meta.label_lower}.system_queryset")
    if lock is not None:
        queryset = lock_if_supported(queryset, of=lock)
    return cast(models.QuerySet[_ModelT], queryset)


def requires_angee_rebac_contract(model: type[models.Model]) -> bool:
    """Return whether ``model`` is an Angee model with declared row authorization."""

    return _is_angee_model(model) and bool(model_resource_type(model))


def _is_angee_model(model: type[models.Model]) -> bool:
    return callable(getattr(model, "system_queryset", None))
