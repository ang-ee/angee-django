"""GraphQL metadata for registry-backed implementation fields."""

from __future__ import annotations

from typing import Any, cast

import strawberry
from angee.base.impl import ImplChoice as BaseImplChoice
from angee.base.models import AngeeModel
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from strawberry.scalars import JSON
from strawberry.utils.str_converters import to_snake_case


@strawberry.type
class ImplChoice:
    """One selectable implementation and the defaults it materializes."""

    key: str
    label: str
    icon: str
    category: str
    defaults: JSON


def impl_choices(model: str, field: str) -> list[ImplChoice]:
    """Return choice metadata for ``model.field`` when it is an ``ImplClassField``.

    The reusable resolver behind the impl-picker query. The framework stays
    auth-agnostic, so an addon wraps this in its own admin-gated query field (e.g.
    integrate's ``ConsoleImplChoicesQuery``) rather than exposing it ungated.
    """

    django_model = _model_for_label(model)
    field_name = _field_name(field)
    try:
        model_field = cast(type[AngeeModel], django_model).impl_field(field_name)
    except AttributeError as error:
        raise ImproperlyConfigured(
            f"{django_model._meta.label}.{field_name} is not an ImplClassField."
        ) from error
    except FieldDoesNotExist as error:
        if str(error).endswith(" is not an ImplClassField."):
            message = f"{django_model._meta.label}.{field_name} is not an ImplClassField."
        else:
            message = f"{django_model._meta.label} has no field {field!r}."
        raise ImproperlyConfigured(message) from error
    return [_project_choice(choice) for choice in model_field.impl_choices()]


def _project_choice(choice: BaseImplChoice) -> ImplChoice:
    """Project the base impl-choice value object onto the GraphQL type."""

    return ImplChoice(
        key=choice.key,
        label=choice.label,
        icon=choice.icon,
        category=choice.category,
        defaults=cast(JSON, choice.defaults),
    )


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
