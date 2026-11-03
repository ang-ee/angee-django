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
from rebac import SubjectRef, current_actor
from rebac import permission_expression as native_permission_expression
from rebac.relation_loading import relation_actor
from strawberry.extensions import FieldExtension
from strawberry.types.field import StrawberryField
from strawberry_django.fields.field import StrawberryDjangoField

from angee.iam.permissions import declared_rebac_definition, instance_actor, require_rebac_queryset

_ACTOR = "_angee_permission_actor"


def permission_annotations(
    model: type[models.Model], names: Iterable[str], *, actor: SubjectRef | None = None
) -> dict[str, Any]:
    """Batch named permission answers in the list query through native SQL scopes.

    Strawberry fields add these as optimizer hints. Authored collection resolvers
    can use the same annotations before materializing rows for held_permissions.
    ``actor`` defaults to the ambient actor; held_permissions supplies the
    record's pinned or ambient actor explicitly.
    No result cache outlives the returned rows, and the actor stamp prevents reuse
    of a projection under another identity.
    """

    actor = actor if actor is not None else current_actor()
    annotations: dict[str, Any] = {_ACTOR: models.Value(str(actor))}
    for name in names:
        annotations[f"_angee_permission_{name}"] = _permission_annotation(model, name, actor=actor)
        annotations[f"_angee_permission_owner_{name}"] = models.Value(model._meta.label_lower)
    return annotations


def held_permissions(
    record: models.Model,
    names: Iterable[str],
    *,
    model: type[models.Model] | None = None,
) -> frozenset[str]:
    """Return declared permission names held by the row's pinned or ambient actor.

    List projections reuse SQL annotations; single records use the same
    annotations on a one-row queryset. Neither instance-local nor ambient
    elevation grants a capability: the permission scopes pin the resolved
    actor. Without an actor no capabilities are granted.
    ``model`` may name an MTI ancestor owning the requested permission; its row
    shares the record's primary key.
    """

    actor = instance_actor(record)
    if actor is None:
        return frozenset()
    owner = _permission_model(type(record), model)
    names = tuple(names)
    values = record.__dict__
    if values.get(_ACTOR) != str(actor) or any(
        f"_angee_permission_{name}" not in values
        or values.get(f"_angee_permission_owner_{name}", record._meta.label_lower) != owner._meta.label_lower
        for name in names
    ):
        annotations = permission_annotations(owner, names, actor=actor)
        values = (
            require_rebac_queryset(owner)
            .system_context(reason="graphql.permissions")
            .filter(pk=record.pk)
            .annotate(**annotations)
            .values(*annotations)
            .first()
            or {}
        )
    return frozenset(name for name in names if values.get(f"_angee_permission_{name}"))


@cache
def _permission_enum(names: tuple[str, ...]) -> Any:
    # Lengths preserve name boundaries even when a permission contains underscores.
    enum_name = "Permissions_" + "_".join(f"{len(name)}_{name}" for name in names)
    return strawberry.enum(cast(Any, Enum(enum_name, {name: name for name in names})))


class PermissionsExtension(FieldExtension):
    """Add native permission projections to a computed field's optimizer hints."""

    def __init__(self, names: tuple[str, ...], *, model: type[models.Model] | None = None) -> None:
        self.names = names
        self.model = model

    def apply(self, field: StrawberryField) -> None:
        django_field = cast(StrawberryDjangoField, field)
        django_definition = django_field.origin_django_type
        if django_definition is None:
            raise ImproperlyConfigured("Permission projections require a strawberry_django field.")
        model = _permission_model(django_definition.model, self.model)
        definition = declared_rebac_definition(model)
        declared = {permission.name for permission in definition.permissions} if definition is not None else set()
        if unknown := set(self.names) - declared:
            raise ImproperlyConfigured(
                f"{model._meta.label} declares unknown permissions: {', '.join(sorted(unknown))}"
            )
        django_field.store.annotate.update(
            {
                _ACTOR: lambda info: models.Value(str(current_actor())),
                **{f"_angee_permission_{name}": partial(_permission_annotation, model, name) for name in self.names},
                **{f"_angee_permission_owner_{name}": models.Value(model._meta.label_lower) for name in self.names},
            }
        )


def _permission_model(model: type[models.Model], owner: type[models.Model] | None) -> type[models.Model]:
    """Require a permission owner on the record's concrete MTI parent chain."""

    if owner is None or owner is model:
        return model
    if owner not in model._meta.get_parent_list():
        raise ImproperlyConfigured(f"{owner._meta.label} is not an MTI ancestor of {model._meta.label}.")
    return owner


def _permission_annotation(
    model: type[models.Model], name: str, info: Any = None, *, actor: SubjectRef | None = None
) -> Any:
    del info
    actor = actor if actor is not None else current_actor()
    queryset = require_rebac_queryset(model)
    return permission_expression(queryset if actor is None else queryset.with_actor(actor), name)


def permission_expression(queryset: models.QuerySet[Any], name: str) -> models.Expression:
    """Project one permission lazily using the target queryset's explicit actor."""
    actor = relation_actor(queryset)
    if actor is None:
        return models.Value(False, output_field=models.BooleanField())
    return native_permission_expression(queryset.model, name, actor=actor)


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
    return strawberry_django.field(resolver=resolve, extensions=[PermissionsExtension(names)])
