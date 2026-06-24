"""Angee metadata bridge for ``strawberry-django-hasura`` resources."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import strawberry
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models, transaction
from strawberry.types import get_object_definition
from strawberry_django.mutations import resolvers as mutation_resolvers
from strawberry_django_aggregates.granularity import NumberGranularity, TimeGranularity
from strawberry_django_hasura import (
    HasuraResource,
    WriteBackend,
)
from strawberry_django_hasura import (
    hasura_resource as build_hasura_resource,
)

from angee.base.models import instance_from_public_id
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.metadata import (
    DataAggregateMeasureMetadata,
    DataGroupDimensionMetadata,
    DataGroupExtractionMetadata,
    DataResourceRoots,
    DataResourceTypeNames,
    attach_data_resource_metadata,
    make_data_resource_metadata,
    resource_type_name,
    resource_wire_field_name,
)
from angee.graphql.deletion import delete_by_public_id
from angee.graphql.ids import require_instance_for_id
from angee.graphql.writes import write_queryset


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
        public_id_fields: Mapping[str, type[models.Model]] | None = None,
    ) -> None:
        self.model = model
        self.public_id_fields = dict(public_id_fields or {})

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create through strawberry-django's stock mutation resolver."""

        return mutation_resolvers.create(
            info,
            self.model,
            self._decode_public_id_fields(data),
            key_attr=PUBLIC_ID_FIELD_NAME,
            full_clean=True,
        )

    def update(self, info: strawberry.Info, pk: str, data: dict[str, Any]) -> Any:
        """Patch one public-id-addressed row through the write queryset."""

        instance = require_instance_for_id(
            self.model,
            pk,
            queryset=write_queryset(self.model),
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
        captured: dict[str, Any] = {}

        def capture(instance: models.Model) -> None:
            captured["instance"] = instance
            captured["pk"] = instance.pk

        preview = delete_by_public_id(
            self.model,
            str(pk),
            confirm=True,
            queryset=write_queryset(self.model),
            before_delete=capture,
        )
        if preview.has_blockers or "instance" not in captured:
            return None
        instance = captured["instance"]
        instance.pk = captured["pk"]
        return instance

    def _decode_public_id_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        """Translate public-id relation fields to Django-native write values."""

        out: dict[str, Any] = {}
        for key, value in data.items():
            related_model = self.public_id_fields.get(key)
            if related_model is None:
                out[key] = value
                continue
            try:
                field = self.model._meta.get_field(key)
            except FieldDoesNotExist:
                field = None
            if getattr(field, "many_to_many", False):
                out[key] = (
                    [_public_instance(related_model, item) for item in value]
                    if value is not None
                    else None
                )
                continue
            out[f"{key}_id"] = _public_pk(related_model, value)
        return out


def public_pk_decoder(model: type[models.Model]) -> Callable[[Any], Any]:
    """Return a decoder from Angee public id to database primary key."""

    return lambda value: _public_pk(model, value)


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


def _public_instance(model: type[models.Model], value: Any) -> Any:
    """Decode one public id to a model instance through the identity owner."""

    if value in (None, ""):
        return None
    instance = instance_from_public_id(
        model,
        str(value),
        queryset=model._base_manager.all(),
    )
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {value!r} was not found")
    return instance


def hasura_resource(  # noqa: PLR0913 - mirrors the upstream declarative builder.
    node: type,
    *,
    model: type[models.Model],
    name: str | None = None,
    filterable: Sequence[str],
    sortable: Sequence[str],
    aggregatable: Sequence[str],
    groupable: Sequence[str] = (),
    writable: Sequence[str] | None = None,
    insertable: Sequence[str] | None = None,
    updatable: Sequence[str] | None = None,
    insert: bool = True,
    update: bool = True,
    delete: bool = True,
    field_id_decode: Mapping[str, Callable[[Any], Any]] | None = None,
    get_queryset: Callable[[strawberry.Info], models.QuerySet[Any]],
    get_aggregate_queryset: Callable[[strawberry.Info], models.QuerySet[Any]] | None = None,
    write_backend: WriteBackend,
    id_decode: Callable[[Any], Any] | None = None,
    id_column: str = "pk",
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
) -> HasuraResource:
    """Build a Hasura resource and attach Angee's model-resource metadata.

    ``strawberry-django-hasura`` owns the portable Hasura dialect mechanics.
    This wrapper owns only the Angee seam around that resource: snake-pinning
    the node fields while this repo still uses Strawberry's default converter,
    and attaching the Phase 1 ``angee.resources`` metadata contribution.
    """

    resource_name = name or model.__name__.lower()
    pin_snake_wire_names(node)
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
        insert=insert,
        update=update,
        delete=delete,
        field_id_decode=field_id_decode,
        get_queryset=get_queryset,
        get_aggregate_queryset=get_aggregate_queryset,
        write_backend=write_backend,
        id_decode=id_decode,
        id_column=id_column,
    )
    return attach_hasura_resource_metadata(
        resource,
        node=node,
        model=model,
        name=resource_name,
        filterable=tuple(filterable),
        sortable=tuple(sortable),
        aggregatable=tuple(aggregatable),
        groupable=tuple(groupable),
        insert=insert,
        update=update,
        delete=delete,
        model_label=model_label,
        public_id_field=public_id_field,
    )


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
    insert: bool = True,
    update: bool = True,
    delete: bool = True,
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
) -> HasuraResource:
    """Attach Angee resource metadata to a built Hasura resource bundle."""

    type_names = _ResourceTypes(resource, name)
    attach_data_resource_metadata(
        resource.query,
        make_data_resource_metadata(
            model=model,
            model_label=model_label,
            public_id_field=public_id_field,
            node_type=node,
            filter_type=type_names.filter_type,
            order_type=type_names.order_type,
            roots=DataResourceRoots(
                list_name=resource_wire_field_name(resource.query, name),
                detail_name=resource_wire_field_name(resource.query, f"{name}_by_pk"),
                aggregate_name=resource_wire_field_name(resource.query, f"{name}_aggregate"),
                group_name=resource_wire_field_name(resource.query, f"{name}_groups") if groupable else None,
            ),
            type_names=DataResourceTypeNames(
                query=resource_type_name(resource.query),
                node=resource_type_name(node),
                filter=resource_type_name(type_names.filter_type),
                order=resource_type_name(type_names.order_type),
                aggregate=resource_type_name(type_names.aggregate_container_type),
                grouped=resource_type_name(type_names.group_type),
                group_key=resource_type_name(type_names.group_key_type),
                group_by_spec=resource_type_name(type_names.group_by_spec_type),
                group_order=resource_type_name(type_names.group_order_type),
                having=resource_type_name(type_names.having_type),
            ),
            capabilities=("list", "detail", "aggregate", *(("groups",) if groupable else ())),
            filter_fields=filterable,
            order_fields=sortable,
            aggregate_fields=aggregatable,
            group_by_fields=groupable,
            group_dimensions=_hasura_group_dimensions(model, groupable),
            aggregate_measures=_hasura_aggregate_measures(model, aggregatable),
            default_measures=(DataAggregateMeasureMetadata(op="count"),),
        ),
    )
    mutation_capabilities = _mutation_capabilities(
        insert=insert,
        update=update,
        delete=delete,
    )
    if mutation_capabilities:
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
                            f"insert_{name}_one",
                        )
                        if insert else None
                    ),
                    update_name=(
                        resource_wire_field_name(
                            resource.mutation,
                            f"update_{name}_by_pk",
                        )
                        if update else None
                    ),
                    delete_name=(
                        resource_wire_field_name(
                            resource.mutation,
                            f"delete_{name}_by_pk",
                        )
                        if delete else None
                    ),
                ),
                type_names=DataResourceTypeNames(
                    node=resource_type_name(node),
                    create_input=resource_type_name(type_names.insert_input_type),
                    update_input=resource_type_name(type_names.set_input_type),
                ),
                create_input_type=type_names.insert_input_type,
                update_input_type=type_names.set_input_type,
                capabilities=mutation_capabilities,
            ),
        )
    return resource


class _ResourceTypes:
    """Named generated types carried by a ``HasuraResource`` bundle."""

    def __init__(self, resource: HasuraResource, name: str) -> None:
        node_name = _node_type_name(resource)
        self.aggregate_container_type = self._require(resource, f"{name}_aggregate")
        self.filter_type = self._require(resource, f"{name}_bool_exp")
        self.order_type = self._require(resource, f"{name}_order_by")
        self.insert_input_type = self._optional(resource, f"{name}_insert_input")
        self.set_input_type = self._optional(resource, f"{name}_set_input")
        self.group_type = self._optional(resource, f"{name}_group")
        self.group_key_type = self._optional(resource, f"{node_name}GroupKey")
        self.group_by_spec_type = self._optional(resource, f"{node_name}GroupBySpec")
        self.group_order_type = self._optional(resource, f"{node_name}GroupOrder")
        self.having_type = self._optional(resource, f"{node_name}Having")

    @staticmethod
    def _require(resource: HasuraResource, name: str) -> type:
        for item in resource.types:
            if resource_type_name(item) == name:
                return item
        raise ImproperlyConfigured(f"Hasura resource is missing generated type {name!r}.")

    @staticmethod
    def _optional(resource: HasuraResource, name: str) -> type | None:
        for item in resource.types:
            if resource_type_name(item) == name:
                return item
        return None


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
) -> tuple[DataGroupDimensionMetadata, ...]:
    """Return typed-key group metadata using the aggregate builder's public contract."""

    return tuple(_hasura_group_dimension(model, path) for path in groupable)


def _hasura_group_dimension(
    model: type[models.Model],
    path: str,
) -> DataGroupDimensionMetadata:
    field = _require_group_field(model, path)
    key = _group_key_path(field, path)
    is_relation = "__" not in path and _is_to_one_relation(field)
    return DataGroupDimensionMetadata(
        field=path,
        input=_group_input_name(path),
        key=key,
        kind="relation" if is_relation else "column",
        scalar="ID" if is_relation else _scalar_for_field(field),
        extractions=_hasura_group_extractions(field, key),
    )


def _hasura_group_extractions(
    field: models.Field[Any, Any],
    key: str,
) -> tuple[DataGroupExtractionMetadata, ...]:
    if not isinstance(field, (models.DateField, models.DateTimeField)):
        return ()
    return tuple(
        DataGroupExtractionMetadata(
            name=granularity.value,
            input=granularity.name,
            key=f"{key}_{granularity.value}",
            range_key=(
                f"{key}_{granularity.value}_range"
                if isinstance(granularity, TimeGranularity)
                else None
            ),
        )
        for granularity in (*TimeGranularity, *NumberGranularity)
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


def _node_type_name(resource: HasuraResource) -> str:
    """Return the node GraphQL type prefix used by the aggregate builder."""

    for item in resource.types:
        name = resource_type_name(item)
        if name and name.endswith("Aggregate") and not name.endswith("_aggregate"):
            return name[: -len("Aggregate")]
    raise ImproperlyConfigured("Hasura resource is missing the free aggregate type.")


def _require_group_field(
    model: type[models.Model],
    path: str,
) -> models.Field[Any, Any]:
    """Resolve a groupable to-one Django field path for metadata emission."""

    current_model: type[models.Model] | None = model
    field: models.Field[Any, Any] | None = None
    for part in path.replace(".", "__").split("__"):
        if current_model is None:
            break
        try:
            field = current_model._meta.get_field(part)
        except FieldDoesNotExist:
            field = None
            break
        if getattr(field, "many_to_many", False) or getattr(field, "one_to_many", False):
            field = None
            break
        remote_field = getattr(field, "remote_field", None)
        related_model = getattr(remote_field, "model", None)
        current_model = related_model if isinstance(related_model, type) else None
    if field is None:
        raise ImproperlyConfigured(
            f"hasura_resource({model._meta.label}) declares unknown groupable field path {path!r}."
        )
    return field


def _group_input_name(path: str) -> str:
    """Return the generated ``<Model>GroupableField`` enum member name."""

    return path.replace(".", "__").upper()


def _group_key_path(
    field: models.Field[Any, Any],
    path: str,
) -> str:
    """Return the typed ``<Model>GroupKey`` field for one group axis."""

    if "__" not in path and _is_to_one_relation(field):
        return f"{path}_id"
    return path.replace(".", "__")


def _measure_ops_for_field(field: models.Field[Any, Any]) -> tuple[str, ...]:
    if isinstance(field, (models.IntegerField, models.DecimalField, models.FloatField)):
        return ("sum", "avg", "min", "max")
    if isinstance(field, (models.DateField, models.DateTimeField)):
        return ("min", "max")
    return ()


def _is_to_one_relation(field: models.Field[Any, Any]) -> bool:
    return bool(
        getattr(field, "is_relation", False)
        and (getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False))
    )


def _scalar_for_field(field: models.Field[Any, Any]) -> str | None:
    if isinstance(field, models.BooleanField):
        return "Boolean"
    if isinstance(field, models.IntegerField):
        return "Int"
    if isinstance(field, (models.DecimalField, models.FloatField)):
        return "Float"
    if isinstance(field, models.DateTimeField):
        return "DateTime"
    if isinstance(field, models.DateField):
        return "Date"
    if isinstance(field, models.JSONField):
        return "JSON"
    return None


def pin_snake_wire_names(strawberry_type: type) -> None:
    """Pin snake_case fields and args on a Strawberry type for Hasura-default wire names."""

    definition = get_object_definition(strawberry_type)
    if definition is None:
        return
    for field in definition.fields:
        if field.graphql_name is None and "_" in field.python_name:
            field.graphql_name = field.python_name
        for argument in field.arguments:
            if argument.graphql_name is None and "_" in argument.python_name:
                argument.graphql_name = argument.python_name
