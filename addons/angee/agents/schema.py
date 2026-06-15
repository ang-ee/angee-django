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

import contextlib
from dataclasses import dataclass
from typing import Any

import strawberry
import strawberry_django
from django.apps import apps
from django.conf import settings
from django.db import models
from rebac import current_actor, system_context
from strawberry import auto, relay
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.agents.context import render_view_context
from angee.base.models import instance_from_public_id
from angee.graphql.actions import ActionResult
from angee.graphql.crud import crud
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.schema import CredentialType, UserType
from angee.integrate.schema import IntegrationType, SourceType, TemplateType, VendorType
from angee.operator.daemon import OperatorDaemon

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


@strawberry.type
class AgentChatEndpoint:
    """Browser-reachable chat endpoint for a running agent.

    ``url`` is the agent's routed WebSocket URL (no token); the browser appends
    ``token`` as a query parameter, which the central Caddy forward-auths against
    the operator. ``mcp_servers`` is the agent's rendered ``.mcp.json`` server map,
    so the chat session can advertise the same MCP servers the agent runs with.
    """

    url: str
    token: str
    expires_at: str
    mcp_servers: JSON


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


def _resolve(
    model: type[models.Model],
    gid: relay.GlobalID,
    *,
    reason: str,
    select_related: tuple[str, ...] = (),
) -> Any:
    """Return the elevated instance addressed by ``gid`` for an action write.

    ``select_related`` joins related rows the caller is about to walk (e.g. an agent's
    inference-credential chain for provisioning) so the action runs in one query.
    """

    queryset = model._default_manager.all()
    if select_related:
        queryset = queryset.select_related(*select_related)
    with system_context(reason=reason):
        instance = instance_from_public_id(model, gid.node_id, queryset=queryset)
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {gid.node_id!r} was not found.")
    return instance


# The inference-credential chain ``_render_plan`` walks (``Agent.inference_secret`` →
# model → provider → integration → credential); joined up front so provisioning reads
# it in one query instead of four lazy FK fetches.
_PROVISION_CHAIN = ("model__provider__integration__credential",)


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


@dataclass(frozen=True)
class _RenderPlan:
    """Everything the daemon render needs for one agent, gathered under elevation.

    ``*_template`` are the agent template's ``(name, kind)`` — the daemon resolves its
    own ref from them; ``secret_value`` is the credential token pushed before render.
    """

    workspace_inputs: dict[str, str]
    service_inputs: dict[str, str]
    secret_name: str
    secret_value: str
    mcp_secrets: dict[str, str]
    workspace_template: tuple[str, str]
    service_template: tuple[str, str] | None


def _render_agent(plan: _RenderPlan) -> dict[str, str]:
    """Drive the daemon render for one agent over its REST API; return the instance names.

    The daemon owns the template ref format (resolve it from its own listing) and the
    secret store; the credential value is pushed before the service renders so the
    service's ``${secret.<name>}`` resolves. If the service render fails after the
    workspace exists, the workspace is torn back down so a retry starts clean. Raises
    on any step so the caller records the failure on the agent.
    """

    daemon = OperatorDaemon.from_settings()
    workspace_ref = daemon.resolve_template_ref(
        name=plan.workspace_template[0], kind=plan.workspace_template[1]
    )
    if not workspace_ref:
        raise ValueError(f"No operator workspace template matches {plan.workspace_template[0]!r}.")
    _sync_secrets(daemon, plan)
    workspace = daemon.create_workspace(template=workspace_ref, inputs=plan.workspace_inputs)
    if not workspace:
        raise ValueError("The operator did not return a workspace.")
    try:
        service = _render_service(daemon, plan, workspace)
    except Exception:
        with contextlib.suppress(Exception):  # best-effort rollback; surface the original failure
            daemon.destroy_workspace(workspace)
        raise
    return {"workspace": workspace, "service": service}


def _render_service(daemon: OperatorDaemon, plan: _RenderPlan, workspace: str) -> str:
    """Render the agent's service into ``workspace``; ``""`` for a workspace-only agent."""

    if plan.service_template is None:
        return ""
    service_ref = daemon.resolve_template_ref(name=plan.service_template[0], kind=plan.service_template[1])
    if not service_ref:
        raise ValueError(f"No operator service template matches {plan.service_template[0]!r}.")
    return daemon.create_service(template=service_ref, workspace=workspace, inputs=plan.service_inputs)


def _render_plan(agent: Any) -> _RenderPlan:
    """Build the operator render plan from an agent's templates, inputs, and secrets.

    Reads the credential, so call inside ``system_context``. ``workspace_template``
    falls back to empty when unset — a service-only recreate (existing workspace)
    never reads it.
    """

    workspace_template = agent.workspace_template
    service_template = agent.service_template
    return _RenderPlan(
        workspace_inputs=agent.provision_workspace_inputs(),
        service_inputs=agent.provision_service_inputs(),
        secret_name=agent.inference_secret_name(),
        secret_value=agent.inference_secret(),
        mcp_secrets=agent.mcp_secrets(),
        workspace_template=(
            (workspace_template.name, workspace_template.kind) if workspace_template else ("", "")
        ),
        service_template=(
            (service_template.name, service_template.kind) if service_template else None
        ),
    )


def _sync_secrets(daemon: OperatorDaemon, plan: _RenderPlan) -> None:
    """Push the agent's inference + MCP secret values to the operator store.

    A service resolves its ``${secret.<name>}`` env at create time, so the values
    must be current before the service renders — recreating the service after a
    credential change is what lands the new value (a restart reuses the old env).
    """

    if plan.secret_value:
        daemon.set_secret(plan.secret_name, plan.secret_value)
    # Each credentialed MCP server's bearer rides through the operator secret store too,
    # so its ${secret.<name>} header in the rendered .mcp.json resolves in the container.
    for name, value in sorted(plan.mcp_secrets.items()):
        daemon.set_secret(name, value)


@strawberry.type
class AgentActionMutation:
    """Server-side provisioning actions for an agent.

    Provisioning is one Django flow: it resolves the agent's template inputs +
    credential, syncs the inference secret to the operator store, and drives the
    daemon's workspace/service render over its REST API (admin bearer — the secret
    never reaches the browser). The console only triggers these and watches live
    runtime health straight from the daemon.
    """

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def provision_agent(self, id: relay.GlobalID) -> ActionResult:
        """Render the agent into an operator workspace + service and record the instance."""

        agent = _resolve(Agent, id, reason="agents.graphql.provision_agent", select_related=_PROVISION_CHAIN)
        with system_context(reason="agents.graphql.provision_agent"):
            if agent.workspace:
                return ActionResult(ok=False, message="Agent is already provisioned — deprovision it first.")
            if agent.workspace_template is None:
                return ActionResult(ok=False, message="Set a workspace template on this agent first.")
            plan = _render_plan(agent)
        try:
            result = _render_agent(plan)
        except Exception as error:  # noqa: BLE001 — a render failure is the result, not a 500
            with system_context(reason="agents.graphql.provision_agent.failed"):
                agent.mark_provision_failed(str(error))
            return ActionResult(ok=False, message=f"Provisioning failed: {error}")
        with system_context(reason="agents.graphql.provision_agent.recorded"):
            agent.mark_provisioned(workspace=result["workspace"], service=result["service"])
        return ActionResult(ok=True, message=f"Provisioned “{result['service'] or result['workspace']}”.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def reprovision_agent(self, id: relay.GlobalID) -> ActionResult:
        """Recreate the agent's service over its existing workspace, re-syncing secrets.

        Use after changing the agent's credential or config: a service resolves its
        ``${secret.<name>}`` env at create time, so a new value lands only on a fresh
        service — destroy + create over the same workspace, not a restart. The
        workspace (and its files) is preserved.
        """

        agent = _resolve(Agent, id, reason="agents.graphql.reprovision_agent", select_related=_PROVISION_CHAIN)
        with system_context(reason="agents.graphql.reprovision_agent"):
            workspace = agent.workspace
            service = agent.service
            if not workspace:
                return ActionResult(ok=False, message="Agent isn't provisioned — provision it first.")
            if agent.service_template is None:
                return ActionResult(ok=False, message="Set a service template on this agent first.")
            plan = _render_plan(agent)
        daemon = OperatorDaemon.from_settings()
        try:
            _sync_secrets(daemon, plan)
            if service:
                daemon.destroy_service(service)
            new_service = _render_service(daemon, plan, workspace)
        except Exception as error:  # noqa: BLE001 — a render failure is the result, not a 500
            with system_context(reason="agents.graphql.reprovision_agent.failed"):
                agent.mark_provision_failed(str(error))
            return ActionResult(ok=False, message=f"Reprovisioning failed: {error}")
        with system_context(reason="agents.graphql.reprovision_agent.recorded"):
            agent.mark_provisioned(workspace=workspace, service=new_service)
        return ActionResult(ok=True, message=f"Recreated service “{new_service}”.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def agent_chat_endpoint(self, id: relay.GlobalID) -> AgentChatEndpoint:
        """Mint the chat WebSocket endpoint + route token for a running agent.

        A mutation, not a query: each call mints a fresh, short-lived per-actor route
        token (the operator admin bearer never reaches the browser). The browser speaks
        ACP to the agent's routed WebSocket through the central Caddy, forward-authed
        with that token. Errors when the agent is not running (no rendered ``service``)
        or its service is not routed. The actor is the same identity
        ``operatorConnection`` mints with — the session user.
        """

        agent = _resolve(Agent, id, reason="agents.graphql.agent_chat_endpoint")
        with system_context(reason="agents.graphql.agent_chat_endpoint"):
            service = agent.service
            mcp_servers = agent.mcp_config().get("mcpServers", {})
        if not service:
            raise ValueError("Agent is not running — provision it first.")
        actor = current_actor()
        if actor is None:
            raise ValueError("No actor in context.")
        daemon = OperatorDaemon.from_settings()
        endpoint = daemon.service_endpoint(service)
        if not endpoint.get("routed"):
            raise ValueError("Agent service is not reachable over a routed endpoint.")
        ttl = getattr(settings, "ANGEE_AGENT_CHAT_TOKEN_TTL", "2h")
        token = daemon.mint_route_token(str(actor.object), service, ttl=ttl)
        return AgentChatEndpoint(
            url=str(endpoint.get("url", "")),
            token=str(token.get("token", "")),
            expires_at=str(token.get("expires_at", "")),
            mcp_servers=mcp_servers,
        )

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def render_agent_prompt(self, id: relay.GlobalID, view: JSON) -> str:
        """Render the ``<system_context>`` block for an agent and the user's open view.

        ``view`` is the view envelope ``{kind, type: "<app>/<model>", sqid?, sqids?,
        params?}``. The chat client calls this each send and prefixes the result, so
        the agent reads what the user is looking at. Resolving the agent (admin-gated)
        confirms the caller may drive it; the model-generic rendering lives in
        ``agents.context``.
        """

        _resolve(Agent, id, reason="agents.graphql.render_agent_prompt")
        return render_view_context(dict(view) if isinstance(view, dict) else {})

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def deprovision_agent(self, id: relay.GlobalID) -> ActionResult:
        """Tear down the agent's operator workspace (and its services) and clear the record."""

        agent = _resolve(Agent, id, reason="agents.graphql.deprovision_agent")
        with system_context(reason="agents.graphql.deprovision_agent"):
            workspace = agent.workspace
            service = agent.service
        daemon = OperatorDaemon.from_settings()
        try:
            # The service is a stack entry distinct from the workspace it mounts, so
            # destroy it explicitly — destroying only the workspace leaves the service
            # behind and the next provision 409s. Service first, then its workspace.
            if service:
                daemon.destroy_service(service)
            if workspace:
                daemon.destroy_workspace(workspace)
        except Exception as error:  # noqa: BLE001 — teardown failure is the result, not a 500
            return ActionResult(ok=False, message=f"Teardown failed: {error}")
        with system_context(reason="agents.graphql.deprovision_agent.recorded"):
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
    AgentChatEndpoint,
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
