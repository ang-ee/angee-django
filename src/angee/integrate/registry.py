"""Deterministic model discovery for integration declarations."""

from __future__ import annotations

from typing import overload

from django.apps import apps
from django.db import models

_Model = type[models.Model]


@overload
def models_with[ModelClass: type[models.Model]](
    *,
    base: ModelClass,
    attribute: str | None = None,
) -> tuple[ModelClass, ...]: ...


@overload
def models_with(
    *,
    base: None = None,
    attribute: str | None = None,
) -> tuple[_Model, ...]: ...


def models_with(
    *,
    base: _Model | None = None,
    attribute: str | None = None,
) -> tuple[_Model, ...]:
    """Return loaded models matching the declared criteria in deterministic order."""

    return tuple(
        sorted(
            (
                model
                for model in apps.get_models()
                if not model._meta.abstract
                and (base is None or issubclass(model, base))
                and (attribute is None or bool(getattr(model, attribute, "")))
            ),
            key=lambda model: model._meta.label_lower,
        )
    )
