"""Native discovery for VCS source output kinds."""

from typing import Any, ClassVar, cast

from django.core import checks
from django.db import models

from angee.integrate.registry import models_with


class VcsOutputManager(models.Manager):
    """Manager contract for reconciling one VCS discovery output kind."""

    def sync_from_source(self, source: Any) -> int:
        raise NotImplementedError


class VcsOutput(models.Model):
    """Explicit output-kind declaration, independent of principal source kinds."""

    source_kind: ClassVar[str] = ""

    class Meta:
        abstract = True


def source_kind_models() -> tuple[type[VcsOutput], ...]:
    """Return only models composing the VCS output declaration."""

    return cast(tuple[type[VcsOutput], ...], models_with(base=VcsOutput))


def check_source_kind_contracts(app_configs=None, **kwargs):
    """Validate source output model declarations."""

    del app_configs, kwargs
    errors = []
    by_kind = {}
    for model in source_kind_models():
        kind = model.source_kind.strip()
        if not kind:
            errors.append(
                checks.Error(
                    f"{model._meta.label} must declare a VCS output source_kind.",
                    obj=model,
                    id="angee.integrate_vcs.E003",
                )
            )
        if kind in by_kind:
            errors.append(
                checks.Error(
                    f"{model._meta.label} duplicates source_kind {kind!r} declared by {by_kind[kind]._meta.label}.",
                    obj=model,
                    id="angee.integrate_vcs.E001",
                )
            )
        else:
            by_kind[kind] = model
        if not isinstance(model._default_manager, VcsOutputManager):
            errors.append(
                checks.Error(
                    f"{model._meta.label} declares source_kind {kind!r} but its default manager "
                    "must compose VcsOutputManager for sync_from_source(source).",
                    obj=model,
                    id="angee.integrate_vcs.E002",
                )
            )
    return errors
