"""Resource-reader role membership, independent of the MCP tool registry."""

from typing import Any

from rebac import ObjectRef
from rebac.roles import grant, revoke

RESOURCE_READER_ROLE = ObjectRef("agents/toolrole", "resource_reader")
"""Built-in bundle granted to successfully provisioned in-process agents."""


def sync_resource_reader_role(agent: Any) -> None:
    """Reconcile the default bundle with the agent's persistent tool policy."""

    if agent.user_id is None:
        return
    if agent.resource_reader and agent.lifecycle == "ready" and agent.runs_in_process:
        grant(actor=agent.principal_subject(), role=RESOURCE_READER_ROLE)
    else:
        revoke(actor=agent.principal_subject(), role=RESOURCE_READER_ROLE)
