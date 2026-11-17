"""Console GraphQL surface for external storage mounts."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone
from strawberry import auto

from angee.graphql.actions import ActionResult, action_target, session_user
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.ids import PublicID, public_id_field
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES
from angee.integrate.schema import BridgeTypeMixin, CredentialType
from angee.storage_integrate.connect import create_local_folder_mount
from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import MountBrowseResult

Mount = apps.get_model("storage_integrate", "Mount")
Backend = apps.get_model("storage", "Backend")
strawberry.enum(cast(Any, MountMode))


def mount_credential(info: strawberry.Info, backend_class: str, credential_id: PublicID | None) -> Any | None:
    """Resolve one of the session user's accounts a mount backend may read with (``connectable_credentials``)."""

    if credential_id is None:
        return None
    accounts = Mount.objects.connectable_credentials(session_user(info), backend_class)
    credential = accounts.from_public_id(str(credential_id))
    if credential is None:
        raise ValidationError({"credential": "Choose one of your connected accounts for this source."})
    return credential


def copy_target_backend(copy_backend_id: PublicID | None) -> Any | None:
    """Resolve the storage backend a copy mount stores into, as the session actor reads it."""

    if copy_backend_id is None:
        return None
    backend = Backend.objects.all().from_public_id(str(copy_backend_id))
    if backend is None:
        raise ValidationError({"copy_backend": "The storage backend was not found."})
    return backend


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
class MountAccountsType:
    """The session user's accounts a credential mount backend reads with, and where to connect another."""

    oauth_client: strawberry.ID | None
    accounts: list[CredentialType]


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
            backend_class,
            credential=mount_credential(info, backend_class, credential_id),
            token=token,
        )
        return cast(MountBrowseResultType, result)

    @strawberry.field(permission_classes=ADMIN_PERMISSION_CLASSES)
    def mount_accounts(self, info: strawberry.Info, backend_class: str) -> MountAccountsType:
        """List the session user's accounts a mount backend reads with, and the registration to connect another."""

        registration = Mount.objects.oauth_registration(backend_class)
        accounts = Mount.objects.connectable_credentials(session_user(info), backend_class).order_by("pk")
        return MountAccountsType(
            oauth_client=strawberry.ID(registration.public_id) if registration is not None else None,
            accounts=cast(list[CredentialType], list(accounts)),
        )


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
        copy_backend_id: PublicID | None = None,
    ) -> MountType:
        """Connect a Django-host folder and queue its first sync."""

        mount = create_local_folder_mount(
            session_user(info),
            name=name,
            path=path,
            mode=mode,
            copy_backend=copy_target_backend(copy_backend_id),
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
            MountAccountsType,
            MountType,
            *_MOUNT_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the storage-integrate addon."""
