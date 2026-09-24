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
through ``django-choices-field``'s ``TextChoicesField``. ``AngeeSchema.get_fields``
projects optional states as nullable enums, including legacy blank, non-null
declarations, and converts their empty-string sentinel to GraphQL null. New
optional states still declare ``null=True, blank=True`` on the model. The
opaque-id ``SqidField`` is a non-concrete column projected explicitly as
``strawberry.ID`` by ``AngeeNode`` — neither reaches ``field_type_map``.
"""

from __future__ import annotations

import copy
from inspect import isawaitable
from typing import Any

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry.extensions import FieldExtension
from strawberry.extensions.field_extension import AsyncExtensionResolver, SyncExtensionResolver
from strawberry.types import Info
from strawberry.types.base import StrawberryOptional
from strawberry.types.enum import StrawberryEnumDefinition
from strawberry.types.field import StrawberryField
from strawberry.utils.aio import resolve_awaitable
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.fields.types import field_type_map

from angee.base.fields import FractionalRankField, StateField


class _OptionalStateExtension(FieldExtension):
    """Translate the legacy storage sentinel at the GraphQL output boundary."""

    @staticmethod
    def _nullable(value: Any) -> Any:
        return None if value == "" else value

    def resolve(self, next_: SyncExtensionResolver, source: Any, info: Info, **kwargs: Any) -> Any:
        value = next_(source, info, **kwargs)
        if isawaitable(value):
            return resolve_awaitable(value, self._nullable)
        return self._nullable(value)

    async def resolve_async(self, next_: AsyncExtensionResolver, source: Any, info: Info, **kwargs: Any) -> Any:
        return await resolve_awaitable(next_(source, info, **kwargs), self._nullable)


def project_state_field(field: StrawberryField) -> StrawberryField:
    """Project an optional model state on a schema-local copy of its native field.

    Strawberry's ``Schema.get_fields`` hook runs before conversion of each output
    field. Keeping the projection there preserves native enum metadata and leaves
    shared declarations intact across schema builds; resolver extensions only
    translate values and never rewrite types during ``apply``.
    """

    if not isinstance(field, StrawberryDjangoField):
        return field
    definition = field.origin_django_type
    if definition is None or definition.is_input:
        return field
    try:
        model_field = definition.model._meta.get_field(field.django_name or field.python_name)
    except FieldDoesNotExist:
        return field
    if not isinstance(model_field, StateField) or not (model_field.blank or model_field.null):
        return field

    projected = copy.copy(field)
    resolved = projected.type
    enum = resolved.of_type if isinstance(resolved, StrawberryOptional) else resolved
    if not isinstance(enum, StrawberryEnumDefinition):
        return field
    projected.type = StrawberryOptional(enum)
    projected.extensions.append(_OptionalStateExtension())
    return projected


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
