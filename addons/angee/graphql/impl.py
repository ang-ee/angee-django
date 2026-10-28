"""GraphQL metadata and enums for registry-backed implementations."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Self, cast

import strawberry
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from strawberry.scalars import JSON
from strawberry.utils.str_converters import to_snake_case

from angee.base.implementations import ImplChoice as BaseImplChoice
from angee.base.fields import ImplClassField
from angee.base.models.implementations import readable_impl_defaults
from angee.graphql.mixins import ImplementationChoicesMixin


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
        model_field = ImplClassField.for_model(django_model, field_name)
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured(str(error)) from error
    choices = [ImplChoice.from_choice(choice) for choice in model_field.impl_choices()]
    for choice in choices:
        choice.defaults = readable_impl_defaults(django_model, cast(dict[str, Any], choice.defaults))
    return choices


def can_read_impl_choices(model: str, field: str, actor: Any) -> bool:
    """Delegate additional implementation metadata visibility to its model owner."""
    owner = _model_for_label(model)
    return issubclass(owner, ImplementationChoicesMixin) and owner.can_read_impl_choices(_field_name(field), actor)


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
