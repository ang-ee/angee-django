"""Guard-aware relation fields for Strawberry-Django schemas."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.expressions import Combinable
from rebac import current_actor
from rebac.graphql.strawberry_django import optimize
from rebac.relation_loading import relation_actor
from rebac.resources import model_resource_type
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.optimizer import OptimizerStore

from angee.base.scoping import aggregate_scoped_queryset, read_scoped_queryset
from angee.data.field_classification import is_to_one_relation
from angee.graphql.ids import PublicID, to_public_id
from angee.graphql.introspection import FieldPathError, fields_for_path

_UNCACHED = object()


def actor_scoped_relation_group_expression(
    queryset: models.QuerySet[Any],
    field_path: str,
) -> Combinable | None:
    """Return a read-safe scalar expression for grouping, filtering and ordering.

    Every protected target crossed by the selected to-one path contributes an
    uncorrelated membership guard. The related scalar is projected only when
    all guarded rows are readable by the source queryset's actor; otherwise it
    becomes SQL ``NULL``. Direct relation keys use the same guard, merging every
    unreadable target into one null bucket without losing source rows. Paths
    with no protected target need no override and return ``None``.
    """

    try:
        fields = fields_for_path(queryset.model, field_path)
    except FieldPathError as error:
        if error.to_many:
            raise ImproperlyConfigured(
                f"{queryset.model._meta.label}.{field_path} must traverse to-one relations to a scalar field"
            ) from error
        return None
    terminal = fields[-1]
    relations = fields if terminal.is_relation else fields[:-1]
    if not relations:
        return None
    if not all(is_to_one_relation(field) for field in relations):
        raise ImproperlyConfigured(
            f"{queryset.model._meta.label}.{field_path} must traverse to-one relations to a scalar field"
        )

    actor = relation_actor(queryset)
    guards: list[models.Q] = []
    traversed: list[str] = []
    parts = field_path.split("__")
    for part, relation in zip(parts[: len(relations)], relations, strict=True):
        related_model = relation.related_model
        if not model_resource_type(related_model):
            traversed.append(part)
            continue
        related_queryset = read_scoped_queryset(related_model, actor)
        if related_queryset is None:
            related_queryset = related_model._default_manager.none()
        else:
            related_queryset = aggregate_scoped_queryset(related_queryset)

        if isinstance(relation, (models.ForeignKey, models.OneToOneField)):
            lookup = "__".join((*traversed, relation.attname))
            target_name = relation.target_field.attname
        else:
            lookup = "__".join((*traversed, part, related_model._meta.pk.attname))
            target_name = related_model._meta.pk.attname
        guards.append(models.Q(**{f"{lookup}__in": related_queryset.values_list(target_name, flat=True)}))
        traversed.append(part)

    if not guards:
        return None
    guard = models.Q()
    for item in guards:
        guard &= item
    return models.Case(
        models.When(guard, then=models.F(field_path)),
        default=models.Value(None),
        output_field=terminal.target_field
        if isinstance(terminal, (models.ForeignKey, models.OneToOneField))
        else terminal,
    )


def actor_scoped_to_one(field_name: str) -> Any:
    """Return a nullable to-one field that redacts targets unreadable by the actor.

    The native prefetch carries the selected target's optimized queryset;
    REBAC scopes and actor-stamps it. Readable parents hit the cache;
    unreadable parents cache as ``None``.
    Unprefetched roots fall back to one actor-scoped lookup per row. The parent
    may be actor-scoped or sudo-loaded; cached targets are reused only for the
    current actor, and ``only`` keeps the parent projection to the FK id. The
    target prefetch retains the selected type's nested optimizer hints.
    """

    return _guarded_to_one_field(field_name, _actor_scoped_to_one_resolver(field_name))


def actor_scoped_public_id(field_name: str) -> Any:
    """Return a related public id, or ``None`` when its target is unreadable.

    Uses the native prefetch hint scoped and stamped by the REBAC optimizer,
    sharing :func:`actor_scoped_to_one`'s readable-parent cache, unreadable-parent
    ``None``, and per-row scoped fallback for unprefetched roots. Only the
    readable target's public id is projected.
    """

    resolve = _actor_scoped_to_one_resolver(field_name)

    def resolve_public_id(root: models.Model) -> PublicID | None:
        related = resolve(root)
        return to_public_id(type(related), related.pk) if related is not None else None

    return _guarded_to_one_field(field_name, resolve_public_id)


def _actor_scoped_to_one_resolver(field_name: str) -> Callable[[models.Model], Any]:
    """Build the shared cached-target resolver with an actor-scoped fallback."""

    def resolve(root: models.Model) -> Any:
        field = root._meta.get_field(field_name)
        if not isinstance(field, (models.ForeignKey, models.OneToOneField)):
            raise ImproperlyConfigured(f"{root._meta.label}.{field_name} must be a forward to-one relation")

        fk_id = field.value_from_object(root)
        if fk_id is None:
            return None

        actor = current_actor()
        if actor is None:
            return None

        cached = root._state.fields_cache.get(field.name, _UNCACHED)
        if cached is None:
            return None
        if cached is not _UNCACHED and getattr(cached, "_rebac_actor", None) == actor:
            return cached

        related_model = field.remote_field.model
        queryset = related_model._default_manager.all()
        with_actor = getattr(queryset, "with_actor", None)
        if not callable(with_actor):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} targets {related_model._meta.label}, "
                "whose default manager is not actor-scoped"
            )
        target_field = field.target_field
        return with_actor(actor).filter(**{target_field.attname: fk_id}).first()

    return resolve


def _guarded_to_one_field(field_name: str, resolver: Callable[[models.Model], Any]) -> Any:
    """Bind a relation projection to the native hint scoped by the REBAC optimizer."""

    return strawberry_django.field(
        resolver=resolver,
        field_name=field_name,
        only=[f"{field_name}_id"],
        prefetch_related=[_guarded_relation_prefetch(field_name)],
    )


def _guarded_relation_prefetch(field_name: str) -> Callable[[strawberry.Info], models.Prefetch | str]:
    """Apply selected node annotations and nested hints alongside the join key.

    The native optimizer resolves ``permissions_field`` annotation callbacks on
    this related queryset, as it does on roots, before any row is materialized.
    """

    def prefetch(info: strawberry.Info) -> models.Prefetch | str:
        field = cast(StrawberryDjangoField, info._field)
        related_model = field.django_model
        if related_model is None:
            # A public-ID scalar has no nested model projection to optimize.
            return field_name
        queryset = read_scoped_queryset(related_model, current_actor())
        if queryset is None:
            queryset = related_model._default_manager.none()
        assert field.origin_django_type is not None
        relation = field.origin_django_type.model._meta.get_field(field_name)
        store = OptimizerStore()
        if isinstance(relation, models.ManyToOneRel):
            store.only.append(relation.field.attname)
        elif isinstance(relation, models.ForeignKey):
            store.only.append(relation.target_field.attname)
        return models.Prefetch(field_name, queryset=optimize(queryset, info, store=store))

    return prefetch


def actor_scoped_to_many(field_name: str) -> Any:
    """Return a to-many field whose rows are scoped to the current actor.

    The parent may be actor-scoped through one relation while the selected
    to-many relation contains other protected rows. Resolve the relation through
    the target model's actor-scoped queryset instead of exposing the raw related
    manager.
    """

    def resolve(root: models.Model) -> Any:
        field = root._meta.get_field(field_name)
        if not (getattr(field, "many_to_many", False) or getattr(field, "one_to_many", False)):
            raise ImproperlyConfigured(f"{root._meta.label}.{field_name} must be a forward or reverse to-many relation")

        actor = current_actor()
        if actor is None:
            return []

        cached = getattr(root, "_prefetched_objects_cache", {}).get(field_name, _UNCACHED)
        if cached is not _UNCACHED:
            rows = list(cached)
            if all(getattr(row, "_rebac_actor", None) == actor for row in rows):
                # Returning an evaluated list preserves the prefetch; a queryset
                # would be cloned by Strawberry's relation ordering machinery.
                return rows

        related_queryset = getattr(root, field_name).all()
        with_actor = getattr(related_queryset, "with_actor", None)
        if callable(with_actor):
            return with_actor(actor)

        related_model = field.related_model
        raise ImproperlyConfigured(
            f"{root._meta.label}.{field_name} targets {related_model._meta.label}, "
            "whose related manager is not actor-scoped"
        )

    return strawberry_django.field(
        resolver=resolve,
        field_name=field_name,
        prefetch_related=[_guarded_relation_prefetch(field_name)],
    )
