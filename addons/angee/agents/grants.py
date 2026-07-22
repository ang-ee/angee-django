"""Pure-tuple REBAC grants governing which tools an agent may invoke."""

from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rebac import ObjectRef, RelationshipTuple, SubjectRef, system_context
from rebac.relationships import delete_relationships, write_relationships
from rebac.types import RelationshipFilter

from angee.mcp.resource_tools import RESOURCE_READER_TOOL_TAG
from angee.mcp.server import mcp_server

TOOL_GRANT_RESOURCE_TYPE = "agents/tool_grant"
"""REBAC definition whose pure-tuple objects are keyed by server-qualified tool id."""

TOOL_GRANTEE_RELATION = "grantee"
"""Direct Agent.mcp_tools mirror relation on a tool-grant object."""

RESOURCE_READER_ROLE = ObjectRef("agents/toolrole", "resource_reader")
"""Built-in bundle granted to successfully provisioned in-process agents."""


def tool_grant_ref(server_sqid: str, tool_name: str) -> ObjectRef:
    """Return the canonical v1 grant object for one server-qualified tool.

    This is the sole constructor for tool-grant refs. Grant writers and runtime
    checkers must both call it so a later scoped-id revision cannot drift between
    persistence and authorization.
    """

    server = str(server_sqid).strip()
    name = str(tool_name).strip()
    if not server or not name:
        raise ValueError("Tool grant server ids and names must not be empty.")
    return ObjectRef(TOOL_GRANT_RESOURCE_TYPE, f"{server}.{name}")


def write_tool_grant(server_sqid: str, tool_name: str, agent: SubjectRef) -> None:
    """Grant ``agent`` use of the named tool through the direct M2M mirror."""

    write_relationships(
        [
            RelationshipTuple(
                resource=tool_grant_ref(server_sqid, tool_name),
                relation=TOOL_GRANTEE_RELATION,
                subject=agent,
            )
        ]
    )


def revoke_tool_grant(server_sqid: str, tool_name: str, agent: SubjectRef) -> None:
    """Revoke ``agent``'s direct M2M-backed grant for the named tool."""

    resource = tool_grant_ref(server_sqid, tool_name)
    delete_relationships(
        RelationshipFilter(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            relation=TOOL_GRANTEE_RELATION,
            subject_type=agent.subject_type,
            subject_id=agent.subject_id,
        )
    )


def builtin_mcp_server() -> Any:
    """Return the single catalogue row for the process-native Angee MCP server."""

    from angee.agents.models import BUILTIN_MCP_ANGEE

    server_model = apps.get_model("agents", "MCPServer")
    servers = [
        server
        for server in server_model._base_manager.order_by("pk")
        if server.builtin == BUILTIN_MCP_ANGEE
    ]
    if len(servers) != 1:
        raise ImproperlyConfigured(
            "Exactly one agents.MCPServer row must declare config.builtin='angee' "
            f"(found {len(servers)})."
        )
    return servers[0]


def sync_builtin_tool_catalogue() -> int:
    """Mirror the live built-in registry into its deterministic pinning catalogue.

    The FastMCP registry remains execution truth. ``MCPTool`` rows are deliberately
    only the grant/pinning catalogue used by agent selections and REBAC ids; this
    sync updates that projection, prunes tools no longer registered in code, and
    owns the ``resource_reader`` bundle's generated-reader grants.
    """

    registered = sorted(async_to_sync(mcp_server().list_tools)(), key=lambda tool: tool.name)
    names = [tool.name for tool in registered]
    tool_model = apps.get_model("agents", "MCPTool")
    with system_context(reason="agents.builtin_tools.sync"), transaction.atomic():
        server = builtin_mcp_server()
        for tool in registered:
            tool_model._base_manager.update_or_create(
                server=server,
                name=tool.name,
                defaults={
                    "description": str(tool.description or ""),
                    "input_schema": dict(tool.parameters or {}),
                },
            )
        tool_model._base_manager.filter(server=server).exclude(name__in=names).delete()
        _sync_resource_reader_grants(server, registered)
    return len(registered)


def grant_resource_reader_role(agent: Any) -> None:
    """Idempotently grant one provisioned in-process agent the reader bundle."""

    from rebac.roles import grant

    grant(actor=agent.principal_subject(), role=RESOURCE_READER_ROLE)


def _sync_resource_reader_grants(server: Any, registered: list[Any]) -> None:
    """Replace the sync-owned generated-reader grants for ``resource_reader``."""

    subject = SubjectRef.of(
        RESOURCE_READER_ROLE.resource_type,
        RESOURCE_READER_ROLE.resource_id,
        "effective_member",
    )
    delete_relationships(
        RelationshipFilter(
            resource_type=TOOL_GRANT_RESOURCE_TYPE,
            relation=TOOL_GRANTEE_RELATION,
            subject_type=subject.subject_type,
            subject_id=subject.subject_id,
            optional_subject_relation=subject.optional_relation,
        )
    )
    writes = [
        RelationshipTuple(
            resource=tool_grant_ref(str(server.sqid), tool.name),
            relation=TOOL_GRANTEE_RELATION,
            subject=subject,
        )
        for tool in registered
        if RESOURCE_READER_TOOL_TAG in tool.tags
    ]
    if writes:
        write_relationships(writes)


def resync_tool_grants() -> int:
    """Replace direct agent grants with the current Agent.mcp_tools selections.

    Run after the current agents zed has been synced. Role and group grants
    are preserved: only ``#grantee@agents/agent`` tuples are reconciled. Returns
    the number of selected tool grants written.
    """

    agent_model = apps.get_model("agents", "Agent")
    writes: list[RelationshipTuple] = []
    with system_context(reason="agents.tool_grants.resync"), transaction.atomic():
        sync_builtin_tool_catalogue()
        delete_relationships(
            RelationshipFilter(
                resource_type=TOOL_GRANT_RESOURCE_TYPE,
                relation=TOOL_GRANTEE_RELATION,
                subject_type="agents/agent",
            )
        )
        agents = agent_model._base_manager.prefetch_related("mcp_tools__server").order_by("pk")
        for agent in agents:
            subject = agent.principal_subject()
            tools: Any = agent.mcp_tools.select_related("server").order_by("server_id", "name", "pk")
            writes.extend(
                RelationshipTuple(
                    resource=tool_grant_ref(str(tool.server.sqid), tool.name),
                    relation=TOOL_GRANTEE_RELATION,
                    subject=subject,
                )
                for tool in tools
            )
        if writes:
            write_relationships(writes)
    return len(writes)
