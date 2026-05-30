"""Read Strawberry / strawberry-django type internals in one place.

Angee composes named schemas and CRUD surfaces from ordinary Strawberry types,
which means a few spots must read Strawberry's private definition attributes.
Centralizing them here keeps that coupling to a single module.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import models


def surface_name(surface: object) -> str:
    """Return a readable label for a schema surface in error messages."""

    return getattr(surface, "__name__", repr(surface))


def surface_field_names(surface: object) -> tuple[str, ...]:
    """Return the field names declared by one Strawberry surface."""

    definition = getattr(surface, "__strawberry_definition__", None)
    if definition is None:
        raise ImproperlyConfigured(
            f"{surface_name(surface)} is not a Strawberry type"
        )
    return tuple(field.python_name for field in definition.fields)


def django_model(node: type) -> type[models.Model]:
    """Return the Django model backing a strawberry-django type."""

    definition = getattr(node, "__strawberry_django_definition__", None)
    if definition is None:
        raise ImproperlyConfigured(
            f"{surface_name(node)} is not a strawberry_django type"
        )
    return definition.model
