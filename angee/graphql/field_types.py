"""Teach strawberry-django to resolve Angee's custom model value fields under ``auto``.

strawberry-django maps a Django model field to its GraphQL type through
``field_type_map`` — an **exact-class** lookup with no MRO walk (see
``strawberry_django.fields.types.resolve_model_field_type``). An Angee value
field that subclasses a Django field but adds only a semantic declaration keeps
the base field's wire shape, yet ``auto`` raises ``NotImplementedError`` on the
subclass because its exact class is absent from the map. Registering the subclass
beside the base it wraps is the strawberry-django-native fix, run once from the
GraphQL app's ``ready`` hook.

Choice and id fields need no entry: strawberry-django resolves ``StateField`` by
``isinstance`` against ``django-choices-field``'s ``TextChoicesField``, and the
opaque-id ``SqidField`` is a non-concrete column projected explicitly as
``strawberry.ID`` by ``AngeeNode`` — neither reaches ``field_type_map``.
"""

from __future__ import annotations

import decimal

from strawberry_django.fields.types import field_type_map

from angee.base.fields import MoneyField


def register_field_types() -> None:
    """Map Angee value fields to the GraphQL type of the Django field they wrap.

    ``MoneyField`` is a ``DecimalField`` carrying only a currency-path declaration
    (dropped at ``deconstruct``), so it projects as the same ``Decimal`` scalar;
    the money widget and currency path come from resource metadata, not the wire
    type. Idempotent — a plain dict assignment safe to run once per process.
    """

    field_type_map[MoneyField] = decimal.Decimal
