"""GraphQL introspection surface for the Angee platform console.

One read-only console query reflects the composed runtime back to platform
admins: ``platformExplorer`` walks the Django app registry for the composed
addons, their concrete models, fields, and relation edges, and rolls up each
addon's import-ledger count. The ledger *listing* itself is owned by the
``resources`` addon (``resources.resourceLedger``), which contributes its own
section into the platform console. Reads here are gated on ``read`` over the
table-less ``platform/explorer`` anchor (``permissions.zed``). The platform addon
owns no data — it asks the owners (the app registry for schema shape, the resource
ledger for the per-addon count) and projects their answers.
"""

from __future__ import annotations

import strawberry
from django.apps import AppConfig, apps
from django.db.models import Model
from pydantic import BaseModel
from rebac import ObjectRef

from angee.addons import is_angee_addon
from angee.graphql.access import actor_can_read
from angee.graphql.data import hasura_pydantic_resource

_EXPLORER = ObjectRef("platform/explorer", "default")


@strawberry.type
class PlatformField:
    """One model field projected for the explorer."""

    name: str
    attname: str
    kind: str
    is_relation: bool
    relation_target: str | None
    addon: str


@strawberry.type
class PlatformModel:
    """One concrete runtime model and its fields."""

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
    fields: list[PlatformField]
    depends_on: list[str]


@strawberry.type
class PlatformEdge:
    """A directed relation between two shown models."""

    id: str
    source: str
    target: str
    kind: str
    field_name: str


@strawberry.type
class PlatformAddon:
    """One composed addon with its model/field/resource rollups."""

    id: str
    label: str
    namespace: str
    kind: str
    model_count: int
    field_count: int
    resource_count: int
    depends_on: list[str]
    model_labels: list[str]


@strawberry.type
class PlatformExplorerData:
    """The whole composed surface: addons, models, and relation edges."""

    addons: list[PlatformAddon]
    models: list[PlatformModel]
    edges: list[PlatformEdge]


@strawberry.type
class PlatformQuery:
    """Read-only platform console introspection queries."""

    @strawberry.field
    def platform_explorer(self) -> PlatformExplorerData | None:
        """Return the composed addons/models/edges for platform readers, else ``None``."""

        if not platform_can_read():
            return None
        return _build_explorer()


def platform_can_read() -> bool:
    """Return whether the current actor may read the platform surface.

    Shared with the ``resources`` addon, which gates its contributed ledger
    listing on the same platform-admin read so the whole console resolves for one
    role.
    """

    return actor_can_read(_EXPLORER)


def _addons() -> list[AppConfig]:
    """Return composed Angee addon app configs, sorted by name."""

    return sorted(
        (config for config in apps.get_app_configs() if is_angee_addon(config)),
        key=lambda config: config.name,
    )


def _data_models(config: AppConfig) -> list[type[Model]]:
    """Return one addon's concrete data models.

    Table-less REBAC anchors (``managed = False``) and simple-history audit
    shadows are excluded — they are not first-class domain models, matching the
    "operator: 0 models" the explorer reports for an addon that only anchors a
    permission type.
    """

    return [
        model
        for model in config.get_models()
        if model._meta.managed and not model._meta.proxy and not _is_historical(model)
    ]


def _is_historical(model: type[Model]) -> bool:
    """Return whether ``model`` is a simple-history audit shadow table.

    Detected via simple-history's own marker: a generated historical model carries
    ``instance_type`` (the tracked model class); ordinary models never do.
    """

    return getattr(model, "instance_type", None) is not None


def _own_fields(model: type[Model]) -> list:
    """Return a model's own concrete columns plus declared many-to-many fields."""

    return [*model._meta.fields, *model._meta.many_to_many]


def _field_rows(model: type[Model]) -> list[PlatformField]:
    """Project one model's own fields for the explorer."""

    addon_label = model._meta.app_label
    rows: list[PlatformField] = []
    for field in _own_fields(model):
        related = field.related_model if field.is_relation else None
        rows.append(
            PlatformField(
                name=field.name,
                attname=getattr(field, "attname", field.name),
                kind=field.get_internal_type(),
                is_relation=bool(field.is_relation),
                relation_target=related._meta.label_lower if related else None,
                addon=addon_label,
            )
        )
    return rows


def _edge_rows(model: type[Model], known: set[str]) -> list[PlatformEdge]:
    """Return relation edges from ``model`` to other shown models."""

    source = model._meta.label_lower
    edges: list[PlatformEdge] = []
    for field in _own_fields(model):
        related = field.related_model if field.is_relation else None
        if related is None:
            continue
        target = related._meta.label_lower
        if target not in known:
            continue
        if field.many_to_many:
            kind = "many_to_many"
        elif field.one_to_one:
            kind = "one_to_one"
        else:
            kind = "foreign_key"
        edges.append(
            PlatformEdge(
                id=f"{source}.{field.name}",
                source=source,
                target=target,
                kind=kind,
                field_name=field.name,
            )
        )
    return edges


def _resource_counts() -> dict[str, int]:
    """Return resource-ledger row counts keyed by source addon.

    The ``resources`` addon owns the ledger and its rollup; ask it rather than
    re-querying its model here.
    """

    try:
        resource = apps.get_model("resources", "Resource")
    except LookupError:
        return {}
    return resource.objects.counts_by_addon()


def _build_explorer() -> PlatformExplorerData:
    """Project the composed addons, models, and relation edges."""

    configs = _addons()
    resource_counts = _resource_counts()

    models_by_addon = {config.name: _data_models(config) for config in configs}
    known = {model._meta.label_lower for models in models_by_addon.values() for model in models}

    models_out: list[PlatformModel] = []
    edges_out: list[PlatformEdge] = []
    addons_out: list[PlatformAddon] = []
    for config in configs:
        models = models_by_addon[config.name]
        field_total = 0
        for model in models:
            fields = _field_rows(model)
            field_total += len(fields)
            relations = [field for field in fields if field.is_relation]
            models_out.append(
                PlatformModel(
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
                    fields=fields,
                    depends_on=sorted({field.relation_target for field in relations if field.relation_target}),
                )
            )
            edges_out.extend(_edge_rows(model, known))
        # The composer owns the root/dependency split; read its annotation rather
        # than re-deriving the closure here (`angee/compose/appgraph.py`).
        kind = "consumer" if getattr(config, "angee_addon_root", False) else "required"
        addons_out.append(
            PlatformAddon(
                id=config.name,
                label=config.label,
                namespace=config.name.split(".")[0],
                kind=kind,
                model_count=len(models),
                field_count=field_total,
                resource_count=resource_counts.get(config.name, 0),
                depends_on=sorted(getattr(config, "angee_depends_on", ())),
                model_labels=sorted(model._meta.label_lower for model in models),
            )
        )
    return PlatformExplorerData(addons=addons_out, models=models_out, edges=edges_out)


class PlatformAddonRow(BaseModel):
    """Computed platform-explorer addon row (no Django table behind it).

    The row-shape SSOT for the ``platform.Addon`` Hasura resource. Reverse
    dependencies (``depended_by``) are resolved server-side across the whole
    addon set, since a paginated client page cannot invert ``depends_on``.
    """

    id: str
    label: str
    namespace: str
    kind: str
    model_count: int
    field_count: int
    resource_count: int
    depends_on: list[str]
    depended_by: list[str]
    model_labels: list[str]


def _addon_rows() -> list[PlatformAddonRow]:
    """Project composed addons as rows, resolving reverse dependencies."""

    explorer = _build_explorer()
    depended_by: dict[str, list[str]] = {}
    for addon in explorer.addons:
        for dependency in addon.depends_on:
            depended_by.setdefault(dependency, []).append(addon.id)
    return [
        PlatformAddonRow(
            id=addon.id,
            label=addon.label,
            namespace=addon.namespace,
            kind=addon.kind,
            model_count=addon.model_count,
            field_count=addon.field_count,
            resource_count=addon.resource_count,
            depends_on=list(addon.depends_on),
            depended_by=sorted(depended_by.get(addon.id, ())),
            model_labels=list(addon.model_labels),
        )
        for addon in explorer.addons
    ]


def _addon_rows_for(info: strawberry.Info) -> list[PlatformAddonRow]:
    """Row provider gated on the same platform-admin read as the explorer."""

    del info
    return _addon_rows() if platform_can_read() else []


_ADDON_RESOURCE = hasura_pydantic_resource(
    PlatformAddonRow,
    name="platform_addons",
    model_label="platform.Addon",
    filterable=[
        "id",
        "label",
        "namespace",
        "kind",
        "model_count",
        "field_count",
        "resource_count",
    ],
    sortable=[
        "label",
        "namespace",
        "kind",
        "model_count",
        "field_count",
        "resource_count",
    ],
    rows=_addon_rows_for,
)


class PlatformModelRow(BaseModel):
    """Computed platform-explorer model row (no Django table behind it).

    The row-shape SSOT for the ``platform.Model`` Hasura resource. The strawberry
    ``PlatformModel`` keys by ``label`` and carries no ``id``; the row adds an
    explicit ``id`` (= ``label``) for by-pk addressing. The nested ``fields`` list
    stays a detail concern (it is the ``platform.Field`` resource, flattened), so
    it is dropped here in favour of the ``field_count``/``relation_count`` rollup.
    """

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


class PlatformFieldRow(BaseModel):
    """Computed platform-explorer field row, flattened across all models.

    The row-shape SSOT for the ``platform.Field`` Hasura resource. The strawberry
    ``PlatformField`` is nested under one model; this row flattens every model's
    fields into one collection, carrying the owning ``model`` label and ``addon``
    context plus a synthetic ``id`` (``f"{model}.{name}"``) for by-pk addressing.
    """

    id: str
    name: str
    attname: str
    kind: str
    is_relation: bool
    relation_target: str | None
    model: str
    addon: str


def _model_rows() -> list[PlatformModelRow]:
    """Project composed models as rows (the explorer's per-model rollup)."""

    explorer = _build_explorer()
    return [
        PlatformModelRow(
            id=model.label,
            label=model.label,
            app_label=model.app_label,
            model_name=model.model_name,
            verbose_name=model.verbose_name,
            db_table=model.db_table,
            addon_id=model.addon_id,
            addon_label=model.addon_label,
            resource_type=model.resource_type,
            field_count=model.field_count,
            relation_count=model.relation_count,
            depends_on=list(model.depends_on),
        )
        for model in explorer.models
    ]


def _field_rows_flat() -> list[PlatformFieldRow]:
    """Project every composed model's fields, flattened into one collection."""

    explorer = _build_explorer()
    return [
        PlatformFieldRow(
            id=f"{model.label}.{field.name}",
            name=field.name,
            attname=field.attname,
            kind=field.kind,
            is_relation=field.is_relation,
            relation_target=field.relation_target,
            model=model.label,
            addon=field.addon,
        )
        for model in explorer.models
        for field in model.fields
    ]


def _model_rows_for(info: strawberry.Info) -> list[PlatformModelRow]:
    """Row provider gated on the same platform-admin read as the explorer."""

    del info
    return _model_rows() if platform_can_read() else []


def _field_rows_for(info: strawberry.Info) -> list[PlatformFieldRow]:
    """Row provider gated on the same platform-admin read as the explorer."""

    del info
    return _field_rows_flat() if platform_can_read() else []


_MODEL_RESOURCE = hasura_pydantic_resource(
    PlatformModelRow,
    name="platform_models",
    model_label="platform.Model",
    filterable=[
        "id",
        "label",
        "app_label",
        "model_name",
        "verbose_name",
        "db_table",
        "addon_id",
        "addon_label",
        "resource_type",
        "field_count",
        "relation_count",
    ],
    sortable=[
        "label",
        "app_label",
        "model_name",
        "verbose_name",
        "db_table",
        "addon_id",
        "addon_label",
        "field_count",
        "relation_count",
    ],
    rows=_model_rows_for,
)


_FIELD_RESOURCE = hasura_pydantic_resource(
    PlatformFieldRow,
    name="platform_fields",
    model_label="platform.Field",
    filterable=[
        "id",
        "name",
        "attname",
        "kind",
        "is_relation",
        "relation_target",
        "model",
        "addon",
    ],
    sortable=[
        "name",
        "attname",
        "kind",
        "relation_target",
        "model",
        "addon",
    ],
    rows=_field_rows_for,
)


schemas = {
    "console": {
        "query": [
            PlatformQuery,
            _ADDON_RESOURCE.query,
            _MODEL_RESOURCE.query,
            _FIELD_RESOURCE.query,
        ],
        "types": [
            PlatformExplorerData,
            *_ADDON_RESOURCE.types,
            *_MODEL_RESOURCE.types,
            *_FIELD_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the platform addon (console surface)."""
