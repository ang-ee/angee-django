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

State and id fields need no map entry: strawberry-django resolves ``StateField``
through ``django-choices-field``'s ``TextChoicesField``. Optional states declare
``null=True, blank=True`` on the model and project natively as nullable enums. The
opaque-id ``SqidField`` is a non-concrete column projected explicitly as
``strawberry.ID`` by ``AngeeNode`` — neither reaches ``field_type_map``.

An implementation-key column (``ImplClassField``) projects as its choices enum
the same way, and every Angee schema reads it nullable: ``AngeeSchema.get_fields``
reads each such output field through :func:`read_implementation_key`, so a key
the registry no longer names reads as null while its declaration stays ``auto``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from datetime import datetime
from typing import Any

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry import Info
from strawberry.annotation import StrawberryAnnotation
from strawberry.extensions.field_extension import FieldExtension, SyncExtensionResolver
from strawberry.types.base import StrawberryOptional
from strawberry.types.field import StrawberryField
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.fields.types import field_type_map

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.ordering import FractionalRankField
from angee.base.fields.text import ColorField, DiagnosticTextField, ModelLabelField
from angee.base.models.timestamps import CreatedAtField


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


register_field_type(FractionalRankField, float)
register_field_type(DiagnosticTextField, str)
register_field_type(ModelLabelField, str)
register_field_type(ColorField, str)
register_field_type(CreatedAtField, datetime)


class ImplementationKeyRead(FieldExtension):
    """Read a stored implementation key its column's choices no longer name as null.

    Such a row keeps the key as its text (``ImplClassField.from_db_value``),
    which the column's GraphQL enum cannot represent; null lets the row's list
    and record still answer, without an error. A key the choices name reads as
    its enum member, as before.
    """

    def __init__(self, field: ImplClassField[Any]) -> None:
        """Read through ``field``'s choices."""

        super().__init__()
        self.field = field

    def apply(self, field: StrawberryField) -> None:
        """Project the column nullable, as Strawberry lets an extension set its field's type."""

        field.type_annotation = StrawberryAnnotation(self.field.choices_enum | None)

    def resolve(self, next_: SyncExtensionResolver, source: Any, info: Info, **kwargs: Any) -> Any:
        """Return the column's value, or null for a key its choices do not name; an awaited value alike."""

        value = next_(source, info, **kwargs)
        if inspect.isawaitable(value):
            return self._read_awaited(value)
        return self._read(value)

    def _read(self, value: Any) -> Any:
        return value if value is None or self.field.has_key(value) else None

    async def _read_awaited(self, value: Awaitable[Any]) -> Any:
        return self._read(await value)


def read_implementation_key(field: StrawberryField) -> None:
    """Read ``field`` through :class:`ImplementationKeyRead`, once, when it projects an implementation key.

    That is a strawberry-django output field reading an ``ImplClassField``
    column as its choices enum. The extension goes on the declared field itself,
    where Strawberry's printer looks for a type's fields, so the column prints
    nullable in every schema the declaration joins; any other field is left
    alone. That includes a field with its own resolver, even one returning the
    choices enum: the column a field projects is known here, not where a
    resolver's value comes from, so such a resolver declares itself nullable and
    returns null for a key the choices do not name (``ImplClassField.has_key``).
    A group's key is not a type's field read here either: the aggregates library
    shapes it while it answers the groups root.
    """

    if not isinstance(field, StrawberryDjangoField):
        return
    if any(isinstance(extension, ImplementationKeyRead) for extension in field.extensions):
        return
    django_type = field.origin_django_type
    if django_type is None or django_type.is_input or field.base_resolver is not None:
        return
    try:
        model_field = django_type.model._meta.get_field(field.django_name or field.python_name)
    except FieldDoesNotExist:
        return
    if not isinstance(model_field, ImplClassField) or model_field.base_class is None:
        return
    projected = field.type
    if isinstance(projected, StrawberryOptional):
        projected = projected.of_type
    if projected is getattr(model_field.choices_enum, "__strawberry_definition__", None):
        field.extensions = [*field.extensions, ImplementationKeyRead(model_field)]
