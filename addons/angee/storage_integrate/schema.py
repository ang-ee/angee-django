"""Console GraphQL surface for external storage mounts."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.utils import timezone
from strawberry import auto
from strawberry.scalars import JSON

from angee.graphql.actions import ActionResult, action_target, resolve_action_target
from angee.graphql.data import hasura_model_resource
from angee.graphql.ids import PublicID, require_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.integrate.queue import queue_bridge_sync
from angee.integrate.schema import BridgeSyncStatusMixin, IntegrationLabelMixin
from angee.storage_integrate.connect import create_local_folder_mount
from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import (
    MountBrowseResult,
    browse_mount_source,
)

Mount = apps.get_model("storage_integrate", "Mount")
Drive = apps.get_model("storage", "Drive")
Credential = apps.get_model("integrate", "Credential")
strawberry.enum(cast(Any, MountMode))


@strawberry.type
class MountLocationType:
    """GraphQL projection of one location in a mount source tree."""

    token: str
    label: str
    is_navigable: bool
    is_mountable: bool
    blocked_reason: str


@strawberry.type
class MountBrowseResultType:
    """GraphQL projection of one bounded page of mount-source locations."""

    location: MountLocationType
    parent_token: str | None
    entries: tuple[MountLocationType, ...]
    truncated: bool
    supports_manual_token: bool


@strawberry_django.type(Mount)
class MountType(IntegrationLabelMixin, BridgeSyncStatusMixin, AngeeNode):
    """Admin projection of an external storage Mount."""

    mode: auto
    backend_class: auto
    lifecycle: auto
    runtime_status: auto
    config: JSON
    last_sync_completed_at: auto
    last_sync_status: auto
    last_sync_items: auto
    last_sync_summary: JSON
    sync_error: auto
    sync_progress: JSON
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["drive_id"])
    def drive(self) -> strawberry.ID:
        """Return the dedicated drive's public id without exposing its backend."""

        return require_public_id(Drive, cast(Any, self).drive_id)


_MOUNT_RESOURCE = hasura_model_resource(
    MountType,
    model=Mount,
    name="mounts",
    filterable=[
        "id",
        "mode",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "last_sync_status",
        "sync_stage",
        "updated_at",
    ],
    sortable=[
        "mode",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "last_sync_completed_at",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id", "last_sync_items"],
    groupable=[
        "mode",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "last_sync_status",
        "sync_stage",
    ],
    insert=False,
    update=False,
    delete=True,
)


@strawberry.type
class StorageIntegrateQuery:
    """Admin reads for browsing mount source roots before connection."""

    @strawberry.field(permission_classes=ADMIN_PERMISSION_CLASSES)
    def browse_mount_source(
        self,
        info: strawberry.Info,
        backend_class: str = "local_folder",
        credential_id: PublicID | None = None,
        token: str = "",
    ) -> MountBrowseResultType:
        """List one bounded level from a configured mount backend."""

        del info
        credential = (
            resolve_action_target(
                Credential,
                credential_id,
                reason="storage_integrate.graphql.browse_mount_source.credential",
            )
            if credential_id is not None
            else None
        )
        result: MountBrowseResult = browse_mount_source(
            backend_class,
            credential=credential,
            token=token,
        )
        return cast(MountBrowseResultType, result)


@strawberry.type
class StorageIntegrateMutation:
    """Admin connection and sync actions for storage mounts."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_local_folder(
        self,
        info: strawberry.Info,
        name: str,
        path: str,
        mode: MountMode,
    ) -> MountType:
        """Connect a Django-host folder and queue its first sync."""

        mount = create_local_folder_mount(
            session_user(info),
            name=name,
            path=path,
            mode=mode,
        )
        return cast(MountType, mount)

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def sync_mount(self, id: PublicID) -> ActionResult:
        """Queue an immediate reconciliation of one Mount."""

        with action_target(Mount, id, reason="storage_integrate.graphql.sync_mount") as mount:
            queue_bridge_sync(mount, now=timezone.now())
        return ActionResult(ok=True, message="Queued mount sync.")


schemas = {
    "console": {
        "query": [_MOUNT_RESOURCE.query, StorageIntegrateQuery],
        "mutation": [_MOUNT_RESOURCE.mutation, StorageIntegrateMutation],
        "subscription": [changes(Mount, field="mountChanged")],
        "types": [
            MountLocationType,
            MountBrowseResultType,
            MountType,
            *_MOUNT_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the storage-integrate addon."""
