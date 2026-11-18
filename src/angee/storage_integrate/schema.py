"""Console GraphQL surface for external storage mounts."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.utils import timezone
from strawberry import auto

from angee.graphql.actions import ActionResult, action_target, resolve_action_target, session_user
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.ids import PublicID, public_id_field
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES
from angee.integrate.schema import BridgeTypeMixin, ConnectWithAccount
from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import MountBrowseResult

Mount = apps.get_model("storage_integrate", "Mount")
Credential = apps.get_model("integrate", "Credential")
Backend = apps.get_model("storage", "Backend")
strawberry.enum(cast(Any, MountMode))


def _by_public_id(model: Any, id: PublicID | None, *, reason: str) -> Any | None:
    """Resolve an optional input row by public id; the Mount manager admits it."""

    return resolve_action_target(model, id, reason=reason) if id is not None else None


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


@strawberry.type
class MountBackendType(ConnectWithAccount):
    """What connecting a mount of one backend asks for: whether it reads with an account, and the user's choices."""

    key: str
    label: str
    requires_credential: bool


@strawberry_django.type(Mount)
class MountType(BridgeTypeMixin, AngeeNode):
    """Admin projection of an external storage Mount."""

    mode: auto
    drive = public_id_field("drive")


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
        """List one bounded level from a configured mount backend, read with one of the user's accounts."""

        result: MountBrowseResult = Mount.objects.browse_source(
            session_user(info),
            backend_class,
            credential=_by_public_id(Credential, credential_id, reason="storage_integrate.graphql.browse.credential"),
            token=token,
        )
        return cast(MountBrowseResultType, result)

    @strawberry.field(permission_classes=ADMIN_PERMISSION_CLASSES)
    def mount_backend(self, info: strawberry.Info, backend_class: str) -> MountBackendType:
        """Describe connecting a mount of one backend for the session user."""

        impl = Mount.objects.backend_impl(backend_class)
        return MountBackendType.of(
            Mount.objects.build(backend_class),
            session_user(info),
            key=impl.key,
            label=str(impl.label),
            requires_credential=impl.requires_credential,
        )


@strawberry.type
class StorageIntegrateMutation:
    """Admin connection and sync actions for storage mounts."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_mount(
        self,
        info: strawberry.Info,
        backend_class: str,
        name: str,
        # A GraphQL argument named ``root`` collides with Strawberry's extension middleware.
        source_root: str,
        mode: MountMode,
        credential_id: PublicID | None = None,
        copy_backend_id: PublicID | None = None,
    ) -> MountType:
        """Connect a mount of any configured backend and queue its first sync; the Mount manager admits every input."""

        mount = Mount.objects.provision(
            session_user(info),
            backend_class=backend_class,
            name=name,
            root=source_root,
            mode=mode,
            credential=_by_public_id(Credential, credential_id, reason="storage_integrate.graphql.connect.credential"),
            copy_backend=_by_public_id(Backend, copy_backend_id, reason="storage_integrate.graphql.connect.backend"),
        )
        return cast(MountType, mount)

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def sync_mount(self, id: PublicID) -> ActionResult:
        """Queue an immediate reconciliation of one Mount."""

        with action_target(Mount, id, reason="storage_integrate.graphql.sync_mount") as mount:
            queued = mount.request_sync(now=timezone.now())
        return ActionResult(ok=True, message="Queued mount sync." if queued else "Sync already running.")


schemas = {
    "console": {
        "query": [_MOUNT_RESOURCE.query, StorageIntegrateQuery],
        "mutation": [_MOUNT_RESOURCE.mutation, StorageIntegrateMutation],
        "subscription": [changes(Mount, field="mountChanged")],
        "types": [
            MountLocationType,
            MountBrowseResultType,
            MountBackendType,
            MountType,
            *_MOUNT_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the storage-integrate addon."""
