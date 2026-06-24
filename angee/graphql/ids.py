"""GraphQL public-id primitives for Angee schemas."""

from __future__ import annotations

from typing import Any, TypeVar

import strawberry
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from strawberry.types import get_object_definition
from strawberry_django.utils.typing import get_django_definition

from angee.base.models import instance_from_public_id, public_data_id_owner, public_data_id_prefix

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


def assert_unique_sqid_prefixes(types: tuple[object, ...]) -> None:
    """Fail schema build when two public model owners declare the same sqid prefix."""

    prefixes_by_owner: dict[str, type[models.Model]] = {}
    for model in _exposed_models(types):
        prefix = public_data_id_prefix(model)
        if not prefix:
            continue
        owner_model = public_data_id_owner(model)
        if owner_model is None:
            continue
        existing = prefixes_by_owner.setdefault(prefix, owner_model)
        if existing._meta.label != owner_model._meta.label:
            raise ImproperlyConfigured(
                f"Sqid prefix {prefix!r} is declared by both {existing._meta.label} and {owner_model._meta.label}"
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
