"""Pure-tuple REBAC grants governing which tools an agent may invoke."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from rebac import ObjectRef, RelationshipTuple, SubjectRef, system_context
from rebac.relationships import delete_relationships, write_relationships
from rebac.types import RelationshipFilter

TOOL_GRANT_RESOURCE_TYPE = "agents/tool_grant"
"""REBAC definition whose pure-tuple objects are keyed by FastMCP tool name."""

TOOL_GRANTEE_RELATION = "grantee"
"""Direct Agent.mcp_tools mirror relation on a tool-grant object."""


def tool_grant_ref(tool_name: str) -> ObjectRef:
    """Return the canonical v1 grant object for ``tool_name``.

    This is the sole constructor for tool-grant refs. Grant writers and runtime
    checkers must both call it so a later scoped-id revision cannot drift between
    persistence and authorization.
    """

    name = str(tool_name).strip()
    if not name:
        raise ValueError("Tool grant names must not be empty.")
    return ObjectRef(TOOL_GRANT_RESOURCE_TYPE, name)


def write_tool_grant(tool_name: str, agent: SubjectRef) -> None:
    """Grant ``agent`` use of the named tool through the direct M2M mirror."""

    write_relationships(
        [
            RelationshipTuple(
                resource=tool_grant_ref(tool_name),
                relation=TOOL_GRANTEE_RELATION,
                subject=agent,
            )
        ]
    )


def revoke_tool_grant(tool_name: str, agent: SubjectRef) -> None:
    """Revoke ``agent``'s direct M2M-backed grant for the named tool."""

    resource = tool_grant_ref(tool_name)
    delete_relationships(
        RelationshipFilter(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            relation=TOOL_GRANTEE_RELATION,
            subject_type=agent.subject_type,
            subject_id=agent.subject_id,
        )
    )


def resync_tool_grants() -> int:
    """Replace direct agent grants with the current Agent.mcp_tools selections.

    Run after the revision-3 agents zed has been synced. Role and group grants
    are preserved: only ``#grantee@agents/agent`` tuples are reconciled. Returns
    the number of selected tool grants written.
    """

    agent_model = apps.get_model("agents", "Agent")
    writes: list[RelationshipTuple] = []
    with system_context(reason="agents.tool_grants.resync"):
        delete_relationships(
            RelationshipFilter(
                resource_type=TOOL_GRANT_RESOURCE_TYPE,
                relation=TOOL_GRANTEE_RELATION,
                subject_type="agents/agent",
            )
        )
        agents = agent_model._base_manager.prefetch_related("mcp_tools").order_by("pk")
        for agent in agents:
            subject = agent.principal_subject()
            tools: Any = agent.mcp_tools.order_by("name", "pk")
            writes.extend(
                RelationshipTuple(
                    resource=tool_grant_ref(tool.name),
                    relation=TOOL_GRANTEE_RELATION,
                    subject=subject,
                )
                for tool in tools
            )
        if writes:
            write_relationships(writes)
    return len(writes)
