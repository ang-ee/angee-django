"""GraphQL metadata and enums for registry-backed implementations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict
from functools import partial
from typing import Any, Self, cast

import strawberry
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models
from rebac import PermissionDenied
from strawberry.permission import BasePermission
from strawberry.scalars import JSON
from strawberry.utils.str_converters import to_snake_case

from angee.base.fields import ImplClassField, create_only_selectors
from angee.base.hooks import resolve_hook
from angee.base.implementations import ImplChoice as BaseImplChoice
from angee.base.implementations import impl_choices as registry_impl_choices
from angee.base.models.implementations import ImplDefaultsMixin, readable_impl_defaults
from angee.graphql.mixins import ImplementationChoicesMixin

IMPL_CHOICES_ADMISSION = "ANGEE_IMPL_CHOICES_ADMISSION"
"""The hook setting naming who may read implementation choices: a callable taking the session user.

It names one intent, who may read a selector's implementations (and so be offered a repair), and is
kept apart from ``ANGEE_PROFILING_PERMISSION``, who may profile requests, even where a host points both
at the same check (IAM points both at ``is_platform_admin``): two intents stay two settings, never one
generic administrator check.
"""


@strawberry.type
class ImplChoice:
    """One selectable implementation and the defaults it materializes."""

    key: str
    label: str
    icon: str
    category: str
    defaults: JSON = strawberry.field(description=(
        "What choosing this implementation sets: an insert's defaults, or, in a resource's repair read "
        "(<resource>_repair_choices), the config it carries over from that record's stored config."
    ))
    config_schema: JSON | None

    @classmethod
    def from_choice(cls, choice: BaseImplChoice, **extra: Any) -> Self:
        """Project implementation-owned metadata once, with declared subtype facts."""
        return cls(**asdict(choice), **extra)


def impl_choices_admitted(info: strawberry.Info, model: str | type[models.Model], field: str) -> bool:
    """Whether the request's actor may read ``model.field``'s implementation choices: the create flow's admission.

    The one owner of that admission, which the ``impl_choices`` root and every resource's generated repair
    read ask: the ``ANGEE_IMPL_CHOICES_ADMISSION`` hook's answer for the session user (IAM points it at
    ``is_platform_admin``), or the model's own ``can_read_impl_choices`` policy. With no hook set only the
    model's policy admits: closed by default. An anonymous request is refused as unauthenticated.
    """

    from angee.graphql.actions import session_user

    actor = session_user(info)
    admission = resolve_hook(IMPL_CHOICES_ADMISSION)
    if admission is not None and admission(actor):
        return True
    return can_read_impl_choices(model, field, actor)


def require_impl_choices_admitted(info: strawberry.Info, model: str | type[models.Model], field: str) -> None:
    """Refuse an actor :func:`impl_choices_admitted` does not admit, in the choices read's one refusal."""

    if not impl_choices_admitted(info, model, field):
        raise PermissionDenied("Implementation choices are not readable by this actor.")


def impl_choices(model: str, field: str) -> list[ImplChoice]:
    """Return choices for a ``model.field`` ImplClassField.

    The reusable resolver behind the impl-picker query. The framework stays
    auth-agnostic; the query owner asks :func:`impl_choices_admitted` before
    calling it. Rowless callers authorize through their own record before
    requesting registry metadata. Relation defaults use readable public
    identities; unavailable suggestions are omitted without changing
    implementation-owned materialization defaults.
    """

    django_model = _model_for_label(model)
    field_name = _field_name(field)
    choices = [ImplChoice.from_choice(choice) for choice in model_impl_choices(django_model, field_name)]
    for choice in choices:
        choice.defaults = JSON(readable_impl_defaults(django_model, cast(dict[str, Any], choice.defaults)))
    return choices


def repair_impl_choices(
    info: strawberry.Info,
    model: type[models.Model],
    field: str,
    record: str,
    *,
    write_permission_classes: Sequence[type[BasePermission]],
    authorize_update: Callable[[strawberry.Info, str], Any],
) -> list[ImplChoice]:
    """What choosing each implementation of ``model.field`` sets on ``record``: a resource's repair read.

    The gate, in order: the create flow's admission (:func:`impl_choices_admitted`), so a repair offers
    only what its reader could choose when creating such a record; the update root's own
    ``write_permission_classes``; the update's own decision on the record (its write backend's
    ``authorize_update``: not found, or the update's denial); then the model, which refuses a registered
    key as create-only, and a name that is not one of its create-only selectors as having no repair.
    Each choice's ``defaults`` is the config it carries over (``ImplDefaultsMixin.repair_impl_defaults``).
    """

    field_name = _field_name(field)
    require_impl_choices_admitted(info, model, field_name)
    for permission_class in write_permission_classes:
        permission = permission_class()
        if not permission.has_permission(None, info):
            permission.on_unauthorized()
    row = authorize_update(info, record)
    if not isinstance(row, ImplDefaultsMixin) or field_name not in create_only_selectors(model):
        raise ValidationError({field_name: f"{model._meta.label}.{field_name} has no repair."})
    repairs = row.repair_impl_defaults(field_name)
    return [
        ImplChoice(**{**asdict(choice), "defaults": JSON(repairs[choice.key])})
        for choice in model_impl_choices(model, field_name)
        if choice.key in repairs
    ]


def model_impl_choices(model: type[models.Model], field_name: str) -> list[BaseImplChoice]:
    """Project one native selector through its model-owned picker policy."""

    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured(str(error)) from error
    if not isinstance(field, ImplClassField) or field.base_class is None:
        raise ImproperlyConfigured(f"{model.__name__}.{field_name} is not an active ImplClassField.")
    include = partial(model.include_impl_choice, field_name) if issubclass(model, ImplementationChoicesMixin) else None
    return registry_impl_choices(field.base_class, include=include)


def can_read_impl_choices(model: str | type[models.Model], field: str, actor: Any) -> bool:
    """Delegate additional implementation metadata visibility to its model owner."""
    owner = model if isinstance(model, type) else _model_for_label(model)
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
