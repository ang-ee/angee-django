"""Tool catalogue bindings."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from rebac import ObjectRef, RelationshipTuple, SubjectRef
from rebac.relationships import delete_relationships, write_relationships
from rebac.types import RelationshipFilter

from angee.agents.reader_role import RESOURCE_READER_ROLE
from angee.mcp import RESOURCE_READER_TOOL_TAG

TOOL_GRANT_RESOURCE_TYPE = "agents/tool_grant"
"""REBAC definition for primary-keyed MCPTool catalogue rows."""

TOOL_ROLE_RELATION = "role"
"""Stored tool-bundle relation resolved through ToolRole.effective_member."""

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

    return apps.get_model("agents", "MCPTool").objects.authorization_ids(server_sqid, tool_names)


def builtin_mcp_server(*, create: bool = False) -> Any:
    """Historical catalogue import; the native server manager owns adoption."""

    return apps.get_model("agents", "MCPServer").objects.builtin(create=create)


def sync_builtin_tool_catalogue() -> int:
    """Historical catalogue import; synchronization belongs to MCPTool's manager."""

    return int(apps.get_model("agents", "MCPTool").objects.sync_builtin())


def sync_resource_reader_grants(server: Any, registered: list[Any]) -> None:
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
    grant_ids = apps.get_model("agents", "MCPTool").objects.db_manager(server._state.db).authorization_ids(
        str(server.sqid), reader_names,
    )
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
