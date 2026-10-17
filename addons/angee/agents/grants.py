"""Tool catalogue bindings."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from asgiref.sync import async_to_sync
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rebac import ObjectRef, RelationshipTuple, SubjectRef, system_context
from rebac.relationships import delete_relationships, write_relationships
from rebac.roles import grant, revoke
from rebac.types import RelationshipFilter

from angee.agents.constants import BUILTIN_MCP_ANGEE
from angee.mcp.resource_tools import RESOURCE_READER_TOOL_TAG
from angee.mcp.server import mcp_server

TOOL_GRANT_RESOURCE_TYPE = "agents/tool_grant"
"""REBAC definition for primary-keyed MCPTool catalogue rows."""

TOOL_ROLE_RELATION = "role"
"""Stored tool-bundle relation resolved through ToolRole.effective_member."""

RESOURCE_READER_ROLE = ObjectRef("agents/toolrole", "resource_reader")
"""Built-in bundle granted to successfully provisioned in-process agents."""


def tool_grant_ref(server_sqid: str, tool_name: str) -> ObjectRef:
    """Resolve one public server/tool key to its model-backed grant object.

    The server sqid and tool name remain catalogue/API keys. The persisted tool
    row owns authorization identity, so grant writers and runtime checkers resolve
    that boundary once and store its primary key.
    """

    tool_model = apps.get_model("agents", "MCPTool")
    grant_ids = tool_grant_ids(server_sqid, (tool_name,))
    try:
        grant_id = grant_ids[tool_name]
    except KeyError as error:
        raise tool_model.DoesNotExist(f"No MCP tool {tool_name!r} exists for server {server_sqid!r}.") from error
    return ObjectRef(TOOL_GRANT_RESOURCE_TYPE, grant_id)


def tool_grant_ids(server_sqid: str, tool_names: Iterable[str]) -> dict[str, str]:
    """Resolve public catalogue keys to primary-key authorization identities."""

    tool_model = apps.get_model("agents", "MCPTool")
    server_model = apps.get_model("agents", "MCPServer")
    server_lookup = {f"server__{field}": value for field, value in server_model.public_id_lookup(server_sqid).items()}
    return {
        str(name): str(pk)
        for name, pk in tool_model._base_manager.filter(
            **server_lookup,
            name__in=tool_names,
        ).values_list("name", "pk")
    }


def builtin_mcp_server(*, create: bool = False) -> Any:
    """Adopt the built-in row, or provision its catalogue during synchronization.

    A predeclared row retains its name and credential. A canonical unique name
    makes concurrent first syncs converge without replacing an external server.
    """

    server_model = apps.get_model("agents", "MCPServer")
    servers = [server for server in server_model._base_manager.order_by("pk") if server.builtin == BUILTIN_MCP_ANGEE]
    if not servers and create:
        server, _ = server_model._base_manager.get_or_create(
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


def sync_builtin_tool_catalogue() -> int:
    """Mirror the live built-in registry into its deterministic pinning catalogue.

    The FastMCP registry remains execution truth. ``MCPTool`` rows are deliberately
    only the grant/pinning catalogue used by agent selections and REBAC ids; this
    sync updates that projection, prunes tools no longer registered in code,
    binds its resource identities, and replaces the ``resource_reader`` bundle's
    generated-reader grants. Agent writes own bundle membership.
    """

    registered = sorted(async_to_sync(mcp_server().list_tools)(), key=lambda tool: tool.name)
    names = [tool.name for tool in registered]
    tool_model = apps.get_model("agents", "MCPTool")
    with system_context(reason="agents.builtin_tools.sync"), transaction.atomic():
        server = builtin_mcp_server(create=True)
        ledger = apps.get_model("resources", "Resource").objects
        addon = apps.get_app_config("agents")
        source = "builtin_tool_catalogue"
        ledger.bind_instance(addon=addon, xref="mcp_angee", instance=server, source=source)
        for tool in registered:
            row, _ = tool_model._base_manager.update_or_create(
                server_id=server.pk,
                name=tool.name,
                defaults={
                    "description": str(tool.description or ""),
                    "input_schema": dict(tool.parameters or {}),
                },
            )
            ledger.bind_instance(addon=addon, xref=f"tool_{tool.name}", instance=row, source=source, replace=True)
        tool_model._base_manager.filter(server=server).exclude(name__in=names).delete()
        _sync_resource_reader_grants(server, registered)
    return len(registered)


def sync_resource_reader_role(agent: Any) -> None:
    """Reconcile the default bundle with the agent's persistent tool policy."""

    if agent.user_id is None:
        return
    if agent.resource_reader and agent.lifecycle == "ready" and agent.runs_in_process:
        grant(actor=agent.principal_subject(), role=RESOURCE_READER_ROLE)
    else:
        revoke(actor=agent.principal_subject(), role=RESOURCE_READER_ROLE)


def _sync_resource_reader_grants(server: Any, registered: list[Any]) -> None:
    """Replace the sync-owned generated-reader grants for ``resource_reader``."""

    subject = SubjectRef(RESOURCE_READER_ROLE)
    delete_relationships(
        RelationshipFilter(
            resource_type=TOOL_GRANT_RESOURCE_TYPE,
            relation=TOOL_ROLE_RELATION,
            subject_type=subject.subject_type,
            subject_id=subject.subject_id,
            optional_subject_relation=subject.optional_relation,
        )
    )
    reader_names = tuple(tool.name for tool in registered if RESOURCE_READER_TOOL_TAG in tool.tags)
    grant_ids = tool_grant_ids(str(server.sqid), reader_names)
    writes = [
        RelationshipTuple(
            resource=ObjectRef(TOOL_GRANT_RESOURCE_TYPE, grant_ids[name]),
            relation=TOOL_ROLE_RELATION,
            subject=subject,
        )
        for name in reader_names
    ]
    if writes:
        write_relationships(writes)
