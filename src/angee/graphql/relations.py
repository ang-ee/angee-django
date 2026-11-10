"""Guard-aware relation fields for Strawberry-Django schemas.

SQL guards resolve scopes lazily at compilation throughout this module. This
keeps declarations and optimizer probes query-free, and avoids copying complete
permission graphs for unused aliases. Compilation may read represented content
types and permission witnesses; neither those reads nor their answers are cached.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Self, cast

import strawberry
import strawberry_django
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.expressions import Combinable
from django.db.models.fields.reverse_related import ForeignObjectRel
from django.db.models.functions import Cast
from django.db.models.lookups import Exact, In
from rebac import current_actor
from rebac.graphql.strawberry_django import optimize
from rebac.managers import RebacQuerySet
from rebac.resources import model_resource_type
from strawberry import Info
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.optimizer import OptimizerStore
from strawberry_django.queryset import run_type_get_queryset

from angee.base.models.base import record_display_label
from angee.base.models.refs import RecordRefMixin
from angee.base.permissions import relation_read_queryset
from angee.base.prefetch import prefetched, prefetched_objects
from angee.base.refs import RecordRef
from angee.graphql.ids import PublicID, optional_public_id, to_public_id
from angee.graphql.introspection import FieldPathError, fields_for_path, is_to_one_relation
from angee.graphql.node import AngeeNode


@strawberry.type
class RecordPointerType:
    """Minimal record identity from an already authorized reference."""

    label: str
    model_label: str = strawberry.field(name="model_label")
    record_id: strawberry.ID = strawberry.field(name="record_id")

    @classmethod
    def from_reference(cls, reference: RecordRef, *, label: str = "") -> Self:
        """Project identity without loading the target; navigation rechecks access."""

        return cls(
            label=label or reference.public_id,
            model_label=reference.model_label,
            record_id=strawberry.ID(reference.public_id),
        )

_UNCACHED = object()
_REFERENCE_TARGET = "_angee_reference_target"


class _ActorScopedSubquery(models.Expression):
    """An uncorrelated scope projected only when the containing SQL uses it.

    Keep the lazy actor-bound queryset outside Django's expression tree until
    SQL compilation. Otherwise every alias, queryset clone and optimizer pass
    copies its entire permission graph, including aliases the query never uses.
    The native aggregate scope still owns authorization and live frontier checks;
    neither SQL nor permission answers are cached across evaluations.
    """

    contains_subquery = True

    def __init__(self, queryset: RebacQuerySet[Any], field_name: str) -> None:
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
        queryset = self.queryset.scoped_for_aggregate()
        return models.Subquery(queryset.order_by().values(self.field_name)).as_sql(compiler, connection)

    def get_group_by_cols(self) -> list[Any]:
        return []


def with_record_reference_access(queryset: models.QuerySet[Any]) -> models.QuerySet[Any]:
    """Annotate references with read guards for their represented target types.

    Retained targets remain readable independently of the edge's current write
    admissions; only tables represented in this source population participate.
    Declarations and optimizer probes perform no reads, even for nonempty sources.
    """

    if not issubclass(queryset.model, RecordRefMixin):
        raise ImproperlyConfigured("Record-reference access requires RecordRefMixin.")
    return queryset.annotate(_angee_record_readable=_RecordReferenceAccess(queryset))


class _RecordReferenceAccess(models.Expression):
    """Resolve represented target types and their live read scopes only when used."""

    contains_subquery = True

    def __init__(self, queryset: models.QuerySet[Any]) -> None:
        super().__init__(output_field=models.BooleanField())
        self.queryset = queryset
        self.reference = cast(type[RecordRefMixin], queryset.model).record_ref_field()
        key_field = queryset.model._meta.get_field(self.reference.fk_field)
        if not isinstance(key_field, models.Field):
            raise ImproperlyConfigured("A record reference must store its object id in a forward field.")
        self.key_field = key_field

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, Any]:
        rows = self.queryset.scoped_for_aggregate() if isinstance(self.queryset, RebacQuerySet) else self.queryset
        content_types = ContentType.objects.filter(
            pk__in=rows.order_by().values(self.reference.ct_field),
        ).order_by("app_label", "model")
        readable = models.Q()
        for content_type in content_types:
            model = content_type.model_class()
            if model is None or model_resource_type(model) is None:
                continue
            keys = relation_read_queryset(model, self.queryset).annotate(
                _angee_reference_key=Cast("pk", output_field=self.key_field),
            )
            readable |= models.Q(
                Exact(models.F(self.reference.ct_field), models.Value(content_type.pk)),
                In(models.F(self.reference.fk_field), _ActorScopedSubquery(keys, "_angee_reference_key")),
            )
        expression = (
            models.ExpressionWrapper(readable, output_field=models.BooleanField()) if readable else models.Value(False)
        )
        return compiler.compile(expression.resolve_expression(compiler.query))


def readable_record_references(queryset: models.QuerySet[Any]) -> models.QuerySet[Any]:
    """Keep only the generic references whose targets the actor currently reads."""

    return with_record_reference_access(queryset).filter(_angee_record_readable=True)


@strawberry.type
class RecordReferenceNode(AngeeNode):
    """Project a generic reference only while its current target is readable.

    ``get_queryset`` batches the target read into the rows' own query. A row
    loaded another way (a to-one fallback, a mutation result) answers the same
    annotation on a one-row queryset under the current actor.
    """

    @classmethod
    def get_queryset(cls, queryset: models.QuerySet[Any], info: Info) -> models.QuerySet[Any]:
        return with_record_reference_access(queryset)

    def reference_model(self) -> str | None:
        row = cast(Any, self)
        return (row.record_model_label or None) if _record_readable(row) else None

    def reference_id(self) -> PublicID | None:
        row = cast(Any, self)
        return optional_public_id(row.record_public_id or None) if _record_readable(row) else None

    def reference_label(self, info: Info) -> str | None:
        """Return the readable target's own label, read under the reader's scope.

        The batched annotation gates the label exactly as it gates the identity
        fields, so a hidden target is never loaded. Declare
        :func:`record_reference_targets` as the field's prefetch hint: a list then
        reads each referenced type's targets once, and a row loaded another way
        reads its own target through the same prefetch.
        """

        row = cast(Any, self)
        if not _record_readable(row):
            return None
        target = prefetched(row, _REFERENCE_TARGET, lambda: record_reference_targets(info))
        return record_display_label(target) if target is not None else None


def record_reference_targets(info: Info) -> models.Prefetch:
    """Prefetch a reference list's readable targets, one read per referenced type, for its labels."""

    field = cast(StrawberryDjangoField, info._field)
    assert field.origin_django_type is not None
    model = cast(type[RecordRefMixin], field.origin_django_type.model)
    return model.record_targets_prefetch(current_actor(), to_attr=_REFERENCE_TARGET)


def _record_readable(row: Any) -> bool:
    """Return the row's batched target-read annotation, computing it once when absent."""

    if "_angee_record_readable" not in row.__dict__:
        rows = with_record_reference_access(
            relation_read_queryset(type(row), type(row)._default_manager.all()).filter(pk=row.pk)
        )
        row._angee_record_readable = bool(rows.values_list("_angee_record_readable", flat=True).first())
    return bool(row._angee_record_readable)


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
    Relation guards use REBAC's projection scope: a bound actor (pinned or
    ambient) scopes the related read, ambient system scope bypasses when no
    actor is bound, and a missing actor fails closed.
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

    guards: list[models.Q] = []
    traversed: list[str] = []
    parts = field_path.split("__")
    for part, relation in zip(parts[: len(relations)], relations, strict=True):
        related_model = relation.related_model
        assert related_model is not None  # The to-one path was validated above.
        if not model_resource_type(related_model):
            traversed.append(part)
            continue
        related_queryset = relation_read_queryset(related_model, queryset)

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
        terminal.target_field if isinstance(terminal, (models.ForeignObject, ForeignObjectRel)) else terminal
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
        if actor is not None:
            cached = root._state.fields_cache.get(field.name, _UNCACHED)
            if cached is None:
                return None
            if cached is not _UNCACHED and cached.actor() == actor:
                return cached

        related_model = field.related_model
        queryset = relation_read_queryset(related_model, related_model._default_manager.all())
        return queryset.filter(**{lookup: fk_id}).first()

    return resolve


def _guarded_to_one_field(field_name: str, resolver: Callable[[models.Model], Any]) -> Any:
    """Bind a relation projection to the native hint scoped by the REBAC optimizer."""

    return strawberry_django.field(
        resolver=resolver,
        field_name=field_name,
        only=[field_name],
        prefetch_related=[_guarded_relation_prefetch(field_name)],
    )


def _guarded_relation_prefetch(field_name: str) -> Callable[[strawberry.Info], models.Prefetch]:
    """Apply selected node annotations and nested hints alongside the join key.

    The native optimizer resolves ``permissions_field`` annotation callbacks on
    this related queryset, as it does on roots, before any row is materialized.
    """

    def prefetch(info: strawberry.Info) -> models.Prefetch:
        field = cast(StrawberryDjangoField, info._field)
        related_model = field.django_model
        if related_model is None:
            # A public-ID scalar has no nested model projection to optimize.
            return models.Prefetch(field_name)
        queryset: models.QuerySet[models.Model] = relation_read_queryset(
            related_model, related_model._default_manager.all(),
        )
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
    Without an actor, the native related queryset resolves ambient scope.
    """

    def resolve(root: models.Model) -> Any:
        field = root._meta.get_field(field_name)
        related_model = field.related_model
        if related_model is None or not (field.many_to_many or field.one_to_many):
            raise ImproperlyConfigured(f"{root._meta.label}.{field_name} must be a forward or reverse to-many relation")

        actor = current_actor()
        rows = prefetched_objects(root, field_name)
        if actor is not None and rows is not None:
            if all(row.actor() == actor for row in rows):
                # Returning an evaluated list preserves the prefetch; a queryset
                # would be cloned by Strawberry's relation ordering machinery.
                return rows

        related_queryset = getattr(root, field_name).all()
        if not isinstance(related_queryset, RebacQuerySet):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} targets {related_model._meta.label}, "
                "whose related manager is not actor-scoped"
            )
        if actor is not None:
            return related_queryset.with_actor(actor)
        # Reapply read scope to a fresh clone even if the manager returned its
        # ordinary prefetch queryset; its evaluated cache cannot answer a new read.
        return related_queryset.with_action("read")

    return strawberry_django.field(
        resolver=resolve,
        field_name=field_name,
        prefetch_related=[_guarded_relation_prefetch(field_name)],
    )
