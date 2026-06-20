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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rebac import current_actor, system_context
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.agents.autoconfig import SETTINGS as _AGENTS_SETTINGS
from angee.agents.context import render_view_context
from angee.agents.models import RuntimeStatus
from angee.base.mixins import actor_user_id
from angee.graphql.actions import ActionResult, action_target, resolve_action_target
from angee.graphql.aggregates import rebac_aggregate_builder
from angee.graphql.crud import crud
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode, detail
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_from_public_id as _user_from_public_id
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.schema import UserType
from angee.integrate import connect as _connect
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.schema import (
    ConnectIntegrationResult,
    CredentialType,
    ExternalAccountType,
    SourceType,
    TemplateType,
    VendorType,
    connect_integration_target,
)
from angee.operator.daemon import OperatorDaemon, OperatorDaemonNotFound

InferenceProvider = apps.get_model("agents", "InferenceProvider")
InferenceModel = apps.get_model("agents", "InferenceModel")
Skill = apps.get_model("agents", "Skill")
MCPServer = apps.get_model("agents", "MCPServer")
MCPTool = apps.get_model("agents", "MCPTool")
Agent = apps.get_model("agents", "Agent")
Integration = apps.get_model("integrate", "Integration")


@strawberry_django.type(InferenceProvider)
class InferenceProviderType(AngeeNode):
    """Admin projection of an inference provider child model."""

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType
    backend_class: auto
    status: auto
    name: auto
    base_url: auto
    credential_env: auto
    config: JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(Integration, name="IntegrationType", extend=True)
class IntegrationInferenceProviderExtension:
    """Contributes the inference provider child onto integrate's IntegrationType."""

    @strawberry_django.field(only=["id"])
    def inference_provider(self) -> InferenceProviderType | None:
        """Return this integration's inference provider child when present."""

        try:
            return cast(InferenceProviderType, cast(Any, self).inferenceprovider)
        except ObjectDoesNotExist:
            return None


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
    inference_credential: CredentialType | None
    skills: list[SkillType]
    mcp_servers: list[MCPServerType]
    mcp_tools: list[MCPToolType]
    service_template: TemplateType | None
    workspace_template: TemplateType | None
    service_inputs: JSON
    workspace_inputs: JSON
    service: auto
    workspace: auto
    lifecycle: auto
    runtime_status: auto
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
    ``model_handle`` is the selected agent model, used to select the ACP session
    model explicitly after session creation.
    """

    url: str
    token: str
    expires_at: str
    mcp_servers: JSON
    model_handle: str


@strawberry.type
class AgentSession:
    """The agent that serves the user's current view, for the side chatter.

    The view-driven counterpart to :class:`AgentChatEndpoint`: the chatter knows the
    *view*, not the agent, so this resolves *which* agent (identity only — name, status,
    model). The client then mints the chat endpoint for ``agent_id`` with
    ``agentChatEndpoint``. A ``None`` result means the user has no running agent.
    """

    agent_id: PublicID
    agent_name: str
    status: str
    model_handle: str


@strawberry.input
class InferenceProviderInput:
    """Fields accepted when creating an inference provider."""

    vendor: PublicID
    owner: PublicID
    credential: PublicID | None = None
    account: PublicID | None = strawberry.UNSET
    backend_class: str | None = strawberry.UNSET
    status: str | None = strawberry.UNSET
    name: str = ""
    base_url: str = ""
    credential_env: str = ""
    # UNSET (not None): an omitted field must fall back to the model default, not
    # overwrite a non-null column with null (see docs/backend/guidelines.md Pitfalls).
    config: JSON | None = strawberry.UNSET


@strawberry.input
class InferenceProviderPatch:
    """Fields accepted when updating an inference provider."""

    id: PublicID
    vendor: PublicID | None = strawberry.UNSET
    owner: PublicID | None = strawberry.UNSET
    credential: PublicID | None = strawberry.UNSET
    account: PublicID | None = strawberry.UNSET
    backend_class: str | None = strawberry.UNSET
    status: str | None = strawberry.UNSET
    name: str | None = strawberry.UNSET
    base_url: str | None = strawberry.UNSET
    credential_env: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET


@strawberry.input
class InferenceModelInput:
    """Fields accepted when creating a catalogue model."""

    provider: PublicID
    name: str
    publisher: PublicID | None = None
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

    id: PublicID
    name: str | None = strawberry.UNSET
    publisher: PublicID | None = strawberry.UNSET
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
    credential: PublicID | None = None
    config: JSON | None = strawberry.UNSET  # UNSET over the non-null column (see InferenceProviderInput).


@strawberry.input
class MCPServerPatch:
    """Fields accepted when updating an MCP server."""

    id: PublicID
    name: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    placement: str | None = strawberry.UNSET
    transport: str | None = strawberry.UNSET
    url: str | None = strawberry.UNSET
    credential: PublicID | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET


@strawberry.input
class MCPToolInput:
    """Fields accepted when creating an MCP tool."""

    server: PublicID
    name: str
    description: str = ""
    input_schema: JSON | None = strawberry.UNSET  # UNSET over the non-null column.
    enabled: bool = True


@strawberry.input
class MCPToolPatch:
    """Fields accepted when updating an MCP tool."""

    id: PublicID
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
    owner: PublicID
    description: str = ""
    is_template: bool = False
    instructions: str = ""
    model: PublicID | None = None
    inference_credential: PublicID | None = None
    service_template: PublicID | None = None
    workspace_template: PublicID | None = None
    # UNSET over non-null columns (see InferenceProviderInput); the nullable FKs above keep None.
    service_inputs: JSON | None = strawberry.UNSET
    workspace_inputs: JSON | None = strawberry.UNSET
    # Only the lifecycle is client-writable; ``runtime_status`` is operator-observed.
    lifecycle: str | None = strawberry.UNSET


@strawberry.input
class AgentPatch:
    """Fields accepted when updating an agent."""

    id: PublicID
    name: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    is_template: bool | None = strawberry.UNSET
    instructions: str | None = strawberry.UNSET
    model: PublicID | None = strawberry.UNSET
    inference_credential: PublicID | None = strawberry.UNSET
    skills: list[PublicID] | None = strawberry.UNSET
    mcp_servers: list[PublicID] | None = strawberry.UNSET
    mcp_tools: list[PublicID] | None = strawberry.UNSET
    service_template: PublicID | None = strawberry.UNSET
    workspace_template: PublicID | None = strawberry.UNSET
    service_inputs: JSON | None = strawberry.UNSET
    workspace_inputs: JSON | None = strawberry.UNSET
    lifecycle: str | None = strawberry.UNSET


@strawberry_django.filter_type(Agent, lookups=True)
class AgentFilter:
    """Field lookups accepted when filtering the agents list.

    ``is_template`` drives the Agents-vs-Templates split — one model, two list tabs.
    """

    name: auto
    is_template: auto
    lifecycle: auto
    runtime_status: auto


@strawberry_django.order_type(Agent)
class AgentOrder:
    """Orderings accepted by the agents list."""

    name: auto
    lifecycle: auto
    runtime_status: auto
    updated_at: auto


@strawberry_django.filter_type(InferenceModel, lookups=True)
class InferenceModelFilter:
    """Field lookups accepted when filtering the inference model catalogue."""

    provider: auto
    publisher: auto
    name: auto
    display_name: auto
    model_use: auto
    is_default: auto
    status: auto


@strawberry_django.order_type(InferenceModel)
class InferenceModelOrder:
    """Orderings accepted by the inference model catalogue."""

    provider: auto
    publisher: auto
    name: auto
    display_name: auto
    model_use: auto
    is_default: auto
    status: auto
    updated_at: auto


_inference_model_aggregates = rebac_aggregate_builder(
    model=InferenceModel,
    name_prefix="InferenceModelAggregate",
    aggregate_fields=["id", "context_window", "max_output_tokens"],
    group_by_fields=["provider", "model_use", "status"],
    filter_type=InferenceModelFilter,
    pagination_style="offset",
    enable_filter_echo=True,
).build()


@strawberry.type
class AgentsConsoleQuery:
    """Admin agent-catalogue queries."""

    agents: OffsetPaginated[AgentType] = strawberry_django.offset_paginated(
        filters=AgentFilter,
        order=AgentOrder,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    agent: AgentType | None = detail(AgentType, permission_classes=_ADMIN_PERMISSION_CLASSES)
    skills: OffsetPaginated[SkillType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    skill: SkillType | None = detail(SkillType, permission_classes=_ADMIN_PERMISSION_CLASSES)
    mcp_servers: OffsetPaginated[MCPServerType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    mcp_server: MCPServerType | None = detail(MCPServerType, permission_classes=_ADMIN_PERMISSION_CLASSES)
    mcp_tools: OffsetPaginated[MCPToolType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    mcp_tool: MCPToolType | None = detail(MCPToolType, permission_classes=_ADMIN_PERMISSION_CLASSES)
    inference_providers: OffsetPaginated[InferenceProviderType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    inference_provider: InferenceProviderType | None = detail(
        InferenceProviderType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    inference_models: OffsetPaginated[InferenceModelType] = strawberry_django.offset_paginated(
        filters=InferenceModelFilter,
        order=InferenceModelOrder,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    inference_model: InferenceModelType | None = detail(
        InferenceModelType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    inference_model_aggregate = _inference_model_aggregates.aggregate_field
    inference_model_groups = _inference_model_aggregates.group_by_field


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


def _provider_oauth_client(provider: Any) -> Any:
    """Return the OAuth client selected by this provider's backend."""

    vendor_slug = str(getattr(getattr(provider, "vendor", None), "slug", "") or "")
    return _connect.enabled_oauth_client_from_hint(
        getattr(provider.backend, "oauth_client", ""),
        owner_label="Inference provider",
        reason="agents.graphql.connect_inference_provider.oauth_client",
        vendor_slug=vendor_slug,
    )


@strawberry.type
class InferenceProviderCreateMutation:
    """Admin create for an inference provider child row."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_inference_provider(self, data: InferenceProviderInput) -> InferenceProviderType:
        """Create an inference provider directly."""

        vendor = resolve_action_target(
            apps.get_model("integrate", "Vendor"),
            data.vendor,
            reason="agents.graphql.inference_provider.create.vendor",
        )
        owner = _user_from_public_id(data.owner)
        credential = (
            None
            if data.credential is None
            else resolve_action_target(
                apps.get_model("integrate", "Credential"),
                data.credential,
                reason="agents.graphql.inference_provider.create.credential",
            )
        )
        account = (
            strawberry.UNSET
            if data.account is strawberry.UNSET
            else (
                None
                if data.account is None
                else resolve_action_target(
                    apps.get_model("integrate", "ExternalAccount"),
                    data.account,
                    reason="agents.graphql.inference_provider.create.account",
                )
            )
        )
        attrs: dict[str, Any] = {
            "vendor": vendor,
            "owner": owner,
            "backend_class": InferenceProvider.impl_key_for(
                "backend_class",
                None if data.backend_class is strawberry.UNSET else data.backend_class,
                default="manual",
            ),
        }
        if credential is not None:
            attrs["credential"] = credential
            if account is strawberry.UNSET:
                account = getattr(credential, "external_account", None)
        if account is not strawberry.UNSET:
            attrs["account"] = account
        if data.name:
            attrs["name"] = data.name
        if data.base_url:
            attrs["base_url"] = data.base_url
        if data.credential_env:
            attrs["credential_env"] = data.credential_env
        if data.status not in (strawberry.UNSET, None):
            attrs["status"] = data.status
        if data.config is not strawberry.UNSET:
            attrs["config"] = data.config
        with system_context(reason="agents.graphql.inference_provider.create"), transaction.atomic():
            provider = InferenceProvider.objects.create(**attrs)
        return cast(InferenceProviderType, provider)


@strawberry.type
class InferenceProviderConnectMutation:
    """Authenticated OAuth connect for an inference provider child row."""

    @strawberry.mutation
    def connect_inference_provider(
        self,
        info: strawberry.Info,
        id: PublicID,
        redirect_uri: str = "",
        next: str = "/agents/providers",
    ) -> ConnectIntegrationResult:
        """Attach the current user's OAuth credential to this inference provider."""

        try:
            provider = resolve_action_target(
                InferenceProvider,
                id,
                reason="agents.graphql.connect_inference_provider",
            )
            return connect_integration_target(
                info,
                provider,
                _provider_oauth_client(provider),
                redirect_uri=redirect_uri,
                next_path=next,
            )
        except OAuthFlowError as error:
            return ConnectIntegrationResult(error=error.public_message, error_code=error.code)


@strawberry.type
class InferenceProviderUpdateMutation:
    """Admin update for an inference provider child row."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_inference_provider(self, data: InferenceProviderPatch) -> InferenceProviderType:
        """Update a provider, rematerializing backend defaults when the backend changes."""

        provider = resolve_action_target(
            InferenceProvider,
            data.id,
            reason="agents.graphql.inference_provider.update",
        )
        provided: set[str] = set()
        backend_changed = False
        with system_context(reason="agents.graphql.inference_provider.update"), transaction.atomic():
            if data.vendor is not strawberry.UNSET:
                provider.vendor = resolve_action_target(
                    apps.get_model("integrate", "Vendor"),
                    data.vendor,
                    reason="agents.graphql.inference_provider.update.vendor",
                )
                provided.add("vendor")
            if data.owner is not strawberry.UNSET:
                provider.owner = _user_from_public_id(data.owner)
                provided.add("owner")
            if data.credential is not strawberry.UNSET:
                provider.credential = (
                    None
                    if data.credential is None
                    else resolve_action_target(
                        apps.get_model("integrate", "Credential"),
                        data.credential,
                        reason="agents.graphql.inference_provider.update.credential",
                    )
                )
                provided.add("credential")
            if data.backend_class is not strawberry.UNSET:
                backend_changed = provider.set_impl_key("backend_class", data.backend_class, default="manual")
            if data.status not in (strawberry.UNSET, None):
                provider.status = data.status
                provided.add("status")
            if data.name is not strawberry.UNSET:
                provider.name = data.name or ""
                provided.add("name")
            if data.base_url is not strawberry.UNSET:
                provider.base_url = data.base_url or ""
                provided.add("base_url")
            if data.credential_env is not strawberry.UNSET:
                provider.credential_env = data.credential_env or ""
                provided.add("credential_env")
            if data.config is not strawberry.UNSET:
                provider.config = data.config
                provided.add("config")
            if backend_changed:
                provider.materialize_impl_defaults("backend_class", provided=frozenset(provided))
            provider.save()
        return cast(InferenceProviderType, provider)


_INFERENCE_PROVIDER_MUTATION = crud(
    InferenceProviderType,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="inference_provider",
    write_context="agents.graphql.inference_provider",
)
"""Admin inference-provider delete; create/update validate backend ownership explicitly."""

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


# The inference-credential chains ``_render_plan`` walks: the per-agent override
# (``inference_credential``, with its ``oauth_client`` for an OAuth refresh) and the
# model→provider→credential fallback — joined up front so provisioning reads
# the credential in one query instead of lazy FK fetches.
_PROVISION_CHAIN = (
    "model__provider__credential",
    "inference_credential__oauth_client",
)


def _mint_session(agent: Any) -> dict[str, Any]:
    """Mint the chat WebSocket endpoint + per-actor route token for a running ``agent``.

    The one owner of "open a chat session against this agent": ``agentChatEndpoint``
    (caller knows the agent) and ``resolveSessionForView`` (caller knows the view) both
    call it, so the token/endpoint logic lives once. Raises when there is no actor, the
    agent isn't running (no rendered ``service``), or its service isn't routed.
    """

    actor = current_actor()
    if actor is None:
        raise ValueError("No actor in context.")
    with system_context(reason="agents.graphql.mint_session"):
        service = agent.service
        mcp_servers = agent.mcp_config().get("mcpServers", {})
    if not service:
        raise ValueError("Agent is not running — provision it first.")
    daemon = OperatorDaemon.from_settings()
    endpoint = daemon.service_endpoint(service)
    if not endpoint.get("routed"):
        raise ValueError("Agent service is not reachable over a routed endpoint.")
    # The agents autoconfig owns the TTL default; source the fallback from it (not a
    # restated literal) so a bare settings module without the composed value still resolves.
    ttl = str(getattr(settings, "ANGEE_AGENT_CHAT_TOKEN_TTL", _AGENTS_SETTINGS["ANGEE_AGENT_CHAT_TOKEN_TTL"]))
    token = daemon.mint_route_token(str(actor.object), service, ttl=ttl)
    return {
        "url": str(endpoint.get("url", "")),
        "token": str(token.get("token", "")),
        "expires_at": str(token.get("expires_at", "")),
        "mcp_servers": mcp_servers,
    }


def _agent_for_view(view: dict[str, Any]) -> Any:
    """Return the running agent that serves ``view`` for the current actor, or ``None``.

    v1 routes every view to the **actor's own** running, service-backed agent (the most
    recently updated). ``view["type"]`` is the routing seam — a later slice dispatches on
    it to pick a view-specialised agent — so it is read here even though v1 ignores it.
    """

    del view  # routing seam: a later slice dispatches on ``view["type"]``; v1 ignores it
    actor = current_actor()
    user_id = actor_user_id(actor) if actor is not None else None
    if user_id is None:
        return None
    with system_context(reason="agents.graphql.agent_for_view"):
        return (
            Agent.objects.filter(owner_id=user_id, is_template=False, runtime_status=RuntimeStatus.RUNNING)
            .exclude(service="")
            .order_by("-updated_at")
            .first()
        )


@strawberry.type
class InferenceActionMutation:
    """Operational actions on an inference provider."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def refresh_provider_models(self, id: PublicID) -> ActionResult:
        """Re-list one provider's models into the catalogue now."""

        with action_target(InferenceProvider, id, reason="agents.graphql.refresh_provider_models") as provider:
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


def _render_agent(
    plan: _RenderPlan,
    *,
    on_workspace_created: Callable[[str], None] | None = None,
    on_service_created: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Drive the daemon render for one agent over its REST API; return the instance names.

    The daemon owns the template ref format (resolve it from its own listing) and the
    secret store; the credential value is pushed before the service renders so the
    service's ``${secret.<name>}`` resolves. If the service render fails after the
    workspace exists, the workspace is torn back down so a retry starts clean. Raises
    on any step so the caller records the failure on the agent.
    """

    daemon = OperatorDaemon.from_settings()
    workspace_ref = daemon.resolve_template_ref(name=plan.workspace_template[0], kind=plan.workspace_template[1])
    if not workspace_ref:
        raise ValueError(f"No operator workspace template matches {plan.workspace_template[0]!r}.")
    _sync_secrets(daemon, plan)
    workspace = daemon.create_workspace(template=workspace_ref, inputs=plan.workspace_inputs)
    if not workspace:
        raise ValueError("The operator did not return a workspace.")
    if on_workspace_created is not None:
        on_workspace_created(workspace)
    try:
        service = _render_service(daemon, plan, workspace)
        if service and on_service_created is not None:
            on_service_created(service)
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
        workspace_template=((workspace_template.name, workspace_template.kind) if workspace_template else ("", "")),
        service_template=((service_template.name, service_template.kind) if service_template else None),
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

    Provisioning is one Django flow: record the status, resolve the agent's template
    inputs + credential, sync secrets to the operator store, and drive the daemon's
    workspace/service render over its REST API (admin bearer — the secret never reaches
    the browser). SQLite contention is handled by the composed database options; this
    layer stays a thin action bridge and the console watches daemon state directly.
    """

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def provision_agent(self, id: PublicID) -> ActionResult:
        """Render the agent into an operator workspace + service and record the instance.

        A render failure (including credential/plan resolution) records the reason and
        turns the run state ``ERROR``, rolling back and clearing any half-created
        workspace (which resets the lifecycle to ``DRAFT``) so a retry starts clean.
        Blocks once a workspace is recorded — even for an agent stranded mid-flow:
        :meth:`deprovision_agent` is the reset for any stuck state, then provision again.
        """

        agent = resolve_action_target(
            Agent,
            id,
            reason="agents.graphql.provision_agent",
            select_related=_PROVISION_CHAIN,
        )
        with system_context(reason="agents.graphql.provision_agent"):
            if agent.workspace:
                return ActionResult(ok=False, message="Agent is already provisioned — deprovision it first.")
            if agent.workspace_template is None:
                return ActionResult(ok=False, message="Set a workspace template on this agent first.")
            if not agent.inference_credential_ready():
                return ActionResult(
                    ok=False,
                    message="Connect a usable inference credential to this agent's provider before provisioning.",
                )
            agent.mark_provisioning()
        created_workspace: list[str] = []

        def record_workspace(workspace: str) -> None:
            created_workspace.append(workspace)
            with system_context(reason="agents.graphql.provision_agent.workspace_recorded"):
                agent.mark_workspace_provisioned(workspace=workspace)

        def record_service(service: str) -> None:
            with system_context(reason="agents.graphql.provision_agent.service_recorded"):
                agent.mark_service_provisioned(service=service)

        try:
            with system_context(reason="agents.graphql.provision_agent.plan"):
                plan = _render_plan(agent)
            result = _render_agent(
                plan,
                on_workspace_created=record_workspace,
                on_service_created=record_service,
            )
        except Exception as error:  # noqa: BLE001 — a render/plan failure is the result, not a 500
            with system_context(reason="agents.graphql.provision_agent.failed"):
                agent.mark_provision_failed(str(error), clear_instances=bool(created_workspace))
            return ActionResult(ok=False, message=f"Provisioning failed: {error}")
        with system_context(reason="agents.graphql.provision_agent.recorded"):
            agent.mark_provisioned(workspace=result["workspace"], service=result["service"])
        return ActionResult(ok=True, message=f"Provisioned “{result['service'] or result['workspace']}”.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def reprovision_agent(self, id: PublicID) -> ActionResult:
        """Recreate the agent's service over its existing workspace, re-syncing secrets.

        Use after changing the agent's credential or config: a service resolves its
        ``${secret.<name>}`` env at create time, so a new value lands only on a fresh
        service — destroy + create over the same workspace, not a restart. The
        workspace (and its files) is preserved.

        A failed recreate records the reason and turns the run state ``ERROR``; the
        preserved workspace (and so the ``READY`` lifecycle) stays, and the destroyed
        service name is cleared so a later deprovision doesn't chase a service the
        daemon already removed. Re-run to retry.
        """

        agent = resolve_action_target(
            Agent,
            id,
            reason="agents.graphql.reprovision_agent",
            select_related=_PROVISION_CHAIN,
        )
        with system_context(reason="agents.graphql.reprovision_agent"):
            workspace = agent.workspace
            service = agent.service
            if not workspace:
                return ActionResult(ok=False, message="Agent isn't provisioned — provision it first.")
            if agent.service_template is None:
                return ActionResult(ok=False, message="Set a service template on this agent first.")
            if not agent.inference_credential_ready():
                return ActionResult(
                    ok=False,
                    message="Connect a usable inference credential to this agent's provider before reprovisioning.",
                )
            agent.mark_provisioning()
        daemon = OperatorDaemon.from_settings()
        service_destroyed = False
        try:
            with system_context(reason="agents.graphql.reprovision_agent.plan"):
                plan = _render_plan(agent)
            _sync_secrets(daemon, plan)
            if service:
                daemon.destroy_service(service)
                service_destroyed = True
            new_service = _render_service(daemon, plan, workspace)
            if new_service:
                with system_context(reason="agents.graphql.reprovision_agent.service_recorded"):
                    agent.mark_service_provisioned(service=new_service)
        except Exception as error:  # noqa: BLE001 — a render/plan failure is the result, not a 500
            with system_context(reason="agents.graphql.reprovision_agent.failed"):
                # Once the old service is destroyed its name is stale; clear it so a later
                # deprovision doesn't try to tear down a service the daemon already removed.
                agent.mark_provision_failed(str(error), clear_service=service_destroyed)
            return ActionResult(ok=False, message=f"Reprovisioning failed: {error}")
        with system_context(reason="agents.graphql.reprovision_agent.recorded"):
            agent.mark_provisioned(workspace=workspace, service=new_service)
        return ActionResult(ok=True, message=f"Recreated service “{new_service}”.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def agent_chat_endpoint(self, id: PublicID) -> AgentChatEndpoint:
        """Mint the chat WebSocket endpoint + route token for a running agent.

        A mutation, not a query: each call mints a fresh, short-lived per-actor route
        token (the operator admin bearer never reaches the browser). The browser speaks
        ACP to the agent's routed WebSocket through the central Caddy, forward-authed
        with that token. Errors when the agent is not running (no rendered ``service``)
        or its service is not routed. The actor is the same identity
        ``operatorConnection`` mints with — the session user.
        """

        agent = resolve_action_target(Agent, id, reason="agents.graphql.agent_chat_endpoint", select_related=("model",))
        session = _mint_session(agent)
        return AgentChatEndpoint(
            url=session["url"],
            token=session["token"],
            expires_at=session["expires_at"],
            mcp_servers=session["mcp_servers"],
            model_handle=str(agent.model.name) if agent.model is not None else "",
        )

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def resolve_session_for_view(self, view: JSON) -> AgentSession | None:
        """Resolve the agent that serves the user's current view, for the side chatter.

        The chatter knows the *view*, not the agent: this picks the actor's running agent
        (``view["type"]`` is the routing seam for a later view-specialised agent) so the
        client can mint its chat endpoint (``agentChatEndpoint``). Returns ``None`` when the
        user has no running agent, so the chatter shows a call-to-action instead of erroring.
        """

        agent = _agent_for_view(dict(view) if isinstance(view, dict) else {})
        if agent is None:
            return None
        model = getattr(agent, "model", None)
        return AgentSession(
            agent_id=PublicID(str(agent.sqid)),
            agent_name=str(agent.name),
            status=str(agent.runtime_status),
            model_handle=str(model) if model is not None else "",
        )

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def render_agent_prompt(self, id: PublicID, view: JSON) -> str:
        """Render the ``<system_context>`` block for an agent and the user's open view.

        ``view`` is the view envelope ``{kind, type: "<app>/<model>", sqid?, sqids?,
        params?}``. The chat client calls this each send and prefixes the result, so
        the agent reads what the user is looking at. Resolving the agent (admin-gated)
        confirms the caller may drive it; the model-generic rendering lives in
        ``agents.context``.
        """

        resolve_action_target(Agent, id, reason="agents.graphql.render_agent_prompt")
        return render_view_context(dict(view) if isinstance(view, dict) else {})

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def deprovision_agent(self, id: PublicID) -> ActionResult:
        """Tear down the agent's operator workspace and services, then clear the record.

        Also the reset for any stuck agent (stranded ``PROVISIONING``/``DEPROVISIONING``
        or a run-state ``ERROR`` with stale instance names): re-running tears down
        whatever names the record holds and returns the agent to ``DEPROVISIONED``
        (run state stopped). An agent with no recorded instances is marked deprovisioned
        directly; a teardown failure records the reason and turns the run state
        ``ERROR``, preserving the names so the teardown can be retried.
        """

        agent = resolve_action_target(Agent, id, reason="agents.graphql.deprovision_agent")
        with system_context(reason="agents.graphql.deprovision_agent"):
            if not agent.workspace and not agent.service:
                agent.mark_deprovisioned()
                return ActionResult(ok=True, message="Deprovisioned.")
            workspace = agent.workspace
            service = agent.service
            agent.mark_deprovisioning()
        daemon = OperatorDaemon.from_settings()
        service_destroyed = False
        try:
            # The service is a stack entry distinct from the workspace it mounts, so destroy
            # it explicitly before the workspace; otherwise the next provision can 409.
            if service:
                try:
                    daemon.destroy_service(service)
                except OperatorDaemonNotFound:
                    pass
                service_destroyed = True
            if workspace:
                try:
                    daemon.destroy_workspace(workspace)
                except OperatorDaemonNotFound:
                    pass
        except Exception as error:  # noqa: BLE001 — teardown failure is the result, not a 500
            with system_context(reason="agents.graphql.deprovision_agent.failed"):
                agent.mark_provision_failed(f"Teardown failed: {error}", clear_service=service_destroyed)
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
    _inference_model_aggregates.aggregate_type,
    _inference_model_aggregates.grouped_type,
    _inference_model_aggregates.grouped_result_type,
    _inference_model_aggregates.group_key_type,
]


schemas = {
    "console": {
        "query": [AgentsConsoleQuery],
        "mutation": [
            _AGENT_MUTATION,
            InferenceProviderCreateMutation,
            InferenceProviderConnectMutation,
            InferenceProviderUpdateMutation,
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
        "type_extensions": [IntegrationInferenceProviderExtension],
    },
}
"""GraphQL contributions installed by the agents addon."""
