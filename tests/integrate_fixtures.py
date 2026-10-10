"""Native adapters shared by connection and stream contract tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx2
from django.conf import settings
from django.db import connection

from angee.base.impl import ImplClassField
from angee.integrate.discovery import ConnectionDiscovery, DerivedCredential
from angee.integrate.errors import IntegrationError
from angee.integrate.impl import BridgeImpl, LiveBridgeImpl
from angee.integrate.models import Bridge
from angee.integrate.oauth.providers import OAuthProviderType
from angee.integrate.states import StreamKind
from angee.integrate.streams import StreamDefinition, StreamPage
from angee.integrate.testing.integration import Integration

CONNECTION_CLASSES = settings.ANGEE_TEST_CONNECTION_CLASSES


class ConnectionAdapter(BridgeImpl):
    """Exercise the discovery owner with deterministic external responses."""

    registry_setting = "ANGEE_TEST_CONNECTION_CLASSES"
    key = "connection"
    label = "Connection adapter"
    oauth_client = "{vendor}"
    sync_parallelism = 1
    requires_connection_discovery = True

    def discover_connection(self, credential: Any) -> ConnectionDiscovery:
        assert not connection.in_atomic_block
        mode = self.bridge.config.get("discovery")
        if mode == "transport":
            raise httpx2.ConnectError("private transport diagnostic")
        if mode == "server":
            response = httpx2.Response(503, request=httpx2.Request("GET", "https://provider.example/resource"))
            raise httpx2.HTTPStatusError("private server diagnostic", request=response.request, response=response)
        if mode == "quota":
            raise IntegrationError("Provider quota reached.", retry_after=timedelta(hours=1))
        if mode == "refusal":
            raise RuntimeError("private provider diagnostic")
        derived = None
        if self.bridge.config.get("derived"):
            derived = DerivedCredential(
                kind="static_token", name=f"Resource {self.bridge.pk}", material={"api_key": "fake"},
            )
        return ConnectionDiscovery(data={"display_name": "Discovered resource"}, credential=derived)

    def apply_discovery(self, discovery: ConnectionDiscovery) -> None:
        assert connection.in_atomic_block
        self.bridge.display_name = discovery.data.get("display_name", "")
        self.bridge.save(update_fields=["display_name"])

    def streams(self, *, deadline: float | None = None) -> tuple[StreamDefinition, ...]:
        return (
            StreamDefinition("first", kind=StreamKind.EVENT_FEED),
            StreamDefinition("second", kind=StreamKind.EVENT_FEED),
        )

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        assert not connection.in_atomic_block
        if self.bridge.config.get("sync_permanent") and stream.key == "first":
            raise IntegrationError("The resource is unavailable.")
        if self.bridge.config.get("sync_quota"):
            hours = 1 if stream.key == "first" else 2
            raise IntegrationError("Provider quota reached.", retry_after=timedelta(hours=hours))
        return StreamPage(records=(), cursor={}, exhausted=True)


class LiveConnectionAdapter(ConnectionAdapter, LiveBridgeImpl):
    key = "live"
    label = "Live connection adapter"
    session_queue = "test-live"


class ConnectionBridge(Bridge, Integration):
    """Use real Integration and Bridge admission and settlement."""

    connection_impl = ImplClassField(ConnectionAdapter, default="connection")
    live_impl_field = "connection_impl"

    @property
    def backend(self) -> ConnectionAdapter:
        return self.capability_impl

    class Meta(Bridge.Meta):
        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_connection_bridge"
        rebac_resource_type = "integrate/integration"


class RefiningProvider(OAuthProviderType):
    """An addon preset extending a grant through the existing Authlib owner."""

    key = "generic_oauth2"
    label = "Refining provider"

    @classmethod
    def userinfo_params(cls, protocol: Any, access_token: str) -> dict[str, str]:
        return {"proof": access_token}

    @classmethod
    def refine_grant(cls, protocol: Any, tokens: dict[str, Any]) -> dict[str, Any]:
        return protocol.exchange_grant("resource_grant", short_token=tokens["access_token"])


class ContributedProvider(RefiningProvider):
    key = "contributed"
