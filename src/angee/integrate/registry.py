"""Model discovery for concrete integration capabilities."""

from __future__ import annotations

from django.apps import apps
from django.db import models

from angee.integrate.models import Bridge, Capability

_Model = type[models.Model]


def capability_models() -> tuple[_Model, ...]:
    """Return loaded concrete ``Capability`` subclasses in deterministic order."""

    return _models_for(Capability)


def bridge_models() -> tuple[_Model, ...]:
    """Return loaded concrete ``Bridge`` subclasses in deterministic order."""

    return _models_for(Bridge)


def _models_for(base: _Model) -> tuple[_Model, ...]:
    return tuple(
        sorted(
            (
                model
                for model in apps.get_models()
                if issubclass(model, base) and not model._meta.abstract
            ),
            key=_model_key,
        )
    )


def _model_key(model: _Model) -> tuple[str, str]:
    return (model._meta.app_label, model._meta.model_name)
