"""Composed-addon introspection — the single walk over the app registry.

One owner for the per-addon rollups (model/field/resource counts, dependency
edges) the platform surface needs. Both the live explorer view
(``schema._build_explorer``) and the ``Addon`` reflection sync
(``AddonManager.reconcile_from_registry``) read these — never two parallel walks.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import AppConfig, apps
from django.db.models import Model

from angee.addons import is_angee_addon


@dataclass(frozen=True, slots=True)
class AddonRollup:
    """One composed addon's rolled-up facts, derived from the app registry."""

    name: str
    label: str
    namespace: str
    kind: str
    model_count: int
    field_count: int
    resource_count: int
    depends_on: list[str]
    model_labels: list[str]


def addons() -> list[AppConfig]:
    """Return the composed Angee addon app configs, sorted by name."""

    return sorted(
        (config for config in apps.get_app_configs() if is_angee_addon(config)),
        key=lambda config: config.name,
    )


def is_historical(model: type[Model]) -> bool:
    """Return whether ``model`` is a simple-history audit shadow (carries ``instance_type``)."""

    return getattr(model, "instance_type", None) is not None


def data_models(config: AppConfig) -> list[type[Model]]:
    """Return one addon's concrete data models (no anchors, proxies, or history shadows)."""

    return [
        model
        for model in config.get_models()
        if model._meta.managed and not model._meta.proxy and not is_historical(model)
    ]


def own_fields(model: type[Model]) -> list:
    """Return a model's own concrete columns plus declared many-to-many fields."""

    return [*model._meta.fields, *model._meta.many_to_many]


def resource_counts() -> dict[str, int]:
    """Return resource-ledger row counts keyed by source addon.

    The ``resources`` addon owns the ledger and its rollup; ask it rather than
    re-querying its model here.
    """

    try:
        resource = apps.get_model("resources", "Resource")
    except LookupError:
        return {}
    return resource.objects.counts_by_addon()


def addon_rollups() -> list[AddonRollup]:
    """Roll up every composed addon's model/field/resource facts from the app graph.

    The single derivation the explorer view and the reflection table both read.
    """

    counts = resource_counts()
    rollups: list[AddonRollup] = []
    for config in addons():
        models = data_models(config)
        rollups.append(
            AddonRollup(
                name=config.name,
                label=config.label,
                namespace=config.name.split(".")[0],
                # The composer owns the root/dependency split; read its annotation.
                kind="consumer" if getattr(config, "angee_addon_root", False) else "required",
                model_count=len(models),
                field_count=sum(len(own_fields(model)) for model in models),
                resource_count=counts.get(config.name, 0),
                depends_on=sorted(getattr(config, "angee_depends_on", ())),
                model_labels=sorted(model._meta.label_lower for model in models),
            )
        )
    return rollups
