"""Native discovery for VCS source output kinds."""

from django.core import checks
from django.db import models

from angee.integrate.registry import models_with

_Model = type[models.Model]


def source_kind_models() -> tuple[_Model, ...]:
    """Return loaded output models that declare a source kind."""

    return models_with(attribute="source_kind")


def check_source_kind_contracts(app_configs=None, **kwargs):
    """Validate source output model declarations."""

    del app_configs, kwargs
    errors = []
    by_kind = {}
    for model in source_kind_models():
        kind = str(getattr(model, "source_kind", "")).strip()
        if kind in by_kind:
            errors.append(
                checks.Error(
                    f"{model._meta.label} duplicates source_kind {kind!r} "
                    f"declared by {by_kind[kind]._meta.label}.",
                    obj=model,
                    id="angee.integrate_vcs.E001",
                )
            )
        else:
            by_kind[kind] = model
        if not callable(getattr(model._default_manager, "sync_from_source", None)):
            errors.append(
                checks.Error(
                    f"{model._meta.label} declares source_kind {kind!r} but its default manager "
                    "does not expose sync_from_source(source).",
                    obj=model,
                    id="angee.integrate_vcs.E002",
                )
            )
    return errors
