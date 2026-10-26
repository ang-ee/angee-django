"""GraphQL metadata and enums for registry-backed implementations."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Self, cast

import strawberry
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry.scalars import JSON
from strawberry.utils.str_converters import to_snake_case

from angee.identity import public_id_of
from angee.impl import ImplBase
from angee.impl import ImplChoice as BaseImplChoice
from angee.models import AngeeModel
from angee.permissions import require_rebac_queryset


@strawberry.type
class ImplChoice:
    """One selectable implementation and the defaults it materializes."""

    key: str
    label: str
    icon: str
    category: str
    defaults: JSON
    config_schema: JSON | None

    @classmethod
    def from_choice(cls, choice: BaseImplChoice, **extra: Any) -> Self:
        """Project implementation-owned metadata once, with declared subtype facts."""
        return cls(**asdict(choice), **extra)


def impl_choices(model: str, field: str) -> list[ImplChoice]:
    """Return choices for a ``model.field`` ImplClassField.

    The reusable resolver behind the impl-picker query. The framework stays
    auth-agnostic; the query owner authorizes administrators or the model's
    explicit ``can_read_impl_choices`` policy before calling it. Rowless callers
    authorize through their own record before requesting registry metadata.
    Relation defaults use readable public identities; unavailable suggestions
    are omitted without changing implementation-owned materialization defaults.
    """

    django_model = _model_for_label(model)
    field_name = _field_name(field)
    try:
        model_field = cast(type[AngeeModel], django_model).impl_field(field_name)
    except AttributeError as error:
        raise ImproperlyConfigured(f"{django_model._meta.label}.{field_name} is not an ImplClassField.") from error
    except FieldDoesNotExist as error:
        if str(error).endswith(" is not an ImplClassField."):
            message = f"{django_model._meta.label}.{field_name} is not an ImplClassField."
        else:
            message = f"{django_model._meta.label} has no field {field!r}."
        raise ImproperlyConfigured(message) from error
    choices = [ImplChoice.from_choice(choice) for choice in model_field.impl_choices()]
    for choice in choices:
        defaults = cast(dict[str, Any], choice.defaults)
        for name, value in tuple(defaults.items()):
            try:
                default_field = django_model._meta.get_field(name)
            except FieldDoesNotExist:
                continue
            if default_field.many_to_one and isinstance(value, str):
                related_model = cast(type[models.Model], default_field.related_model)
                target = ImplBase.relation_default_queryset(
                    default_field, value, queryset=require_rebac_queryset(related_model).scoped(),
                ).first()
                if target is None:
                    del defaults[name]
                else:
                    defaults[name] = public_id_of(target)
    return choices


def can_read_impl_choices(model: str, field: str, actor: Any) -> bool:
    """Delegate additional implementation metadata visibility to its model owner."""
    owner = _model_for_label(model)
    return issubclass(owner, AngeeModel) and owner.can_read_impl_choices(_field_name(field), actor)


def _model_for_label(label: str) -> type[Any]:
    """Return a Django model from ``app.Model`` or a unique object-name label."""

    raw = label.strip()
    if "." in raw:
        app_label, model_name = raw.split(".", 1)
        return apps.get_model(app_label, model_name)

    matches = [model for model in apps.get_models() if model._meta.object_name == raw]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ImproperlyConfigured(f"Unknown model {label!r}.")
    names = ", ".join(sorted(model._meta.label for model in matches))
    raise ImproperlyConfigured(f"Model {label!r} is ambiguous; use one of: {names}.")


def _field_name(name: str) -> str:
    """Return the Django field name for a GraphQL/camel or model/snake field label."""

    return to_snake_case(name)
