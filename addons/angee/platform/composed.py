"""Native composed-addon, model, and field read projections.

This module owns the app-registry reads behind both the live platform explorer
and the persisted ``Addon`` reflection sync. Computed model and field resources
are projected here directly from Django's native objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.apps import AppConfig, apps
from django.db.models import Model
from pydantic import BaseModel, PrivateAttr

from angee.addons import addon_manifest, is_angee_addon


@dataclass(frozen=True, slots=True)
class AddonRollup:
    """One composed addon's rolled-up facts, derived from the app registry."""

    name: str
    label: str
    namespace: str
    kind: str
    forced: bool
    model_count: int
    field_count: int
    resource_count: int
    depends_on: list[str]
    model_labels: list[str]
    # Manifest metadata (the addon's ``addon.toml`` ``[addon]`` block), surfaced for the
    # marketplace board — the freeform ``category`` it groups by, and the
    # ``description``/``keywords`` the cards show. The contract owns these; we only read.
    description: str
    keywords: list[str]
    category: str


class PlatformFieldRow(BaseModel):
    """Canonical computed row for one native Django model field."""

    id: str
    name: str
    attname: str
    kind: str
    is_relation: bool
    relation_target: str | None
    model: str
    addon: str

    _field: Any = PrivateAttr()

    @classmethod
    def from_field(cls, model: type[Model], field: Any) -> PlatformFieldRow:
        """Project a native Django field while retaining its request-local reference."""

        related = field.related_model if field.is_relation else None
        row = cls(
            id=f"{model._meta.label_lower}.{field.name}",
            name=field.name,
            attname=getattr(field, "attname", field.name),
            kind=field.get_internal_type(),
            is_relation=bool(field.is_relation),
            relation_target=related._meta.label_lower if related else None,
            model=model._meta.label_lower,
            addon=model._meta.app_label,
        )
        row._field = field
        return row

    def relation_kind(self) -> str | None:
        """Return the graph-edge kind for this native relation field."""

        if not self.is_relation:
            return None
        if self._field.many_to_many:
            return "many_to_many"
        if self._field.one_to_one:
            return "one_to_one"
        return "foreign_key"


class PlatformModelRow(BaseModel):
    """Canonical computed row for one native Django model."""

    id: str
    label: str
    app_label: str
    model_name: str
    verbose_name: str
    db_table: str
    addon_id: str
    addon_label: str
    resource_type: str | None
    field_count: int
    relation_count: int
    depends_on: list[str]

    _model: type[Model] = PrivateAttr()
    _native_fields: tuple[Any, ...] = PrivateAttr()
    _field_rows: tuple[PlatformFieldRow, ...] | None = PrivateAttr(default=None)

    @classmethod
    def from_model(cls, config: AppConfig, model: type[Model]) -> PlatformModelRow:
        """Project a native Django model and retain its already-read native fields."""

        fields = tuple(own_fields(model))
        relations = [field for field in fields if field.is_relation]
        row = cls(
            id=model._meta.label_lower,
            label=model._meta.label_lower,
            app_label=model._meta.app_label,
            model_name=model._meta.model_name,
            verbose_name=str(model._meta.verbose_name),
            db_table=model._meta.db_table,
            addon_id=config.name,
            addon_label=config.label,
            resource_type=getattr(model._meta, "rebac_resource_type", None),
            field_count=len(fields),
            relation_count=len(relations),
            depends_on=sorted(
                {
                    field.related_model._meta.label_lower
                    for field in relations
                    if field.related_model is not None
                }
            ),
        )
        row._model = model
        row._native_fields = fields
        return row

    def fields(self) -> list[PlatformFieldRow]:
        """Lazily project and cache field rows from the retained native fields."""

        if self._field_rows is None:
            self._field_rows = tuple(
                PlatformFieldRow.from_field(self._model, field)
                for field in self._native_fields
            )
        return list(self._field_rows)


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
        # The manifest owns the addon's descriptive metadata; read it, never re-derive.
        manifest = addon_manifest(config)
        rollups.append(
            AddonRollup(
                name=config.name,
                label=config.label,
                namespace=config.name.split(".")[0],
                # The composer owns the root/dependency split; read its annotation.
                kind="consumer" if getattr(config, "angee_addon_root", False) else "required",
                # The composer owns the dependency closure; read its "forced" annotation
                # (cannot be uninstalled), never re-derive it from the registry here.
                forced=bool(getattr(config, "angee_forced", False)),
                model_count=len(models),
                field_count=sum(len(own_fields(model)) for model in models),
                resource_count=counts.get(config.name, 0),
                depends_on=sorted(manifest.depends_on) if manifest else [],
                model_labels=sorted(model._meta.label_lower for model in models),
                description=manifest.description if manifest else "",
                keywords=list(manifest.keywords) if manifest else [],
                category=(manifest.category or "") if manifest else "",
            )
        )
    return rollups


def model_rows() -> list[PlatformModelRow]:
    """Project composed Django models without reading addon resource rollups or graph edges."""

    return [
        PlatformModelRow.from_model(config, model)
        for config in addons()
        for model in data_models(config)
    ]


def field_rows() -> list[PlatformFieldRow]:
    """Project composed Django fields without building an explorer envelope."""

    return [
        PlatformFieldRow.from_field(model, field)
        for config in addons()
        for model in data_models(config)
        for field in own_fields(model)
    ]
