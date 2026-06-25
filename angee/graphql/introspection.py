"""Strawberry and strawberry-django introspection helpers."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from strawberry.types import get_object_definition
from strawberry_django.utils.typing import get_django_definition


def surface_name(surface: object) -> str:
    """Return a readable name for a Strawberry surface."""

    return getattr(surface, "__name__", repr(surface))


def surface_field_names(surface: object) -> tuple[str, ...]:
    """Return field names declared by a Strawberry type."""

    definition = get_object_definition(surface)
    if definition is None:
        raise ImproperlyConfigured(f"{surface_name(surface)} is not a Strawberry type")
    return tuple(field.python_name for field in definition.fields)


def django_model(node: type) -> type[models.Model]:
    """Return the Django model backing a strawberry-django type."""

    definition = get_django_definition(node)
    if definition is None:
        raise ImproperlyConfigured(f"{surface_name(node)} is not a strawberry_django type")
    return definition.model


def is_to_one_relation(field: models.Field[Any, Any]) -> bool:
    """Return whether ``field`` is a forward to-one relation."""

    return bool(getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False))


def is_to_many_relation(field: models.Field[Any, Any]) -> bool:
    """Return whether ``field`` represents a to-many relation path."""

    return bool(getattr(field, "many_to_many", False) or getattr(field, "one_to_many", False))
