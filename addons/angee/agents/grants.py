"""Tool catalogue bindings and legacy principal migration."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from asgiref.sync import async_to_sync
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rebac import ObjectRef, RelationshipTuple, SubjectRef, system_context
from rebac.models import active_relationship_model
from rebac.relationships import delete_relationships, write_relationships
from rebac.types import RelationshipFilter

from angee.mcp.resource_tools import RESOURCE_READER_TOOL_TAG
from angee.mcp.server import mcp_server

TOOL_GRANT_RESOURCE_TYPE = "agents/tool_grant"
"""REBAC definition for primary-keyed MCPTool catalogue rows."""

TOOL_GRANTEE_RELATION = "grantee"
"""Stored grant relation independent of live Agent.mcp_tools selections."""

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
        raise tool_model.DoesNotExist(
            f"No MCP tool {tool_name!r} exists for server {server_sqid!r}."
        ) from error
    return ObjectRef(TOOL_GRANT_RESOURCE_TYPE, grant_id)


def tool_grant_ids(server_sqid: str, tool_names: Iterable[str]) -> dict[str, str]:
    """Resolve public catalogue keys to primary-key authorization identities."""

    tool_model = apps.get_model("agents", "MCPTool")
    server_model = apps.get_model("agents", "MCPServer")
    server_lookup = {
        f"server__{field}": value
        for field, value in server_model.public_id_lookup(server_sqid).items()
    }
    return {
        str(name): str(pk)
        for name, pk in tool_model._base_manager.filter(
            **server_lookup,
            name__in=tool_names,
        ).values_list("name", "pk")
    }


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
    reader_names = tuple(
        tool.name for tool in registered if RESOURCE_READER_TOOL_TAG in tool.tags
    )
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


def resync_tool_grants() -> int:
    """Migrate legacy agent subjects and synchronize the built-in catalogue.

    Agent tool selections are now live-backed and require no tuple rewrite.
    Existing tool-role and IAM-group memberships are rewritten from the retired
    ``agents/agent`` principal to the agent's service user, preserving caveats
    and expiration. Returns the number of migrated membership tuples.
    """

    agent_model = apps.get_model("agents", "Agent")
    with system_context(reason="agents.tool_grants.resync"), transaction.atomic():
        agents = list(
            agent_model._base_manager.select_related("user")
            .order_by("pk")
        )
        for agent in agents:
            if agent.user_id is None:
                agent.user = agent_model.objects.sync_service_user(agent)
        migrated = _migrate_agent_principal_memberships(agents)
        sync_builtin_tool_catalogue()
        delete_relationships(
            RelationshipFilter(
                resource_type=TOOL_GRANT_RESOURCE_TYPE,
                relation=TOOL_GRANTEE_RELATION,
                subject_type="agents/agent",
            )
        )
    return migrated


def _migrate_agent_principal_memberships(agents: list[Any]) -> int:
    """Rewrite persisted memberships from agent resources to service users."""

    relationship_model = active_relationship_model()
    legacy = list(
        relationship_model.objects.filter(
            subject_type="agents/agent",
        ).filter(
            resource_type="agents/toolrole",
            relation="member",
        )
    )
    legacy_groups = list(
        relationship_model.objects.filter(
            resource_type="auth/group",
            relation="agent_member",
            subject_type="agents/agent",
        )
    )
    legacy_group_refs = list(
        relationship_model.objects.filter(
            subject_type="auth/group",
            optional_subject_relation="agent_member",
        )
    )
    legacy_subject_ids = {str(row.subject_id) for row in legacy + legacy_groups}
    agents_by_sqid = {
        str(agent.sqid): agent
        for agent in agents
        if str(agent.sqid) in legacy_subject_ids
    }
    migrated: list[RelationshipTuple] = []
    for row in legacy + legacy_groups:
        agent = agents_by_sqid.get(str(row.subject_id))
        if agent is None:
            continue
        migrated.append(
            RelationshipTuple(
                resource=ObjectRef(str(row.resource_type), str(row.resource_id)),
                relation="member" if str(row.relation) == "agent_member" else str(row.relation),
                subject=agent.principal_subject(),
                caveat_name=str(row.caveat_name),
                caveat_context=dict(row.caveat_context or {}),
                expires_at=row.expires_at,
            )
        )
    migrated.extend(
        RelationshipTuple(
            resource=ObjectRef(str(row.resource_type), str(row.resource_id)),
            relation=str(row.relation),
            subject=SubjectRef.of("auth/group", str(row.subject_id), "member"),
            caveat_name=str(row.caveat_name),
            caveat_context=dict(row.caveat_context or {}),
            expires_at=row.expires_at,
        )
        for row in legacy_group_refs
    )
    if migrated:
        write_relationships(migrated)
    delete_relationships(
        RelationshipFilter(
            resource_type="agents/toolrole",
            relation="member",
            subject_type="agents/agent",
        )
    )
    delete_relationships(
        RelationshipFilter(
            resource_type="auth/group",
            relation="agent_member",
            subject_type="agents/agent",
        )
    )
    delete_relationships(
        RelationshipFilter(
            subject_type="auth/group",
            optional_subject_relation="agent_member",
        )
    )
    return len(migrated)
