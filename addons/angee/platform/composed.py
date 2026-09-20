"""Native composed-addon, model, and field read projections.

This module owns the app-registry reads behind both the live platform explorer
and the persisted ``Addon`` reflection sync. Computed model and field resources
are projected here directly from Django's native objects.
"""

from __future__ import annotations

import inspect
from typing import Any

from django.apps import AppConfig, apps
from django.db import DatabaseError, router
from django.db.models import Model
from pydantic import BaseModel, PrivateAttr

from angee.addons import is_angee_addon
from angee.base.impl import ImplChoice, ImplClassField


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


class ContributedFieldRow(BaseModel):
    """One generated concrete field and the addon donor that supplied it."""

    addon_id: str
    model_label: str
    field_name: str
    verbose_name: str


class PlatformImplementationRow(BaseModel):
    """List projection for one registered ``ImplClassField`` implementation."""

    id: str
    model: str
    field: str
    key: str
    label: str
    category: str
    icon: str
    registry_setting: str
    class_path: str
    base_class_path: str
    addon_id: str
    addon_label: str

    _implementation: type = PrivateAttr()
    _choice: ImplChoice = PrivateAttr()

    def detail(self) -> PlatformImplementationDetail:
        """Inspect source only when this canonical registered row is selected."""

        try:
            source_lines, source_start_line = inspect.getsourcelines(self._implementation)
            source = "".join(source_lines)
            source_file = inspect.getsourcefile(self._implementation)
            unavailable = None
        except (OSError, TypeError) as error:
            source = None
            source_file = None
            source_start_line = None
            unavailable = str(error) or type(error).__name__
        return PlatformImplementationDetail(
            **self.model_dump(),
            defaults=self._choice.defaults,
            config_schema=self._choice.config_schema,
            description=inspect.getdoc(self._implementation) or "",
            source=source,
            source_file=source_file,
            source_start_line=source_start_line,
            source_unavailable_reason=unavailable,
        )


class PlatformImplementationDetail(PlatformImplementationRow):
    """Admin-only implementation detail, including declaration and source facts."""

    defaults: dict[str, Any]
    config_schema: dict[str, Any] | None
    description: str
    source: str | None
    source_file: str | None
    source_start_line: int | None
    source_unavailable_reason: str | None


def addons() -> list[AppConfig]:
    """Return the composed Angee addon app configs, sorted by name."""

    return sorted(
        (config for config in apps.get_app_configs() if is_angee_addon(config)),
        key=lambda config: config.name,
    )


def root_app_names() -> frozenset[str]:
    """Return effective root declarations recorded by the composed app graph."""

    return frozenset(
        declaration
        for config in apps.get_app_configs()
        if (declaration := getattr(config, "angee_root_declaration", None)) is not None
    )


def root_app_aliases() -> dict[str, str]:
    """Map exact authored root declarations to their normalized AppConfig names."""

    return {
        declaration: config.name
        for config in apps.get_app_configs()
        if (declaration := getattr(config, "angee_root_declaration", None)) is not None
    }


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


def contributed_fields(config: AppConfig) -> list[ContributedFieldRow]:
    """Return loaded concrete fields emitted from this addon's abstract donors."""

    rows = []
    for owner in addons():
        for model in data_models(owner):
            for field_name, addon_id in model.__dict__.get("angee_contributed_field_origins", ()):
                if addon_id != config.name:
                    continue
                field = model._meta.get_field(field_name)
                rows.append(
                    ContributedFieldRow(
                        addon_id=addon_id,
                        model_label=model._meta.label_lower,
                        field_name=field.name,
                        verbose_name=str(field.verbose_name),
                    )
                )
    return sorted(rows, key=lambda row: (row.model_label, row.field_name))


def resource_counts(*, using: str | None = None) -> dict[str, int]:
    """Return resource-ledger row counts keyed by source addon on ``using``.

    The ``resources`` addon owns the ledger and its rollup; ask it rather than
    re-querying its model here. During migration, a routed-away or not-yet-created
    ledger has no counts to project.
    """

    try:
        resource = apps.get_model("resources", "Resource")
    except LookupError:
        return {}
    ledger = resource.objects.using(using)
    if not router.allow_migrate_model(ledger.db, resource):
        return {}
    try:
        return ledger.counts_by_addon()
    except DatabaseError:
        return {}


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


def _class_path(value: type | None) -> str:
    """Return the canonical import path for a declared class."""

    return "" if value is None else f"{value.__module__}.{value.__qualname__}"


def _implementation_addon(
    implementation: type, configs: list[AppConfig]
) -> AppConfig | None:
    """Return the installed addon whose native Python module owns ``implementation``."""

    module_name = implementation.__module__
    candidates = [
        config
        for config in configs
        if module_name == config.module.__name__
        or module_name.startswith(f"{config.module.__name__}.")
    ]
    return max(candidates, key=lambda config: len(config.module.__name__), default=None)


def _implementation_row(
    model: type[Model], field: ImplClassField, key: str, choice: ImplChoice, configs: list[AppConfig]
) -> PlatformImplementationRow:
    """Project one field-owned registered key without inspecting Python source."""

    implementation = field.resolve_class(key)
    owner = _implementation_addon(implementation, configs)
    row = PlatformImplementationRow(
        id=f"{model._meta.label}.{field.name}:{key}",
        model=model._meta.label,
        field=field.name,
        key=key,
        label=choice.label,
        category=choice.category,
        icon=choice.icon,
        registry_setting=field.registry_setting,
        class_path=_class_path(implementation),
        base_class_path=_class_path(field.base_class),
        addon_id=owner.name if owner is not None else "",
        addon_label=owner.label if owner is not None else "",
    )
    row._implementation = implementation
    row._choice = choice
    return row


def implementation_rows() -> list[PlatformImplementationRow]:
    """Enumerate registered implementations from composed model field owners."""

    configs = addons()
    rows: list[PlatformImplementationRow] = []
    for config in configs:
        for model in data_models(config):
            for field in own_fields(model):
                if not isinstance(field, ImplClassField):
                    continue
                keys = field.registered_keys()
                choices = {choice.key: choice for choice in field.impl_choices()}
                rows.extend(_implementation_row(model, field, key, choices[key], configs) for key in keys)
    return sorted(rows, key=lambda row: row.id)


def implementation_detail(row_id: str) -> PlatformImplementationDetail | None:
    """Resolve detail only for a canonical registered implementation identity."""

    row = next((row for row in implementation_rows() if row.id == row_id), None)
    return None if row is None else row.detail()
