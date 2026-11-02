"""Guard-aware relation fields for Strawberry-Django schemas."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

import strawberry
import strawberry_django
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.expressions import Combinable
from django.db.models.fields.reverse_related import ForeignObjectRel
from django.db.models.functions import Cast
from django.db.models.sql.where import WhereNode
from rebac import current_actor
from rebac.graphql.strawberry_django import optimize
from rebac.managers import RebacQuerySet
from rebac.relation_loading import relation_actor
from rebac.resources import model_resource_type
from strawberry import Info
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.optimizer import OptimizerStore
from strawberry_django.queryset import run_type_get_queryset

from angee.graphql.ids import PublicID, optional_public_id, to_public_id
from angee.graphql.introspection import FieldPathError, fields_for_path, is_to_one_relation
from angee.iam.graphql.node import ProtectedNode
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.iam.permissions import require_rebac_queryset

_UNCACHED = object()


class _ActorScopedSubquery(models.Expression):
    """An uncorrelated scope projected only when the containing SQL uses it.

    Keep the actor-bound queryset outside Django's expression tree until SQL
    compilation. Resolve its native aggregate scope before rendering. REBAC's
    own scope expression still consults live policy while compiling SQL until
    its rendering path is made query-free.
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

    def resolve_expression(
        self, query: Any = None, allow_joins: bool = True, reuse: Any = None,
        summarize: bool = False, for_save: bool = False,
    ) -> _ActorScopedSubquery:
        resolved = cast(
            _ActorScopedSubquery,
            super().resolve_expression(query, allow_joins, reuse, summarize, for_save),
        )
        resolved.queryset = self.queryset.using(self.queryset.db).scoped_for_aggregate()
        return resolved

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, Any]:
        queryset = self.queryset
        if queryset.db != connection.alias or not queryset._rebac_scope_applied:
            queryset = queryset.using(connection.alias).scoped_for_aggregate()
        return models.Subquery(queryset.order_by().values(self.field_name)).as_sql(compiler, connection)

    def get_group_by_cols(self) -> list[Any]:
        return []


class _RecordReferenceAccess(models.Expression):
    """Discover present target types once when the annotation resolves.

    The actor is an authored snapshot: bind it before calling this owner, and
    redeclare this annotation after an actor change. Native expression children
    carry the containing row's columns through aliases, clones and optimizer paths.
    Discovery uses the source queryset's alias. A type first referenced after
    resolution, or present only on a later compiling alias, remains unreadable.
    On another alias, target scopes are rebuilt and ContentType IDs are rendered
    as subqueries; the discovered set itself stays fixed and fails closed.
    """

    contains_subquery = True
    targets: tuple[tuple[int, str, str, _ActorScopedSubquery], ...] | None = None
    content_type_alias: str | None = None

    def __init__(self, *, actor: Any) -> None:
        super().__init__(output_field=models.BooleanField())
        self.actor = actor
        self.columns: list[Any] = []

    def get_source_expressions(self) -> list[Any]:
        return self.columns

    def set_source_expressions(self, expressions: Sequence[Combinable | models.Expression]) -> None:
        self.columns = list(expressions)

    def resolve_expression(
        self, query: Any = None, allow_joins: bool = True, reuse: Any = None,
        summarize: bool = False, for_save: bool = False,
    ) -> _RecordReferenceAccess:
        if self.targets is not None:
            return cast(
                _RecordReferenceAccess,
                super().resolve_expression(query, allow_joins, reuse, summarize, for_save),
            )
        model = query.model
        if not issubclass(model, ProtectedRecordRefMixin):
            raise ImproperlyConfigured("Record-reference access requires ProtectedRecordRefMixin.")
        reference = model.record_ref_field()
        key_field = model._meta.get_field(reference.fk_field)
        if not isinstance(key_field, models.Field):
            raise ImproperlyConfigured("A record reference must store its object id in a forward field.")
        self.columns = [models.F(reference.ct_field_attname), models.F(reference.fk_field)]
        resolved = cast(
            _RecordReferenceAccess,
            super().resolve_expression(query, allow_joins, reuse, summarize, for_save),
        )
        source = model._default_manager.get_queryset()
        # Bare pointer models may use an ordinary manager; their source has no REBAC scope.
        if isinstance(source, RebacQuerySet) and self.actor is not None:
            source = require_rebac_queryset(model).with_actor(self.actor).with_action("read")
        elif self.actor is None:
            source = source.none()
        alias = source.db
        source = source.using(alias)
        if isinstance(source, RebacQuerySet):
            source = source.scoped_for_aggregate()
        content_types = ContentType.objects.db_manager(alias).filter(
            pk__in=source.order_by().values_list(reference.ct_field, flat=True),
        ).order_by("app_label", "model")
        targets = []
        for content_type in content_types:
            target_model = content_type.model_class()
            if target_model is None or not model_resource_type(target_model):
                continue
            queryset = require_rebac_queryset(target_model).using(alias).with_actor(self.actor).with_action("read")
            keys = queryset.order_by().annotate(_angee_reference_key=Cast("pk", output_field=key_field))
            targets.append((
                content_type.pk,
                target_model._meta.app_label,
                target_model._meta.model_name,
                _ActorScopedSubquery(keys, "_angee_reference_key").resolve_expression(query),
            ))
        resolved.targets = tuple(targets)
        resolved.content_type_alias = alias
        return resolved

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, Any]:
        branches = []
        content_type_column, object_id_column = self.columns
        for content_type_id, app_label, model_name, keys in self.targets or ():
            target_type: int | models.Subquery = content_type_id
            if connection.alias != self.content_type_alias:
                target_type = models.Subquery(
                    ContentType.objects.using(connection.alias)
                    .filter(app_label=app_label, model=model_name)
                    .values("pk")[:1]
                )
            branch = WhereNode(connector="AND")
            branch.add(content_type_column.get_lookup("exact")(content_type_column, target_type), "AND")
            branch.add(
                object_id_column.get_lookup("in")(
                    object_id_column,
                    keys,
                ),
                "AND",
            )
            branches.append(branch)
        if not branches:
            return compiler.compile(models.Value(False, output_field=models.BooleanField()))
        predicate = WhereNode(connector="OR")
        for branch in branches:
            predicate.add(branch, "OR")
        return compiler.compile(predicate)


def _record_reference_hint(info: Info | None) -> _RecordReferenceAccess:
    del info
    return _RecordReferenceAccess(actor=current_actor())


RECORD_REFERENCE_ANNOTATIONS = {"_angee_record_readable": _record_reference_hint}


def with_record_reference_access(
    queryset: models.QuerySet[Any],
    *,
    select: bool = True,
) -> models.QuerySet[Any]:
    """Declare current target privacy, selecting it for explicit Python consumers.

    Record filters use an alias; selected reference fields declare their own
    expression through the native annotation hint. Bind the actor before declaration;
    reapplying this helper after changing actor refreshes the captured projection.
    """
    annotation = {"_angee_record_readable": _RecordReferenceAccess(actor=relation_actor(queryset))}
    return queryset.annotate(**annotation) if select else queryset.alias(**annotation)


@strawberry.type
class RecordReferenceNode(ProtectedNode):
    """Project a generic reference only while its current target is readable.

    Selected reference fields request their native annotation hint. A row loaded
    another way (a to-one fallback, a mutation result) answers the same
    annotation on a one-row queryset under the current actor.
    """

    @classmethod
    def get_queryset(cls, queryset: models.QuerySet[Any], info: Info) -> models.QuerySet[Any]:
        return queryset

    def reference_model(self) -> str | None:
        row = cast(Any, self)
        return (row.record_model_label or None) if _record_readable(row) else None

    def reference_id(self) -> PublicID | None:
        row = cast(Any, self)
        return optional_public_id(row.record_public_id or None) if _record_readable(row) else None


def _record_readable(row: Any) -> bool:
    """Return the row's batched target-read annotation, computing it once when absent."""

    if "_angee_record_readable" not in row.__dict__:
        actor = current_actor()
        rows = with_record_reference_access(
            type(row).objects.none()
            if actor is None
            else type(row).objects.with_actor(actor).with_action("read").filter(pk=row.pk)
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
        assert related_model is not None  # The to-one path was validated above.
        if not model_resource_type(related_model):
            traversed.append(part)
            continue
        related_queryset = require_rebac_queryset(related_model).with_actor(actor).with_action("read")

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
    output_field = terminal.target_field if isinstance(terminal, (models.ForeignObject, ForeignObjectRel)) else terminal
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
        actor = current_actor()
        manager = require_rebac_queryset(related_model)
        queryset: models.QuerySet[models.Model] = (
            manager.none() if actor is None else manager.with_actor(actor).with_action("read")
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
    """

    def resolve(root: models.Model) -> Any:
        field = root._meta.get_field(field_name)
        related_model = field.related_model
        if related_model is None or not (field.many_to_many or field.one_to_many):
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
        if not isinstance(related_queryset, RebacQuerySet):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} targets {related_model._meta.label}, "
                "whose related manager is not actor-scoped"
            )
        return related_queryset.with_actor(actor)

    return strawberry_django.field(
        resolver=resolve,
        field_name=field_name,
        prefetch_related=[_guarded_relation_prefetch(field_name)],
    )
