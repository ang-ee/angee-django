"""Native table owners for the agent MCP authorization catalogue."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from asgiref.sync import async_to_sync
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import router, transaction
from rebac import database_context, system_context

from angee.agents.constants import BUILTIN_MCP_ANGEE
from angee.agents.grants import sync_resource_reader_grants
from angee.iam.managers import ProtectedManager
from angee.mcp.server import mcp_server
from angee.provenance.contrib.angee.managers import AngeeHistoryManager

if TYPE_CHECKING:
    from angee.agents.models import MCPServer, MCPTool


class MCPServerManager[ServerT: MCPServer](AngeeHistoryManager[ServerT]):
    """Own canonical builtin-server adoption and first catalogue provisioning."""

    def builtin(self, *, create: bool = False) -> ServerT:
        """Return the one declared builtin, preserving authored name and credential."""

        rows = self.sudo(reason="agents.builtin_server")
        servers = list(rows.filter(config__builtin=BUILTIN_MCP_ANGEE).order_by("pk"))
        if not servers and create:
            server, _ = rows.get_or_create(
                name="angee", defaults={"placement": "internal", "config": {"builtin": BUILTIN_MCP_ANGEE}},
            )
            if server.builtin != BUILTIN_MCP_ANGEE:
                raise ImproperlyConfigured("MCP server 'angee' is already assigned to an external server.")
            servers = [server]
        if len(servers) != 1:
            raise ImproperlyConfigured(
                f"Exactly one agents.MCPServer row must declare config.builtin='angee' (found {len(servers)})."
            )
        return servers[0]


class MCPToolManager[ToolT: MCPTool](ProtectedManager[ToolT]):
    """Own deterministic catalogue synchronization and grant-row identities."""

    def authorization_ids(self, server_sqid: str, tool_names: Iterable[str]) -> dict[str, str]:
        """Resolve catalogue public keys to primary-key authorization identities."""

        server_model = self.model.server.field.related_model
        lookup = {f"server__{field}": value for field, value in server_model.public_id_lookup(server_sqid).items()}
        return {
            str(name): str(pk)
            for name, pk in self.sudo(reason="agents.tool_grant_ids").filter(
                **lookup, name__in=tool_names, enabled=True,
            ).values_list("name", "pk")
        }

    def sync_builtin(self) -> int:
        """Mirror execution truth into catalogue rows, then reconcile grant policy."""

        registered = sorted(async_to_sync(mcp_server().list_tools)(), key=lambda tool: tool.name)
        using = self._db or router.db_for_write(self.model)
        with (
            database_context(using),
            system_context(reason="agents.builtin_tools.sync"),
            transaction.atomic(using=using),
        ):
            tools = self.db_manager(using)
            server = apps.get_model("agents", "MCPServer").objects.db_manager(using).builtin(create=True)
            ledger = apps.get_model("resources", "Resource").objects.db_manager(using)
            addon = apps.get_app_config("agents")
            source = "builtin_tool_catalogue"
            ledger.bind_instance(addon=addon, xref="mcp_angee", instance=server, source=source)
            for tool in registered:
                row, _ = tools.update_or_create(
                    server_id=server.pk, name=tool.name,
                    defaults={"description": str(tool.description or ""), "input_schema": dict(tool.parameters or {})},
                )
                ledger.bind_instance(addon=addon, xref=f"tool_{tool.name}", instance=row, source=source, replace=True)
            tools.filter(server=server).exclude(name__in=[tool.name for tool in registered]).delete()
            sync_resource_reader_grants(server, registered)
        return len(registered)
