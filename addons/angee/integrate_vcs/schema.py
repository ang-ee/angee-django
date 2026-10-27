"""GraphQL resources and actions for the VCS capability."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from strawberry import auto
from strawberry.permission import PermissionExtension
from strawberry.scalars import JSON
from strawberry.types import get_object_definition

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
    IntegrationLabelMixin,
    IntegrationWriteBackend,
    VendorType,
    integration_input_extensions,
)

Vendor = apps.get_model("integrate", "Vendor")
Credential = apps.get_model("integrate", "Credential")
ExternalAccount = apps.get_model("integrate", "ExternalAccount")
VcsBridge = apps.get_model("integrate_vcs", "VcsBridge")
Repository = apps.get_model("integrate_vcs", "Repository")
Source = apps.get_model("integrate_vcs", "Source")
Template = apps.get_model("integrate_vcs", "Template")

# --- VCS inventory: integrations, repositories, sources, templates ----------


@strawberry_django.type(VcsBridge)
class VcsBridgeType(IntegrationLabelMixin, BridgeSyncStatusMixin, AngeeNode):
    """Admin projection of a VCS bridge child model."""

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType | None
    backend_class: auto
    lifecycle: auto
    runtime_status: auto
    config: JSON
    last_sync_completed_at: auto
    last_sync_summary: JSON
    sync_error: auto
    sync_progress: JSON
    created_at: auto
    updated_at: auto


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
        "sync_stage",
    ],
    delete=True,
    insertable=[
        "vendor",
        "credential",
        "account",
        "backend_class",
        "lifecycle",
        "config",
        "display_name",
        "webhook_secret",
    ],
    updatable=[
        "vendor",
        "credential",
        "account",
        "backend_class",
        "lifecycle",
        "config",
        "display_name",
        "webhook_secret",
    ],
    write_backend=IntegrationWriteBackend(
        VcsBridge,
        public_id_fields=("vendor", "owner", "credential", "account"),
    ),
    field_id_decode={
        "vendor": public_pk_decoder(Vendor),
        "owner": public_pk_decoder(get_user_model()),
        "credential": public_pk_decoder(Credential),
        "account": public_pk_decoder(ExternalAccount),
    },
)
for _field in get_object_definition(_VCS_BRIDGE_RESOURCE.mutation, strict=True).fields:
    if _field.python_name in (
        _VCS_BRIDGE_RESOURCE.insert_one_root,
        _VCS_BRIDGE_RESOURCE.update_by_pk_root,
    ):
        _field.extensions.append(
            PermissionExtension(
                permissions=[permission() for permission in _ADMIN_PERMISSION_CLASSES],
            )
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
    VcsBridgeType,
    *_VCS_BRIDGE_RESOURCE.types,
    RepositoryType,
    *_REPOSITORY_RESOURCE.types,
    SourceType,
    *_SOURCE_RESOURCE.types,
    TemplateType,
    *_TEMPLATE_RESOURCE.types,
    RepoCandidate,
]

schemas = {
    "console": {
        "input_extensions": integration_input_extensions(_VCS_BRIDGE_RESOURCE),
        "query": [
            _VCS_BRIDGE_RESOURCE.query,
            _REPOSITORY_RESOURCE.query,
            _SOURCE_RESOURCE.query,
            _TEMPLATE_RESOURCE.query,
            VCSConsoleQuery,
        ],
        "mutation": [
            _VCS_BRIDGE_RESOURCE.mutation,
            _REPOSITORY_RESOURCE.mutation,
            _SOURCE_RESOURCE.mutation,
            _TEMPLATE_RESOURCE.mutation,
            VCSActionMutation,
        ],
        "subscription": [changes(VcsBridge, field="vcsBridgeChanged")],
        "types": _CONSOLE_TYPES,
    }
}
