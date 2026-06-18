"""GraphQL metadata for registry-backed implementation fields."""

from __future__ import annotations

import re
from typing import Any

import strawberry
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from strawberry.scalars import JSON

from angee.base.fields import ImplClassField


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
    try:
        model_field = django_model._meta.get_field(_field_name(field))
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured(f"{django_model._meta.label} has no field {field!r}.") from error
    if not isinstance(model_field, ImplClassField):
        raise ImproperlyConfigured(f"{django_model._meta.label}.{model_field.name} is not an ImplClassField.")
    return [
        ImplChoice(
            key=str(choice["key"]),
            label=str(choice["label"]),
            icon=str(choice["icon"]),
            category=str(choice["category"]),
            defaults=choice["defaults"],
        )
        for choice in model_field.impl_choices()
    ]


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

    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
