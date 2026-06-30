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
import strawberry_django
from django.apps import apps
from django.db.models import Model
from pydantic import BaseModel
from rebac import ObjectRef
from strawberry import auto

from angee.graphql.access import actor_can_read
from angee.graphql.actions import ActionResult
from angee.graphql.data import hasura_model_resource, hasura_pydantic_resource
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.platform import composed

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


def _field_rows(model: type[Model]) -> list[PlatformField]:
    """Project one model's own fields for the explorer."""

    addon_label = model._meta.app_label
    rows: list[PlatformField] = []
    for field in composed.own_fields(model):
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
    for field in composed.own_fields(model):
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


def _build_explorer() -> PlatformExplorerData:
    """Project the composed models + relation edges, and the addon rollups.

    Addons come from the shared rollup (``composed.addon_rollups``), the single
    derivation the reflection table reads too — not a second walk here.
    """

    configs = composed.addons()
    models_by_addon = {config.name: composed.data_models(config) for config in configs}
    known = {model._meta.label_lower for models in models_by_addon.values() for model in models}

    models_out: list[PlatformModel] = []
    edges_out: list[PlatformEdge] = []
    for config in configs:
        for model in models_by_addon[config.name]:
            fields = _field_rows(model)
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
    addons_out = [
        PlatformAddon(
            id=rollup.name,
            label=rollup.label,
            namespace=rollup.namespace,
            kind=rollup.kind,
            model_count=rollup.model_count,
            field_count=rollup.field_count,
            resource_count=rollup.resource_count,
            depends_on=rollup.depends_on,
            model_labels=rollup.model_labels,
        )
        for rollup in composed.addon_rollups()
    ]
    return PlatformExplorerData(addons=addons_out, models=models_out, edges=edges_out)


_Addon = apps.get_model("platform", "Addon")


@strawberry_django.type(_Addon)
class AddonNode:
    """Read-only projection of one composed/available addon (the reflection table).

    Identity is the addon ``name`` (e.g. ``angee.iam``) — the stable key the whole
    console cross-links on (the model/field pages filter by it) — not a sqid. The
    table is system-synced (``post_migrate``), so this resource is read-only.
    """

    label: auto
    namespace: auto
    description: auto
    category: auto
    keywords: list[str]
    # Exposed as the string value, not an `auto` enum: strawberry would name the
    # generated enums `Source`/`State`, colliding with `integrate`'s connection-source
    # enum and the shared StateField names.
    kind: str
    source: str
    state: str
    forced: auto
    pending: auto
    model_count: auto
    field_count: auto
    resource_count: auto
    depends_on: list[str]
    depended_by: list[str]
    model_labels: list[str]

    @strawberry_django.field
    def id(self) -> str:
        """Return the addon name as the row identity."""

        return self.name  # type: ignore[attr-defined]


# Read is gated by the model's own ``platform/addon`` REBAC scope (const-backed
# admin) via the default queryset — no second explorer gate. Read-only: the table
# is system-synced (``signals.py``).
_ADDON_RESOURCE = hasura_model_resource(
    AddonNode,
    model=_Addon,
    name="platform_addons",
    model_label="platform.Addon",
    filterable=[
        "label", "namespace", "category", "kind", "source", "state", "forced", "pending",
        "model_count", "field_count", "resource_count",
    ],
    sortable=["label", "namespace", "category", "kind", "state", "model_count", "field_count", "resource_count"],
    aggregatable=["id"],
    groupable=["namespace", "category", "kind", "source", "state", "forced", "pending"],
    insert=False,
    update=False,
    delete=False,
    id_decode=lambda value: value,
    id_column="name",
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


@strawberry.type
class AddonInstallMutation:
    """Install/uninstall an addon by editing ``settings.yaml``'s ``INSTALLED_APPS``.

    Thin admin-gated edge over :class:`~angee.platform.models.AddonManager`, which owns
    the whole flow — validate the target, edit the one install source (``settings.yaml``)
    through the :class:`~angee.platform.installer.AddonInstaller`, refuse a forced
    (depended-on) addon, and re-run the reflection reconcile so the board shows the new
    ``pending`` state at once (the addon itself composes on the next ``angee dev`` boot).
    These resolvers only dispatch and relay the result's ``ok``/``summary``.
    """

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def install(self, addon: str) -> ActionResult:
        """Install an addon root and report the manager's outcome."""

        result = _Addon.objects.install(addon)
        return ActionResult(ok=result.ok, message=result.summary)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def uninstall(self, addon: str) -> ActionResult:
        """Uninstall an addon root and report the manager's outcome."""

        result = _Addon.objects.uninstall(addon)
        return ActionResult(ok=result.ok, message=result.summary)


schemas = {
    "console": {
        "query": [
            PlatformQuery,
            _ADDON_RESOURCE.query,
            _MODEL_RESOURCE.query,
            _FIELD_RESOURCE.query,
        ],
        "mutation": [AddonInstallMutation],
        "types": [
            PlatformExplorerData,
            *_ADDON_RESOURCE.types,
            *_MODEL_RESOURCE.types,
            *_FIELD_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the platform addon (console surface)."""
