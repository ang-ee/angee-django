"""Transport-neutral queryset scoping adapters for Django models."""

from __future__ import annotations

from typing import Any, TypeVar, cast

from django.db import models
from rebac.resources import model_resource_type

_ModelT = TypeVar("_ModelT", bound=models.Model)


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
) -> models.QuerySet[_ModelT] | None:
    """Return a queryset scoped to ``actor`` for models with a REBAC row policy."""

    if not model_resource_type(model) or actor is None:
        return None
    if _is_angee_model(model):
        manager = cast(Any, model._default_manager)
        return cast(models.QuerySet[_ModelT], manager.with_actor(actor).with_action(action))
    manager = model._default_manager
    with_actor = getattr(manager, "with_actor", None)
    if not callable(with_actor):
        return None
    queryset = with_actor(actor)
    with_action = getattr(queryset, "with_action", None)
    return cast(models.QuerySet[_ModelT], with_action(action) if callable(with_action) else queryset)


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
    using: str | None = None,
    lock: tuple[str, ...] | None = None,
) -> models.QuerySet[_ModelT]:
    """Return the model's unscoped system queryset, with a third-party fallback."""

    owner = getattr(model, "system_queryset", None)
    if callable(owner):
        return cast(models.QuerySet[_ModelT], owner(using=using, lock=lock))
    queryset = model._base_manager.all()
    queryset = queryset.using(using) if using is not None else queryset
    system_context = getattr(queryset, "system_context", None)
    if callable(system_context):
        queryset = system_context(reason=f"{model._meta.label_lower}.system_queryset")
    locker = getattr(queryset, "lock_if_supported", None)
    if lock is not None and callable(locker):
        queryset = locker(of=lock)
    return cast(models.QuerySet[_ModelT], queryset)


def requires_angee_rebac_contract(model: type[models.Model]) -> bool:
    """Return whether ``model`` is an Angee model with declared row authorization."""

    return _is_angee_model(model) and bool(model_resource_type(model))


def _is_angee_model(model: type[models.Model]) -> bool:
    return callable(getattr(model, "system_queryset", None))
