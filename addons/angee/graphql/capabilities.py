"""Typed permission answers composed from REBAC's native query scopes."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from functools import cache, partial
from types import GenericAlias
from typing import Any, cast

import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac import current_actor
from strawberry.extensions import FieldExtension
from strawberry.types.field import StrawberryField

from angee.base.permissions import effective_rebac_definition
from angee.base.scoping import aggregate_scoped_queryset, read_scoped_queryset, system_queryset
from angee.graphql.introspection import django_model

_ACTOR = "_angee_permission_actor"


def permission_annotations(model: type[models.Model], names: Iterable[str]) -> dict[str, Any]:
    """Batch named permission answers in the list query through native SQL scopes.

    Strawberry fields add these as optimizer hints. Authored collection resolvers
    can use the same annotations before materializing rows for held_permissions.
    No result cache outlives the returned rows, and the actor stamp prevents reuse
    of a projection under another identity.
    """

    actor = current_actor()
    annotations: dict[str, Any] = {_ACTOR: models.Value(str(actor))}
    for name in names:
        annotations[f"_angee_permission_{name}"] = _permission_annotation(model, name)
    return annotations


def held_permissions(record: models.Model, names: Iterable[str]) -> frozenset[str]:
    """Return declared permission names held by the ambient actor on this row.

    List projections reuse SQL annotations; single records use the same
    annotations on a one-row queryset. Neither instance-local nor ambient
    elevation grants a capability: the permission scopes pin the concrete
    ambient actor. Without an actor no capabilities are granted.
    """

    actor = current_actor()
    if actor is None:
        return frozenset()
    names = tuple(names)
    values = record.__dict__
    if values.get(_ACTOR) != str(actor) or any(f"_angee_permission_{name}" not in values for name in names):
        annotations = permission_annotations(type(record), names)
        values = (
            system_queryset(type(record)).filter(pk=record.pk).annotate(**annotations).values(*annotations).first()
            or {}
        )
    return frozenset(name for name in names if values.get(f"_angee_permission_{name}"))


@cache
def _permission_enum(names: tuple[str, ...]) -> Any:
    # Lengths preserve name boundaries even when a permission contains underscores.
    enum_name = "Permissions_" + "_".join(f"{len(name)}_{name}" for name in names)
    return strawberry.enum(cast(Any, Enum(enum_name, {name: name for name in names})))


class _PermissionsExtension(FieldExtension):
    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names

    def apply(self, field: StrawberryField) -> None:
        model = django_model(cast(type, field.origin))
        definition = effective_rebac_definition(model)
        declared = {permission.name for permission in definition.permissions} if definition is not None else set()
        if unknown := set(self.names) - declared:
            raise ImproperlyConfigured(
                f"{model._meta.label} declares unknown permissions: {', '.join(sorted(unknown))}"
            )
        cast(Any, field).store.annotate.update(
            {
                _ACTOR: lambda info: models.Value(str(current_actor())),
                **{f"_angee_permission_{name}": partial(_permission_annotation, model, name) for name in self.names},
            }
        )


def _permission_annotation(model: type[models.Model], name: str, info: Any = None) -> Any:
    del info
    queryset = read_scoped_queryset(model, current_actor(), action=name)
    return (
        models.Value(False)
        if queryset is None
        else models.Exists(aggregate_scoped_queryset(queryset).filter(pk=models.OuterRef("pk")))
    )


def permissions_field(names: Iterable[str]) -> Any:
    """Declare a typed ``permissions`` field, validating zed names at schema build.

    Example: ``permissions = permissions_field(("write", "share"))`` on a
    strawberry-django type. Enum values on the wire keep their zed spelling.
    """

    names = tuple(sorted(set(names)))
    if not names:
        raise ImproperlyConfigured("A permissions field must declare at least one permission.")
    enum = _permission_enum(names)

    def resolve(root: models.Model) -> Any:
        held = held_permissions(root, names)
        return [enum(name) for name in names if name in held]

    resolve.__annotations__["return"] = GenericAlias(list, (enum,))
    return strawberry_django.field(resolver=resolve, extensions=[_PermissionsExtension(names)])
