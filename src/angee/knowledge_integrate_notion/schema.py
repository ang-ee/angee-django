"""Native resource declarations for Notion connections."""

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.integrate.queue import queue_bridge_sync
from angee.integrate.schema import (
    BridgeTypeMixin,
    CredentialType,
    IntegrationWriteBackend,
    VendorType,
    integration_input_extensions,
)
from angee.knowledge.schema import VaultType

NotionConnection = apps.get_model("knowledge_integrate_notion", "NotionConnection")


@strawberry_django.type(NotionConnection)
class NotionConnectionType(BridgeTypeMixin, AngeeNode):
    """Connection identity and health; credential material stays write-only."""

    vendor: VendorType
    credential: CredentialType | None
    vault: VaultType | None
    rewrite_page_links: auto
    last_error: auto


_CONNECTION_RESOURCE = hasura_model_resource(
    NotionConnectionType,
    model=NotionConnection,
    name="notion_connections",
    filterable=["id", "display_name", "lifecycle", "runtime_status"],
    sortable=["display_name", "lifecycle", "runtime_status", "created_at", "updated_at"],
    aggregatable=["id"],
    insertable=["display_name", "credential", "rewrite_page_links"],
    updatable=["display_name", "credential", "rewrite_page_links"],
    delete=True,
    write_backend=IntegrationWriteBackend(NotionConnection, public_id_fields=("credential", "owner")),
)


@strawberry.type
class NotionMutation:
    """Dispatch to the existing authorized, queued bridge-sync operation."""

    @strawberry.mutation
    @action_guard("Unable to synchronize Notion.")
    def sync_notion_connection(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Queue one resumable import from the saved connection."""
        connection = authorized_action_target(info, NotionConnection, id, "write")
        queue_bridge_sync(connection)
        return ActionResult(ok=True, message="Queued Notion synchronization.")


schemas = {
    "console": {
        "types": [NotionConnectionType, *_CONNECTION_RESOURCE.types],
        "query": [_CONNECTION_RESOURCE.query],
        "mutation": [_CONNECTION_RESOURCE.mutation, NotionMutation],
        "subscription": [changes(NotionConnection, field="notionConnectionChanged")],
        "input_extensions": integration_input_extensions(_CONNECTION_RESOURCE),
    },
}
