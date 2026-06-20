"""Revision query surfaces for Strawberry schemas."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Optional, cast

import strawberry
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from rebac.errors import MissingActorError
from strawberry.scalars import JSON

from angee.base.mixins import RevisionMixin
from angee.graphql.access import assert_no_gated_read_fields
from angee.graphql.ids import PublicID, instance_for_id
from angee.graphql.introspection import django_model, surface_name

_SURFACE_CACHE: dict[tuple[type[models.Model], str], type] = {}
_REVISION_TYPE_CACHE: dict[tuple[type[models.Model], str], Any] = {}
_REVISION_FIRST_DEFAULT = 50
_REVISION_FIRST_MAX = 200


def revisions(
    node: type,
    *,
    name: str | None = None,
) -> type:
    """Return a Strawberry query surface for one revision-tracked model type."""

    model = django_model(node)
    if not issubclass(model, RevisionMixin):
        raise ImproperlyConfigured(f"revisions({surface_name(node)}) needs a RevisionMixin model")
    if not model.revisioned_fields:
        raise ImproperlyConfigured(f"revisions({surface_name(node)}) needs revisioned_fields")
    _validate_revision_visibility(model)

    singular = name or model._meta.model_name
    cache_key = (model, singular)
    if cached := _SURFACE_CACHE.get(cache_key):
        return cached

    attr = f"{singular}_revisions"
    revision_type = _revision_type(model, singular)
    annotations: dict[str, Any] = {attr: _list_annotation(revision_type)}
    namespace: dict[str, Any] = {
        "__annotations__": annotations,
        attr: strawberry.field(resolver=_revision_resolver(model, revision_type)),
    }
    type_name = f"{_type_stem(singular)}RevisionQuery"
    surface = type(type_name, (), namespace)
    typed_surface = strawberry.type(surface)
    _SURFACE_CACHE[cache_key] = typed_surface
    return typed_surface


def _revision_resolver(model: type[models.Model], revision_type: Any) -> Any:
    """Return a field resolver for one model's newest-first revisions."""

    def resolve(id: PublicID, first: int = _REVISION_FIRST_DEFAULT) -> list[Any]:
        """Return actor-visible revisions for one model instance."""

        instance = _resolve_instance(model, str(id))
        if instance is None:
            return []
        versions = cast(Any, instance).revisions[:_revision_first(first)]
        return [revision_type(instance, version) for version in versions]

    resolve.__annotations__ = {
        "id": PublicID,
        "first": int,
        "return": _list_annotation(revision_type),
    }
    return resolve


def _resolve_instance(
    model: type[models.Model],
    public_id: str,
) -> models.Model | None:
    """Return the actor-visible model instance addressed by ``public_id``."""

    try:
        return instance_for_id(model, public_id)
    except MissingActorError:
        return None


def _revision_type(model: type[models.Model], singular: str) -> Any:
    """Return the Strawberry revision projection for ``model``."""

    cache_key = (model, singular)
    if cached := _REVISION_TYPE_CACHE.get(cache_key):
        return cached

    annotations: dict[str, Any] = {
        "id": strawberry.ID,
        "created_at": datetime,
        "comment": str | None,
    }
    fields = [_revisioned_field(model, name) for name in model.revisioned_fields]
    for field in fields:
        annotations[field.name] = _field_annotation(field)

    def __init__(self: Any, instance: models.Model, version: Any) -> None:
        self.id = strawberry.ID(f"{cast(Any, instance).public_id}.{version.pk}")
        self.created_at = cast(datetime, version.revision.date_created)
        self.comment = cast(str | None, version.revision.comment or None)
        field_dict = cast(dict[str, Any], version.field_dict)
        for field in fields:
            setattr(self, field.name, _revision_value(field, field_dict))

    namespace = {
        "__annotations__": annotations,
        "__doc__": f"Versioned field snapshot for one {model._meta.verbose_name} revision.",
        "__init__": __init__,
        "__module__": __name__,
    }
    type_name = f"{_type_stem(singular)}Revision"
    revision_type = strawberry.type(type(type_name, (), namespace))
    _REVISION_TYPE_CACHE[cache_key] = revision_type
    return revision_type


def _validate_revision_visibility(model: type[models.Model]) -> None:
    """Fail before schema build when revision snapshots would bypass redaction."""

    assert_no_gated_read_fields(model, model.revisioned_fields, "revisioned_fields", "snapshots leak gated values")


def _list_annotation(item_type: Any) -> Any:
    """Return a runtime ``list[item_type]`` annotation for Strawberry."""

    return list[item_type]


def _type_stem(singular: str) -> str:
    """Return the GraphQL type stem for a generated revision surface name."""

    return singular[:1].upper() + singular[1:]


def _revision_first(first: int) -> int:
    """Return a non-negative revision query limit capped by the public contract."""

    return min(max(first, 0), _REVISION_FIRST_MAX)


def _revisioned_field(
    model: type[models.Model],
    name: str,
) -> models.Field[Any, Any]:
    """Return a declared revision field from the model owner."""

    try:
        field = model._meta.get_field(name)
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured(f"{model._meta.label}.revisioned_fields includes unknown field {name!r}") from error
    if not isinstance(field, models.Field):
        raise ImproperlyConfigured(f"{model._meta.label}.revisioned_fields includes non-column field {name!r}")
    if field.is_relation:
        raise ImproperlyConfigured(f"{model._meta.label}.revisioned_fields includes relation field {name!r}")
    return field


def _field_annotation(field: models.Field[Any, Any]) -> Any:
    """Return the GraphQL annotation for a revisioned Django field."""

    annotation: Any
    if isinstance(field, models.BooleanField):
        annotation = bool
    elif isinstance(field, models.IntegerField):
        annotation = int
    elif isinstance(field, models.FloatField):
        annotation = float
    elif isinstance(field, models.DecimalField):
        annotation = Decimal
    elif isinstance(field, models.DateTimeField):
        annotation = datetime
    elif isinstance(field, models.DateField):
        annotation = date
    elif isinstance(field, models.TimeField):
        annotation = time
    elif isinstance(field, models.JSONField):
        annotation = JSON
    else:
        annotation = str
    return Optional[annotation] if field.null else annotation


def _revision_value(
    field: models.Field[Any, Any],
    field_dict: dict[str, Any],
) -> Any:
    """Return one revisioned value coerced by its Django field."""

    if field.name not in field_dict:
        return None if field.null else field.get_default()
    return field.to_python(field_dict[field.name])
