"""Explicit database selection and relation reloads for Django operations."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TypeVar

from django.db import models, router

_ModelT = TypeVar("_ModelT", bound=models.Model)


def get_write_alias(
    model: type[_ModelT],
    *,
    using: str | None = None,
    bound: models.Manager[_ModelT] | models.QuerySet[_ModelT] | None = None,
    instance: _ModelT | None = None,
) -> str:
    """Return one write alias for the operation's reads, locks and writes.

    Prefer explicit ``using``, then an explicitly bound manager/queryset, then
    a persisted instance's database, then Django's write router with the instance
    hint. An unsaved instance's database is only a router hint. Never consult
    ``bound.db``: an unbound manager/queryset can route that property as a read.
    Derive at the write owner and pass the result to every nested database API.
    """

    alias = _bound_alias(using=using, bound=bound, instance=instance)
    return alias if alias is not None else router.db_for_write(model, instance=instance)


def get_read_alias(
    model: type[_ModelT],
    *,
    using: str | None = None,
    bound: models.Manager[_ModelT] | models.QuerySet[_ModelT] | None = None,
    instance: _ModelT | None = None,
) -> str:
    """Return one alias for a pure read, preserving explicit and persisted bindings.

    Use the same precedence as ``get_write_alias``: explicit ``using``, a bound
    manager/queryset, a persisted instance's database, then Django's read router
    with the instance hint. Unsaved instance affinity is only a router hint.
    Never use read routing for locking reads, a write, or a read feeding that
    write: the write owner derives ``get_write_alias`` once and passes it down.
    """

    alias = _bound_alias(using=using, bound=bound, instance=instance)
    return alias if alias is not None else router.db_for_read(model, instance=instance)


def _bound_alias(
    *,
    using: str | None,
    bound: models.Manager[_ModelT] | models.QuerySet[_ModelT] | None,
    instance: _ModelT | None,
) -> str | None:
    """Resolve the shared bindings before either native routing policy runs."""

    if using is not None:
        return using
    if bound is not None and bound._db is not None:
        return bound._db
    if instance is not None and not instance._state.adding and instance._state.db is not None:
        return instance._state.db
    return None


def refresh_deferred(
    instance: _ModelT,
    *,
    using: str,
    fields: Iterable[str] | None = None,
) -> _ModelT:
    """Load deferred columns before a write reads them on its pinned alias.

    ``fields`` optionally limits the refresh to concrete field attnames. Loaded
    values, including unsaved assignments, remain untouched. Django owns the
    refresh, cache invalidation and instance database affinity; an empty set
    performs no query. Return the same instance.
    """

    deferred = instance.get_deferred_fields()
    if fields is not None:
        deferred.intersection_update(fields)
    if deferred:
        instance.refresh_from_db(using=using, fields=sorted(deferred))
    return instance


def related_on(
    instance: models.Model,
    field_name: str,
    *,
    using: str,
    required: bool = True,
    select_related: Sequence[str] = (),
) -> models.Model | None:
    """Reload a primary-key FK target on the operation's explicit alias.

    The caller supplies the write alias, or a read alias it has already derived.

    Resolve the field and its stored ID through Django's metadata, then query
    its remote model's base manager. A null FK returns ``None`` without querying
    the target; a missing non-null target raises its native ``DoesNotExist``
    unless ``required=False``. ``select_related`` names native eager joins.
    A deferred FK ID is first refreshed on the same alias, including Django's
    native cache invalidation and repointing of ``instance._state.db``.

    Never reuse or populate the FK result cache: callers retain cache policy.
    In particular, instance affinity alone cannot pin a forward-FK descriptor,
    which otherwise consults Django's read router with an instance hint.
    """

    field = instance._meta.get_field(field_name)
    if not isinstance(field, models.ForeignKey):
        raise TypeError(f"{instance._meta.label}.{field_name} is not a forward foreign key.")
    refresh_deferred(instance, using=using, fields=(field.attname,))
    related_id = getattr(instance, field.attname)
    if related_id is None:
        return None
    queryset = field.remote_field.model._base_manager.db_manager(using).all()
    if select_related:
        queryset = queryset.select_related(*select_related)
    if required:
        return queryset.get(pk=related_id)
    return queryset.filter(pk=related_id).first()
