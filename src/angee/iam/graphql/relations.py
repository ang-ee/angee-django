"""Guard-aware relation fields for Strawberry-Django schemas."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from inspect import isawaitable
from typing import Any, cast

import strawberry
import strawberry_django
from asgiref.sync import sync_to_async
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.expressions import Combinable
from django.db.models.fields.reverse_related import ForeignObjectRel
from django.db.models.functions import Cast
from django.db.models.sql.where import WhereNode
from django.http import HttpRequest
from rebac import current_actor
from rebac.graphql.strawberry_django import optimize
from rebac.managers import RebacQuerySet
from rebac.relation_loading import relation_actor
from rebac.resources import model_resource_type
from rebac.schema.cache import schema_operation_scope
from strawberry import Info
from strawberry.extensions import FieldExtension
from strawberry.types import get_object_definition
from strawberry.types.base import StrawberryContainer
from strawberry.types.nodes import SelectedField, Selection
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.optimizer import OptimizerStore
from strawberry_django.queryset import run_type_get_queryset
from strawberry_django.utils.requests import get_request

from angee.base.models.base import AngeeModel
from angee.graphql.ids import PublicID, optional_public_id, to_public_id
from angee.graphql.introspection import FieldPathError, fields_for_path, is_to_one_relation
from angee.graphql.node import display_name_paths, node_display_name
from angee.iam.graphql.capabilities import _proven_read_annotation, _share_permission_projections
from angee.iam.graphql.node import ProtectedNode
from angee.iam.models.base import ProtectedModel
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.iam.permissions import require_rebac_queryset

_UNCACHED = object()


class _ActorScopedSubquery(models.Expression):
    """An uncorrelated scope projected only when the containing SQL uses it.

    Keep the actor-bound queryset outside Django's expression tree until SQL
    compilation. Resolve its native aggregate scope while rendering, so an
    unused alias never builds a permission graph. REBAC's
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

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, Any]:
        queryset = self.queryset.using(connection.alias).scoped_for_aggregate()
        return models.Subquery(queryset.order_by().values(self.field_name)).as_sql(compiler, connection)

    def get_group_by_cols(self) -> list[Any]:
        return []


class _RecordReferenceAccess(models.Expression):
    """Prove target readability for one known record or discovered target types.

    The actor is an authored snapshot: bind it before calling this owner, and
    redeclare this annotation after an actor change. Native expression children
    carry the containing row's columns through aliases, clones and optimizer paths.
    Unbounded discovery uses the source queryset's alias. A type first referenced after
    resolution, or present only on a later compiling alias, remains unreadable.
    On another alias, target scopes are rebuilt and ContentType IDs are rendered
    as subqueries; the discovered set itself stays fixed and fails closed.
    A bound target uses its model's content type and filters the native REBAC
    scope to its primary key before compiling the SQL proof.
    """

    contains_subquery = True
    targets: tuple[tuple[int, str, str, _ActorScopedSubquery], ...] | None = None
    content_type_alias: str | None = None

    def __init__(self, *, actor: Any, target: tuple[type[models.Model], Any] | None = None) -> None:
        super().__init__(output_field=models.BooleanField())
        self.actor = actor
        self.bound_target = target
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
        alias = source.db
        content_types: Iterable[ContentType]
        bound_model: type[models.Model] | None = None
        if self.bound_target is None:
            # Bare pointer models may use an ordinary manager; their source has no REBAC scope.
            if isinstance(source, RebacQuerySet) and self.actor is not None:
                source = require_rebac_queryset(model).with_actor(self.actor).with_action("read")
            elif self.actor is None:
                source = source.none()
            source = source.using(alias)
            if isinstance(source, RebacQuerySet):
                source = source.scoped_for_aggregate()
            content_types = ContentType.objects.db_manager(alias).filter(
                pk__in=source.order_by().values_list(reference.ct_field, flat=True),
            ).order_by("app_label", "model")
        elif self.actor is None:
            content_types = ()
        else:
            target_model, _ = self.bound_target
            bound_model = target_model._meta.concrete_model or target_model
            content_types = ContentType.objects.db_manager(alias).filter(
                app_label=bound_model._meta.app_label, model=bound_model._meta.model_name,
            )
        targets = []
        for content_type in content_types:
            resolved_target_model = bound_model or content_type.model_class()
            if resolved_target_model is None or not model_resource_type(resolved_target_model):
                continue
            queryset = (
                require_rebac_queryset(resolved_target_model).using(alias)
                .with_actor(self.actor).with_action("read")
            )
            if self.bound_target is not None:
                queryset = queryset.filter(pk=self.bound_target[1])
            keys = queryset.order_by().annotate(_angee_reference_key=Cast("pk", output_field=key_field))
            model_name = resolved_target_model._meta.model_name
            if model_name is None:
                raise ImproperlyConfigured(
                    f"{resolved_target_model._meta.label} has no model name for reference access."
                )
            targets.append((
                content_type.pk,
                resolved_target_model._meta.app_label,
                model_name,
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


def _record_reference_display_hint(info: Info | None) -> models.Value:
    """Leave display-only target admission to the bounded page proof."""
    del info
    return models.Value(None, output_field=models.BooleanField(null=True))


RECORD_REFERENCE_ANNOTATIONS = {"_angee_record_readable": _record_reference_display_hint}


def record_reference_field(
    model: type[models.Model], *, only: Sequence[str] = (), label: bool = False, **kwargs: Any,
) -> Any:
    """Declare a display reference with its model-owned pointer columns selected."""
    reference = cast(type[ProtectedRecordRefMixin], model).record_ref_field()
    field = strawberry_django.field(
        only=[*only, reference.ct_field_attname, reference.fk_field],
        annotate=RECORD_REFERENCE_ANNOTATIONS,
        **kwargs,
    )
    field._angee_reference_label = label
    return field


def _record_reference_memo(
    request: HttpRequest | None,
) -> dict[tuple[Any, str, type[models.Model], str], dict[Any, models.Model | bool]] | None:
    if not isinstance(request, HttpRequest):
        return None
    memo: dict[tuple[Any, str, type[models.Model], str], dict[Any, models.Model | bool]] | None = getattr(
        request,
        "_iam_record_reference_memo",
        None,
    )
    if memo is None:
        memo = {}
        setattr(request, "_iam_record_reference_memo", memo)
    return memo


def prove_record_references(
    rows: Sequence[ProtectedRecordRefMixin], *, request: HttpRequest | None = None,
    label_models: frozenset[type[models.Model]] = frozenset(),
) -> None:
    """Prove referenced keys, loading label columns with the same admitted target read."""
    if not rows:
        return
    actor = current_actor()
    pointers: dict[tuple[str, int], set[Any]] = {}
    labeled_pointers: set[tuple[str, int]] = set()
    row_pointers: list[tuple[ProtectedRecordRefMixin, str, int | None, Any]] = []
    for row in rows:
        reference = type(row).record_ref_field()
        alias = row._state.db or "default"
        content_type_id = getattr(row, reference.ct_field_attname)
        object_id = getattr(row, reference.fk_field)
        row_pointers.append((row, alias, content_type_id, object_id))
        if content_type_id is not None and object_id is not None:
            pointers.setdefault((alias, content_type_id), set()).add(object_id)
            if type(row) in label_models:
                labeled_pointers.add((alias, content_type_id))
    memo = _record_reference_memo(request)
    visible: dict[tuple[str, int], dict[Any, models.Model | bool]] = {}
    with schema_operation_scope():
        for (alias, content_type_id), ids in pointers.items():
            try:
                target_model = ContentType.objects.db_manager(alias).get_for_id(content_type_id).model_class()
            except ContentType.DoesNotExist:
                continue
            if target_model is None or not model_resource_type(target_model):
                continue
            key = (actor, alias, target_model, "read")
            decisions = memo.setdefault(key, {}) if memo is not None else {}
            labels = (alias, content_type_id) in labeled_pointers
            missing = {
                identity for identity in ids
                if identity not in decisions or (labels and decisions[identity] is True)
            }
            if missing:
                pk_field = target_model._meta.pk
                assert pk_field is not None
                admitted: dict[Any, models.Model | bool] = {}
                if actor is not None:
                    queryset = (
                        require_rebac_queryset(target_model).using(alias)
                        .with_actor(actor).with_action("read").filter(pk__in=missing)
                    )
                    if labels:
                        only, related = display_name_paths(cast(type[AngeeModel], target_model))
                        prefetches = []
                        for path in related:
                            field = cast(models.ForeignKey, target_model._meta.get_field(path))
                            related_model = field.related_model
                            assert related_model is not None
                            related_rows = (
                                require_rebac_queryset(related_model).using(alias)
                                .with_actor(actor).with_action("read")
                                if issubclass(related_model, ProtectedModel)
                                else related_model._default_manager.using(alias).all()
                            )
                            prefetches.append(models.Prefetch(
                                path, queryset=related_rows,
                                to_attr=f"_angee_display_{path}",
                            ))
                        admitted = {
                            target.pk: target for target in queryset.only(*only).prefetch_related(*prefetches)
                        }
                    else:
                        admitted = {identity: True for identity in queryset.values_list("pk", flat=True)}
                decisions.update({identity: admitted.get(pk_field.to_python(identity), False) for identity in missing})
            visible[alias, content_type_id] = decisions
    for row, alias, content_type_id, object_id in row_pointers:
        target = (
            visible.get((alias, content_type_id), {}).get(object_id, False)
            if content_type_id is not None else False
        )
        setattr(
            row,
            "_angee_record_readable",
            bool(target),
        )
        row.__dict__["_angee_record_target"] = target if isinstance(target, models.Model) else None
        setattr(row, "_angee_record_readable_actor", actor)


class RecordReferencePageProof(FieldExtension):
    """Materialize one native Hasura page before resolving its reference fields."""

    def __init__(self, node: type) -> None:
        self.node = node

    def _materialize(self, result: Any, info: Info) -> Any:
        selected, label_models = _selected_record_reference(info.selected_fields, self.node, info)
        if not selected:
            return result
        rows = list(result)
        references: list[ProtectedRecordRefMixin] = []
        seen: set[int] = set()
        pending = list(rows)
        while pending:
            row = pending.pop()
            if not isinstance(row, models.Model) or id(row) in seen:
                continue
            seen.add(id(row))
            if isinstance(row, ProtectedRecordRefMixin):
                references.append(row)
            pending.extend(value for value in row._state.fields_cache.values() if isinstance(value, models.Model))
            for cached in getattr(row, "_prefetched_objects_cache", {}).values():
                pending.extend(cached)
        prove_record_references(references, request=get_request(info), label_models=frozenset(label_models))
        return rows

    def resolve(self, next_: Callable[..., Any], source: Any, info: Info, **kwargs: Any) -> Any:
        result = next_(source, info, **kwargs)
        if isawaitable(result):
            return self._resolve_awaitable(result, info)
        return self._materialize(result, info)

    async def _resolve_awaitable(self, result: Awaitable[Any], info: Info) -> Any:
        return await sync_to_async(self._materialize)(await result, info)

    async def resolve_async(
        self, next_: Callable[..., Awaitable[Any]], source: Any, info: Info, **kwargs: Any,
    ) -> Any:
        result = await next_(source, info, **kwargs)
        return await sync_to_async(self._materialize)(result, info)


def _selected_record_reference(
    selections: Sequence[Selection], node: Any, info: Info,
) -> tuple[bool, set[type[models.Model]]]:
    definition = get_object_definition(node, strict=False)
    if definition is None:
        return False, set()
    selected = False
    label_models: set[type[models.Model]] = set()
    for selection in selections:
        field = (
            info.schema.get_field_for_type(selection.name, definition.name)
            if isinstance(selection, SelectedField) else None
        )
        if (
            isinstance(field, StrawberryDjangoField)
            and field.store.annotate.get("_angee_record_readable") is _record_reference_display_hint
        ):
            selected = True
            if getattr(field, "_angee_reference_label", False):
                origin = getattr(field, "origin_django_type", None)
                if origin is not None:
                    label_models.add(origin.model)
        target = field.type if field is not None else node
        while isinstance(target, StrawberryContainer):
            target = target.of_type
        if selection.selections:
            nested, nested_labels = _selected_record_reference(selection.selections, target, info)
            selected |= nested
            label_models.update(nested_labels)
    return selected, label_models


def with_record_reference_access(
    queryset: models.QuerySet[Any],
    *,
    select: bool = True,
    target: tuple[type[models.Model], Any] | None = None,
) -> models.QuerySet[Any]:
    """Declare current target privacy, selecting it for explicit Python consumers.

    Record filters use an alias; display-only selected fields use the bounded
    page proof instead. Bind the actor before declaration;
    reapplying this helper after changing actor refreshes the captured projection.
    A target supplied by a positive model-and-ID filter bounds the proof to that row.
    """
    annotation = {"_angee_record_readable": _RecordReferenceAccess(actor=relation_actor(queryset), target=target)}
    return queryset.annotate(**annotation) if select else queryset.alias(**annotation)


@strawberry.type
class RecordReferenceNode(ProtectedNode):
    """Project a generic reference only while its current target is readable.

    Selected reference fields request a cheap marker; the list page owner
    proves its targets after rows are known. Other rows use the request memo
    and a bounded single-target fallback.
    """

    @classmethod
    def get_queryset(cls, queryset: models.QuerySet[Any], info: Info) -> models.QuerySet[Any]:
        return queryset

    def reference_model(self, info: Info) -> str | None:
        row = cast(Any, self)
        return (row.record_model_label or None) if _record_readable(row, info) else None

    def reference_id(self, info: Info) -> PublicID | None:
        row = cast(Any, self)
        return optional_public_id(row.record_public_id or None) if _record_readable(row, info) else None

    def reference_label(self, info: Info) -> str | None:
        """Project the readable target's model-owned label from the selected reference page."""
        row = cast(Any, self)
        if not _record_readable(row, info):
            return None
        target = row.__dict__.get("_angee_record_target")
        if target is None:
            prove_record_references([row], request=get_request(info), label_models=frozenset({type(row)}))
            target = row.__dict__.get("_angee_record_target")
        return node_display_name(target) if target is not None else None


def _record_readable(row: Any, info: Info | None = None) -> bool:
    """Return the row's batched target-read annotation, computing it once when absent."""

    actor = current_actor()
    if (
        row.__dict__.get("_angee_record_readable") is None
        or row.__dict__.get("_angee_record_readable_actor", actor) != actor
    ):
        if info is not None:
            prove_record_references([row], request=get_request(info))
            return bool(row._angee_record_readable)
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
    Relation guards use REBAC's projection scope: ambient system scope bypasses,
    while a missing actor fails closed.
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
        related_queryset = require_rebac_queryset(related_model)
        if actor is not None:
            related_queryset = related_queryset.with_actor(actor)
        related_queryset = related_queryset.with_action("read")

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


def actor_scoped_to_one(
    field_name: str,
    *,
    prepare: Callable[[models.QuerySet[Any], strawberry.Info], models.QuerySet[Any]] | None = None,
) -> Any:
    """Return a nullable forward/reverse to-one field redacting unreadable targets.

    The native prefetch carries the selected target's optimized queryset;
    REBAC scopes and actor-stamps it. Readable parents hit the cache;
    unreadable parents cache as ``None``.
    Unprefetched roots fall back to one actor-scoped lookup per row. The parent
    may be actor-scoped or sudo-loaded; cached targets are reused only for the
    current actor, and ``only`` keeps the parent projection to the FK id. The
    target prefetch retains the selected type's nested optimizer hints. A
    ``prepare`` callback may add the target owner's selected page batch.
    """

    return _guarded_to_one_field(field_name, _actor_scoped_to_one_resolver(field_name), prepare=prepare)


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


def _guarded_to_one_field(
    field_name: str,
    resolver: Callable[[models.Model], Any],
    *,
    prepare: Callable[[models.QuerySet[Any], strawberry.Info], models.QuerySet[Any]] | None = None,
) -> Any:
    """Bind a relation projection to the native hint scoped by the REBAC optimizer."""

    return strawberry_django.field(
        resolver=resolver,
        field_name=field_name,
        only=[field_name],
        prefetch_related=[_guarded_relation_prefetch(field_name, prepare)],
    )


def _guarded_relation_prefetch(
    field_name: str,
    prepare: Callable[[models.QuerySet[Any], strawberry.Info], models.QuerySet[Any]] | None = None,
) -> Callable[[strawberry.Info], models.Prefetch]:
    """Apply selected node annotations and nested hints alongside the join key.

    The native optimizer resolves ``permissions_field`` annotation callbacks on
    this related queryset, as it does on roots, before any row is materialized.
    Only a hook-preserved actor/read scope can reuse that read proof.
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
        # REBAC exposes actor and sudo pins, but no public action accessor.
        # The exact stored action is needed before replacing a selected read proof.
        read_scoped = (
            actor is not None
            and isinstance(queryset, RebacQuerySet)
            and queryset.actor() == actor
            and not queryset.is_sudo()
            and queryset._rebac_action == "read"
        )
        assert field.origin_django_type is not None
        relation = field.origin_django_type.model._meta.get_field(field_name)
        store = OptimizerStore()
        if isinstance(relation, models.ManyToOneRel):
            store.only.append(relation.field.attname)
        elif isinstance(relation, models.ForeignKey):
            store.only.append(relation.target_field.attname)
        optimized = optimize(queryset, info, store=store)
        if prepare is not None:
            optimized = prepare(optimized, info)
        if read_scoped and actor is not None:
            optimized = _share_permission_projections(_proven_read_annotation(optimized), actor)
        return models.Prefetch(field_name, queryset=optimized)

    return prefetch


def actor_scoped_to_many(
    field_name: str,
    *,
    prepare: Callable[[models.QuerySet[Any], strawberry.Info], models.QuerySet[Any]] | None = None,
) -> Any:
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
        prefetch_related=[_guarded_relation_prefetch(field_name, prepare)],
    )
