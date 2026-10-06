"""Angee metadata bridge for ``strawberry-django-hasura`` resources."""

from __future__ import annotations

import dataclasses
import types as _types
from collections.abc import Awaitable, Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache, partial
from typing import Any, cast

import strawberry
import strawberry_django
from asgiref.sync import sync_to_async
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.db.models.expressions import Combinable, CombinedExpression
from django.db.models.functions import Cast, Concat
from django.db.models.lookups import Exact, In
from rebac import PermissionDenied, current_actor, system_context
from rebac.relation_loading import relation_actor
from rebac.resources import model_resource_type
from strawberry.extensions import FieldExtension
from strawberry.types import get_object_definition
from strawberry_django.fields.types import field_type_map
from strawberry_django.mutations import resolvers as mutation_resolvers
from strawberry_django_aggregates import (
    default_operators_for,
    group_by_alias,
    group_by_enum_member,
    group_by_range_alias,
)
from strawberry_django_aggregates.granularity import NumberGranularity, TimeGranularity
from strawberry_django_hasura import (
    HasuraResource,
    NestedInsert,
    SortAlias,
    WriteBackend,
    input_to_dict,
)
from strawberry_django_hasura import (
    hasura_resource as build_hasura_resource,
)
from strawberry_django_hasura.filtering import where_to_q
from strawberry_django_hasura.inputs import comparison_for_python_type

from angee.base.identity import (
    instance_from_public_id,
    instances_from_public_ids,
    public_data_id_field,
    public_id_for,
)
from angee.base.impl import resolve_hooks
from angee.base.mixins import CreationKeyMixin, OptimisticLockMixin, RowLockMixin
from angee.base.refs import RecordRefMixin
from angee.base.scoping import (
    aggregate_scoped_queryset,
    lock_if_supported,
    read_scoped_queryset,
    system_queryset,
)
from angee.data.field_classification import (
    is_to_one_relation,
)
from angee.data.metadata import (
    DataAggregateMeasureMetadata,
    DataLinesMetadata,
    DataQueryAxis,
    DataQueryDrill,
    DataQueryExtraction,
    DataQueryServerAxis,
    DataResourceFieldMetadata,
    DataResourceRoots,
    DataResourceSubtitleMetadata,
)
from angee.graphql.access import assert_no_gated_read_fields
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.lookups import resource_filter_lookups
from angee.graphql.data.metadata import (
    DataResourceContribution,
    DataResourcePolicy,
    attach_data_resource_contribution,
    relation_group_by_fields,
    resource_type_name,
    resource_wire_field_name,
    resource_wire_field_names,
)
from angee.graphql.data.resource_fields import (
    final_input_extension_fields,
    final_input_only_resource_fields,
    final_input_wire_fields,
    final_required_input_wire_fields,
    final_resource_fields,
)
from angee.graphql.deletion import delete_by_public_id
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.introspection import (
    FieldPathError,
    django_model,
    require_field_for_path,
)
from angee.graphql.relations import RecordReferenceNode, actor_scoped_relation_expression, with_record_reference_access
from angee.graphql.writes import write_queryset


@dataclass(frozen=True)
class HasuraLines:
    """A declared editable child-lines relation for a document resource (F6).

    A resource passes ``lines=HasuraLines(field="lines", model=OrderLine)`` to
    :func:`hasura_model_resource` to gain (a) Hasura-native nested inserts
    (``insert_<res>_one(object: {..., lines: {data: [...]}})``, riding the
    ``strawberry-django-hasura`` nested-insert shape) and (b) an authored
    ``<res>_save(pk, patch, lines)`` mutation that diff-applies children
    (create/update/delete by public id) and patches the parent in one
    transaction, REBAC-checked on the parent (children ride the §3.4 elevation
    after that preflight).

    ``field`` is the parent's reverse-FK accessor to the child rows; the child's
    FK back to the parent is derived from it and set by the write, never asked
    for on the wire. ``writable`` overrides the child's editable-column allowlist;
    ``public_id_fields`` names the child relation columns exposed as public ids
    (decoded on write). ``node`` is the child GraphQL node, used only to name the
    child field metadata the frontend line cells render. ``defaults`` seeds scalar
    cells on a newly added row and may name only writable child fields.
    ``position_field`` names the integer order column (advertised so the composer
    maintains it).

    Lines are owned parts: rows that exist only as part of their parent and are
    saved with it. Their writes are the user's writes, elevated only because the
    parent write authorizes them, so they never bypass the child's own guards. A
    child composing :class:`~angee.base.mixins.RowLockMixin` has each row checked
    against its persisted lock before the elevated write: a line may not change a
    locked field, delete a system row, or create or turn into one. Its node must
    project the same fact as ``locked_fields``, which the metadata advertises as
    the lines' ``lock_field`` so the composer keeps those rows in place.

    Completeness contract: ``<res>_save(lines=…)`` takes the **full desired child
    set** — deletion is by omission, so an id absent from the set is deleted. The
    caller must therefore send back every stored line (each kept row carrying its
    public id); a partial read that omits stored lines would ask to delete them.
    The write enforces the enforceable half of this contract server-side: every
    public id the caller sends must address a currently stored line of this parent
    (a stale, foreign, or truncated baseline is rejected wholesale with a
    ``ValidationError`` rather than silently mis-applied). The full desired set is
    resolved under a parent-row lock so concurrent saves cannot cross-delete each
    other's lines. A nested insert only inserts: children the parent's own save
    provisioned were never part of the caller's set and are kept.
    """

    field: str
    model: type[models.Model]
    node: type | None = None
    writable: Sequence[str] | None = None
    public_id_fields: Sequence[str] = ()
    position_field: str = "position"
    defaults: Mapping[str, str | int | float | bool | None] = dataclasses.field(default_factory=dict)


def _child_back_fk(parent_model: type[models.Model], relation: str) -> str:
    """Return the child FK field name behind a parent's to-many ``relation``."""

    reverse = parent_model._meta.get_field(relation)
    field = getattr(reverse, "field", None)
    if field is None:
        raise ImproperlyConfigured(f"{parent_model._meta.label}.{relation} is not a to-many child relation.")
    return field.name


class AngeeHasuraWriteBackend:
    """Authorized write backend for Angee Hasura resources.

    ``strawberry-django-hasura`` owns the Hasura mutation envelope. This class
    owns the Angee write semantics inside that envelope: Django validation,
    REBAC row-scoped write targets, model save/delete signals, and returning a
    deleted instance in Hasura's ``delete_<res>_by_pk`` shape.

    Values for input-extension fields that are not model fields never reach
    strawberry-django, which would ignore them: each write persists the row,
    then hands them to the row's :meth:`AngeeModel.apply_input_extensions` in
    the same transaction.
    """

    def __init__(
        self,
        model: type[models.Model],
        *,
        public_id_fields: Iterable[str] | None = None,
        lines: HasuraLines | None = None,
    ) -> None:
        self.model = model
        self.public_id_fields = _public_id_field_models(model, public_id_fields or ())
        self.lines = lines
        if lines is not None:
            self._line_back_fk = _child_back_fk(model, lines.field)
            self._line_public_id_fields = _public_id_field_models(lines.model, lines.public_id_fields)
        else:
            self._line_back_fk = ""
            self._line_public_id_fields = {}

    def write_target_queryset(self) -> models.QuerySet[Any]:
        """Return the queryset that resolves this backend's update/delete/save targets.

        Defaults to the model's write-scoped queryset (REBAC row scope kept,
        field-read redaction off). A subclass narrows it to keep rows a surface
        must never reach by pk off the generic update/delete/save mutations — even
        when the row's own REBAC would allow the write. Record-attached chatter,
        isolated to the record-scoped ``record_thread`` surface, is the motivating
        case: its own ``owner``/``admin`` permission would otherwise let a creator
        who lost record access delete the thread through ``delete_<res>_by_pk``.
        """

        return write_queryset(self.model)

    def create(self, info: strawberry.Info, data: dict[str, Any], *, client_creation_key: str | None = None) -> Any:
        """Create one row, its declared nested child lines and input extensions atomically."""

        with transaction.atomic():
            data = dict(data)
            line_rows = self._pop_line_rows(data) if self.lines is not None else None
            extensions = _pop_input_extensions(self.model, data)

            def insert() -> Any:
                instance = self._create_row(info, data)
                if line_rows is not None:
                    self._apply_line_diff(info, instance, line_rows, replace=False)
                if extensions:
                    instance.apply_input_extensions(**extensions)
                return instance

            if client_creation_key is None:
                return insert()
            if not issubclass(self.model, CreationKeyMixin):
                raise ValidationError({"client_creation_key": "This resource does not support creation keys."})
            decoded = self._decode_public_id_fields(data)
            scope = self.model.creation_key_actor_scope(current_actor(), decoded)
            fingerprint = self._creation_fingerprint({**decoded, **extensions}, line_rows)
            scope_field = self.model._meta.get_field(self.model.creation_key_scope)
            data.update({
                scope_field.attname: scope,
                "client_creation_key": client_creation_key,
                "creation_fingerprint": fingerprint,
            })
            if not model_resource_type(self.model) or current_actor() is None:
                raise ImproperlyConfigured("Creation-key resources require an actor-scoped queryset.")
            replays = read_scoped_queryset(self.model, current_actor())
            instance, _created = replays.replay_or_insert(scope, client_creation_key, fingerprint, insert)
            return instance

    def _creation_fingerprint(self, data: dict[str, Any], line_rows: list[dict[str, Any]] | None) -> str:
        """Fingerprint decoded write inputs, including the declared line envelope."""

        content = {"object": data, "lines": self._prepare_line_rows(line_rows) if line_rows is not None else None}
        return self.model.creation_fingerprint_for(content)

    def save(
        self,
        info: strawberry.Info,
        pk: str,
        patch: dict[str, Any],
        line_rows: list[dict[str, Any]] | None,
        *,
        expected_revision: int | None = None,
    ) -> Any:
        """Patch one parent and diff-apply its child lines in one transaction.

        REBAC preflight is on the parent, unconditionally: the row is loaded
        through the write-scoped queryset (field-read redaction off, REBAC row
        scope still evaluating ``read``), so an actor who may read but not write
        the parent still resolves the row — the explicit ``has_access("write")``
        gate below is what denies them. That gate must run even when ``patch`` is
        empty: a lines-only edit (``patch={}``, the FormView shape) skips the
        update resolver's write signal, so without the preflight the §3.4 child
        elevation would run unauthorized. Only after the parent write is verified
        do the children ride the elevation — created, updated, and deleted under
        ``system_context``, authorized by the parent write, not per child row.
        ``line_rows`` is the full desired child set: ``None`` leaves the lines
        untouched (a parent-only save), an empty list clears them.
        """

        if self.lines is None:
            raise ImproperlyConfigured(f"{self.model._meta.label} resource declares no editable lines.")
        with transaction.atomic():
            instance = require_instance_for_id(
                self.model,
                pk,
                queryset=lock_if_supported(self.write_target_queryset()),
            )
            if not instance.has_access("write"):
                raise PermissionDenied(f"Denied: cannot write {self.model._meta.label} {pk!r}")
            if expected_revision is not None:
                instance.require_revision(expected_revision)
            patch = dict(patch)
            extensions = _pop_input_extensions(self.model, patch)
            if patch:
                instance = mutation_resolvers.update(
                    info,
                    instance,
                    self._decode_public_id_fields(patch),
                    key_attr=PUBLIC_ID_FIELD_NAME,
                    full_clean=True,
                )
            elif (line_rows is not None or extensions) and isinstance(instance, OptimisticLockMixin):
                # A document's revision covers its child set and extension values as well as its columns.
                instance.save(update_fields={instance.REVISION_FIELD})
            if line_rows is not None:
                self._apply_line_diff(info, instance, line_rows)
            if extensions:
                instance.apply_input_extensions(**extensions)
            return instance

    def _create_row(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create one row through strawberry-django's resolver and native manager."""

        decoded_data = self._decode_public_id_fields(data)
        return mutation_resolvers.create(
            info,
            self.model,
            decoded_data,
            key_attr=PUBLIC_ID_FIELD_NAME,
            full_clean=True,
        )

    def _pop_line_rows(self, data: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Pop the nested-insert envelope for the lines relation off ``data``."""

        assert self.lines is not None
        envelope = data.pop(self.lines.field, None)
        if envelope is None:
            return None
        rows = envelope.get("data", []) if isinstance(envelope, Mapping) else envelope
        return [dict(row) for row in rows]

    def _apply_line_diff(
        self,
        info: strawberry.Info,
        parent: models.Model,
        rows: list[dict[str, Any]],
        *,
        replace: bool = True,
    ) -> None:
        """Create/update/delete child lines to match ``rows`` under elevation.

        A row with an ``id`` addresses an existing child (update); a row without
        one is a new child (create). With ``replace`` (``<res>_save``) an existing
        child no row keeps is deleted; a nested insert passes ``replace=False``
        and only inserts. The child FK back to the parent is set here, never sent
        by the client.

        Two phases with two authorities. Relation public ids on the incoming
        rows are decoded first, under the **caller's** actor, so a referenced row
        the caller cannot see is rejected (never resolved by the elevation that
        follows). Only then do the child writes run under ``system_context`` —
        the parent write is their gate (§3.4), including for each child's
        input-extension values, applied after its write. The elevation authorizes;
        it does not make the writes system work, so a row-locked child's lock is
        checked before each write (removals before any write). Reached by both
        ``save`` and the nested ``create`` path; the parent row is locked before its
        child set is read so concurrent saves cannot cross-delete each other's lines.
        """

        assert self.lines is not None
        child_model = self.lines.model
        back_fk_id = f"{self._line_back_fk}_id"
        row_locked = issubclass(child_model, RowLockMixin)
        # Phase 1 — decode line relation ids under the caller's actor, before any
        # elevation. Each entry is ``(public id | None, decoded child payload)``.
        prepared = self._prepare_line_rows(rows)
        # Phase 2 — child writes elevated, under a parent-row lock.
        with system_context(reason="graphql.hasura.save.lines"):
            self.model._default_manager.lock_if_supported().filter(pk=parent.pk).first()
            children = child_model._base_manager.filter(**{self._line_back_fk: parent})
            existing = children.in_bulk()
            by_public_id = {child.public_id: child for child in existing.values()}
            unknown = sorted({pid for pid, _ in prepared if pid is not None and pid not in by_public_id})
            if unknown:
                raise ValidationError(
                    f"{child_model._meta.object_name} lines {unknown!r} are not part of "
                    f"{self.model._meta.object_name} {parent.public_id!r}; reload and retry."
                )
            kept_pks = {by_public_id[public_id].pk for public_id, _ in prepared if public_id is not None}
            removed = sorted(set(existing) - kept_pks) if replace else []
            if row_locked:
                for pk in removed:
                    existing[pk].validate_row_delete()
            if removed:
                # Removals free their unique values (a name) for the rows that follow.
                child_model._base_manager.filter(pk__in=removed).delete()
            for public_id, decoded in prepared:
                extensions = _pop_input_extensions(child_model, decoded)
                if public_id is not None:
                    child = by_public_id[public_id]
                    mutation_resolvers.update(
                        info,
                        child,
                        decoded,
                        key_attr=PUBLIC_ID_FIELD_NAME,
                        full_clean=True,
                        pre_save_hook=_row_lock_check(child.row_lock()) if row_locked else None,
                    )
                else:
                    values = {**decoded, back_fk_id: parent.pk}
                    if row_locked:
                        _unsaved_row(child_model, values).validate_row_lock(None)
                    # Created through the manager, so its factory invariants still apply.
                    child = mutation_resolvers.create(
                        info,
                        child_model,
                        values,
                        key_attr=PUBLIC_ID_FIELD_NAME,
                        full_clean=True,
                    )
                if extensions:
                    child.apply_input_extensions(**extensions)

    def _prepare_line_rows(self, rows: list[dict[str, Any]]) -> list[tuple[str | None, dict[str, Any]]]:
        """Decode line payloads under the caller for both fingerprinting and writes."""

        prepared = []
        for row in rows:
            payload = dict(row)
            public_id = payload.pop("id", None)
            prepared.append(
                (
                    str(public_id) if public_id else None,
                    self._decode_public_id_fields(payload, self._line_public_id_fields),
                )
            )
        return prepared

    def update(
        self, info: strawberry.Info, pk: str, data: dict[str, Any], *, expected_revision: int | None = None
    ) -> Any:
        """Patch one public-id-addressed row through the write queryset, then its input extensions."""

        with transaction.atomic():
            instance = require_instance_for_id(
                self.model,
                pk,
                queryset=lock_if_supported(self.write_target_queryset()),
            )
            if expected_revision is not None:
                instance.require_revision(expected_revision)
            data = dict(data)
            extensions = _pop_input_extensions(self.model, data)
            instance = mutation_resolvers.update(
                info,
                instance,
                self._decode_public_id_fields(data),
                key_attr=PUBLIC_ID_FIELD_NAME,
                full_clean=True,
            )
            if extensions:
                instance.apply_input_extensions(**extensions)
            return instance

    def delete(self, info: strawberry.Info, pk: str) -> Any | None:
        """Delete one public-id-addressed row and return the deleted instance."""

        del info

        preview = delete_by_public_id(
            self.model,
            str(pk),
            confirm=True,
            queryset=self.write_target_queryset(),
        )
        preview.require_no_blockers()
        return preview.deleted_instance

    def _decode_public_id_fields(
        self,
        data: dict[str, Any],
        public_id_fields: Mapping[str, type[models.Model]] | None = None,
    ) -> dict[str, Any]:
        """Translate public IDs under the caller into Django-native write values.

        ``public_id_fields`` defaults to the parent's map; a child line write
        passes the child's own map (its owner model resolves the field kind).
        """

        field_models: Mapping[str, type[models.Model]]
        if public_id_fields is None:
            field_models = self.public_id_fields
            owner_model = self.model
        else:
            field_models = public_id_fields
            owner_model = self.lines.model if self.lines is not None else self.model
        out: dict[str, Any] = {}
        for key, value in data.items():
            related_model = field_models.get(key)
            if related_model is None:
                # ImplClassField.key_for/enum_member_for prefer member names;
                # choices writes must prefer stored values when those collide.
                out[key] = _choices_wire_value(owner_model, key, value)
                continue
            try:
                field = owner_model._meta.get_field(key)
            except FieldDoesNotExist:
                field = None
            if getattr(field, "many_to_many", False):
                instances = (
                    tuple(_write_public_instance(related_model, item) for item in value) if value is not None else ()
                )
                out[key] = list(instances) if value is not None else None
                continue
            instance = _write_public_instance(related_model, value)
            out[f"{key}_id"] = None if instance is None else instance.pk
        return out


def _pop_input_extensions(model: type[models.Model], data: dict[str, Any]) -> dict[str, Any]:
    """Pop write values whose input field names no field of ``model``.

    Generated insert/set inputs carry model fields, which strawberry-django
    applies; a composed input extension may add others, which it would ignore.
    The caller hands them to the written row's ``apply_input_extensions``.
    """

    def is_model_field(name: str) -> bool:
        try:
            model._meta.get_field(name)
        except FieldDoesNotExist:
            return False
        return True

    return {name: data.pop(name) for name in [name for name in data if not is_model_field(name)]}


def _row_lock_check(previous: Mapping[str, Any]) -> Callable[[RowLockMixin], None]:
    """Return the pre-save check of an elevated line update against its persisted lock."""

    return lambda instance: instance.validate_row_lock(previous)


def _unsaved_row(model: type[models.Model], values: Mapping[str, Any]) -> Any:
    """Return an unsaved row carrying a new line's concrete column values, for its lock check."""

    columns = {name for field in model._meta.concrete_fields for name in (field.name, field.attname)}
    return model(**{name: value for name, value in values.items() if name in columns})


def _choices_wire_value(owner_model: type[models.Model], name: str, value: Any) -> Any:
    """Translate a read-side enum member name onto the choices value it stores.

    Hasura insert/patch inputs carry choices columns as ``String`` while the
    read surface projects the ``TextChoices`` enum serialized by member name,
    so a console read→write round-trip posts the NAME (``"PYDANTIC"``) where
    the column stores the value (``"pydantic"``). Accept the member name
    alongside the stored value; a string that is neither passes through for
    ``full_clean`` to reject. A value that is itself a valid stored value is
    never remapped, even when it collides with another member's name.
    """

    if not isinstance(value, str):
        return value
    try:
        field = owner_model._meta.get_field(name)
    except FieldDoesNotExist:
        return value
    enum = getattr(field, "choices_enum", None)
    if enum is None:
        return value
    member = enum.__members__.get(value)
    if member is None or value in enum._value2member_map_:
        return value
    return member.value


def public_pk_decoder(model: type[models.Model]) -> Callable[[Any], Any]:
    """Return a decoder from Angee public id to database primary key."""

    return lambda value: _public_pk(model, value)


def _relation_axis_fields(model: type[models.Model], paths: Sequence[str]) -> dict[str, Any]:
    """Resolve declared to-one relation axes."""
    relations = {}
    for path in paths:
        try:
            field = require_field_for_path(model, path)
        except FieldPathError:
            continue
        if is_to_one_relation(field):
            relations[path] = field
    return relations


class _UnresolvedRelationID(models.Value):
    """An identity miss, distinct from an explicit NULL comparison operand."""

    def __init__(self) -> None:
        super().__init__(None, output_field=models.IntegerField())


def _nonmatching_relation_sql(lookup: models.Lookup, compiler: Any, connection: Any) -> tuple[str, list[Any]]:
    """False for a readable key, UNKNOWN for a redacted/missing parent."""

    lhs, params = lookup.process_lhs(compiler, connection)
    return f"{lhs} <> {lhs}", [*params, *params]


class _RelationExact(Exact):
    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, list[Any]]:
        if isinstance(self.rhs, _UnresolvedRelationID):
            return _nonmatching_relation_sql(self, compiler, connection)
        return super().as_sql(compiler, connection)


class _RelationIn(In):
    rhs: Any

    def get_prep_lookup(self) -> Any:
        if self.rhs_is_direct_value():
            self.rhs = [value for value in self.rhs if not isinstance(value, _UnresolvedRelationID)]
        return super().get_prep_lookup()

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, list[Any]]:
        if self.rhs_is_direct_value() and not self.rhs:
            return _nonmatching_relation_sql(self, compiler, connection)
        return super().as_sql(compiler, connection)


def _relation_axis_alias(path: str) -> str:
    """An injective ORM alias without lookup separators or model-field names."""

    return f"_angee_relation_{path.encode().hex()}"


@dataclass(frozen=True)
class _DecodedRelationID:
    value: Any


@dataclass(frozen=True)
class _RelationIDDecoder:
    """Actor-readable public identities prepared once for each input tree."""

    model: type[models.Model]
    decoder: Callable[[Any], Any] | None = None

    def __call__(self, value: _DecodedRelationID) -> Any:
        return value.value

    def resolve(self, values: Iterable[str], actor: Any) -> dict[str, _DecodedRelationID]:
        queryset = read_scoped_queryset(self.model, actor)
        instances = instances_from_public_ids(self.model, values, queryset=queryset)
        return {
            value: _DecodedRelationID(self.decoder(value) if self.decoder is not None else instance.pk)
            for value, instance in instances.items()
        }


class _RelationFilterExtension(FieldExtension):
    """Batch identity operands; leave bool-expression traversal to Hasura.

    Relation comparisons run against the redacted key alias, which is the raw
    key or NULL. Outside ``_not``, an ``_eq``/``_in`` on it implies the same
    match on the raw key column, which the database seeks by index instead of
    evaluating the guarded alias for every row; that match is added. A direct
    relation's operands resolve through its target's read scope, the alias's
    only guard, so there the raw-key match replaces the alias. Under ``_not``
    the alias's UNKNOWN must not become FALSE, so negated comparisons keep only
    the alias.
    """

    def __init__(
        self, decoders: Mapping[str, Callable[[Any], Any]], source: Callable[..., Any],
        filter_type: type, aliases: Mapping[str, str],
    ) -> None:
        self.decoders = {name: decoder for name, decoder in decoders.items() if isinstance(decoder, _RelationIDDecoder)}
        self.source = source
        self.filter_type = filter_type
        self.aliases = aliases
        self.keys = {alias: path for path, alias in aliases.items()}
        self.resolved = {alias for alias, path in self.keys.items() if alias in self.decoders and "__" not in path}
        self.negation = next(
            field.python_name for field in get_object_definition(filter_type, strict=True).fields
            if field.graphql_name == "_not"
        )
        # An internal dataclass adapts field names only, plus each relation's raw
        # key path. The public input and native Hasura's boolean/operator
        # translation remain unchanged.
        self.aliased_type = dataclasses.make_dataclass(
            "RelationFilter", [
                *((aliases.get(field.name, field.name), Any) for field in dataclasses.fields(filter_type)),
                *((path, Any, dataclasses.field(default=strawberry.UNSET)) for path in aliases),
            ],
            kw_only=True,
        )

    def resolve(self, next_: Callable[..., Any], source: Any, info: strawberry.Info, **kwargs: Any) -> Any:
        kwargs["where"] = self.prepare(info, kwargs.get("where"))
        return next_(source, info, **kwargs)

    async def resolve_async(
        self, next_: Callable[..., Awaitable[Any]], source: Any, info: strawberry.Info, **kwargs: Any,
    ) -> Any:
        kwargs["where"] = await sync_to_async(self.prepare)(info, kwargs.get("where"))
        return await next_(source, info, **kwargs)

    def prepare(self, info: strawberry.Info, where: Any) -> Any:
        comparisons: dict[str, list[Any]] = {}
        positive: list[tuple[Any, str, Any]] = []

        def adapt(value: Any, negated: bool = False) -> Any:
            if isinstance(value, list):
                return [adapt(item, negated) for item in value]
            if not dataclasses.is_dataclass(value) or isinstance(value, type):
                return value
            if not isinstance(value, self.filter_type):
                return dataclasses.replace(value, **{
                    field.name: adapt(getattr(value, field.name), negated) for field in dataclasses.fields(value)
                })
            fields = {
                self.aliases.get(field.name, field.name): adapt(
                    getattr(value, field.name), negated or field.name == self.negation,
                )
                for field in dataclasses.fields(value)
            }
            adapted = self.aliased_type(**fields)
            for name, item in fields.items():
                if item is None or item is strawberry.UNSET:
                    continue
                if name in self.decoders:
                    comparisons.setdefault(name, []).append(item)
                if name in self.keys and not negated:
                    positive.append((adapted, name, item))
            return adapted

        prepared = adapt(where)
        if comparisons:
            actor = relation_actor(self.source(info))
            for name, items in comparisons.items():
                operands = [
                    (item, field.name, getattr(item, field.name))
                    for item in items for field in dataclasses.fields(item)
                    if field.name != "is_null" and getattr(item, field.name) not in (None, strawberry.UNSET)
                ]
                values = {
                    str(value) for _, _, operand in operands
                    for value in (operand if isinstance(operand, list) else [operand])
                }
                resolved = self.decoders[name].resolve(values, actor) if values else {}
                missing = _DecodedRelationID(_UnresolvedRelationID())
                for item, attribute, operand in operands:
                    setattr(item, attribute, (
                        [resolved.get(str(value), missing) for value in operand]
                        if isinstance(operand, list) else resolved.get(str(operand), missing)
                    ))
        for adapted, alias, item in positive:
            match, whole = _key_match(item)
            setattr(adapted, self.keys[alias], match)
            if whole and alias in self.resolved:
                setattr(adapted, alias, strawberry.UNSET)
        return prepared


def _key_match(comparison: Any) -> tuple[Any, bool]:
    """Return the raw-key ``_eq``/``_in`` a redacted relation comparison implies.

    The flag says whether that match states the whole comparison. An operand
    that resolved to no readable row matches nothing, so it becomes an empty
    membership; ``UNSET`` means the comparison implies no key match.
    """

    def unresolved(operand: Any) -> bool:
        return isinstance(operand, _DecodedRelationID) and isinstance(operand.value, _UnresolvedRelationID)

    operators = {field.name: getattr(comparison, field.name) for field in dataclasses.fields(comparison)}
    identity = {
        name for name in ("eq", "in_")
        if name in operators and operators[name] is not None and operators[name] is not strawberry.UNSET
    }
    if not identity:
        return strawberry.UNSET, False
    whole = all(name in identity or value is strawberry.UNSET for name, value in operators.items())
    if unresolved(operators.get("eq")):
        match: dict[str, Any] = {"in_": []}
    else:
        match = {name: operators[name] for name in identity}
        if "in_" in match:
            match["in_"] = [operand for operand in match["in_"] if not unresolved(operand)]
    return dataclasses.replace(comparison, **{name: match.get(name, strawberry.UNSET) for name in operators}), whole


def _relation_filter_decoders(
    model: type[models.Model],
    *,
    filterable: Sequence[str],
    declared: Mapping[str, Callable[[Any], Any]] | None,
) -> Mapping[str, Callable[[Any], Any]] | None:
    """Return the filter decoders for filterable public-id relation columns.

    A filterable relation whose related model carries a
    public identity is filtered by that related row's public id — the node
    projects the relation as one — so its ``bool_exp`` operand is a public id,
    never the raw primary key. Each such operand resolves through the related
    model's identity owner through its actor read scope. Caller-declared
    decoders retain their conversion after the same read preflight.
    """

    decoders = dict(declared or {})
    for name, field in _relation_axis_fields(model, filterable).items():
        related = field.related_model
        if public_data_id_field(related) is None:
            continue
        target = getattr(field, "target_field", None)
        if target is not None and not target.primary_key and name not in decoders:
            raise ImproperlyConfigured(
                f"{model._meta.label} filter axis {name!r} targets a non-primary "
                "identity; provide an explicit field_id_decode for that target."
            )
        decoders[name] = _RelationIDDecoder(related, decoder=decoders.get(name))
    return decoders or None


def _public_id_field_models(
    model: type[models.Model],
    fields: Iterable[str],
) -> dict[str, type[models.Model]]:
    """Return related models for public-id write fields declared by name."""

    related: dict[str, type[models.Model]] = {}
    for field_name in fields:
        name = str(field_name)
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(f"{model._meta.label} public id field {name!r} does not exist.") from error
        related_model = getattr(field, "related_model", None)
        if not isinstance(related_model, type) or not issubclass(related_model, models.Model):
            raise ImproperlyConfigured(f"{model._meta.label} public id field {name!r} must be a relation.")
        related[name] = related_model
    return related


def aggregate_queryset(queryset: models.QuerySet[Any]) -> models.QuerySet[Any]:
    """Return the aggregate-safe variant of a REBAC queryset when available.

    REBAC's ``scoped_for_aggregate`` preserves row authorization while disabling
    model field redaction for projection. A plain queryset is returned unchanged.
    User-authored joins retain their normal Django aggregate cardinality.
    """

    return aggregate_scoped_queryset(queryset)


def _model_queryset(
    model: type[models.Model],
) -> Callable[[strawberry.Info], models.QuerySet[Any]]:
    """Return the default unscoped read source for a model resource."""

    def get_queryset(info: strawberry.Info) -> models.QuerySet[Any]:
        del info
        return model.objects.all()

    return get_queryset


def _aggregate_queryset(
    read_queryset: Callable[[strawberry.Info], models.QuerySet[Any]],
) -> Callable[[strawberry.Info], models.QuerySet[Any]]:
    """Return the default aggregate source derived from the read source."""

    def get_aggregate_queryset(info: strawberry.Info) -> models.QuerySet[Any]:
        return aggregate_queryset(read_queryset(info))

    return get_aggregate_queryset


def _relation_scalar_queryset(
    model: type[models.Model],
    source: Callable[[strawberry.Info], models.QuerySet[Any]],
    paths: Iterable[str],
) -> Callable[[strawberry.Info], models.QuerySet[Any]]:
    """Make relation-path filters and ordering see the same redacted SQL values."""

    scalar_paths = []
    for path in sorted(set(paths)):
        if "." in path:
            continue
        try:
            field = require_field_for_path(model, path)
        except FieldPathError:
            continue
        if is_to_one_relation(field) or ("__" in path and not field.is_relation):
            scalar_paths.append(path)
    if not scalar_paths:
        return source

    def get_queryset(info: strawberry.Info) -> models.QuerySet[Any]:
        queryset = source(info)
        expressions = {}
        for path in scalar_paths:
            field = require_field_for_path(model, path)
            expression = actor_scoped_relation_expression(queryset, path)
            if is_to_one_relation(field):
                target = field.target_field if hasattr(field, "target_field") else field.related_model._meta.pk
                # A target PK may itself be an MTI parent link. Django's
                # get_col resolves that chain to its actual scalar field;
                # cloning the link would leave an unbound relation field.
                output = target.get_col(target.model._meta.db_table).output_field.clone()
                output.null = False  # SQL NOT must preserve UNKNOWN for redacted NULLs.
                output.register_lookup(_RelationExact)
                output.register_lookup(_RelationIn)
                expression = models.ExpressionWrapper(expression if expression is not None else models.F(path), output)
            if expression is not None:
                alias = _relation_axis_alias(path) if field.is_relation else path
                expressions[alias] = _scalar_alias_expression(queryset, expression)
        return queryset.alias(**expressions) if expressions else queryset

    return get_queryset


def _group_by_expression_provider(
    info: strawberry.Info,
    queryset: models.QuerySet[Any],
    spec: list[tuple[str, Any]],
) -> Mapping[str, Combinable]:
    """Project selected related scalar axes through actor-scoped guards."""

    del info
    expressions: dict[str, Combinable] = {}
    for path, _granularity in spec:
        if "." in path:
            continue
        expression = actor_scoped_relation_expression(queryset, path)
        if expression is not None:
            expressions[path] = expression
    return expressions


def declared_hasura_resource_fields(
    model: type[models.Model],
    attribute: str,
) -> tuple[str, ...]:
    """Return Hasura resource fields declared by a composed model or extension base.

    Same-row model extensions own the fields they add and may declare which of
    those fields are writable/filterable/sortable on a Hasura resource by
    setting ``attribute`` on their source model class. The composed runtime model
    inherits those bases; this helper gathers only directly declared attributes
    from the MRO so a downstream extension can contribute without the base addon
    importing it. Filter/sort and container-scope declarations also accept
    scalar/to-one ORM paths; write declarations retain concrete-field contracts.
    """

    fields: list[str] = []
    for cls in reversed(model.__mro__):
        if attribute not in cls.__dict__:
            continue
        value = cls.__dict__[attribute]
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise ImproperlyConfigured(
                f"{cls.__module__}.{cls.__name__}.{attribute} must be a sequence of field names."
            )
        for item in value:
            field = str(item)
            try:
                if attribute in {"hasura_sortable_fields", "hasura_filterable_fields", "hasura_container_scope_fields"}:
                    require_field_for_path(model, field)
                else:
                    model._meta.get_field(field)
            except (FieldDoesNotExist, FieldPathError) as error:
                raise ImproperlyConfigured(
                    f"{cls.__module__}.{cls.__name__}.{attribute} declares invalid field {field!r} "
                    f"on {model._meta.label}."
                ) from error
            if attribute in ("hasura_sortable_fields", "hasura_filterable_fields"):
                field = field.replace(".", "__")
            if field not in fields:
                fields.append(field)
    return tuple(fields)


def declared_hasura_write_relation_fields(model: type[models.Model]) -> tuple[str, ...]:
    """Project contributed writable relations from the canonical field declarations."""

    fields = dict.fromkeys(
        field
        for attribute in ("hasura_insertable_fields", "hasura_updatable_fields")
        for field in declared_hasura_resource_fields(model, attribute)
    )
    return tuple(name for name in fields if _is_writable_relation(model._meta.get_field(name)))


@cache
def _declared_aliases(
    model: type[models.Model],
) -> tuple[dict[str, SortAlias], dict[str, tuple[models.Expression, tuple[str, ...]]]]:
    """Collect model-owned ``hasura_aliases`` for sorting and filtering.

    Each directly declared mapping contributes wire names and Django expressions,
    e.g. ``{"label": NullIf(F("party__display_name"), Value(""))}``. Expressions
    compose ``F``, ``Value``, ``Func`` and arithmetic; opaque SQL, subqueries and
    predicate trees are refused because their read paths cannot be validated by
    this contract. Every referenced path is checked for field gates and guarded
    by the same actor-scoped expression as grouping and filtering. An unreadable
    hop makes the entire alias NULL, including expressions with fallbacks.

    Aliases are automatically sortable. Duplicate MRO declarations fail rather
    than letting an extension silently replace another extension's expression.
    """

    aliases: dict[str, SortAlias] = {}
    filters: dict[str, tuple[models.Expression, tuple[str, ...]]] = {}
    for cls in reversed(model.__mro__):
        declaration = cls.__dict__.get("hasura_aliases", {})
        if not isinstance(declaration, Mapping):
            raise ImproperlyConfigured(f"{cls.__name__}.hasura_aliases must be a mapping.")
        for name, expression in declaration.items():
            if name in aliases:
                raise ImproperlyConfigured(f"{model._meta.label} declares duplicate sortable alias {name!r}.")
            if isinstance(expression, str):
                path = expression.replace(".", "__")
                try:
                    field = require_field_for_path(model, path)
                except FieldPathError as error:
                    raise ImproperlyConfigured(
                        f"{model._meta.label} alias {name!r} declares invalid path {path!r}."
                    ) from error
                if field.is_relation:
                    raise ImproperlyConfigured(f"{model._meta.label} alias {name!r} must target a scalar field.")
                expression = models.F(path)
            if not isinstance(expression, (models.F, models.Expression)):
                raise ImproperlyConfigured(f"{model._meta.label} alias {name!r} must be a path or Django expression.")
            paths: set[str] = set()
            for part in (expression,) if isinstance(expression, models.F) else expression.flatten():
                if isinstance(part, models.F):
                    try:
                        require_field_for_path(model, part.name)
                    except FieldPathError as error:
                        raise ImproperlyConfigured(
                            f"{model._meta.label} sortable alias {name!r} declares invalid path {part.name!r}."
                        ) from error
                    paths.add(part.name)
                elif not isinstance(part, (models.Func, models.Value, models.ExpressionWrapper, CombinedExpression)):
                    raise ImproperlyConfigured(
                        f"{model._meta.label} sortable alias {name!r} must compose F, Value, Func or arithmetic."
                    )
            assert_no_gated_read_fields(
                model, paths, f"sortable alias {name!r}", "field-gated reads cannot be query axes",
            )
            expression = models.ExpressionWrapper(
                expression, output_field=expression.resolve_expression(model._default_manager.all().query).output_field,
            )
            aliases[name] = SortAlias(
                f"_angee_sort_{name}", partial(_sortable_alias_expression, expression, tuple(sorted(paths))),
            )
            filters[name] = (expression, tuple(sorted(paths)))
    return aliases, filters


def _declared_filter_expressions(
    model: type[models.Model],
) -> dict[str, Callable[[models.QuerySet[Any]], models.Expression]]:
    """Collect model-owned ``hasura_filter_expressions`` from the model and its extension bases.

    Each mapping names filter-only predicates as providers taking the target
    queryset, exactly as ``filter_expressions`` takes them, e.g.
    ``{"requested_by_viewer": requested_by_viewer}`` on an intake base composed
    onto ``projects.Task``. A provider may read the queryset's actor; it owns its
    access rule, because unlike ``hasura_aliases`` its subqueries are not path-
    checked. Duplicate MRO declarations fail rather than letting one extension
    replace another's predicate.
    """

    expressions: dict[str, Callable[[models.QuerySet[Any]], models.Expression]] = {}
    for cls in reversed(model.__mro__):
        declaration = cls.__dict__.get("hasura_filter_expressions", {})
        if not isinstance(declaration, Mapping):
            raise ImproperlyConfigured(f"{cls.__name__}.hasura_filter_expressions must be a mapping.")
        for name, provider in declaration.items():
            if name in expressions:
                raise ImproperlyConfigured(f"{model._meta.label} declares duplicate filter expression {name!r}.")
            if not callable(provider):
                raise ImproperlyConfigured(
                    f"{model._meta.label} filter expression {name!r} must be a provider taking the target queryset."
                )
            expressions[name] = provider
    return expressions


def _check_filter_expression_names(
    model: type[models.Model], names: Iterable[str], existing_filters: set[str],
) -> None:
    """Keep computed filters additive to model fields, resource filters and Hasura combinators."""

    for name in sorted(names):
        if name in {"_and", "_or", "_not"}:
            raise ImproperlyConfigured(
                f"{model._meta.label} filter expression {name!r} is a reserved Hasura combinator."
            )
        if _has_model_field(model, name):
            raise ImproperlyConfigured(f"{model._meta.label} filter expression {name!r} shadows a model field.")
        if name in existing_filters:
            raise ImproperlyConfigured(f"{model._meta.label} filter expression {name!r} shadows a resource filter.")


def _sortable_alias_expression(
    expression: Combinable,
    paths: tuple[str, ...],
    info: strawberry.Info | None,
    queryset: models.QuerySet[Any],
) -> Combinable:
    """Guard a declared value at native Hasura's resolved ordering boundary."""

    del info
    for path in paths:
        guarded = actor_scoped_relation_expression(queryset, path, value=expression)
        if guarded is not None:
            expression = guarded
    return expression


def with_filter_aliases(queryset: models.QuerySet[Any]) -> models.QuerySet[Any]:
    """Register unselected aliases; field ``annotate`` hints promote selected ones."""
    projected = {}
    for name, (expression, paths) in _declared_aliases(queryset.model)[1].items():
        if name in queryset.query.annotations:
            continue
        projected[name] = _scalar_alias_expression(
            queryset, _sortable_alias_expression(expression, paths, None, queryset),
        )
    return queryset.alias(**projected) if projected else queryset


def _scalar_alias_expression(queryset: models.QuerySet[Any], expression: Combinable) -> models.Subquery:
    """Keep an unused alias's related joins out of its containing row query."""

    source = system_queryset(queryset.model).order_by().filter(pk=models.OuterRef("pk"))
    return models.Subquery(source.annotate(_angee_scalar=expression).values("_angee_scalar")[:1])


def _public_pk(model: type[models.Model], value: Any) -> Any:
    """Decode identity without an existence check; read roots own row scope."""

    field = public_data_id_field(model)
    if field is not None:
        return field.public_id_to_value(value)
    instance = instance_from_public_id(model, str(value), queryset=system_queryset(model, lock=None))
    return None if instance is None else instance.pk


def _write_public_instance(model: type[models.Model], value: Any) -> Any:
    """Decode one write relation public id through the actor-scoped write owner."""

    if value in (None, ""):
        return None
    return require_instance_for_id(
        model,
        str(value),
        queryset=write_queryset(model),
    )


def _relation_group_key_encoders(
    model: type[models.Model],
    paths: Sequence[str],
) -> dict[str, Callable[[Any], Any]]:
    """Bind public identities once; the aggregate owner shapes each key."""

    encoders: dict[str, Callable[[Any], Any]] = {}
    for path, field in _relation_axis_fields(model, paths).items():
        target = getattr(field, "target_field", None)
        if target is not None and not target.primary_key:
            raise ImproperlyConfigured(
                f"{model._meta.label} group axis {path!r} targets non-primary "
                "identity; public group keys require a primary-key relation."
            )
        encoders[path] = partial(public_id_for, field.related_model)
    return encoders


class _FilterInputExtension(FieldExtension):
    """Bridge input donors and safe SQL aliases to Hasura's dataclass visitor.

    Nested relation expressions use flat aliases because Django interprets
    overlapping ``__`` annotation names as transforms of the shortest prefix.
    Public inputs retain their native names; only the runtime visitor keys move.
    """

    def __init__(self, base: type, donor: type | None, aliases: Mapping[str, str]) -> None:
        self.base = base
        self.aliases = aliases
        self.filter_aliases = frozenset(aliases.values())
        self.combined = base if donor is None else dataclasses.make_dataclass(
            f"{base.__name__}WithExpressions", [], bases=(base, donor), kw_only=True,
        )
        self.mapped = dataclasses.make_dataclass(
            f"{base.__name__}Columns",
            [(aliases.get(field.name, field.name), Any, dataclasses.field(default=strawberry.UNSET))
             for field in dataclasses.fields(self.combined)],
            kw_only=True,
        ) if aliases else self.combined

    def map_arguments(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {key: self._value(value) for key, value in kwargs.items()}

    def resolve(self, next_: Callable[..., Any], source: Any, info: strawberry.Info, **kwargs: Any) -> Any:
        return next_(source, info, **self.map_arguments(kwargs))

    async def resolve_async(self, next_: Callable[..., Any], source: Any, info: strawberry.Info, **kwargs: Any) -> Any:
        return await next_(source, info, **self.map_arguments(kwargs))

    def _value(self, value: Any) -> Any:
        if isinstance(value, self.base):
            return self.mapped(**{
                self.aliases.get(field.name, field.name): self._value(getattr(value, field.name, strawberry.UNSET))
                for field in dataclasses.fields(self.combined)
            })
        if isinstance(value, list):
            return [self._value(item) for item in value]
        return value


def _resource_filter_annotation(
    source: Callable[[strawberry.Info], models.QuerySet[Any]], expression: Any, info: strawberry.Info,
) -> Any:
    """Resolve a contributed scalar for the actor loading the resource node."""
    queryset = source(info)
    return expression(queryset) if callable(expression) else expression


def _resource_filter_projection(
    node: type, model: type[models.Model], expressions: tuple[tuple[str, Any], ...],
    source: Callable[[strawberry.Info], models.QuerySet[Any]],
) -> type:
    """Native type extensions expose contributed filters as readable badge values."""
    definition = get_object_definition(node, strict=True)
    if collisions := {field.python_name for field in definition.fields} & dict(expressions).keys():
        raise ImproperlyConfigured(f"{definition.name} shadows contributed resource fields: {sorted(collisions)}.")
    empty = model._default_manager.none()
    attributes = {
        "__annotations__": {
            name: field_type_map[type((expression(empty) if callable(expression) else expression).output_field)]
            for name, expression in expressions
        },
        **{
            name: strawberry_django.field(
                annotate={name: partial(_resource_filter_annotation, source, expression)},
            )
            for name, expression in expressions
        },
    }
    return strawberry_django.type(model, name=definition.name, extend=True)(
        type(f"{definition.name}ResourceFilters", (), attributes),
    )


def hasura_model_resource(  # noqa: PLR0913 - mirrors the upstream declarative builder.
    node: type,
    *,
    model: type[models.Model],
    name: str | None = None,
    filterable: Sequence[str],
    filter_expressions: Mapping[str, models.Expression | Callable[[models.QuerySet[Any]], models.Expression]]
    | None = None,
    record_ref_filters: tuple[str, str] | None = None,
    record_ref_requires_read: bool = False,
    sortable: Sequence[str],
    sortable_aliases: Mapping[str, str | SortAlias] | None = None,
    aggregatable: Sequence[str],
    groupable: Sequence[str] = (),
    json_paths: Mapping[str, str] | None = None,
    writable: Sequence[str] | None = None,
    insertable: Sequence[str] | None = None,
    updatable: Sequence[str] | None = None,
    lines: HasuraLines | None = None,
    insert: bool = True,
    update: bool = True,
    delete: bool = True,
    field_id_decode: Mapping[str, Callable[[Any], Any]] | None = None,
    get_queryset: Callable[[strawberry.Info], models.QuerySet[Any]] | None = None,
    get_aggregate_queryset: Callable[[strawberry.Info], models.QuerySet[Any]] | None = None,
    write_backend: WriteBackend | None = None,
    id_decode: Callable[[Any], Any] | None = None,
    id_column: str = "pk",
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
    subject_field: str | None = None,
    row_model: str = "server",
    subtitle: DataResourceSubtitleMetadata | None = None,
    record_representation: str | None = None,
    record_search_fields: Sequence[str] | None = None,
) -> HasuraResource:
    """Build a Hasura resource and attach Angee's model-resource metadata.

    ``strawberry-django-hasura`` owns the portable Hasura dialect mechanics.
    This wrapper owns the Angee seam around that resource: attaching the Phase 1
    ``angee.resources`` metadata contribution, and defaulting the standard glue a
    public-id model resource shares — base/aggregate querysets, the authorized
    write backend, and the public-id ``id`` decoder. A caller overrides any knob
    only where the resource's intent differs (REBAC-scoped reads, a custom write
    backend, a non-``pk`` identity column).

    ``lines=HasuraLines(field="lines", model=...)`` (F6) declares an editable
    child-lines relation: the insert surface rides the upstream nested-insert
    shape (``insert_<res>_one(object: {..., lines: {data: [...]}})``) and an
    authored ``<res>_save(pk, patch, lines)`` mutation diff-applies children plus
    patches the parent in one transaction. The default write backend becomes a
    lines-aware :class:`AngeeHasuraWriteBackend`; a caller supplying its own
    ``write_backend`` must make it lines-aware.

    ``subtitle=DataResourceSubtitleMetadata(...)`` declares the renderer's
    closed created/updated/word-count vocabulary as dotted GraphQL selection
    paths. Every path resolves against ``node`` during metadata emission; adding
    another semantic fact extends the declaration and renderer together.

    ``record_representation`` selects the readable String field generic record
    and relation surfaces use as their human label after final GraphQL naming.
    ``record_search_fields`` declares the readable filterable String fields relation
    pickers search together; resources that omit it retain the standard single
    representation-field search.

    Model ``hasura_aliases`` mappings contribute named Django expression
    sorts automatically, including from extension bases. They use native lazy
    ``SortAlias`` preparation with protected relation hops redacted to NULL.

    A ``filter_expressions`` provider receives the target queryset so actor-aware
    expressions work for requests and stored filters alike. Its output field is
    inspected on an empty queryset at composition; it must perform no row reads.
    That field's ``null`` states whether the filter can match NULL: a total
    predicate such as ``BooleanField()`` advertises neither nullability nor
    ``isNull``, while a subquery that can find no row declares ``null=True``.
    Model ``hasura_filter_expressions`` mappings contribute the same filter-only
    predicates from extension bases, so an addon composing onto another addon's
    model adds a filter its owner's resource never names. Both model declaration
    seams must use new names, distinct from existing resource filters and Hasura
    boolean combinators.
    """

    model_aliases, model_filter_aliases = _declared_aliases(cast(Hashable, model))
    expressions = dict(filter_expressions or {})
    contributed_filters: dict[str, Any] = {}
    for provider in resolve_hooks("ANGEE_GRAPHQL_RESOURCE_FILTERS"):
        contributed = provider(model)
        if collisions := contributed.keys() & (expressions.keys() | contributed_filters.keys()):
            raise ImproperlyConfigured(f"{model._meta.label} has duplicate resource filters: {sorted(collisions)}.")
        contributed_filters.update(contributed)
    expressions.update(contributed_filters)
    if collisions := expressions.keys() & model_filter_aliases.keys():
        raise ImproperlyConfigured(f"{model._meta.label} declares duplicate filter aliases: {sorted(collisions)}.")
    expressions.update({name: expression for name, (expression, _) in model_filter_aliases.items()})
    declared_expressions = _declared_filter_expressions(model)
    if collisions := expressions.keys() & declared_expressions.keys():
        raise ImproperlyConfigured(f"{model._meta.label} declares duplicate filter expressions: {sorted(collisions)}.")
    expressions.update(declared_expressions)
    if record_ref_requires_read and record_ref_filters is None:
        raise ImproperlyConfigured("A record-reference read guard requires record-reference filters.")
    if record_ref_filters is not None:
        if not issubclass(model, RecordRefMixin):
            raise ImproperlyConfigured("Record-reference filters require RecordRefMixin.")
        model_name, id_name = record_ref_filters
        claimed = set(expressions) | set(field_id_decode or {})
        if model_name == id_name or set(record_ref_filters) & claimed:
            raise ImproperlyConfigured("Record-reference filter names must be distinct and unclaimed.")
        reference = model.record_ref_field()
        expressions.update({
            model_name: Concat(
                f"{reference.ct_field}__app_label", models.Value("."), f"{reference.ct_field}__model",
                output_field=models.CharField(),
            ),
            id_name: Cast(reference.fk_field, output_field=models.CharField()),
        })
        field_id_decode = {
            **(field_id_decode or {}), model_name: str.lower, id_name: model.record_public_id_operand,
        }
    container_scopes = set(declared_hasura_resource_fields(model, "hasura_container_scope_fields"))
    # Caller-owned computed filters name themselves in filterable; extension
    # declarations must not replace any filter the resource already owns.
    _check_filter_expression_names(
        model, expressions,
        (set(filterable) | container_scopes) - set(filter_expressions or {}) - set(record_ref_filters or ()),
    )
    filterable = tuple(dict.fromkeys((
        *filterable, *contributed_filters, *sorted(container_scopes), *model_filter_aliases,
        *declared_expressions, *(record_ref_filters or ()),
    )))
    if collisions := model_aliases.keys() & (sortable_aliases or {}).keys():
        raise ImproperlyConfigured(f"{model._meta.label} declares duplicate sortable aliases: {sorted(collisions)}.")
    sortable = tuple(dict.fromkeys((*sortable, *model_aliases)))
    sortable_aliases = {**model_aliases, **(sortable_aliases or {})}
    active_groupable = relation_group_by_fields(node, model, tuple(groupable))
    for axis, fields in (
        ("filterable", filterable),
        ("sortable", sortable),
        ("groupable", active_groupable),
        ("aggregatable", aggregatable),
    ):
        assert_no_gated_read_fields(
            model,
            tuple(field for field in fields if field not in expressions),
            f"hasura_model_resource {axis} axis",
            "field-gated reads cannot be query axes",
        )

    resource_name = name or model.__name__.lower()
    relation_paths = (
        *(field for field in filterable if field not in expressions),
        *sortable,
        *(alias.path if isinstance(alias, SortAlias) else alias for alias in (sortable_aliases or {}).values()),
    )
    for path in sorted(container_scopes):
        field = require_field_for_path(model, path)
        if (
            "__" not in path or field.is_relation
            or path in {*sortable, *active_groupable, *aggregatable}
        ):
            raise ImproperlyConfigured(
                f"{model._meta.label}.{path}: container scope keys must be filter-only related scalars."
            )
    # Scope membership is intentionally testable without reading the container.
    # It never changes the actor scope of the root rows or their projections.
    relation_paths = tuple(path for path in relation_paths if path not in container_scopes)
    source_read_queryset = _relation_scalar_queryset(model, get_queryset or _model_queryset(model), relation_paths)
    source_aggregate = (
        _relation_scalar_queryset(model, get_aggregate_queryset, relation_paths)
        if get_aggregate_queryset is not None
        else _aggregate_queryset(source_read_queryset)
    )
    if unknown := expressions.keys() - set(filterable):
        raise ImproperlyConfigured(f"Filter expressions must be declared filterable: {sorted(unknown)}")

    def prepare_filters(queryset: models.QuerySet[Any]) -> models.QuerySet[Any]:
        if issubclass(node, RecordReferenceNode) or record_ref_requires_read:
            queryset = with_record_reference_access(queryset)
        queryset = with_filter_aliases(queryset)
        aliases: dict[str, models.Expression] = {}
        for name, expression in expressions.items():
            if name in model_filter_aliases:
                continue
            if callable(expression):
                expression = expression(queryset)
            if record_ref_requires_read and name in (record_ref_filters or ()):
                expression = models.Case(
                    models.When(_angee_record_readable=True, then=expression),
                    default=models.Value(None), output_field=expression.output_field,
                )
            aliases[name] = expression
        # Django aliases enter SQL only when referenced. This also covers
        # variable inputs and aggregates whose resolver is below the root field.
        return queryset.alias(**aliases) if aliases else queryset

    def read_queryset(info: strawberry.Info) -> models.QuerySet[Any]:
        return prepare_filters(source_read_queryset(info))

    def aggregate_source(info: strawberry.Info) -> models.QuerySet[Any]:
        return prepare_filters(source_aggregate(info))

    model_filters = tuple(field for field in filterable if field not in expressions)
    # Native Hasura aliases cannot shadow a model field (or contain path
    # separators). Keep the public relation name on the Strawberry input while
    # its Python name orders the same redacted key used by relation filters.
    relation_sort_aliases = {path: _relation_axis_alias(path) for path in _relation_axis_fields(model, sortable)}
    sortable_aliases = {**{alias: alias for alias in relation_sort_aliases.values()}, **sortable_aliases}
    if id_decode is None and id_column == "pk":
        id_decode = public_pk_decoder(model)
    active_write_backend = write_backend or AngeeHasuraWriteBackend(model, lines=lines)
    declared_writable = [seq for seq in (writable, insertable, updatable) if seq is not None]
    _check_writable_relations_decoded(
        model,
        writable=[name for seq in declared_writable for name in seq] if declared_writable else None,
        id_column=id_column,
        declared={*(field_id_decode or {}), *getattr(active_write_backend, "public_id_fields", {})},
        surface=f"resource {resource_name!r}",
    )
    if lines is not None:
        _check_writable_relations_decoded(
            lines.model,
            writable=lines.writable,
            id_column=PUBLIC_ID_FIELD_NAME,
            declared=lines.public_id_fields,
            exclude=(_child_back_fk(model, lines.field),),
            surface=f"resource {resource_name!r} lines",
        )
    # A filterable relation column is filtered by the related row's public id;
    # auto-derive its filter decoder so a bare read resource (no write surface,
    # no hand-declared field_id_decode) filters relations by sqid too. The
    # writable-relation guard above still runs on the caller's declaration, so
    # this never widens what a write may target.
    field_id_decode = _relation_filter_decoders(
        model,
        filterable=filterable,
        declared=field_id_decode,
    )
    relation_filter_aliases = {path: _relation_axis_alias(path) for path in _relation_axis_fields(model, filterable)}
    field_id_decode = {
        **(field_id_decode or {}),
        **{alias: field_id_decode[path] for path, alias in relation_filter_aliases.items()
           if field_id_decode is not None and path in field_id_decode},
    }
    active_json_paths = dict(json_paths or {})
    filter_lookups = resource_filter_lookups(model, model_filters)
    resource = build_hasura_resource(
        node,
        model=model,
        name=name,
        filterable=list(model_filters),
        sortable=[relation_sort_aliases.get(path, path) for path in sortable],
        sortable_aliases=sortable_aliases,
        aggregatable=list(aggregatable),
        groupable=list(active_groupable) or None,
        json_paths=active_json_paths,
        group_key_encoders=_relation_group_key_encoders(model, active_groupable),
        get_group_by_expressions=_group_by_expression_provider,
        filter_lookups=filter_lookups,
        writable=list(writable) if writable is not None else None,
        insertable=list(insertable) if insertable is not None else None,
        updatable=list(updatable) if updatable is not None else None,
        nested=_nested_inserts(lines) if lines is not None else None,
        insert=insert,
        update=update,
        delete=delete,
        insert_arguments={"client_creation_key": str | None}
        if insert and issubclass(model, CreationKeyMixin)
        else None,
        update_arguments={"expected_revision": int | None}
        if update and issubclass(model, OptimisticLockMixin)
        else None,
        field_id_decode=field_id_decode,
        get_queryset=read_queryset,
        get_aggregate_queryset=aggregate_source,
        write_backend=active_write_backend,
        id_decode=id_decode,
        id_column=id_column,
    )
    if contributed_filters:
        resource.types.append(_resource_filter_projection(
            node, model, tuple(contributed_filters.items()), source_read_queryset,
        ))
    if relation_sort_aliases:
        assert resource.order_by_type is not None
        wire_names = {alias: path for path, alias in relation_sort_aliases.items()}
        for field in get_object_definition(resource.order_by_type, strict=True).fields:
            if field.python_name in wire_names:
                field.graphql_name = wire_names[field.python_name]
    adapter = None
    non_null_filter_fields: tuple[str, ...] = ()
    if expressions:
        assert resource.filter_type is not None
        filter_name = get_object_definition(resource.filter_type, strict=True).name
        empty = model._default_manager.none()
        output_fields = {
            key: (expression(empty) if callable(expression) else expression).output_field
            for key, expression in expressions.items()
        }
        # Aliases and record references redact to NULL, so only declared expressions can be non-null.
        non_null_filter_fields = tuple(
            key for key in (*(filter_expressions or {}), *declared_expressions) if not output_fields[key].null
        )
        donor = type(f"{filter_name}Expressions", (), {
            "__annotations__": {
                key: comparison_for_python_type(
                    field_type_map[type(output_field)], public_id=key in (field_id_decode or {}),
                ) | None
                for key, output_field in output_fields.items()
            },
            **{key: strawberry.field(name=key, default=strawberry.UNSET) for key in expressions},
        })
        donor = strawberry.input(donor, name=filter_name, extend=True)
        resource.types.append(donor)
        adapter = _FilterInputExtension(resource.filter_type, donor, {})
        for field in get_object_definition(resource.query, strict=True).fields:
            if any(argument.python_name == "where" for argument in field.arguments):
                field.extensions.insert(0, adapter)
    if any(isinstance(decoder, _RelationIDDecoder) for decoder in (field_id_decode or {}).values()):
        assert resource.filter_type is not None
        filter_type = adapter.combined if adapter is not None else resource.filter_type
        for field in get_object_definition(resource.query, strict=True).fields:
            if any(argument.python_name == "where" for argument in field.arguments):
                source = read_queryset if field.graphql_name == resource.list_root else aggregate_source
                field.extensions.append(_RelationFilterExtension(
                    field_id_decode, source, filter_type, relation_filter_aliases,
                ))
    if lines is not None:
        resource = _attach_lines_save(resource, node=node, lines=lines, write_backend=active_write_backend)

    def compile_filter(where: Any) -> Callable[[Any], Any]:
        """Share this resource's native filter wiring with non-request consumers."""
        if adapter is not None:
            where = adapter.map_arguments({"where": where})["where"]
        predicate = where_to_q(
            where, id_column=id_column, id_decode=id_decode,
            field_decoders=field_id_decode, lookups=filter_lookups,
        )
        return lambda queryset: prepare_filters(queryset).filter(predicate)

    return attach_hasura_resource_metadata(
        resource,
        node=node,
        model=model,
        filterable=tuple(filterable),
        sortable=tuple(sortable),
        aggregatable=tuple(aggregatable),
        groupable=active_groupable,
        json_paths=active_json_paths,
        filter_operators=tuple(filter_lookups),
        non_null_filter_fields=non_null_filter_fields,
        lines=lines,
        model_label=model_label,
        public_id_field=public_id_field,
        subject_field=subject_field,
        row_model=row_model,
        subtitle=subtitle,
        record_representation=record_representation,
        record_search_fields=(tuple(record_search_fields) if record_search_fields is not None else None),
        compile_filter=compile_filter,
    )


def _is_writable_relation(field: Any) -> bool:
    """Return whether a writable column is a forward relation (FK / one-to-one / M2M)."""

    if not isinstance(field, models.Field):
        return False  # a reverse accessor is never a client-settable column
    if not (is_to_one_relation(field) or getattr(field, "many_to_many", False)):
        return False
    return bool(getattr(field, "many_to_many", False) or getattr(field, "editable", False))


def _check_writable_relations_decoded(
    model: type[models.Model],
    *,
    writable: Sequence[str] | None,
    id_column: str,
    declared: Iterable[str],
    exclude: Iterable[str] = (),
    surface: str,
) -> None:
    """Reject an *explicitly* writable relation column that declares no public-id decode.

    A relation column (FK / M2M) written raw bypasses the actor-scoped public-id
    decode (``_write_public_instance`` → ``write_queryset``), so a caller could set
    the relation to a target it cannot read — an escalation the §3.4 child-lines
    elevation would then persist under the parent's authority. Every relation a
    surface declares writable must name a decode (the parent's ``field_id_decode`` /
    write-backend public-id fields, a child's ``public_id_fields``) so the write
    resolves the target through the visible write owner. Fails the build, not the
    first write.

    ``writable is None`` means the surface exposes the framework's default editable
    set (server-managed audit relations like ``created_by`` included); that default
    is the framework's convention, not a per-surface declaration, so it is left to
    its owner rather than swept in here — only a *declared* writable column is
    guarded.
    """

    if writable is None:
        return
    known = set(declared)
    skip = {id_column, *exclude}
    for name in writable:
        if name in skip:
            continue
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        if _is_writable_relation(field) and name not in known:
            raise ImproperlyConfigured(
                f"{surface} exposes writable relation column {name!r} on {model._meta.label} "
                "without a public-id decode; declare it (field_id_decode / public_id_fields) so the "
                "write resolves the target through the actor-scoped write owner."
            )


def _nested_inserts(lines: HasuraLines) -> list[NestedInsert]:
    """Return the upstream nested-insert declaration for a lines relation.

    ``public_id_columns`` names the child relation columns exposed as public ids
    (typed ``ID`` in the generated child input); decoding them to write values
    stays this backend's concern (:meth:`AngeeHasuraWriteBackend._apply_line_diff`),
    exactly like the parent write path. ``id_column`` is Angee's public-id column
    so the child's own sqid is excluded from the writable set, mirroring the
    parent resource's ``id_column``.
    """

    return [
        NestedInsert(
            relation=lines.field,
            model=lines.model,
            insertable=lines.writable,
            public_id_columns=lines.public_id_fields or None,
            id_column=PUBLIC_ID_FIELD_NAME,
        )
    ]


def _attach_lines_save(
    resource: HasuraResource,
    *,
    node: type,
    lines: HasuraLines,
    write_backend: Any,
) -> HasuraResource:
    """Merge the authored ``<res>_save`` mutation into a built resource.

    The nested-insert shape rides the upstream builder; the diff-apply ``_save``
    operation is Angee dialect glue registered here, beside the CRUD roots — the
    frontend drives it to persist an edited document (parent patch + line diff)
    in one REBAC-checked transaction. The line argument reuses the upstream child
    input (an optional public ``id`` per row keys the diff).
    """

    res = resource.name or node.__name__.lower()
    line_input = resource.nested_input_types.get(lines.field)
    if line_input is None:
        raise ImproperlyConfigured(f"{res} declares lines but built no nested line input.")
    if not callable(getattr(write_backend, "save", None)):
        raise ImproperlyConfigured(
            f"{res} declares lines but its write_backend {type(write_backend).__name__} is not "
            "lines-aware (it must expose save(info, pk, patch, lines))."
        )
    patch_type = resource.set_input_type
    if patch_type is None:
        raise ImproperlyConfigured(
            f"{res} declares lines but exposes no parent set-input (update=False); a document "
            "save patches the parent, so lines require the update surface."
        )
    save_root = f"{res}_save"

    revisioned = issubclass(django_model(node), OptimisticLockMixin)

    def save(info: strawberry.Info, pk: PublicID, patch: Any, lines: Any, **kwargs: Any) -> Any:
        patch_data = input_to_dict(patch) if patch is not None else {}
        rows = None if lines is None else [input_to_dict(row) for row in lines]
        return write_backend.save(info, str(pk), patch_data, rows, **kwargs)

    def resolve_save(self: Any, info: strawberry.Info, pk: PublicID, patch: Any = None, lines: Any = None) -> Any:
        return save(info, pk, patch, lines)

    def resolve_revisioned_save(
        self: Any, info: strawberry.Info, pk: PublicID, patch: Any = None, lines: Any = None,
        expected_revision: int | None = None,
    ) -> Any:
        return save(info, pk, patch, lines, expected_revision=expected_revision)

    resolver = resolve_revisioned_save if revisioned else resolve_save
    resolver.__annotations__.update({
        "self": Any, "info": strawberry.Info, "pk": PublicID,
        "patch": patch_type | None, "lines": _types.GenericAlias(list, (line_input,)) | None, "return": node,
    })
    if revisioned:
        resolver.__annotations__["expected_revision"] = int | None

    save_holder = strawberry.type(
        type(
            f"{res}__save_mutation",
            (),
            {save_root: strawberry.mutation(resolver=resolver, name=save_root)},
        )
    )
    combined_mutation = strawberry.type(type(f"{res}__mutation", (resource.mutation, save_holder), {}))
    return dataclasses.replace(resource, mutation=combined_mutation)


def attach_hasura_resource_metadata(
    resource: HasuraResource,
    *,
    node: type,
    model: type[models.Model],
    filterable: tuple[str, ...],
    sortable: tuple[str, ...],
    aggregatable: tuple[str, ...],
    groupable: tuple[str, ...] = (),
    json_paths: Mapping[str, str] | None = None,
    filter_operators: tuple[str, ...] = (),
    non_null_filter_fields: tuple[str, ...] = (),
    lines: HasuraLines | None = None,
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
    subject_field: str | None = None,
    row_model: str = "server",
    subtitle: DataResourceSubtitleMetadata | None = None,
    record_representation: str | None = None,
    record_search_fields: tuple[str, ...] | None = None,
    compile_filter: Callable[[Any], Callable[[Any], Any]] | None = None,
) -> HasuraResource:
    """Attach the native bundle and Angee-only policy for final projection."""

    active_json_paths = dict(json_paths or {})
    if resource.detail_root is None:
        raise ImproperlyConfigured(f"{model._meta.label} Hasura resource did not expose a detail root.")
    contribution = DataResourceContribution(
        model=model,
        model_label=model_label or model._meta.label,
        native_resource=resource,
        compile_filter=compile_filter,
        roots=DataResourceRoots(
            save_name=(
                resource_wire_field_name(resource.mutation, f"{resource.name}_save") if lines is not None else None
            )
        ),
        policy=DataResourcePolicy(
            filter_fields=filterable,
            filter_operators=filter_operators,
            non_null_filter_fields=non_null_filter_fields,
            order_fields=sortable,
            aggregate_fields=aggregatable,
            group_by_fields=groupable,
            query_axes=hasura_query_axes(model, groupable, filterable, json_paths=active_json_paths),
            aggregate_measures=_hasura_aggregate_measures(model, aggregatable),
            default_measures=(DataAggregateMeasureMetadata(op="count"),),
            public_id_field=public_id_field,
            subject_field=subject_field,
            row_model=row_model,
            subtitle=subtitle,
            record_representation=record_representation,
            record_search_fields=record_search_fields,
            lines_declaration=lines,
            save_argument_names=("expected_revision",) if lines is not None else (),
        ),
    )
    attach_data_resource_contribution(resource.query, contribution)
    attach_data_resource_contribution(resource.mutation, contribution)
    return resource


def _parent_write_exclude(lines: HasuraLines | None) -> tuple[str, ...]:
    """Return parent create-field wire names to skip (id + the lines envelope)."""

    return ("id",) if lines is None else ("id", lines.field)


def _line_metadata(
    lines: HasuraLines,
    resource: HasuraResource,
    schema: Any,
) -> DataLinesMetadata:
    """Return the frontend editable-lines contract for a document resource."""

    line_input = resource.nested_input_types.get(lines.field)
    input_name = resource_type_name(line_input)
    child_fields = final_input_wire_fields(
        schema,
        input_name,
        accepted=(
            *resource_wire_field_names(line_input, exclude=("id",)),
            *final_input_extension_fields(schema, line_input),
        ),
    )
    unknown_defaults = set(lines.defaults) - set(child_fields)
    if unknown_defaults:
        names = ", ".join(sorted(unknown_defaults))
        raise ImproperlyConfigured(
            f"editable lines {lines.model._meta.label}.{lines.field} declare defaults for non-writable fields: {names}."
        )
    return DataLinesMetadata(
        field=lines.field,
        model_label=lines.model._meta.label,
        input_type=input_name,
        fields=_line_child_fields(lines, child_fields, schema, input_name),
        position_field=lines.position_field if _has_model_field(lines.model, lines.position_field) else None,
        defaults=dict(lines.defaults),
        lock_field=_line_lock_field(lines, schema),
    )


def _line_lock_field(lines: HasuraLines, schema: Any) -> str | None:
    """Return the child node's ``locked_fields`` wire name when the child locks rows."""

    if not issubclass(lines.model, RowLockMixin):
        return None
    wire_name = resource_wire_field_name(lines.node, "locked_fields")
    node_name = resource_type_name(lines.node)
    node_type = schema.get_type(node_name) if node_name is not None else None
    if wire_name is None or node_type is None or wire_name not in getattr(node_type, "fields", {}):
        raise ImproperlyConfigured(
            f"editable lines {lines.model._meta.label}.{lines.field} lock rows; "
            "their node must project locked_fields."
        )
    return wire_name


def _line_child_fields(
    lines: HasuraLines,
    child_fields: tuple[str, ...],
    schema: Any,
    input_name: str | None,
) -> tuple[DataResourceFieldMetadata, ...]:
    """Return per-column metadata for a document's editable child fields.

    The child **node** surface owns each field's projected shape — an enum's
    values, a relation/list target — so the line cells read it there through the
    final composed node and input types instead of re-deriving enum members and
    item shapes from the model. An M2M child is a ``kind="list"`` relation whose
    target the frontend renders as a multi-select and persists as public ids; an
    enum child carries its final wire values. Accepted input-only fields retain
    Django relation and widget semantics with ``readable=False``.
    """

    required = final_required_input_wire_fields(
        schema,
        input_name,
        accepted=child_fields,
    )
    readable: tuple[DataResourceFieldMetadata, ...] = ()
    node_name = resource_type_name(lines.node)
    if node_name is not None and schema.get_type(node_name) is not None:
        readable = final_resource_fields(
            schema,
            node_name,
            lines.model,
            aggregate_fields=(),
            create_fields=child_fields,
            update_fields=child_fields,
            required_create_fields=required,
        )
    input_only = final_input_only_resource_fields(
        schema,
        create_input_name=input_name,
        update_input_name=input_name,
        model=lines.model,
        aggregate_fields=(),
        create_fields=child_fields,
        update_fields=child_fields,
        required_create_fields=required,
        readable_fields=readable,
    )
    wanted = set(child_fields)
    return tuple(
        field for field in (*readable, *input_only) if field.name in wanted or field.model_field_name in wanted
    )


def _has_model_field(model: type[models.Model], name: str) -> bool:
    """Return whether ``model`` declares a field named ``name``."""

    try:
        model._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return True


def hasura_query_axes(
    model: type[models.Model],
    groupable: tuple[str, ...],
    filterable: tuple[str, ...],
    *,
    json_paths: Mapping[str, str] | None = None,
) -> tuple[DataQueryAxis, ...]:
    """Return typed-key group metadata using the aggregate builder's public contract.

    ``model`` is the Django model the aggregate builder groups: a resource's
    own model, or a run-query resource's upstream ``HasuraResource.row_model``.
    """

    active_json_paths = dict(json_paths or {})
    return tuple(_hasura_query_axis(model, path, filterable, json_paths=active_json_paths) for path in groupable)


def _hasura_query_axis(
    model: type[models.Model],
    path: str,
    filterable: tuple[str, ...],
    *,
    json_paths: Mapping[str, str] | None = None,
) -> DataQueryAxis:
    declared_json_type = (json_paths or {}).get(path)
    if declared_json_type is not None:
        key = group_by_alias(path, None)
        filter_metadata = _hasura_json_group_bucket_filter(model, path, key)
        return DataQueryAxis(
            field=path,
            server=DataQueryServerAxis(input=group_by_enum_member(path), key=key),
            kind="json",
            drill=filter_metadata,
            extractions=_hasura_group_extractions(
                path,
                declared_json_type=declared_json_type,
            ),
        )
    field = _require_group_field(model, path)
    key = group_by_alias(path, None, field)
    is_relation = is_to_one_relation(field)
    filter_metadata = _hasura_group_bucket_filter(
        field,
        path,
        key,
        filterable=filterable,
        is_relation=is_relation,
    )
    return DataQueryAxis(
        field=path,
        server=DataQueryServerAxis(input=group_by_enum_member(path), key=key),
        kind="relation"
        if is_relation
        else "date"
        if isinstance(field, (models.DateField, models.DateTimeField))
        else "column",
        drill=filter_metadata,
        extractions=_hasura_group_extractions(
            path,
            field=field,
            bucket_filter=filter_metadata,
        ),
    )


def _hasura_group_extractions(
    path: str,
    *,
    field: models.Field[Any, Any] | None = None,
    declared_json_type: str | None = None,
    bucket_filter: DataQueryDrill | None = None,
) -> tuple[DataQueryExtraction, ...]:
    if not (isinstance(field, (models.DateField, models.DateTimeField)) or declared_json_type in {"date", "datetime"}):
        return ()
    extractions: list[DataQueryExtraction] = []
    for granularity in (*TimeGranularity, *NumberGranularity):
        extraction_key = group_by_alias(path, granularity, field)
        range_key = group_by_range_alias(path, granularity) if isinstance(granularity, TimeGranularity) else None
        extractions.append(
            DataQueryExtraction(
                name=granularity.value,
                input=granularity.name,
                key=extraction_key,
                range_key=range_key,
                drill=(
                    _hasura_group_range_filter(
                        bucket_filter,
                        value_key=extraction_key,
                        range_key=range_key,
                    )
                    if range_key is not None
                    else None
                ),
            )
        )
    return tuple(extractions)


def _hasura_group_bucket_filter(
    field: models.Field[Any, Any],
    path: str,
    key: str,
    *,
    filterable: tuple[str, ...],
    is_relation: bool,
) -> DataQueryDrill | None:
    """Return the backend-owned drill-down filter for a group dimension."""

    filter_field = _group_filter_field(path, filterable)
    if filter_field is None:
        return None
    if is_relation:
        return DataQueryDrill(
            kind="identity",
            field=filter_field,
            value_key=key,
        )
    if isinstance(field, models.JSONField):
        return DataQueryDrill(
            kind="value",
            field=filter_field,
            value_key=key,
            value_transform="json",
        )
    return DataQueryDrill(
        kind="value",
        field=filter_field,
        value_key=key,
    )


def _hasura_json_group_bucket_filter(
    model: type[models.Model],
    path: str,
    key: str,
) -> DataQueryDrill | None:
    """Return the JSON containment drill-down filter for an allowlisted path."""

    root, *json_path = path.split(".")
    if not root or not json_path:
        return None
    try:
        field = model._meta.get_field(root)
    except FieldDoesNotExist:
        return None
    if not isinstance(field, models.JSONField):
        return None
    return DataQueryDrill(
        kind="json",
        field=root,
        value_key=key,
        json_path=".".join(json_path),
        null_mode="value",
    )


def _hasura_group_range_filter(
    bucket_filter: DataQueryDrill | None,
    *,
    value_key: str,
    range_key: str,
) -> DataQueryDrill | None:
    if bucket_filter is None:
        return None
    return DataQueryDrill(
        kind="range",
        field=bucket_filter.field,
        value_key=value_key,
        range_key=range_key,
        null_mode=bucket_filter.null_mode,
    )


def _group_filter_field(path: str, filterable: tuple[str, ...]) -> str | None:
    """Return the declared bool-exp field that can filter one group path."""

    normalized = path.replace(".", "__")
    for candidate in (path, normalized):
        if candidate in filterable:
            return candidate
    return None


def _hasura_aggregate_measures(
    model: type[models.Model],
    aggregatable: tuple[str, ...],
) -> tuple[DataAggregateMeasureMetadata, ...]:
    measures: list[DataAggregateMeasureMetadata] = []
    for path in aggregatable:
        field = _require_group_field(model, path)
        if getattr(field, "primary_key", False):
            continue
        for op in _measure_ops_for_field(field):
            measures.append(DataAggregateMeasureMetadata(op=op, field=path, input=path))
    return tuple(measures)


def _require_group_field(
    model: type[models.Model],
    path: str,
) -> models.Field[Any, Any]:
    """Resolve a groupable to-one Django field path for metadata emission."""

    try:
        return require_field_for_path(model, path)
    except FieldPathError:
        raise ImproperlyConfigured(
            f"hasura_model_resource({model._meta.label}) declares unknown groupable field path {path!r}."
        ) from None


#: The aggregate ops Angee advertises on the data surface, in metadata order
#: (``aggregate_measures`` JSON is order-sensitive for ``schema --check``). This
#: is Angee's curated subset; the op *vocabulary* per Django field type is owned
#: upstream by ``default_operators_for`` — intersecting the two keeps the
#: vocabulary from drifting while keeping the advertised subset an Angee decision.
_ANGEE_CURATED_OPS: tuple[str, ...] = ("sum", "avg", "min", "max")


def _measure_ops_for_field(field: models.Field[Any, Any]) -> tuple[str, ...]:
    """Return Angee's advertised aggregate ops for one measurable field.

    The valid-op vocabulary for the field's Django type is resolved upstream via
    ``default_operators_for`` and then clipped to :data:`_ANGEE_CURATED_OPS`,
    preserving curated order so emitted metadata stays byte-stable.
    """

    available = {op.value for op in default_operators_for(type(field).__name__)}
    return tuple(op for op in _ANGEE_CURATED_OPS if op in available)
