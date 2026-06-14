"""GraphQL schema contributions for the agents addon.

Admin console surface for the agent catalogue: agents (and their templates), the
skills they mount, the MCP servers/tools they reach, and the inference
provider/model catalogue they run on. Platform-admin gated like the integrate
console, so the REBAC-guarded relations these types expose (integration, credential,
source, template) are safe — the const-admin reaches every related row. Skill
*sources* are managed in the integrate VCS console (a ``kind="skill"`` source);
this addon owns only the discovered :class:`Skill` rows.
"""

from __future__ import annotations

from typing import Any

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from rebac import system_context
from strawberry import auto, relay
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import instance_from_public_id
from angee.graphql.actions import ActionResult
from angee.graphql.crud import crud
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.schema import CredentialType, UserType
from angee.integrate.schema import IntegrationType, SourceType, TemplateType, VendorType

InferenceProvider = apps.get_model("agents", "InferenceProvider")
InferenceModel = apps.get_model("agents", "InferenceModel")
Skill = apps.get_model("agents", "Skill")
MCPServer = apps.get_model("agents", "MCPServer")
MCPTool = apps.get_model("agents", "MCPTool")
Agent = apps.get_model("agents", "Agent")


@strawberry_django.type(InferenceProvider)
class InferenceProviderType(AngeeNode):
    """Admin projection of an inference provider (a capability over an integration)."""

    integration: IntegrationType
    name: auto
    base_url: auto
    backend_class: auto
    status: auto
    config: JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(InferenceModel)
class InferenceModelType(AngeeNode):
    """Admin projection of one model in a provider's catalogue."""

    provider: InferenceProviderType
    publisher: VendorType | None
    name: auto
    display_name: auto
    description: auto
    model_use: auto
    is_default: auto
    status: auto
    context_window: auto
    max_output_tokens: auto
    capabilities: JSON
    config: JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(Skill)
class SkillType(AngeeNode):
    """Admin projection of one discovered skill."""

    source: SourceType
    name: auto
    description: auto
    path: auto
    metadata: JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(MCPServer)
class MCPServerType(AngeeNode):
    """Admin projection of one MCP server."""

    name: auto
    description: auto
    placement: auto
    transport: auto
    url: auto
    credential: CredentialType | None
    config: JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(MCPTool)
class MCPToolType(AngeeNode):
    """Admin projection of one MCP tool."""

    server: MCPServerType
    name: auto
    description: auto
    input_schema: JSON
    enabled: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Agent)
class AgentType(AngeeNode):
    """Admin projection of an agent (or, when ``is_template``, an agent template)."""

    owner: UserType
    name: auto
    description: auto
    is_template: auto
    instructions: auto
    model: InferenceModelType | None
    skills: list[SkillType]
    mcp_servers: list[MCPServerType]
    mcp_tools: list[MCPToolType]
    service_template: TemplateType | None
    workspace_template: TemplateType | None
    service_inputs: JSON
    workspace_inputs: JSON
    service: auto
    workspace: auto
    status: auto
    last_error: auto
    created_at: auto
    updated_at: auto


@strawberry.input
class InferenceProviderInput:
    """Fields accepted when creating an inference provider."""

    integration: relay.GlobalID
    name: str
    base_url: str = ""
    backend_class: str = "manual"
    # UNSET (not None): an omitted field must fall back to the model default, not
    # overwrite a non-null column with null (see docs/backend/guidelines.md Pitfalls).
    config: JSON | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry.input
class InferenceProviderPatch:
    """Fields accepted when updating an inference provider."""

    id: relay.GlobalID
    name: str | None = strawberry.UNSET
    base_url: str | None = strawberry.UNSET
    backend_class: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry.input
class InferenceModelInput:
    """Fields accepted when creating a catalogue model."""

    provider: relay.GlobalID
    name: str
    publisher: relay.GlobalID | None = None
    display_name: str = ""
    description: str = ""
    model_use: str = "chat"
    is_default: bool = False
    context_window: int = 0
    max_output_tokens: int | None = None
    # UNSET over non-null columns (see InferenceProviderInput); the nullable
    # ``publisher``/``max_output_tokens`` FKs/ints keep ``None``.
    status: str | None = strawberry.UNSET
    capabilities: JSON | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET


@strawberry.input
class InferenceModelPatch:
    """Fields accepted when updating a catalogue model."""

    id: relay.GlobalID
    name: str | None = strawberry.UNSET
    publisher: relay.GlobalID | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    model_use: str | None = strawberry.UNSET
    is_default: bool | None = strawberry.UNSET
    status: str | None = strawberry.UNSET
    context_window: int | None = strawberry.UNSET
    max_output_tokens: int | None = strawberry.UNSET
    capabilities: JSON | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET


@strawberry.input
class MCPServerInput:
    """Fields accepted when creating an MCP server."""

    name: str
    description: str = ""
    placement: str = "external"
    transport: str = "http"
    url: str = ""
    credential: relay.GlobalID | None = None
    config: JSON | None = strawberry.UNSET  # UNSET over the non-null column (see InferenceProviderInput).


@strawberry.input
class MCPServerPatch:
    """Fields accepted when updating an MCP server."""

    id: relay.GlobalID
    name: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    placement: str | None = strawberry.UNSET
    transport: str | None = strawberry.UNSET
    url: str | None = strawberry.UNSET
    credential: relay.GlobalID | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET


@strawberry.input
class MCPToolInput:
    """Fields accepted when creating an MCP tool."""

    server: relay.GlobalID
    name: str
    description: str = ""
    input_schema: JSON | None = strawberry.UNSET  # UNSET over the non-null column.
    enabled: bool = True


@strawberry.input
class MCPToolPatch:
    """Fields accepted when updating an MCP tool."""

    id: relay.GlobalID
    name: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    input_schema: JSON | None = strawberry.UNSET
    enabled: bool | None = strawberry.UNSET


@strawberry.input
class AgentInput:
    """Fields accepted when creating an agent.

    ``owner`` is field-backed REBAC, so writing it derives the owner tuple. M2M skill
    and MCP selections are set on the agent's update (``skills``/``mcpServers``/``mcpTools``
    on ``AgentPatch``), not at create.
    """

    name: str
    owner: relay.GlobalID
    description: str = ""
    is_template: bool = False
    instructions: str = ""
    model: relay.GlobalID | None = None
    service_template: relay.GlobalID | None = None
    workspace_template: relay.GlobalID | None = None
    # UNSET over non-null columns (see InferenceProviderInput); the nullable FKs above keep None.
    service_inputs: JSON | None = strawberry.UNSET
    workspace_inputs: JSON | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry.input
class AgentPatch:
    """Fields accepted when updating an agent."""

    id: relay.GlobalID
    name: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    is_template: bool | None = strawberry.UNSET
    instructions: str | None = strawberry.UNSET
    model: relay.GlobalID | None = strawberry.UNSET
    skills: list[relay.GlobalID] | None = strawberry.UNSET
    mcp_servers: list[relay.GlobalID] | None = strawberry.UNSET
    mcp_tools: list[relay.GlobalID] | None = strawberry.UNSET
    service_template: relay.GlobalID | None = strawberry.UNSET
    workspace_template: relay.GlobalID | None = strawberry.UNSET
    service_inputs: JSON | None = strawberry.UNSET
    workspace_inputs: JSON | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry_django.filter_type(Agent, lookups=True)
class AgentFilter:
    """Field lookups accepted when filtering the agents list.

    ``is_template`` drives the Agents-vs-Templates split — one model, two list tabs.
    """

    name: auto
    is_template: auto
    status: auto


@strawberry_django.order_type(Agent)
class AgentOrder:
    """Orderings accepted by the agents list."""

    name: auto
    status: auto
    updated_at: auto


@strawberry.type
class AgentsConsoleQuery:
    """Admin agent-catalogue queries."""

    agents: OffsetPaginated[AgentType] = strawberry_django.offset_paginated(
        filters=AgentFilter,
        order=AgentOrder,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    agent: AgentType | None = strawberry_django.node(permission_classes=_ADMIN_PERMISSION_CLASSES)
    skills: OffsetPaginated[SkillType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    skill: SkillType | None = strawberry_django.node(permission_classes=_ADMIN_PERMISSION_CLASSES)
    mcp_servers: OffsetPaginated[MCPServerType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    mcp_server: MCPServerType | None = strawberry_django.node(permission_classes=_ADMIN_PERMISSION_CLASSES)
    mcp_tools: OffsetPaginated[MCPToolType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    mcp_tool: MCPToolType | None = strawberry_django.node(permission_classes=_ADMIN_PERMISSION_CLASSES)
    inference_providers: OffsetPaginated[InferenceProviderType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    inference_provider: InferenceProviderType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    inference_models: OffsetPaginated[InferenceModelType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    inference_model: InferenceModelType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )


_AGENT_MUTATION = crud(
    AgentType,
    create=AgentInput,
    update=AgentPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="agent",
    write_context="agents.graphql.agent",
)
"""Admin agent CRUD: owner is field-backed REBAC; written elevated."""

_INFERENCE_PROVIDER_MUTATION = crud(
    InferenceProviderType,
    create=InferenceProviderInput,
    update=InferenceProviderPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="inference_provider",
    write_context="agents.graphql.inference_provider",
)
"""Admin inference-provider CRUD: FK input resolves via strawberry-django; written elevated."""

_INFERENCE_MODEL_MUTATION = crud(
    InferenceModelType,
    create=InferenceModelInput,
    update=InferenceModelPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="inference_model",
    write_context="agents.graphql.inference_model",
)
"""Admin catalogue-model CRUD: rows also arrive via ``refreshProviderModels``."""

_MCP_SERVER_MUTATION = crud(
    MCPServerType,
    create=MCPServerInput,
    update=MCPServerPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="mcp_server",
    write_context="agents.graphql.mcp_server",
)
"""Admin MCP-server CRUD: written elevated."""

_MCP_TOOL_MUTATION = crud(
    MCPToolType,
    create=MCPToolInput,
    update=MCPToolPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="mcp_tool",
    write_context="agents.graphql.mcp_tool",
)
"""Admin MCP-tool CRUD: FK input resolves via strawberry-django; written elevated."""

_SKILL_MUTATION = crud(
    SkillType,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="skill",
    write_context="agents.graphql.skill",
)
"""Admin skill delete: rows arrive via source discovery; removal is inventory cleanup
(re-discovered on the next source sync). No create/update — the source owns the data."""


def _resolve(model: type[models.Model], gid: relay.GlobalID, *, reason: str) -> Any:
    """Return the elevated instance addressed by ``gid`` for an action write."""

    with system_context(reason=reason):
        instance = instance_from_public_id(model, gid.node_id, queryset=model._default_manager.all())
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {gid.node_id!r} was not found.")
    return instance


@strawberry.type
class InferenceActionMutation:
    """Operational actions on an inference provider."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def refresh_provider_models(self, id: relay.GlobalID) -> ActionResult:
        """Re-list one provider's models into the catalogue now."""

        provider = _resolve(InferenceProvider, id, reason="agents.graphql.refresh_provider_models")
        with system_context(reason="agents.graphql.refresh_provider_models"):
            try:
                count = provider.refresh_models()
            except Exception as error:  # noqa: BLE001 — backend failure is the result, not a 500
                return ActionResult(ok=False, message=f"Refresh failed: {error}")
        return ActionResult(ok=True, message=f"Synced {count} model(s).")


@strawberry.type
class AgentActionMutation:
    """Provisioning write-backs for an agent.

    The console renders the workspace/service against the operator daemon (the
    daemon owns the lifecycle); these only persist the outcome on the agent row —
    the one server-side step of the browser-orchestrated provision flow.
    """

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def provision_agent(self, id: relay.GlobalID, workspace: str, service: str = "") -> ActionResult:
        """Record the operator instance the console rendered and mark the agent running."""

        agent = _resolve(Agent, id, reason="agents.graphql.provision_agent")
        with system_context(reason="agents.graphql.provision_agent"):
            agent.mark_provisioned(workspace=workspace, service=service)
        return ActionResult(ok=True, message=f"Provisioned “{service or workspace}”.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def deprovision_agent(self, id: relay.GlobalID) -> ActionResult:
        """Clear the agent's operator instance after the console tore it down."""

        agent = _resolve(Agent, id, reason="agents.graphql.deprovision_agent")
        with system_context(reason="agents.graphql.deprovision_agent"):
            agent.mark_deprovisioned()
        return ActionResult(ok=True, message="Deprovisioned.")


# Explicit annotation widens a homogeneous AngeeNode list past mypy's invariance check
# (see integrate.schema._CONSOLE_TYPES).
_CONSOLE_TYPES: list[type] = [
    InferenceProviderType,
    InferenceModelType,
    SkillType,
    MCPServerType,
    MCPToolType,
    AgentType,
]

schemas = {
    "console": {
        "query": [AgentsConsoleQuery],
        "mutation": [
            _AGENT_MUTATION,
            _INFERENCE_PROVIDER_MUTATION,
            _INFERENCE_MODEL_MUTATION,
            _MCP_SERVER_MUTATION,
            _MCP_TOOL_MUTATION,
            _SKILL_MUTATION,
            InferenceActionMutation,
            AgentActionMutation,
        ],
        "subscription": [changes(Agent, field="agentChanged")],
        "types": _CONSOLE_TYPES,
    },
}
"""GraphQL contributions installed by the agents addon."""
