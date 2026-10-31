"""GraphQL public-id primitives for Angee schemas."""

from __future__ import annotations

from typing import Any, TypeVar

import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from strawberry.types import get_object_definition
from strawberry_django.utils.typing import get_django_definition

from angee.base.identity import public_data_id_field
from angee.base.models.identity import instance_from_public_id, public_id_for

PublicID = strawberry.ID
"""GraphQL ID scalar carrying an Angee public id, usually a model sqid."""

RECORD_NOT_FOUND_MESSAGE = "The requested record was not found."
"""Public-safe refusal shared by target lookup and confirm-time disappearance."""

_ModelT = TypeVar("_ModelT", bound=models.Model)


def public_id_value(value: Any) -> str:
    """Return ``value`` as the raw public id used at GraphQL boundaries."""

    return str(value or "")


def to_public_id(model: type[models.Model], fk_id: Any) -> PublicID | None:
    """Project a model foreign-key value to its public id, or ``None`` when unset."""

    if fk_id is None:
        return None
    return PublicID(public_id_for(model, fk_id))


def require_public_id(model: type[models.Model], fk_id: Any) -> PublicID:
    """Project a required (non-null) model foreign-key value to its public id."""

    public_id = to_public_id(model, fk_id)
    if public_id is None:
        raise ValueError(f"{model._meta.object_name} reference is unexpectedly unset")
    return public_id


def public_id_field(field_name: str, *, nullable: bool = False) -> Any:
    """Project a PK-targeting forward FK without loading or authorizing its target.

    Use only where the containing row may disclose the reference independently
    of target readability. Actor-scoped references use ``actor_scoped_public_id``.
    Explicit nullable output preserves shared/unassigned reference semantics.
    The model field owns its target and stored attname; native ``only`` retains
    the existing scalar projection's query optimization. Non-PK ``to_field``
    relations are unsupported: their stored value is not the target's primary key.
    """
    def resolve(root: models.Model) -> Any:
        field = root._meta.get_field(field_name)
        if not isinstance(field, models.ForeignKey):
            raise ImproperlyConfigured(f"{root._meta.label}.{field_name} must be a forward foreign key.")
        if not field.target_field.primary_key:
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} must target a primary key "
                "to project a public id without loading its target."
            )
        value = field.value_from_object(root)
        return to_public_id(field.related_model, value) if nullable else require_public_id(field.related_model, value)

    resolve.__annotations__["return"] = PublicID | None if nullable else PublicID
    return strawberry_django.field(resolver=resolve, only=[field_name])


def optional_public_id(value: str | None) -> PublicID | None:
    """Wrap an optional already-resolved public id string as a GraphQL ID."""

    return None if value is None else PublicID(value)


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
    not_found: str | None = None,
) -> _ModelT:
    """Resolve within the supplied queryset or raise a typed validation refusal.

    ``not_found`` preserves a domain surface's established error wording. This
    helper never elevates; hidden and unknown rows have the same refusal when
    the caller supplies its authorized queryset. It is for required action and
    write targets. Nullable read roots use optional identity resolution and
    return null without errors for hidden, absent, or malformed identities.
    Action-specific authorization belongs to the action.
    """

    instance = instance_for_id(model, value, queryset=queryset)
    if instance is None:
        raise ValidationError(not_found or RECORD_NOT_FOUND_MESSAGE, code="not_found")
    return instance


def assert_unique_sqid_prefixes(types: tuple[object, ...]) -> None:
    """Fail schema build when two public model owners declare the same sqid prefix."""

    prefixes_by_owner: dict[str, type[models.Model]] = {}
    for model in _exposed_models(types):
        field = public_data_id_field(model)
        if field is None or not field.prefix:
            continue
        prefix = field.prefix
        owner_model = field.model
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
