"""Strawberry and strawberry-django introspection helpers."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
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


class FieldPathError(Exception):
    """A ``__``/``.``-separated relation path could not resolve to a leaf to-one field.

    Carries the failure mode so callers translate it to their own wording:
    ``to_many`` marks a rejected to-many segment, ``unknown`` covers a missing
    field or a path that walks past a non-relation leaf.
    """

    def __init__(self, *, to_many: bool) -> None:
        self.to_many = to_many
        super().__init__("to-many" if to_many else "unknown")


def require_field_for_path(model: type[models.Model], path: str) -> models.Field[Any, Any]:
    """Resolve a ``__``/``.``-separated relation ``path`` to a leaf to-one field.

    Walks each segment with ``_meta.get_field``, following the forward relation at
    each step, and raises :class:`FieldPathError` (``to_many`` or ``unknown``) so
    each caller renders its own diagnostic message.
    """

    current_model: type[models.Model] | None = model
    field: models.Field[Any, Any] | None = None
    for part in path.replace(".", "__").split("__"):
        if current_model is None:
            raise FieldPathError(to_many=False)
        try:
            field = current_model._meta.get_field(part)
        except FieldDoesNotExist:
            raise FieldPathError(to_many=False) from None
        if is_to_many_relation(field):
            raise FieldPathError(to_many=True)
        remote_field = getattr(field, "remote_field", None)
        related_model = getattr(remote_field, "model", None)
        current_model = related_model if isinstance(related_model, type) else None
    if field is None:
        raise FieldPathError(to_many=False)
    return field
