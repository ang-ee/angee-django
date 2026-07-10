"""Teach strawberry-django to resolve custom model value fields under ``auto``.

strawberry-django maps a Django model field to its GraphQL type through
``field_type_map`` — an **exact-class** lookup with no MRO walk (see
``strawberry_django.fields.types.resolve_model_field_type``). An Angee value
field that subclasses a Django field but adds only a semantic declaration keeps
the base field's wire shape, yet ``auto`` raises ``NotImplementedError`` on the
subclass because its exact class is absent from the map. Registering the subclass
beside the base it wraps is the strawberry-django-native fix: a value field
registers its GraphQL type at its own module import. Self-registration guarantees
the type exists before any schema resource that uses the field can be constructed,
independent of ``INSTALLED_APPS`` order.

Choice and id fields need no entry: strawberry-django resolves ``StateField`` by
``isinstance`` against ``django-choices-field``'s ``TextChoicesField``, and the
opaque-id ``SqidField`` is a non-concrete column projected explicitly as
``strawberry.ID`` by ``AngeeNode`` — neither reaches ``field_type_map``.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from strawberry_django.fields.types import field_type_map


def register_field_type(field_class: type[models.Field[Any, Any]], wire_type: type) -> None:
    """Map one Django model field subclass to the GraphQL type it projects under ``auto``.

    This is the extension seam a field's owning addon uses to teach
    strawberry-django its wire type: a value field that subclasses a Django field
    but adds only a semantic declaration keeps the base field's wire shape, yet the
    exact-class ``field_type_map`` lookup misses the subclass. The field module
    registers its own field here as an import-time declaration, so a consumer can
    write ``field: auto`` instead of hand-annotating the wire type. Repeating the
    same registration is a no-op; conflicting registrations fail loudly.
    """

    existing = field_type_map.get(field_class)
    if existing is wire_type:
        return
    if existing is not None:
        raise ImproperlyConfigured(
            f"{field_class.__module__}.{field_class.__qualname__} is already registered "
            f"with GraphQL type {existing!r}, not {wire_type!r}."
        )
    field_type_map[field_class] = wire_type
