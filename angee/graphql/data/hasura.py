"""Angee metadata bridge for ``strawberry-django-hasura`` resources."""

from __future__ import annotations

import dataclasses
import types as _types
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import strawberry
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models, transaction
from rebac import PermissionDenied, system_context
from strawberry_django.mutations import resolvers as mutation_resolvers
from strawberry_django_aggregates import (
    compute_aggregation,
    default_operators_for,
    group_by_alias,
    shape_aggregate_row,
)
from strawberry_django_aggregates.granularity import NumberGranularity, TimeGranularity
from strawberry_django_hasura import (
    HasuraResource,
    NestedInsert,
    WriteBackend,
    input_to_dict,
)
from strawberry_django_hasura import filtering as hasura_filtering
from strawberry_django_hasura import grouping as hasura_grouping
from strawberry_django_hasura import (
    hasura_resource as build_hasura_resource,
)

from angee.base.models import (
    aggregate_scoped_queryset,
    bind_actor,
    instance_from_public_id,
    public_data_id_field,
    requires_angee_rebac_contract,
)
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.field_classification import model_field_scalar
from angee.graphql.data.metadata import (
    DataAggregateMeasureMetadata,
    DataGroupBucketFilterMetadata,
    DataGroupBucketFilterValueMapMetadata,
    DataGroupDimensionMetadata,
    DataGroupExtractionMetadata,
    DataLinesMetadata,
    DataResourceFieldMetadata,
    DataResourceRoots,
    DataResourceTypeNames,
    attach_data_resource_metadata,
    make_data_resource_metadata,
    model_resource_fields,
    resource_fields,
    resource_type_name,
    resource_wire_field_name,
    resource_wire_field_names,
)
from angee.graphql.data.resource_bundle import (
    resource_attr,
    resource_type_by_name,
    resource_type_by_suffix,
)
from angee.graphql.deletion import delete_by_public_id
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.introspection import (
    FieldPathError,
    is_to_one_relation,
    require_field_for_path,
)
from angee.graphql.writes import write_queryset

# The stock refine Hasura provider encodes startsWith/endsWith as anchored
# regexes (`_iregex: "^v"` / `_iregex: "v$"`). The library leaves regex lookups
# project-supplied (`filtering._LOOKUPS` is the registration seam) and Django
# owns `__iregex`, so Angee registers the mapping here — one wire contract for
# rows and aggregates. The case-sensitive family rides `_similar`, which needs
# LIKE-pattern conversion the seam cannot express; Angee's toolbar does not
# offer it (see `ANGEE_TEXT_FILTER_LOOKUP_OPERATORS`).
hasura_filtering._LOOKUPS["iregex"] = ("__iregex", False)


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
    child field metadata the frontend line cells render. ``position_field`` names
    the integer order column (advertised so the composer maintains it).

    Completeness contract: ``<res>_save(lines=…)`` takes the **full desired child
    set** — deletion is by omission, so an id absent from the set is deleted. The
    caller must therefore send back every stored line (each kept row carrying its
    public id); a partial read that omits stored lines would ask to delete them.
    The write enforces the enforceable half of this contract server-side: every
    public id the caller sends must address a currently stored line of this parent
    (a stale, foreign, or truncated baseline is rejected wholesale with a
    ``ValidationError`` rather than silently mis-applied). The full desired set is
    resolved under a parent-row lock so concurrent saves cannot cross-delete each
    other's lines.
    """

    field: str
    model: type[models.Model]
    node: type | None = None
    writable: Sequence[str] | None = None
    public_id_fields: Sequence[str] = ()
    position_field: str = "position"


def _child_back_fk(parent_model: type[models.Model], relation: str) -> str:
    """Return the child FK field name behind a parent's to-many ``relation``."""

    reverse = parent_model._meta.get_field(relation)
    field = getattr(reverse, "field", None)
    if field is None:
        raise ImproperlyConfigured(
            f"{parent_model._meta.label}.{relation} is not a to-many child relation."
        )
    return field.name


class AngeeHasuraWriteBackend:
    """Authorized write backend for Angee Hasura resources.

    ``strawberry-django-hasura`` owns the Hasura mutation envelope. This class
    owns the Angee write semantics inside that envelope: Django validation,
    REBAC row-scoped write targets, model save/delete signals, and returning a
    deleted instance in Hasura's ``delete_<res>_by_pk`` shape.
    """

    def __init__(
        self,
        model: type[models.Model],
        *,
        public_id_fields: Iterable[str] | None = None,
        delete_guard: Callable[[models.Model], str | None] | None = None,
        lines: HasuraLines | None = None,
    ) -> None:
        self.model = model
        self.public_id_fields = _public_id_field_models(model, public_id_fields or ())
        self.delete_guard = delete_guard
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

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create one row (and any declared nested child lines) atomically."""

        if self.lines is None:
            return self._create_row(info, data)
        with transaction.atomic():
            line_rows = self._pop_line_rows(data)
            instance = self._create_row(info, data)
            if line_rows is not None:
                self._apply_line_diff(info, instance, line_rows)
            return instance

    def save(
        self,
        info: strawberry.Info,
        pk: str,
        patch: dict[str, Any],
        line_rows: list[dict[str, Any]] | None,
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
                queryset=self.write_target_queryset(),
            )
            if not instance.has_access("write"):
                raise PermissionDenied(f"Denied: cannot write {self.model._meta.label} {pk!r}")
            if patch:
                instance = mutation_resolvers.update(
                    info,
                    instance,
                    self._decode_public_id_fields(patch),
                    key_attr=PUBLIC_ID_FIELD_NAME,
                    full_clean=True,
                )
            if line_rows is not None:
                self._apply_line_diff(info, instance, line_rows)
            return instance

    def _create_row(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create one row through strawberry-django's stock mutation resolver."""

        decoded_data, relationships = self._decode_public_id_fields_with_relationships(data)
        check_create = getattr(self.model._default_manager, "check_create", None)
        if not callable(check_create):
            if requires_angee_rebac_contract(self.model):
                raise ImproperlyConfigured(f"{self.model._meta.label} manager must expose check_create().")
            return mutation_resolvers.create(
                info,
                self.model,
                decoded_data,
                key_attr=PUBLIC_ID_FIELD_NAME,
                full_clean=True,
            )

        verified_actor: Any | None = None

        def pre_save_hook(instance: models.Model) -> None:
            nonlocal verified_actor
            # The gate must see the row as it will persist: let the model apply
            # its blank-on-input create defaults (a CompanyScopedMixin ``company``
            # defaulted from the actor's sole membership) before gating, and fold
            # the subject relations those defaults add into the preflight. A
            # caller-supplied relation always wins the merge, so an explicit
            # cross-company id still rides the gate — no bypass through the default.
            apply_defaults = getattr(instance, "apply_create_defaults", None)
            default_relationships = apply_defaults() if callable(apply_defaults) else {}
            verified_actor = check_create({**default_relationships, **relationships})
            sudo = getattr(instance, "sudo", None)
            if callable(sudo):
                sudo(reason="graphql.hasura.create")

        instance = mutation_resolvers.create(
            info,
            self.model,
            decoded_data,
            key_attr=PUBLIC_ID_FIELD_NAME,
            full_clean=True,
            pre_save_hook=pre_save_hook,
        )
        bind_actor(instance, verified_actor)
        return instance

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
    ) -> None:
        """Create/update/delete child lines to match ``rows`` under elevation.

        A row with an ``id`` addresses an existing child (update); a row without
        one is a new child (create); an existing child no row keeps is deleted.
        The child FK back to the parent is set here, never sent by the client.

        Two phases with two authorities. Relation public ids on the incoming
        rows are decoded first, under the **caller's** actor, so a referenced row
        the caller cannot see is rejected (never resolved by the elevation that
        follows). Only then do the child writes run under ``system_context`` —
        the parent write is their gate (§3.4). Reached by both ``save`` and the
        nested ``create`` path; the parent row is locked before its child set is
        read so concurrent saves cannot cross-delete each other's lines.
        """

        assert self.lines is not None
        child_model = self.lines.model
        back_fk_id = f"{self._line_back_fk}_id"
        # Phase 1 — decode line relation ids under the caller's actor, before any
        # elevation. Each entry is ``(public id | None, decoded child payload)``.
        prepared: list[tuple[str | None, dict[str, Any]]] = []
        for row in rows:
            payload = dict(row)
            public_id = payload.pop("id", None)
            decoded = self._decode_public_id_fields(payload, self._line_public_id_fields)
            prepared.append((str(public_id) if public_id else None, decoded))
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
            kept_pks: set[Any] = set()
            for public_id, decoded in prepared:
                if public_id is not None:
                    child = by_public_id[public_id]
                    kept_pks.add(child.pk)
                    mutation_resolvers.update(
                        info,
                        child,
                        decoded,
                        key_attr=PUBLIC_ID_FIELD_NAME,
                        full_clean=True,
                    )
                else:
                    mutation_resolvers.create(
                        info,
                        child_model,
                        {**decoded, back_fk_id: parent.pk},
                        key_attr=PUBLIC_ID_FIELD_NAME,
                        full_clean=True,
                    )
            removed = set(existing) - kept_pks
            if removed:
                child_model._base_manager.filter(pk__in=removed).delete()

    def update(self, info: strawberry.Info, pk: str, data: dict[str, Any]) -> Any:
        """Patch one public-id-addressed row through the write queryset."""

        instance = require_instance_for_id(
            self.model,
            pk,
            queryset=self.write_target_queryset(),
        )
        with transaction.atomic():
            return mutation_resolvers.update(
                info,
                instance,
                self._decode_public_id_fields(data),
                key_attr=PUBLIC_ID_FIELD_NAME,
                full_clean=True,
            )

    def delete(self, info: strawberry.Info, pk: str) -> Any | None:
        """Delete one public-id-addressed row and return the deleted instance."""

        del info

        def guard(instance: models.Model) -> None:
            if self.delete_guard is None:
                return
            message = self.delete_guard(instance)
            if message:
                raise ValueError(message)

        preview = delete_by_public_id(
            self.model,
            str(pk),
            confirm=True,
            queryset=self.write_target_queryset(),
            before_delete=guard,
        )
        if preview.has_blockers:
            return None
        return preview.deleted_instance

    def _decode_public_id_fields(
        self,
        data: dict[str, Any],
        public_id_fields: Mapping[str, type[models.Model]] | None = None,
    ) -> dict[str, Any]:
        """Translate public-id relation fields to Django-native write values."""

        decoded, _relationships = self._decode_public_id_fields_with_relationships(
            data,
            public_id_fields,
        )
        return decoded

    def _decode_public_id_fields_with_relationships(
        self,
        data: dict[str, Any],
        public_id_fields: Mapping[str, type[models.Model]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, tuple[Any, ...]]]:
        """Translate public-id relation fields and keep relationship instances.

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
        relationships: dict[str, tuple[Any, ...]] = {}
        for key, value in data.items():
            related_model = field_models.get(key)
            if related_model is None:
                out[key] = value
                continue
            try:
                field = owner_model._meta.get_field(key)
            except FieldDoesNotExist:
                field = None
            if getattr(field, "many_to_many", False):
                instances = (
                    tuple(_write_public_instance(related_model, item) for item in value)
                    if value is not None
                    else ()
                )
                out[key] = list(instances) if value is not None else None
                if instances:
                    relationships[key] = instances
                continue
            instance = _write_public_instance(related_model, value)
            out[f"{key}_id"] = None if instance is None else instance.pk
            if instance is not None:
                relationships[key] = (instance,)
        return out, relationships


def public_pk_decoder(model: type[models.Model]) -> Callable[[Any], Any]:
    """Return a decoder from Angee public id to database primary key."""

    return lambda value: _public_pk(model, value)


def _relation_filter_decoders(
    model: type[models.Model],
    *,
    filterable: Sequence[str],
    declared: Mapping[str, Callable[[Any], Any]] | None,
) -> Mapping[str, Callable[[Any], Any]] | None:
    """Return the filter decoders for filterable public-id relation columns.

    A filterable foreign-key / one-to-one column whose related model carries a
    public identity is filtered by that related row's public id — the node
    projects the relation as one — so its ``bool_exp`` operand is a public id,
    never the raw primary key. Each such operand resolves through the related
    model's identity owner (:func:`public_pk_decoder` →
    :func:`instance_from_public_id`, which also keeps a raw primary key working)
    instead of being handed to the ORM raw, which rejects a sqid on a numeric
    key. A caller-declared ``field_id_decode`` always wins, so an explicit
    write-scoped decoder is never overridden.
    """

    decoders = dict(declared or {})
    for name in filterable:
        if name in decoders:
            continue
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        if not isinstance(field, models.Field) or not is_to_one_relation(field):
            continue
        related = field.related_model
        if not isinstance(related, type) or public_data_id_field(related) is None:
            continue
        decoders[name] = public_pk_decoder(related)
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
            raise ImproperlyConfigured(
                f"{model._meta.label} public id field {name!r} does not exist."
            ) from error
        related_model = getattr(field, "related_model", None)
        if not isinstance(related_model, type) or not issubclass(related_model, models.Model):
            raise ImproperlyConfigured(
                f"{model._meta.label} public id field {name!r} must be a relation."
            )
        related[name] = related_model
    return related


def aggregate_queryset(queryset: models.QuerySet[Any]) -> models.QuerySet[Any]:
    """Return the aggregate-safe variant of a REBAC queryset when available.

    A REBAC-scoped queryset exposes ``scoped_for_aggregate`` to drop row-fanout
    joins before aggregation; a plain queryset has no such method and is returned
    unchanged. Resources with a custom aggregate source wrap it through here.
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
    importing it.
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
                model._meta.get_field(field)
            except FieldDoesNotExist as error:
                raise ImproperlyConfigured(
                    f"{cls.__module__}.{cls.__name__}.{attribute} declares unknown field {field!r} "
                    f"on {model._meta.label}."
                ) from error
            if field not in fields:
                fields.append(field)
    return tuple(fields)


def _public_pk(model: type[models.Model], value: Any) -> Any:
    """Decode one public id through the identity owner, not row permissions."""

    instance = _public_instance(model, value)
    return None if instance is None else instance.pk


def _write_public_instance(model: type[models.Model], value: Any) -> Any:
    """Decode one write relation public id through the actor-scoped write owner."""

    return _public_instance(model, value, queryset=write_queryset(model))


def _public_instance(
    model: type[models.Model],
    value: Any,
    *,
    queryset: models.QuerySet[Any] | None = None,
) -> Any:
    """Decode one public id to a model instance through the identity owner."""

    if value in (None, ""):
        return None
    active_queryset = queryset if queryset is not None else model._base_manager.all()
    instance = instance_from_public_id(
        model,
        str(value),
        queryset=active_queryset,
    )
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {value!r} was not found")
    return instance


def _make_json_groups_field(  # noqa: PLR0913 - mirrors strawberry-django-hasura's groups field.
    *,
    builder: Any,
    built: Any,
    resource_name: str,
    filter_type: type,
    get_queryset: Callable[[strawberry.Info], models.QuerySet[Any]],
    json_paths: Mapping[str, str],
    id_decode: Callable[[Any], Any] | None = None,
    id_column: str = "pk",
    field_decoders: Mapping[str, Callable[[Any], Any]] | None = None,
    max_groups: int | None = None,
) -> tuple[Any, list[type]]:
    """Return a Hasura groups field that carries JSON-paths into execution."""

    module = hasura_grouping._host_module(resource_name)
    group_key_type = built.group_key_type
    aggregate_type = built.aggregate_type
    group_by_spec = built.group_by_spec
    having_input = built.having_input
    group_order_input = built.group_order_input
    group_type = strawberry.type(
        type(
            f"{resource_name}_group",
            (),
            {
                "__module__": module.__name__,
                "__annotations__": {
                    "key": group_key_type,
                    "aggregate": aggregate_type,
                },
            },
        ),
        name=f"{resource_name}_group",
    )
    setattr(module, f"{resource_name}_group", group_type)

    def resolve_groups(
        self: Any,
        info: strawberry.Info,
        group_by: list[Any],
        where: Any = None,
        having: Any = None,
        order_by: Any = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Any]:
        del self
        qs = get_queryset(info)
        if where is not None:
            qs = qs.filter(
                hasura_filtering.where_to_q(
                    where,
                    id_column=id_column,
                    id_decode=id_decode,
                    field_decoders=field_decoders,
                )
            )
        spec = builder.translate_group_by(group_by)
        requested = hasura_grouping._requested_group_ops(info)
        having_dict = builder.translate_having(having, requested)
        order_terms = builder.translate_order_by(order_by, spec, requested)
        rows = compute_aggregation(
            qs,
            group_by=spec,
            aggregates=requested,
            having=having_dict,
            order_by=order_terms,
            limit=hasura_grouping._capped(limit, max_groups),
            offset=offset or 0,
            json_paths=dict(json_paths),
        )
        return [
            group_type(
                key=builder.shape_group_key(group_key_type, row, spec),
                aggregate=shape_aggregate_row(
                    aggregate_type,
                    row,
                    requested,
                    json_paths=dict(json_paths),
                ),
            )
            for row in rows
        ]

    resolve_groups.__annotations__ = {
        "self": Any,
        "info": strawberry.Info,
        "group_by": list[group_by_spec],  # type: ignore[valid-type]
        "where": filter_type | None,
        "having": having_input | None,
        "order_by": list[group_order_input] | None,  # type: ignore[valid-type]
        "limit": int | None,
        "offset": int | None,
        "return": list[group_type],  # type: ignore[valid-type]
    }
    return (
        strawberry.field(
            resolver=resolve_groups,
            name=f"{resource_name}_groups",
        ),
        [
            group_type,
            group_key_type,
            group_by_spec,
            having_input,
            group_order_input,
        ],
    )


def hasura_model_resource(  # noqa: PLR0913 - mirrors the upstream declarative builder.
    node: type,
    *,
    model: type[models.Model],
    name: str | None = None,
    filterable: Sequence[str],
    sortable: Sequence[str],
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
    declared_fields: Sequence[str] = (),
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
    row_model: str = "server",
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
    """

    resource_name = name or model.__name__.lower()
    read_queryset = get_queryset or _model_queryset(model)
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
    active_json_paths = dict(json_paths or {})
    aggregate_builder_globals = build_hasura_resource.__globals__
    original_aggregate_builder = None
    original_groups_field_builder = None
    if active_json_paths:
        # strawberry-django-aggregates supports JSON-path group axes, but the
        # strawberry-django-hasura facade has not exposed that knob yet. Keep
        # the adaptation local to resource construction so Angee can advertise
        # the same typed group surface without forking the upstream builder.
        original_aggregate_builder = aggregate_builder_globals["AggregateBuilder"]
        original_groups_field_builder = aggregate_builder_globals["make_groups_field"]
        json_path_allowlist = dict(active_json_paths)

        class _JsonPathAggregateBuilder(original_aggregate_builder):  # type: ignore[misc, valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("json_paths", json_path_allowlist)
                super().__init__(*args, **kwargs)

        def _make_json_path_groups_field(**kwargs: Any) -> tuple[Any, list[type]]:
            return _make_json_groups_field(json_paths=json_path_allowlist, **kwargs)

        aggregate_builder_globals["AggregateBuilder"] = _JsonPathAggregateBuilder
        aggregate_builder_globals["make_groups_field"] = _make_json_path_groups_field
    try:
        resource = build_hasura_resource(
            node,
            model=model,
            name=name,
            filterable=list(filterable),
            sortable=list(sortable),
            aggregatable=list(aggregatable),
            groupable=list(groupable) or None,
            writable=list(writable) if writable is not None else None,
            insertable=list(insertable) if insertable is not None else None,
            updatable=list(updatable) if updatable is not None else None,
            nested=_nested_inserts(lines) if lines is not None else None,
            insert=insert,
            update=update,
            delete=delete,
            field_id_decode=field_id_decode,
            get_queryset=read_queryset,
            get_aggregate_queryset=get_aggregate_queryset or _aggregate_queryset(read_queryset),
            write_backend=active_write_backend,
            id_decode=id_decode,
            id_column=id_column,
        )
    finally:
        if original_groups_field_builder is not None:
            aggregate_builder_globals["make_groups_field"] = original_groups_field_builder
        if original_aggregate_builder is not None:
            aggregate_builder_globals["AggregateBuilder"] = original_aggregate_builder
    if lines is not None:
        resource = _attach_lines_save(resource, node=node, lines=lines, write_backend=active_write_backend)
    return attach_hasura_resource_metadata(
        resource,
        node=node,
        model=model,
        name=resource_name,
        filterable=tuple(filterable),
        sortable=tuple(sortable),
        aggregatable=tuple(aggregatable),
        groupable=tuple(groupable),
        json_paths=active_json_paths,
        insert=insert,
        update=update,
        delete=delete,
        lines=lines,
        declared_fields=tuple(declared_fields),
        model_label=model_label,
        public_id_field=public_id_field,
        row_model=row_model,
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

    def resolve_save(
        self: Any,
        info: strawberry.Info,
        pk: PublicID,
        patch: Any = None,
        lines: Any = None,
    ) -> Any:
        patch_data = input_to_dict(patch) if patch is not None else {}
        rows = None if lines is None else [input_to_dict(row) for row in lines]
        return write_backend.save(info, str(pk), patch_data, rows)

    annotations: dict[str, Any] = {"self": Any, "info": strawberry.Info, "pk": PublicID}
    annotations["patch"] = patch_type | None
    annotations["lines"] = _types.GenericAlias(list, (line_input,)) | None
    annotations["return"] = node
    resolve_save.__annotations__ = annotations

    save_holder = strawberry.type(
        type(
            f"{res}__save_mutation",
            (),
            {save_root: strawberry.mutation(resolver=resolve_save, name=save_root)},
        )
    )
    combined_mutation = strawberry.type(
        type(f"{res}__mutation", (resource.mutation, save_holder), {})
    )
    return dataclasses.replace(resource, mutation=combined_mutation)


def attach_hasura_resource_metadata(
    resource: HasuraResource,
    *,
    node: type,
    model: type[models.Model],
    name: str,
    filterable: tuple[str, ...],
    sortable: tuple[str, ...],
    aggregatable: tuple[str, ...],
    groupable: tuple[str, ...] = (),
    json_paths: Mapping[str, str] | None = None,
    insert: bool = True,
    update: bool = True,
    delete: bool = True,
    lines: HasuraLines | None = None,
    declared_fields: tuple[str, ...] = (),
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
    row_model: str = "server",
) -> HasuraResource:
    """Attach Angee resource metadata to a built Hasura resource bundle."""

    list_root = resource_attr(resource, "list_root", name)
    detail_root = resource_attr(resource, "detail_root", f"{name}_by_pk")
    aggregate_root = resource_attr(resource, "aggregate_root", f"{name}_aggregate")
    groups_root = resource_attr(resource, "groups_root", f"{name}_groups")
    filter_type = resource_attr(
        resource,
        "filter_type",
        resource_type_by_name(resource, f"{name}_bool_exp"),
    )
    order_by_type = resource_attr(
        resource,
        "order_by_type",
        resource_type_by_name(resource, f"{name}_order_by"),
    )
    aggregate_container_type = resource_attr(
        resource,
        "aggregate_container_type",
        resource_type_by_name(resource, f"{name}_aggregate"),
    )
    group_type = resource_attr(
        resource,
        "group_type",
        resource_type_by_name(resource, f"{name}_group") if groupable else None,
    )
    group_key_type = resource_attr(
        resource,
        "group_key_type",
        resource_type_by_suffix(resource, "GroupKey") if groupable else None,
    )
    group_by_spec_type = resource_attr(
        resource,
        "group_by_spec_type",
        resource_type_by_suffix(resource, "GroupBySpec") if groupable else None,
    )
    group_order_type = resource_attr(
        resource,
        "group_order_type",
        resource_type_by_suffix(resource, "GroupOrder") if groupable else None,
    )
    having_type = resource_attr(
        resource,
        "having_type",
        resource_type_by_suffix(resource, "Having") if groupable else None,
    )
    insert_input_type = resource_attr(
        resource,
        "insert_input_type",
        resource_type_by_name(resource, f"{name}_insert_input") if insert else None,
    )
    set_input_type = resource_attr(
        resource,
        "set_input_type",
        resource_type_by_name(resource, f"{name}_set_input") if update else None,
    )
    insert_one_root = resource_attr(resource, "insert_one_root", f"insert_{name}_one")
    update_by_pk_root = resource_attr(resource, "update_by_pk_root", f"update_{name}_by_pk")
    delete_by_pk_root = resource_attr(resource, "delete_by_pk_root", f"delete_{name}_by_pk")

    parent_create_fields = (
        resource_wire_field_names(insert_input_type, exclude=_parent_write_exclude(lines))
        if insert
        else ()
    )
    parent_update_fields = resource_wire_field_names(set_input_type, exclude=("id",)) if update else ()
    active_json_paths = dict(json_paths or {})
    fields = model_resource_fields(
        model,
        declared_fields,
        filter_fields=filterable,
        order_fields=sortable,
        aggregate_fields=aggregatable,
        group_by_fields=groupable,
        create_fields=parent_create_fields,
        update_fields=parent_update_fields,
    )
    if detail_root is None:
        raise ImproperlyConfigured(f"{model._meta.label} Hasura resource did not expose a detail root.")
    attach_data_resource_metadata(
        resource.query,
        make_data_resource_metadata(
            model=model,
            model_label=model_label,
            public_id_field=public_id_field,
            node_type=node,
            filter_type=filter_type,
            order_type=order_by_type,
            roots=DataResourceRoots(
                list_name=resource_wire_field_name(resource.query, str(list_root or name)),
                detail_name=resource_wire_field_name(resource.query, str(detail_root)),
                aggregate_name=resource_wire_field_name(
                    resource.query,
                    str(aggregate_root or f"{name}_aggregate"),
                ),
                group_name=(
                    resource_wire_field_name(resource.query, str(groups_root))
                    if groupable and groups_root is not None
                    else None
                ),
            ),
            type_names=DataResourceTypeNames(
                query=resource_type_name(resource.query),
                node=resource_type_name(node),
                filter=resource_type_name(filter_type),
                order=resource_type_name(order_by_type),
                aggregate=resource_type_name(aggregate_container_type),
                grouped=resource_type_name(group_type),
                group_key=resource_type_name(group_key_type),
                group_by_spec=resource_type_name(group_by_spec_type),
                group_order=resource_type_name(group_order_type),
                having=resource_type_name(having_type),
            ),
            capabilities=("list", "detail", "aggregate", *(("groups",) if groupable else ())),
            filter_fields=filterable,
            order_fields=sortable,
            aggregate_fields=aggregatable,
            group_by_fields=groupable,
            group_dimensions=_hasura_group_dimensions(model, groupable, filterable, json_paths=active_json_paths),
            aggregate_measures=_hasura_aggregate_measures(model, aggregatable),
            default_measures=(DataAggregateMeasureMetadata(op="count"),),
            fields=fields,
            row_model=row_model,
        ),
    )
    mutation_capabilities = _mutation_capabilities(
        insert=insert,
        update=update,
        delete=delete,
    )
    if lines is not None:
        mutation_capabilities = (*mutation_capabilities, "save")
    if mutation_capabilities:
        save_root = (
            resource_wire_field_name(resource.mutation, f"{name}_save")
            if lines is not None
            else None
        )
        attach_data_resource_metadata(
            resource.mutation,
            make_data_resource_metadata(
                model=model,
                model_label=model_label,
                public_id_field=public_id_field,
                node_type=node,
                roots=DataResourceRoots(
                    create_name=(
                        resource_wire_field_name(
                            resource.mutation,
                            insert_one_root,
                        )
                        if insert and insert_one_root is not None
                        else None
                    ),
                    update_name=(
                        resource_wire_field_name(
                            resource.mutation,
                            update_by_pk_root,
                        )
                        if update and update_by_pk_root is not None
                        else None
                    ),
                    save_name=save_root,
                    delete_name=(
                        resource_wire_field_name(
                            resource.mutation,
                            delete_by_pk_root,
                        )
                        if delete and delete_by_pk_root is not None
                        else None
                    ),
                ),
                type_names=DataResourceTypeNames(
                    node=resource_type_name(node),
                    create_input=resource_type_name(insert_input_type),
                    update_input=resource_type_name(set_input_type),
                ),
                create_input_type=insert_input_type,
                update_input_type=set_input_type,
                create_fields=parent_create_fields,
                update_fields=parent_update_fields,
                lines=_line_metadata(lines, resource) if lines is not None else None,
                capabilities=mutation_capabilities,
            ),
        )
    return resource


def _parent_write_exclude(lines: HasuraLines | None) -> tuple[str, ...]:
    """Return parent create-field wire names to skip (id + the lines envelope)."""

    return ("id",) if lines is None else ("id", lines.field)


def _line_metadata(lines: HasuraLines, resource: HasuraResource) -> DataLinesMetadata:
    """Return the frontend editable-lines contract for a document resource."""

    line_input = resource.nested_input_types.get(lines.field)
    child_fields = resource_wire_field_names(line_input, exclude=("id",))
    return DataLinesMetadata(
        field=lines.field,
        model_label=lines.model._meta.label,
        input_type=resource_type_name(line_input),
        fields=_line_child_fields(lines, child_fields),
        position_field=lines.position_field if _has_model_field(lines.model, lines.position_field) else None,
    )


def _line_child_fields(
    lines: HasuraLines,
    child_fields: tuple[str, ...],
) -> tuple[DataResourceFieldMetadata, ...]:
    """Return per-column metadata for a document's editable child fields.

    The child **node** surface owns each field's projected shape — an enum's
    values, a relation/list target — so the line cells read it there through the
    same :func:`resource_fields` classifier the parent resource uses, instead of
    re-deriving enum members and item shapes from the model (which the bare model
    reconstruction cannot do). An M2M child is a ``kind="list"`` relation whose
    target the frontend renders as a multi-select and persists as public ids; an
    enum child carries its wire values. A writable child column the node does not
    project (a write-only relation) falls back to the model reconstruction.
    """

    wanted = set(child_fields)
    by_name: dict[str, DataResourceFieldMetadata] = {
        field.name: field for field in _line_node_fields(lines, child_fields) if field.name in wanted
    }
    unprojected = tuple(name for name in child_fields if name not in by_name)
    for field in model_resource_fields(
        lines.model,
        unprojected,
        create_fields=unprojected,
        update_fields=unprojected,
    ):
        by_name[field.name] = field
    return tuple(by_name[name] for name in child_fields)


def _line_node_fields(
    lines: HasuraLines,
    child_fields: tuple[str, ...],
) -> tuple[DataResourceFieldMetadata, ...]:
    """Return node-surface field metadata for the editable child columns."""

    if lines.node is None:
        return ()
    return resource_fields(
        lines.node,
        lines.model,
        filter_fields=(),
        order_fields=(),
        aggregate_fields=(),
        group_by_fields=(),
        create_fields=child_fields,
        update_fields=child_fields,
        required_create_fields=(),
        relation_axes=(),
    )


def _has_model_field(model: type[models.Model], name: str) -> bool:
    """Return whether ``model`` declares a field named ``name``."""

    try:
        model._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return True

def _mutation_capabilities(
    *,
    insert: bool,
    update: bool,
    delete: bool,
) -> tuple[str, ...]:
    """Return mutation capabilities in the resource metadata order."""

    return tuple(
        name
        for name, enabled in (
            ("create", insert),
            ("update", update),
            ("delete", delete),
        )
        if enabled
    )


def _hasura_group_dimensions(
    model: type[models.Model],
    groupable: tuple[str, ...],
    filterable: tuple[str, ...],
    *,
    json_paths: Mapping[str, str] | None = None,
) -> tuple[DataGroupDimensionMetadata, ...]:
    """Return typed-key group metadata using the aggregate builder's public contract."""

    active_json_paths = dict(json_paths or {})
    return tuple(
        _hasura_group_dimension(model, path, filterable, json_paths=active_json_paths)
        for path in groupable
    )


def _hasura_group_dimension(
    model: type[models.Model],
    path: str,
    filterable: tuple[str, ...],
    *,
    json_paths: Mapping[str, str] | None = None,
) -> DataGroupDimensionMetadata:
    declared_json_type = (json_paths or {}).get(path)
    if declared_json_type is not None:
        key = group_by_alias(path, None)
        filter_metadata = _hasura_json_group_bucket_filter(model, path, key)
        return DataGroupDimensionMetadata(
            field=path,
            input=_group_input_name(path),
            key=key,
            kind="json",
            scalar=_scalar_for_json_group_type(declared_json_type),
            filter=filter_metadata,
            extractions=_hasura_json_group_extractions(path, declared_json_type, key),
        )
    field = _require_group_field(model, path)
    key = _group_key_path(field, path)
    is_relation = "__" not in path and is_to_one_relation(field)
    filter_metadata = _hasura_group_bucket_filter(
        field,
        path,
        key,
        filterable=filterable,
        is_relation=is_relation,
    )
    return DataGroupDimensionMetadata(
        field=path,
        input=_group_input_name(path),
        key=key,
        kind="relation" if is_relation else "column",
        scalar="ID" if is_relation else _scalar_for_field(field),
        filter=filter_metadata,
        extractions=_hasura_group_extractions(field, key, filter_metadata),
    )


def _hasura_group_extractions(
    field: models.Field[Any, Any],
    key: str,
    bucket_filter: DataGroupBucketFilterMetadata | None,
) -> tuple[DataGroupExtractionMetadata, ...]:
    if not isinstance(field, (models.DateField, models.DateTimeField)):
        return ()
    extractions: list[DataGroupExtractionMetadata] = []
    for granularity in (*TimeGranularity, *NumberGranularity):
        extraction_key = f"{key}_{granularity.value}"
        range_key = f"{key}_{granularity.value}_range" if isinstance(granularity, TimeGranularity) else None
        extractions.append(
            DataGroupExtractionMetadata(
                name=granularity.value,
                input=granularity.name,
                key=extraction_key,
                range_key=range_key,
                filter=(
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


def _hasura_json_group_extractions(
    path: str,
    declared_type: str,
    key: str,
) -> tuple[DataGroupExtractionMetadata, ...]:
    """Return date/datetime extraction metadata for a JSON-path group axis."""

    if declared_type not in {"date", "datetime"}:
        return ()
    extractions: list[DataGroupExtractionMetadata] = []
    for granularity in (*TimeGranularity, *NumberGranularity):
        extraction_key = group_by_alias(path, granularity)
        range_key = f"{extraction_key}_range" if isinstance(granularity, TimeGranularity) else None
        extractions.append(
            DataGroupExtractionMetadata(
                name=granularity.value,
                input=granularity.name,
                key=extraction_key,
                range_key=range_key,
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
) -> DataGroupBucketFilterMetadata | None:
    """Return the backend-owned drill-down filter for a group dimension."""

    filter_field = _group_filter_field(path, filterable)
    if filter_field is None:
        return None
    if is_relation:
        return DataGroupBucketFilterMetadata(
            kind="equality",
            field=filter_field,
            value_key=key,
            lookup=PUBLIC_ID_FIELD_NAME,
        )
    if isinstance(field, models.JSONField):
        return DataGroupBucketFilterMetadata(
            kind="equality",
            field=filter_field,
            value_key=key,
            lookup="exact",
            value_transform="json",
        )
    return DataGroupBucketFilterMetadata(
        kind="equality",
        field=filter_field,
        value_key=key,
        value_map=_enum_value_map_for_field(field),
    )


def _hasura_json_group_bucket_filter(
    model: type[models.Model],
    path: str,
    key: str,
) -> DataGroupBucketFilterMetadata | None:
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
    return DataGroupBucketFilterMetadata(
        kind="equality",
        field=root,
        value_key=key,
        lookup="jsonContains",
        null_lookup=None,
        value_transform=f"jsonObject:{'.'.join(json_path)}",
    )


def _hasura_group_range_filter(
    bucket_filter: DataGroupBucketFilterMetadata | None,
    *,
    value_key: str,
    range_key: str,
) -> DataGroupBucketFilterMetadata | None:
    if bucket_filter is None:
        return None
    return DataGroupBucketFilterMetadata(
        kind="range",
        field=bucket_filter.field,
        value_key=value_key,
        range_key=range_key,
        null_lookup=bucket_filter.null_lookup,
    )


def _group_filter_field(path: str, filterable: tuple[str, ...]) -> str | None:
    """Return the declared bool-exp field that can filter one group path."""

    normalized = path.replace(".", "__")
    for candidate in (path, normalized):
        if candidate in filterable:
            return candidate
    return None


def _enum_value_map_for_field(
    field: models.Field[Any, Any],
) -> tuple[DataGroupBucketFilterValueMapMetadata, ...]:
    choices_enum = getattr(field, "choices_enum", None)
    members = getattr(choices_enum, "__members__", None)
    if not members:
        return ()
    return tuple(
        DataGroupBucketFilterValueMapMetadata(
            from_value=str(name),
            to_value=str(member.value),
        )
        for name, member in members.items()
    )


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


def _group_input_name(path: str) -> str:
    """Return the generated ``<Model>GroupableField`` enum member name."""

    return path.replace(".", "__").upper()


def _group_key_path(
    field: models.Field[Any, Any],
    path: str,
) -> str:
    """Return the typed ``<Model>GroupKey`` field for one group axis.

    The FK-alias rule (many-to-one → ``<path>_id``) is owned upstream by
    ``group_by_alias``, which appends ``_id`` only for a many-to-one
    relation; we gate on ``field.many_to_one`` so Angee's alias decision
    is exactly what ``group_by_alias`` produces (a pure one-to-one axis
    keeps its bare path there, not ``<path>_id``). Only the Angee
    dotted-path normalization (``.`` → ``__``) for non-relation axes
    stays local (no owner).
    """

    if "__" not in path and getattr(field, "many_to_one", False):
        return group_by_alias(path, None, field)
    return path.replace(".", "__")


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


def _scalar_for_field(field: models.Field[Any, Any]) -> str | None:
    """Return the group-dimension key scalar for a field; String columns carry none."""

    scalar = model_field_scalar(field)
    return None if scalar == "String" else scalar


def _scalar_for_json_group_type(declared_type: str) -> str | None:
    """Return the group-dimension key scalar for a declared JSON-path type."""

    return {
        "str": None,
        "int": "Int",
        "float": "Float",
        "Decimal": "Decimal",
        "bool": "Boolean",
        "date": "Date",
        "datetime": "DateTime",
    }.get(declared_type)
