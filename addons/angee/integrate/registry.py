"""Deterministic model discovery for integration declarations."""

from __future__ import annotations

from collections.abc import Callable

from django.apps import apps
from django.core import checks
from django.db import models

_Model = type[models.Model]


def bridge_models(base: _Model) -> tuple[_Model, ...]:
    """Return loaded concrete ``Bridge`` subclasses in deterministic order."""

    return models_with(base=base)


def source_kind_models() -> tuple[_Model, ...]:
    """Return loaded models that declare a source output kind."""

    return models_with(attribute="source_kind")


def models_with(
    *,
    base: _Model | None = None,
    attribute: str | None = None,
    predicate: Callable[[_Model], bool] | None = None,
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
                and (predicate is None or predicate(model))
            ),
            key=_model_key,
        )
    )


def _model_key(model: _Model) -> tuple[str, str]:
    return (model._meta.app_label, model._meta.model_name)


def check_source_kind_contracts(
    app_configs: list[object] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Validate source output model declarations."""

    del app_configs, kwargs
    errors: list[checks.CheckMessage] = []
    by_kind: dict[str, _Model] = {}
    for model in source_kind_models():
        kind = str(getattr(model, "source_kind", "")).strip()
        if kind in by_kind:
            errors.append(
                checks.Error(
                    f"{model._meta.label} duplicates source_kind {kind!r} declared by {by_kind[kind]._meta.label}.",
                    obj=model,
                    id="angee.integrate.E001",
                )
            )
        else:
            by_kind[kind] = model
        sync_from_source = getattr(model._default_manager, "sync_from_source", None)
        if not callable(sync_from_source):
            errors.append(
                checks.Error(
                    f"{model._meta.label} declares source_kind {kind!r} but its default manager "
                    "does not expose sync_from_source(source).",
                    obj=model,
                    id="angee.integrate.E002",
                )
            )
    return errors
