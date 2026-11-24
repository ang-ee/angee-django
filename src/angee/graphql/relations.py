"""Guard-aware relation fields for Strawberry-Django schemas.

SQL guards resolve scopes lazily at compilation throughout this module. This
keeps declarations and optimizer probes query-free, and avoids copying complete
permission graphs for unused aliases. A used record-reference read guard resolves
its represented content types in one bounded statement per compile, counted by
statement budgets, as Django's GenericRelation resolves content types while
compiling. Permission scopes may also read their witnesses. Neither these reads
nor their answers are cached across compilations.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Self, cast

import strawberry
import strawberry_django
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from django.db.models.expressions import Combinable
from django.db.models.fields.reverse_related import ForeignObjectRel
from django.db.models.functions import Cast
from django.db.models.lookups import Exact, In
from rebac import current_actor, display
from rebac.graphql.strawberry_django import optimize
from rebac.managers import RebacQuerySet
from rebac.resources import model_resource_type
from strawberry import Info
from strawberry.types import get_object_definition
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.optimizer import OptimizerStore
from strawberry_django.queryset import run_type_get_queryset
from strawberry_django.utils.typing import get_django_definition

from angee.base.models.base import record_display_label
from angee.base.models.refs import RecordRefMixin
from angee.base.permissions import display_rows_requested, read_arms, relation_read_queryset
from angee.base.prefetch import prefetched, prefetched_objects
from angee.base.refs import RecordRef
from angee.graphql.ids import PublicID, optional_public_id, require_public_id, to_public_id
from angee.graphql.introspection import FieldPathError, fields_for_path, is_to_one_relation
from angee.graphql.node import AngeeNode
from graphql import (
    BREAK,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLInterfaceType,
    GraphQLObjectType,
    GraphQLSchema,
    InlineFragmentNode,
    Node,
    ObjectFieldNode,
    SelectionSetNode,
    TypeInfo,
    TypeInfoVisitor,
    Visitor,
    get_named_type,
    visit,
)

if TYPE_CHECKING:
    from angee.graphql.schema import GraphQLSchemas


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
_NODE_RESOLVERS = {
    field.python_name: field.base_resolver.wrapped_func
    for field in get_object_definition(AngeeNode, strict=True).fields
    if field.base_resolver is not None
}
# A node's public id reads the row's key, and its redaction ledger the row's own marks.
_NODE_ID = _NODE_RESOLVERS["id"]
_ROW_OWN_RESOLVERS = frozenset({_NODE_ID, _NODE_RESOLVERS["redacted_fields"]})


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
        prefetch_related=[_GuardedRelationPrefetch(field_name)],
    )


class _GuardedRelationPrefetch:
    """A guarded relation field's prefetch hint over its own relation ``field_name``.

    Apply selected node annotations and nested hints alongside the join key. The
    native optimizer resolves ``permissions_field`` annotation callbacks on this
    related queryset, as it does on roots, before any row is materialized.

    Under :func:`~angee.base.permissions.display_rows_requested`, the queryset of a
    forward to-one whose target declares display fields asks REBAC for display rows
    (``for_display()``) when every reader of the relation in the executing document
    reads them alone (:func:`_reads_display_fields_alone`). The prefetch fills
    Django's relation cache either way: REBAC reads the rows by its permission
    statement wherever it cannot deliver display rows, and a delivered display row
    is what the relation then holds for every reader, so nothing reading it through
    its row loads the target.
    """

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name

    def __call__(self, info: strawberry.Info) -> models.Prefetch:
        field_name = self.field_name
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
        queryset = optimize(queryset, info, store=store)
        if (
            isinstance(relation, models.ForeignKey)
            and display_rows_requested()
            and _reads_display_fields_alone(info, relation)
        ):
            queryset = cast(RebacQuerySet[Any], queryset).for_display()
        return models.Prefetch(field_name, queryset=queryset)


def _reads_display_fields_alone(info: strawberry.Info, relation: models.ForeignKey[Any, Any]) -> bool:
    """Whether every reader of ``relation`` in the executing document reads its target's display fields alone.

    Decided for the whole document (every operation path and fragment, with
    ``@include`` and ``@skip`` disregarded), so every prefetch of the relation in
    it decides alike, and because rows of one model resolved at several paths can
    be one instance: Django sets a reverse relation's rows' foreign key to their
    parent row. All of:

    - the relation names its target's primary key, is not a parent link, and the
      target declares display fields its policy does not gate (``rebac.display.fields``);
    - every field over the relation selects nothing but ``__typename``, the target's
      ``id`` and stored columns that are display fields (:func:`_selects_display_fields`);
    - every other field the document selects on a row of the relation's model, or of
      a parent or child model, reads nothing through the relation (:func:`_reads_through`);
    - no argument, literal or variable, names the relation: a filter or an ordering
      on its target keeps the relation's scoped read.
    """

    target = cast(type[models.Model], relation.related_model)
    if not relation.target_field.primary_key or relation.remote_field.parent_link:
        return False
    shown = display.fields(target)
    if not shown:
        return False
    raw = info._raw_info
    holders = _model_type_names(raw.schema, cast(type[models.Model], relation.model))
    names = {relation.name}
    alone = True

    class _Readers(Visitor):
        def enter_field(self, node: FieldNode, *_args: object) -> Any:
            nonlocal alone
            parent = type_info.get_parent_type()
            if not isinstance(parent, GraphQLObjectType | GraphQLInterfaceType) or parent.name not in holders:
                return None
            field = parent.fields.get(node.name.value)
            if field is None:
                return None
            source = (field.extensions or {}).get("strawberry-definition")
            reading = _reads_through(source, relation)
            if reading == "label":
                names.add(node.name.value)
                target_type = get_named_type(field.type)
                if isinstance(target_type, GraphQLObjectType) and _selects_display_fields(
                    node.selection_set, target_type, target, shown, raw.fragments,
                ):
                    return None
            elif reading == "none":
                return None
            alone = False
            return BREAK

    for definition in (raw.operation, *raw.fragments.values()):
        type_info = TypeInfo(raw.schema)
        visit(definition, TypeInfoVisitor(type_info, _Readers()))
        if not alone:
            return False
    return not _names_relation((raw.operation, *raw.fragments.values()), info.variable_values, names)


def _reads_through(source: object, relation: models.ForeignKey[Any, Any]) -> str:
    """How a field selected on a row of ``relation``'s model reads ``relation`` through the row.

    ``"label"``: a field over the relation built by :func:`actor_scoped_to_one`,
    whose selection decides. ``"none"``: a field that reads no relation through
    its row, or another one: ``__typename``, the node's ``id`` and redaction
    ledger, a stored column, a field over another relation, or a resolver that
    declares what it reads through native optimizer hints, none naming the
    relation. ``"row"``: any other field, which may read the relation through the
    row: a public id or another field named for the relation, a resolver that
    declares nothing or builds a prefetch at execution, or a field that is not
    strawberry-django's.
    """

    if source is None:
        return "none"
    resolver = getattr(source, "base_resolver", None)
    if resolver is not None and resolver.wrapped_func in _ROW_OWN_RESOLVERS:
        return "none"
    if not isinstance(source, StrawberryDjangoField):
        return "row"
    store = source.store
    guarded = [hint.field_name for hint in store.prefetch_related if isinstance(hint, _GuardedRelationPrefetch)]
    if guarded:
        if relation.name not in guarded:
            return "none"
        # A public id projects the target's key without a model projection; it is not a label read.
        return "label" if source.django_model is not None else "row"
    if str(source.django_name) == relation.name:
        return "row"
    if resolver is None and (source.is_relation or _stored_column(source, cast(type[models.Model], relation.model))):
        return "none"
    if not store:
        return "row"

    def names(path: str) -> bool:
        return path in (relation.name, relation.attname) or path.startswith(f"{relation.name}__")

    paths = [*store.only, *store.select_related]
    for hint in store.prefetch_related:
        if isinstance(hint, str):
            paths.append(hint)
        elif isinstance(hint, models.Prefetch):
            paths.append(hint.prefetch_through)
        else:
            # A prefetch built at execution is not read here.
            return "row"
    return "row" if any(names(path) for path in paths) else "none"


def _selects_display_fields(
    selection_set: SelectionSetNode | None,
    graphql_type: GraphQLObjectType,
    model: type[models.Model],
    shown: frozenset[str],
    fragments: dict[str, FragmentDefinitionNode],
) -> bool:
    """Whether ``selection_set`` selects only ``__typename``, ``id`` and stored columns of ``model`` among ``shown``.

    Fragments and inline fragments are opened; one on another type than
    ``graphql_type`` or its interfaces, or anything else selected, is not within.
    """

    if selection_set is None:
        return False
    conditions = {graphql_type.name, *(interface.name for interface in graphql_type.interfaces)}
    for selection in selection_set.selections:
        if isinstance(selection, FieldNode):
            if selection.name.value == "__typename":
                continue
            field = graphql_type.fields.get(selection.name.value)
            source = (field.extensions or {}).get("strawberry-definition") if field is not None else None
            resolver = getattr(source, "base_resolver", None)
            if selection.selection_set is None and (
                (resolver is not None and resolver.wrapped_func is _NODE_ID)
                or _stored_column(source, model) in shown
            ):
                continue
            return False
        fragment = fragments.get(selection.name.value) if isinstance(selection, FragmentSpreadNode) else selection
        if not isinstance(fragment, FragmentDefinitionNode | InlineFragmentNode) or (
            fragment.type_condition is not None and fragment.type_condition.name.value not in conditions
        ):
            return False
        if not _selects_display_fields(fragment.selection_set, graphql_type, model, shown, fragments):
            return False
    return True


def _stored_column(source: object, model: type[models.Model]) -> str | None:
    """The column of ``model`` a strawberry-django field projects as stored: no resolver, hint or relation."""

    if not isinstance(source, StrawberryDjangoField) or source.base_resolver is not None:
        return None
    if source.is_relation or source.store:
        return None
    try:
        field = model._meta.get_field(str(source.django_name))
    except FieldDoesNotExist:
        return None
    if not isinstance(field, models.Field) or field.is_relation or field not in model._meta.concrete_fields:
        return None
    return field.name


def _names_relation(definitions: tuple[Node, ...], variables: Mapping[str, Any], names: set[str]) -> bool:
    """Whether an argument in ``definitions``, as written or through a variable, has an input field in ``names``."""

    found = False

    class _Inputs(Visitor):
        def enter_object_field(self, node: ObjectFieldNode, *_args: object) -> Any:
            nonlocal found
            if node.name.value in names:
                found = True
                return BREAK
            return None

    for definition in definitions:
        visit(definition, _Inputs())
    pending: list[Any] = list(variables.values())
    while pending and not found:
        value = pending.pop()
        if isinstance(value, dict):
            found = any(key in names for key in value)
            pending.extend(value.values())
        elif isinstance(value, list | tuple):
            pending.extend(value)
    return found


@lru_cache(maxsize=64)
def _model_type_names(schema: GraphQLSchema, model: type[models.Model]) -> frozenset[str]:
    """The object types of ``schema`` whose rows are ``model``'s, or a parent or child model's, and their interfaces."""

    names: set[str] = set()
    for graphql_type in schema.type_map.values():
        rows = _rows_model(graphql_type)
        if isinstance(graphql_type, GraphQLObjectType) and rows is not None and (
            issubclass(rows, model) or issubclass(model, rows)
        ):
            names.add(graphql_type.name)
            names.update(interface.name for interface in graphql_type.interfaces)
    return frozenset(names)


def _rows_model(graphql_type: object) -> type[models.Model] | None:
    """The model whose rows a graphql-core object type resolves, from its optional Strawberry source definition."""

    if not isinstance(graphql_type, GraphQLObjectType):
        return None
    origin = getattr((graphql_type.extensions or {}).get("strawberry-definition"), "origin", None)
    definition = get_django_definition(origin) if isinstance(origin, type) else None
    return definition.model if definition is not None else None


@dataclass(frozen=True, slots=True)
class DisplayAudience:
    """A GraphQL field that can show a model's display fields, and who reads the rows that hold its foreign key."""

    schema: str
    type_name: str
    field: str
    holder: str
    """The model whose rows hold the foreign key, as ``app_label.Model``."""
    kind: str
    """``prefetch``: built by :func:`actor_scoped_to_one`, whose prefetch asks for display rows where every
    reader of the relation in the document reads them alone. ``plain``: a strawberry-django relation field,
    which REBAC joins and shows display rows through while ``REBAC_DISPLAY_FIELDS`` is on
    (``angee.graphql.E006`` refuses one). ``other``: any other field named for the relation."""
    readers: tuple[str, ...]
    """Who reads the holder: the arms of its read (:func:`~angee.base.permissions.read_arms`)."""


def display_audience(model: type[models.Model], schemas: GraphQLSchemas | None = None) -> tuple[DisplayAudience, ...]:
    """Every field of every named schema named for a foreign key (or one-to-one) to ``model``'s primary key.

    The fields of the types whose rows hold such a relation, whatever ``model``
    declares: every field that can show its display fields is among them, with the
    readers of its holder, who see them. ``manage.py display_audience`` prints it.
    """

    from angee.graphql.schema import GraphQLSchemas

    schemas = schemas if schemas is not None else GraphQLSchemas.from_discovery()
    found: list[DisplayAudience] = []
    for name in schemas.names():
        core = schemas.graphql_schema(name)
        for type_name in sorted(core.type_map):
            graphql_type = core.type_map[type_name]
            holder = _rows_model(graphql_type)
            if holder is None:
                continue
            for wire, graphql_field in cast(GraphQLObjectType, graphql_type).fields.items():
                source = (graphql_field.extensions or {}).get("strawberry-definition")
                if not isinstance(source, StrawberryDjangoField):
                    continue
                try:
                    relation = holder._meta.get_field(str(source.django_name))
                except FieldDoesNotExist:
                    continue
                if not (
                    isinstance(relation, models.ForeignKey)
                    and relation.related_model is model
                    and relation.target_field.primary_key
                    and not relation.remote_field.parent_link
                ):
                    continue
                if _reads_through(source, relation) == "label":
                    kind = "prefetch"
                elif source.base_resolver is None and source.is_relation:
                    kind = "plain"
                else:
                    kind = "other"
                found.append(DisplayAudience(name, type_name, wire, holder._meta.label, kind, read_arms(holder)))
    return tuple(found)


def actor_scoped_to_many(field_name: str) -> Any:
    """Return a to-many field whose rows are scoped to the current actor.

    The parent may be actor-scoped through one relation while the selected
    to-many relation contains other protected rows. Resolve the relation through
    the target model's actor-scoped queryset instead of exposing the raw related
    manager. Both cached and queried rows retain their model's native ordering.
    Without an actor, the native related queryset resolves ambient scope.
    """

    return strawberry_django.field(
        resolver=_actor_scoped_to_many_resolver(field_name),
        field_name=field_name,
        prefetch_related=[_GuardedRelationPrefetch(field_name)],
    )


def actor_scoped_public_ids(field_name: str) -> Any:
    """Return a to-many relation as the public ids of the rows the reader may read.

    The many-valued counterpart of :func:`actor_scoped_public_id`: it shares
    :func:`actor_scoped_to_many`'s guarded prefetch, prefetched-rows check and
    per-row scoped fallback, and projects only each readable row's public id, the
    value a console form's relation picker edits through the resource's write
    backend.
    """

    resolve = _actor_scoped_to_many_resolver(field_name)

    def resolve_public_ids(root: models.Model) -> list[PublicID]:
        related_model = cast(type[models.Model], root._meta.get_field(field_name).related_model)
        return [require_public_id(related_model, row.pk) for row in resolve(root)]

    return strawberry_django.field(
        resolver=resolve_public_ids,
        field_name=field_name,
        prefetch_related=[_GuardedRelationPrefetch(field_name)],
    )


def _actor_scoped_to_many_resolver(field_name: str) -> Callable[[models.Model], Any]:
    """Build the shared to-many resolver: actor-stamped prefetched rows, else a scoped read."""

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

    return resolve
