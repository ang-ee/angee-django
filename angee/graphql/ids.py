"""GraphQL public-id primitives for Angee schemas."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

import strawberry
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry import UNSET
from strawberry.types import get_object_definition
from strawberry_django.utils.typing import get_django_definition

from angee.base.fields import SqidField
from angee.base.models import instance_from_public_id
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME

PublicID = strawberry.ID
"""GraphQL ID scalar carrying an Angee public id, usually a model sqid."""

_ModelT = TypeVar("_ModelT", bound=models.Model)


def public_id_value(value: Any) -> str:
    """Return ``value`` as the raw public id used at GraphQL boundaries."""

    return str(value or "")


def instance_for_id(
    model: type[_ModelT],
    value: Any,
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
) -> _ModelT | None:
    """Return ``model`` row addressed by a GraphQL public id."""

    public_id = public_id_value(value)
    if public_id == "":
        return None
    return instance_from_public_id(model, public_id, queryset=queryset)


def require_instance_for_id(
    model: type[_ModelT],
    value: Any,
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
) -> _ModelT:
    """Return the row for ``value`` or raise a stable not-found error."""

    instance = instance_for_id(model, value, queryset=queryset)
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {public_id_value(value)!r} was not found")
    return instance


def coerce_relation_public_ids(
    model: type[models.Model],
    data: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve FK/M2M public-id inputs into model instances before Django writes.

    Strawberry-Django owns the mutation execution, validation, and m2m application.
    Its parser still treats plain string relation inputs as database primary keys,
    so Angee resolves public ids at the GraphQL boundary and hands the library the
    model instances it already accepts natively.
    """

    coerced = dict(data)
    fields = {field.name: field for field in model._meta.get_fields()}
    for name, value in list(coerced.items()):
        if value in (None, UNSET):  # noqa: PLR6201
            continue
        field = fields.get(name)
        if isinstance(field, models.ForeignKey):
            coerced[name] = _coerce_relation_value(cast(type[models.Model], field.remote_field.model), value)
        elif isinstance(field, models.ManyToManyField):
            coerced[name] = _coerce_relation_list(cast(type[models.Model], field.remote_field.model), value)
    return coerced


def assert_unique_sqid_prefixes(types: tuple[object, ...]) -> None:
    """Fail schema build when two public model owners declare the same sqid prefix."""

    prefixes_by_owner: dict[str, type[models.Model]] = {}
    for model in _exposed_models(types):
        field = _sqid_field(model)
        if field is None or not field.prefix:
            continue
        owner_model = field.model
        existing = prefixes_by_owner.setdefault(field.prefix, owner_model)
        if existing._meta.label != owner_model._meta.label:
            raise ImproperlyConfigured(
                f"Sqid prefix {field.prefix!r} is declared by both "
                f"{existing._meta.label} and {owner_model._meta.label}"
            )
    for left in prefixes_by_owner:
        for right in prefixes_by_owner:
            if left == right:
                continue
            if right.startswith(left):
                raise ImproperlyConfigured(
                    f"Sqid prefix {left!r} overlaps with {right!r}; exposed public-id "
                    "prefixes must not be prefixes of one another"
                )


def _coerce_relation_value(model: type[models.Model], value: Any) -> Any:
    """Return a model instance for a scalar public id, preserving nested inputs."""

    if isinstance(value, models.Model):
        return value
    if isinstance(value, str):
        return require_instance_for_id(model, value)
    return value


def _coerce_relation_list(model: type[models.Model], value: Any) -> Any:
    """Resolve scalar ids inside list-style relation inputs."""

    if isinstance(value, list):
        return [_coerce_relation_value(model, item) for item in value]
    return value


def _exposed_models(types: tuple[object, ...]) -> tuple[type[models.Model], ...]:
    """Return models backing public Strawberry-Django node types."""

    models_by_label: dict[str, type[models.Model]] = {}
    for item in types:
        definition = get_object_definition(item)
        django_definition = get_django_definition(item)
        if definition is None or django_definition is None:
            continue
        if definition.is_input:
            continue
        model = django_definition.model
        models_by_label.setdefault(model._meta.label, model)
    return tuple(models_by_label.values())


def _sqid_field(model: type[models.Model]) -> SqidField | None:
    """Return the sqid field visible on ``model``, including MTI parents."""

    try:
        field = model._meta.get_field(PUBLIC_ID_FIELD_NAME)
    except FieldDoesNotExist:
        return None
    return field if isinstance(field, SqidField) else None
