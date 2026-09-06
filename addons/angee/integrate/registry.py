"""Deterministic model discovery for integration declarations."""

from __future__ import annotations

from django.apps import apps
from django.db import models

_Model = type[models.Model]


def bridge_models(base: _Model) -> tuple[_Model, ...]:
    """Return loaded concrete ``Bridge`` subclasses in deterministic order."""

    return models_with(base=base)


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
            key=_model_key,
        )
    )


def _model_key(model: _Model) -> tuple[str, str]:
    return (model._meta.app_label, model._meta.model_name)
