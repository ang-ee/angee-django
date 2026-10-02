"""Guard-aware relation fields for Strawberry-Django schemas."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import strawberry
import strawberry_django
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.expressions import Combinable
from django.db.models.functions import Cast
from rebac import current_actor
from rebac.graphql.strawberry_django import optimize
from rebac.relation_loading import relation_actor
from rebac.resources import model_resource_type
from strawberry import Info
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.optimizer import OptimizerStore
from strawberry_django.queryset import run_type_get_queryset

from angee.base.refs import RecordRefMixin
from angee.base.scoping import aggregate_scoped_queryset, read_scoped_queryset
from angee.data.field_classification import is_to_one_relation
from angee.graphql.ids import PublicID, optional_public_id, to_public_id
from angee.graphql.introspection import FieldPathError, fields_for_path
from angee.graphql.node import AngeeNode

_UNCACHED = object()


class _ActorScopedSubquery(models.Expression):
    """An uncorrelated scope projected only when the containing SQL uses it.

    Keep the lazy actor-bound queryset outside Django's expression tree until
    SQL compilation. Otherwise every alias, queryset clone and optimizer pass
    copies its entire permission graph, including aliases the query never uses.
    The native aggregate scope still owns authorization and live frontier checks;
    neither SQL nor permission answers are cached across evaluations.
    """

    contains_subquery = True

    def __init__(self, queryset: models.QuerySet[Any], field_name: str) -> None:
        field = queryset.query.annotations.get(field_name)
        if field is None:
            field = queryset.model._meta.pk if field_name == "pk" else queryset.model._meta.get_field(field_name)
            output_field = field.get_col(queryset.model._meta.db_table).output_field
        else:
            output_field = field.output_field
        super().__init__(output_field=output_field)
        self.queryset = queryset
        self.field_name = field_name

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, Any]:
        queryset = aggregate_scoped_queryset(self.queryset.using(connection.alias))
        return models.Subquery(queryset.order_by().values(self.field_name)).as_sql(compiler, connection)

    def get_group_by_cols(self) -> list[Any]:
        return []


def with_record_reference_access(queryset: models.QuerySet[Any]) -> models.QuerySet[Any]:
    """Annotate generic references with their targets' current read policy."""

    if not issubclass(queryset.model, RecordRefMixin):
        raise ImproperlyConfigured("Record-reference access requires RecordRefMixin.")
    reference = queryset.model.record_ref_field()
    key_field = queryset.model._meta.get_field(reference.fk_field)
    content_types = ContentType.objects.filter(pk__in=aggregate_scoped_queryset(queryset).order_by().values(
        reference.ct_field,
    )).order_by("app_label", "model")
    actor = relation_actor(queryset)
    readable = models.Q(pk__in=[])
    for content_type in content_types:
        model = content_type.model_class()
        if model is None:
            continue
        targets = read_scoped_queryset(model, actor)
        keys = targets.order_by().annotate(_angee_reference_key=Cast("pk", output_field=key_field))
        readable |= models.Q(**{
            reference.ct_field_attname: content_type.pk,
            f"{reference.fk_field}__in": _ActorScopedSubquery(keys, "_angee_reference_key"),
        })
    return queryset.annotate(_angee_record_readable=models.ExpressionWrapper(
        readable, output_field=models.BooleanField(),
    ))


@strawberry.type
class RecordReferenceNode(AngeeNode):
    """Project a generic reference only while its current target is readable."""

    @classmethod
    def get_queryset(cls, queryset: models.QuerySet[Any], info: Info) -> models.QuerySet[Any]:
        return with_record_reference_access(queryset)

    def reference_model(self) -> str | None:
        row = cast(Any, self)
        return (row.record_model_label or None) if row._angee_record_readable else None

    def reference_id(self) -> PublicID | None:
        row = cast(Any, self)
        return optional_public_id(row.record_public_id or None) if row._angee_record_readable else None


def actor_scoped_relation_expression(
    queryset: models.QuerySet[Any],
    field_path: str,
    *,
    value: Combinable | None = None,
) -> Combinable | None:
    """Return a read-safe scalar expression for grouping, filtering and ordering.

    Every protected target crossed by the selected to-one path contributes an
    uncorrelated membership guard. The related scalar is projected only when
    all guarded rows are readable by the source queryset's actor; otherwise it
    becomes SQL ``NULL``. Direct relation keys use the same guard, merging every
    unreadable target into one null bucket without losing source rows. Paths
    with no protected target need no override and return ``None``.
    ``value`` substitutes a model-owned expression for the path's scalar value;
    sort aliases compose the same guard around each referenced path.
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

        if isinstance(relation, (models.ForeignKey, models.OneToOneField)):
            lookup = "__".join((*traversed, relation.attname))
            target_name = relation.target_field.attname
        else:
            lookup = "__".join((*traversed, part, related_model._meta.pk.attname))
            target_name = related_model._meta.pk.attname
        guards.append(models.Q(**{f"{lookup}__in": _ActorScopedSubquery(related_queryset, target_name)}))
        traversed.append(part)

    if not guards:
        return None
    guard = models.Q()
    for item in guards:
        guard &= item
    output_field = (
        terminal.target_field if isinstance(terminal, (models.ForeignKey, models.OneToOneField)) else terminal
    )
    return models.Case(
        models.When(guard, then=models.F(field_path) if value is None else value),
        default=models.Value(None),
        output_field=output_field if value is None else None,
    )


def actor_scoped_to_one(field_name: str) -> Any:
    """Return a nullable forward/reverse to-one field redacting unreadable targets.

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
        if isinstance(field, models.OneToOneRel):
            fk_id = field.field.target_field.value_from_object(root)
            lookup = field.field.attname
        elif isinstance(field, models.ForeignKey):
            fk_id = field.value_from_object(root)
            lookup = field.target_field.attname
        else:
            raise ImproperlyConfigured(f"{root._meta.label}.{field_name} must be a to-one relation")
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

        related_model = field.related_model
        queryset = related_model._default_manager.all()
        with_actor = getattr(queryset, "with_actor", None)
        if not callable(with_actor):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} targets {related_model._meta.label}, "
                "whose default manager is not actor-scoped"
            )
        return with_actor(actor).filter(**{lookup: fk_id}).first()

    return resolve


def _guarded_to_one_field(field_name: str, resolver: Callable[[models.Model], Any]) -> Any:
    """Bind a relation projection to the native hint scoped by the REBAC optimizer."""

    return strawberry_django.field(
        resolver=resolver,
        field_name=field_name,
        only=[field_name],
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
        queryset = run_type_get_queryset(queryset, field.django_type, info)
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
    manager. Both cached and queried rows retain their model's native ordering.
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
        if not callable(with_actor):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} targets {field.related_model._meta.label}, "
                "whose related manager is not actor-scoped"
            )
        return with_actor(actor)

    return strawberry_django.field(
        resolver=resolve,
        field_name=field_name,
        prefetch_related=[_guarded_relation_prefetch(field_name)],
    )
