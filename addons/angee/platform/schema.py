"""GraphQL introspection surface for the Angee platform console.

One read-only console query reflects the runtime back to platform admins:
``platformExplorer`` reads addon detail and reconciled resource counts from the
persistent catalogue and walks the Django app registry for concrete models,
fields, and relation edges. The ledger *listing* itself is owned by the
``resources`` addon (``resources.resourceLedger``), which contributes its own
section into the platform console. Reads here are gated on ``read`` over the
table-less ``platform/explorer`` anchor (``permissions.zed``). The platform addon
owns the persisted lifecycle catalogue and asks the app registry and resource
ledger for the runtime facts projected alongside it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from rebac import ObjectRef, system_context
from strawberry import auto
from strawberry.scalars import JSON

from angee.graphql.access import actor_can_read
from angee.graphql.actions import ActionResult
from angee.graphql.data import hasura_model_resource, hasura_pydantic_resource
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.platform import composed

_EXPLORER = ObjectRef("platform/explorer", "default")


@strawberry.enum
class AddonChangeAction(StrEnum):
    INSTALL = "install"
    DISABLE = "disable"


@strawberry.type
class AddonChangeImpact:
    name: str
    label: str
    root: bool
    depends_on: list[str]


@strawberry.type
class AddonModelInventory:
    label: str
    verbose_name: str
    row_count: int | None


@strawberry.type
class AddonContributedFieldInventory:
    model_label: str
    field_name: str
    verbose_name: str


@strawberry.type
class AddonDataInventory:
    addon: str
    models: list[AddonModelInventory]
    contributed_fields: list[AddonContributedFieldInventory]


@strawberry.type
class AddonChangePreview:
    action: str
    addon: str
    revision: str
    can_apply: bool
    refusal: str | None
    roots_before: list[str]
    roots_after: list[str]
    addons_to_enable: list[AddonChangeImpact]
    addons_to_disable: list[AddonChangeImpact]
    data_inventory: list[AddonDataInventory]
    migration_warning: str | None


@strawberry.type
class PlatformField:
    """One model field projected for the explorer."""

    name: str
    attname: str
    kind: str
    is_relation: bool
    relation_target: str | None
    addon: str


def _nested_model_fields(root: Any) -> list[PlatformField]:
    """Return a model row's lazily projected canonical field rows."""

    return cast(list[PlatformField], cast(composed.PlatformModelRow, root).fields())


@strawberry.type
class PlatformModel:
    """Legacy nested binding over a canonical computed platform model row."""

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
    fields: list[PlatformField] = strawberry.field(resolver=_nested_model_fields)
    depends_on: list[str]


@strawberry.type
class PlatformEdge:
    """A directed relation between two shown models."""

    id: str
    source: str
    target: str
    kind: str
    field_name: str


def _addon_id(root: Any) -> str:
    """Return the catalogue addon's native name as the explorer identity."""

    return root.name


@strawberry.type
class PlatformAddon:
    """Detail projection over the persisted addon catalogue."""

    id: str = strawberry.field(resolver=_addon_id)
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
    """Selected live composed views sharing one request-local model projection."""

    _model_rows: strawberry.Private[list[composed.PlatformModelRow] | None] = None

    def model_rows(self) -> list[composed.PlatformModelRow]:
        """Return one lazily materialized model projection for nested consumers."""

        if self._model_rows is None:
            self._model_rows = composed.model_rows()
        return self._model_rows

    @strawberry.field
    def addons(self) -> list[PlatformAddon]:
        """Return persisted catalogue rows, including disabled and historical addons."""

        return cast(list[PlatformAddon], _Addon.objects.all())

    @strawberry.field
    def models(self) -> list[PlatformModel]:
        """Return canonical model rows through the legacy nested binding."""

        return cast(list[PlatformModel], self.model_rows())

    @strawberry.field
    def edges(self) -> list[PlatformEdge]:
        """Build graph edges from the same request-local model and field facts."""

        return _edge_rows(self.model_rows())

    @strawberry.field
    def pending_addon_changes(self) -> bool | None:
        """Return whether a verified settings edit still awaits graph reload."""

        return _Addon.objects.pending_changes()


@strawberry.type
class PlatformImplementationDetail:
    """Detail-only implementation declaration and registered Python source."""

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
    defaults: JSON
    config_schema: JSON | None
    description: str
    source: str | None
    source_file: str | None
    source_start_line: int | None
    source_unavailable_reason: str | None

    @classmethod
    def from_row(cls, row: composed.PlatformImplementationDetail) -> PlatformImplementationDetail:
        """Project the Pydantic owner row onto its GraphQL detail type."""

        return cls(**row.model_dump())


@strawberry.type
class PlatformQuery:
    """Read-only platform console introspection queries."""

    @strawberry.field
    def platform_explorer(self) -> PlatformExplorerData | None:
        """Return the composed addons/models/edges for platform readers, else ``None``."""

        if not platform_can_read():
            return None
        return PlatformExplorerData()

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def addon_change_preview(
        self, addon: str, action: AddonChangeAction
    ) -> AddonChangePreview:
        """Return the manager-owned dependency and data inventory forecast."""

        with system_context(reason="platform.addon.change_preview"):
            return cast(
                AddonChangePreview,
                _Addon.objects.change_preview(addon, action.value),
            )

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def platform_implementation(self, id: str) -> PlatformImplementationDetail | None:
        """Return declaration and Python source for one registered implementation."""

        detail = composed.implementation_detail(id)
        return None if detail is None else PlatformImplementationDetail.from_row(detail)


def platform_can_read() -> bool:
    """Return whether the current actor may read the platform surface.

    Shared with the ``resources`` addon, which gates its contributed ledger
    listing on the same platform-admin read so the whole console resolves for one
    role.
    """

    return actor_can_read(_EXPLORER)


def _edge_rows(models: list[composed.PlatformModelRow]) -> list[PlatformEdge]:
    """Return relation edges from already-read canonical model and field rows."""

    known = {model.label for model in models}
    edges: list[PlatformEdge] = []
    for model in models:
        for field in model.fields():
            if field.relation_target not in known:
                continue
            kind = field.relation_kind()
            if kind is None:
                continue
            edges.append(
                PlatformEdge(
                    id=field.id,
                    source=model.label,
                    target=field.relation_target,
                    kind=kind,
                    field_name=field.name,
                )
            )
    return edges


_Addon = apps.get_model("platform", "Addon")


@strawberry_django.type(_Addon)
class AddonNode:
    """Read-only projection of one composed/available addon (the reflection table).

    Identity is the addon ``name`` (e.g. ``angee.iam``) — the stable key the whole
    console cross-links on (the model/field pages filter by it) — not a sqid. The
    table is system-synced (``post_migrate``), so this resource is read-only.
    """

    name: auto
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

        return self.name


# Read is gated by the model's own ``platform/addon`` REBAC scope (const-backed
# admin) via the default queryset — no second explorer gate. Read-only: the table
# is system-synced (``signals.py``).
_ADDON_RESOURCE = hasura_model_resource(
    AddonNode,
    model=_Addon,
    name="platform_addons",
    model_label="platform.Addon",
    public_id_field="id",
    filterable=[
        "name",
        "label",
        "namespace",
        "category",
        "kind",
        "source",
        "state",
        "forced",
        "pending",
        "model_count",
        "field_count",
        "resource_count",
    ],
    sortable=["name", "namespace", "category", "kind", "state", "model_count", "field_count", "resource_count"],
    aggregatable=["id"],
    groupable=["namespace", "category", "kind", "source", "state", "forced", "pending"],
    insert=False,
    update=False,
    delete=False,
    id_decode=lambda value: value,
    id_column="name",
)


def _model_rows_for(info: strawberry.Info) -> list[composed.PlatformModelRow]:
    """Row provider gated on the same platform-admin read as the explorer."""

    del info
    return composed.model_rows() if platform_can_read() else []


def _field_rows_for(info: strawberry.Info) -> list[composed.PlatformFieldRow]:
    """Row provider gated on the same platform-admin read as the explorer."""

    del info
    return composed.field_rows() if platform_can_read() else []


def _implementation_rows_for(info: strawberry.Info) -> list[composed.PlatformImplementationRow]:
    """List registered implementations only for platform administrators."""

    del info
    return composed.implementation_rows() if platform_can_read() else []


_MODEL_RESOURCE = hasura_pydantic_resource(
    composed.PlatformModelRow,
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
    composed.PlatformFieldRow,
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
    frontend_row_model="server",
)


_IMPLEMENTATION_RESOURCE = hasura_pydantic_resource(
    composed.PlatformImplementationRow,
    name="platform_implementations",
    model_label="platform.Implementation",
    filterable=[
        "id",
        "model",
        "field",
        "key",
        "label",
        "category",
        "icon",
        "registry_setting",
        "class_path",
        "base_class_path",
        "addon_id",
        "addon_label",
    ],
    sortable=[
        "model",
        "field",
        "key",
        "label",
        "category",
        "registry_setting",
        "class_path",
        "addon_id",
        "addon_label",
    ],
    rows=_implementation_rows_for,
    frontend_row_model="client",
)


@strawberry.type
class AddonInstallMutation:
    """Dispatch admin-authorized changes to the addon manager's reviewed plan.

    The manager owns loaded-graph admission, settings edits and reconciliation.
    These resolvers only relay its outcome.
    """

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def install(self, addon: str, revision: str | None = None) -> ActionResult:
        """Install an addon root and report the manager's outcome."""

        result = _Addon.objects.install(addon, revision)
        return ActionResult(ok=result.ok, message=result.summary)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def disable(self, addon: str, revision: str | None = None) -> ActionResult:
        """Disable an addon root and report the manager's outcome."""

        result = _Addon.objects.disable(addon, revision)
        return ActionResult(ok=result.ok, message=result.summary)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def uninstall(self, addon: str, revision: str | None = None) -> ActionResult:
        """Compatibility alias for the canonical disable mutation."""

        result = _Addon.objects.uninstall(addon, revision)
        return ActionResult(ok=result.ok, message=result.summary)


schemas = {
    "console": {
        "query": [
            PlatformQuery,
            _ADDON_RESOURCE.query,
            _MODEL_RESOURCE.query,
            _FIELD_RESOURCE.query,
            _IMPLEMENTATION_RESOURCE.query,
        ],
        "mutation": [AddonInstallMutation],
        "types": [
            PlatformExplorerData,
            AddonChangePreview,
            *_ADDON_RESOURCE.types,
            *_MODEL_RESOURCE.types,
            *_FIELD_RESOURCE.types,
            PlatformImplementationDetail,
            *_IMPLEMENTATION_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the platform addon (console surface)."""
