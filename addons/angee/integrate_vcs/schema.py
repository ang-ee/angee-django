"""GraphQL resources and actions for the VCS capability."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import transaction
from django.utils import timezone
from rebac import system_context
from strawberry import auto
from strawberry.scalars import JSON

from angee.graphql.actions import ActionResult, action_target
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.schema import UserType
from angee.integrate.queue import queue_bridge_sync
from angee.integrate.schema import (
    BridgeSyncStatusMixin,
    CredentialType,
    ExternalAccountType,
    VendorType,
    apply_integration_patch_fields,
    integration_create_attrs,
    save_provided_fields,
)

Vendor = apps.get_model("integrate", "Vendor")
VcsBridge = apps.get_model("integrate_vcs", "VcsBridge")
Repository = apps.get_model("integrate_vcs", "Repository")
Source = apps.get_model("integrate_vcs", "Source")
Template = apps.get_model("integrate_vcs", "Template")

# --- VCS inventory: integrations, repositories, sources, templates ----------


@strawberry_django.type(VcsBridge)
class VcsBridgeType(BridgeSyncStatusMixin, AngeeNode):
    """Admin projection of a VCS bridge child model."""

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType
    backend_class: auto
    lifecycle: auto
    runtime_status: auto
    config: JSON
    last_sync_completed_at: auto
    last_sync_status: auto
    last_sync_summary: JSON
    sync_error: auto
    sync_progress: JSON
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["display_name", "vendor", "lifecycle"])
    def display_name(self) -> str:
        """Return a human label for the record header and relation pickers."""

        return cast(Any, self).display_label


@strawberry_django.type(Repository)
class RepositoryType(AngeeNode):
    """Admin projection of one inventoried repository."""

    vcs_bridge: VcsBridgeType
    org: auto
    name: auto
    remote: auto
    ssh_remote: auto
    remote_id: auto
    default_branch: auto
    visibility: auto
    web_url: auto
    archived: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Source)
class SourceType(AngeeNode):
    """Admin projection of one source (a ref+path pointer into a repository)."""

    repository: RepositoryType
    kind: auto
    ref: auto
    path: auto
    last_synced_at: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Template)
class TemplateType(AngeeNode):
    """Admin projection of one discovered template."""

    source: SourceType
    name: auto
    kind: auto
    path: auto
    inputs: JSON
    created_at: auto
    updated_at: auto


@strawberry.type
class RepoCandidate:
    """A repository the host returns for the add typeahead (not yet inventoried)."""

    name: str
    org: str
    remote: str
    ssh_remote: str
    default_branch: str
    visibility: str
    web_url: str
    archived: bool


@strawberry.input
class VcsBridgeInput:
    """Fields accepted when creating a VCS bridge child row."""

    vendor: PublicID
    owner: PublicID
    display_name: str = ""
    credential: PublicID | None = None
    account: PublicID | None = strawberry.UNSET
    backend_class: str | None = strawberry.UNSET
    lifecycle: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET
    webhook_secret: str = ""


@strawberry.input
class VcsBridgePatch:
    """Fields accepted when updating a VCS bridge child model."""

    id: PublicID
    display_name: str | None = strawberry.UNSET
    vendor: PublicID | None = strawberry.UNSET
    credential: PublicID | None = strawberry.UNSET
    account: PublicID | None = strawberry.UNSET
    owner: PublicID | None = strawberry.UNSET
    backend_class: str | None = strawberry.UNSET
    lifecycle: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET
    webhook_secret: str | None = strawberry.UNSET


def _repo_candidate(descriptor: Any) -> RepoCandidate:
    """Project a host ``RepoDescriptor`` into a typeahead candidate."""

    return RepoCandidate(
        name=str(descriptor.name),
        org=str(descriptor.org),
        remote=str(descriptor.remote),
        ssh_remote=str(descriptor.ssh_remote),
        default_branch=str(descriptor.default_branch),
        visibility=str(descriptor.visibility),
        web_url=str(descriptor.web_url),
        archived=bool(descriptor.archived),
    )


_VCS_BRIDGE_RESOURCE = hasura_model_resource(
    VcsBridgeType,
    model=VcsBridge,
    name="vcs_bridges",
    filterable=[
        "id",
        "vendor",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "last_sync_status",
        "sync_stage",
        "updated_at",
    ],
    sortable=[
        "vendor",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "last_sync_completed_at",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id", "last_sync_items"],
    groupable=[
        "vendor",
        "vendor__display_name",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "last_sync_status",
        "sync_stage",
    ],
    insert=False,
    update=False,
    delete=True,
    field_id_decode={"vendor": public_pk_decoder(Vendor)},
)
_REPOSITORY_RESOURCE = hasura_model_resource(
    RepositoryType,
    model=Repository,
    name="repositories",
    filterable=["id", "vcs_bridge", "org", "name", "visibility", "archived", "updated_at"],
    sortable=["vcs_bridge", "org", "name", "visibility", "archived", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["vcs_bridge", "vcs_bridge__backend_class", "org", "visibility", "archived"],
    insert=False,
    update=False,
    delete=True,
    field_id_decode={"vcs_bridge": public_pk_decoder(VcsBridge)},
)
_SOURCE_RESOURCE = hasura_model_resource(
    SourceType,
    model=Source,
    name="sources",
    filterable=["id", "repository", "kind", "ref", "updated_at"],
    sortable=["repository", "kind", "ref", "path", "last_synced_at", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["repository", "repository__name", "kind", "last_synced_at"],
    insertable=["repository", "kind", "ref", "path"],
    updatable=["kind", "ref", "path"],
    field_id_decode={"repository": public_pk_decoder(Repository)},
    write_backend=AngeeHasuraWriteBackend(Source, public_id_fields=("repository",)),
)
_TEMPLATE_RESOURCE = hasura_model_resource(
    TemplateType,
    model=Template,
    name="templates",
    filterable=["id", "source", "name", "kind", "path", "updated_at"],
    sortable=["source", "name", "kind", "path", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["source", "source__path", "kind", "updated_at"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"source": public_pk_decoder(Source)},
)


@strawberry.type
class VCSConsoleQuery:
    """Admin VCS inventory queries."""

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def search_repositories(self, vcs_bridge_id: PublicID, query: str) -> list[RepoCandidate]:
        """Return host repositories matching ``query`` for the add typeahead."""

        with action_target(
            VcsBridge,
            vcs_bridge_id,
            reason="integrate.graphql.search_repositories",
        ) as vcs:
            return [_repo_candidate(descriptor) for descriptor in vcs.search_repositories(query)]


@strawberry.type
class VcsBridgeCreateMutation:
    """Admin create for a VCS bridge child, validating backend-owned fields."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_vcs_bridge(self, data: VcsBridgeInput) -> VcsBridgeType:
        """Create a VCS child row directly."""

        attrs = {
            **integration_create_attrs(data, reason="integrate.graphql.vcs_bridge.create"),
            "backend_class": VcsBridge.impl_key_for(
                "backend_class",
                None if data.backend_class is strawberry.UNSET else data.backend_class,
                default="local",
            ),
            "webhook_secret": data.webhook_secret,
        }
        if data.config is not strawberry.UNSET:
            attrs["config"] = data.config
        with system_context(reason="integrate.graphql.vcs_bridge.create"), transaction.atomic():
            bridge = VcsBridge.objects.create(**attrs)
        return cast(VcsBridgeType, bridge)


@strawberry.type
class VcsBridgeUpdateMutation:
    """Admin update for a VCS bridge child, validating backend-owned fields."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_vcs_bridge(self, data: VcsBridgePatch) -> VcsBridgeType:
        """Update a VCS child row, rematerializing backend defaults on backend change."""

        with (
            action_target(
                VcsBridge,
                data.id,
                reason="integrate.graphql.vcs_bridge.update",
            ) as bridge,
            transaction.atomic(),
        ):
            if data.backend_class is not strawberry.UNSET:
                bridge.set_impl_key("backend_class", data.backend_class, default="local")
            provided = apply_integration_patch_fields(
                bridge,
                data,
                reason="integrate.graphql.vcs_bridge.update",
            )
            if data.config is not strawberry.UNSET:
                bridge.config = data.config
                provided.add("config")
            if data.webhook_secret is not strawberry.UNSET:
                bridge.webhook_secret = data.webhook_secret or ""
                provided.add("webhook_secret")
            save_provided_fields(bridge, provided)
        return cast(VcsBridgeType, bridge)


@strawberry.type
class VCSActionMutation:
    """Operational actions on a VCS bridge and its inventory."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def add_repository(self, vcs_bridge_id: PublicID, name: str) -> RepositoryType:
        """Inventory one repository by its host ``name`` (a picked typeahead result)."""

        with action_target(VcsBridge, vcs_bridge_id, reason="integrate.graphql.add_repository") as vcs:
            return cast(RepositoryType, vcs.import_repository(name))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def discover_repositories(self, vcs_bridge_id: PublicID, org: str = "") -> ActionResult:
        """Inventory every repository the account exposes (bulk import; prunes vanished)."""

        with action_target(VcsBridge, vcs_bridge_id, reason="integrate.graphql.discover_repositories") as vcs:
            count = vcs.discover_repositories(org=org)
        return ActionResult(ok=True, message=f"Inventoried {count} repository(ies).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def sync_vcs_bridge(self, id: PublicID) -> ActionResult:
        """Queue a refresh of every repository's sources for one VCS bridge."""

        with action_target(VcsBridge, id, reason="integrate.graphql.sync_vcs_bridge") as vcs:
            queue_bridge_sync(vcs, now=timezone.now())
        return ActionResult(ok=True, message="Queued bridge sync.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def refresh_source(self, id: PublicID) -> ActionResult:
        """Re-enumerate one source's output rows now."""

        with action_target(Source, id, reason="integrate.graphql.refresh_source") as source:
            count = source.refresh()
        return ActionResult(ok=True, message=f"Synced {count} item(s).")



_CONSOLE_TYPES: list[object] = [
    VcsBridgeType, *_VCS_BRIDGE_RESOURCE.types,
    RepositoryType, *_REPOSITORY_RESOURCE.types,
    SourceType, *_SOURCE_RESOURCE.types,
    TemplateType, *_TEMPLATE_RESOURCE.types, RepoCandidate,
]

schemas = {
    "console": {
        "query": [
            _VCS_BRIDGE_RESOURCE.query, _REPOSITORY_RESOURCE.query,
            _SOURCE_RESOURCE.query, _TEMPLATE_RESOURCE.query, VCSConsoleQuery,
        ],
        "mutation": [
            _VCS_BRIDGE_RESOURCE.mutation, _REPOSITORY_RESOURCE.mutation,
            _SOURCE_RESOURCE.mutation, _TEMPLATE_RESOURCE.mutation,
            VcsBridgeCreateMutation, VcsBridgeUpdateMutation, VCSActionMutation,
        ],
        "subscription": [changes(VcsBridge, field="vcsBridgeChanged")],
        "types": _CONSOLE_TYPES,
    }
}
