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

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rebac import current_actor, system_context
from strawberry import auto
from strawberry.scalars import JSON

from angee.agents import provisioning
from angee.agents.autoconfig import SETTINGS as _AGENTS_SETTINGS
from angee.agents.context import render_view_context
from angee.agents.models import RuntimeStatus
from angee.base.actors import actor_user_id
from angee.graphql.actions import ActionResult, action_target, resolve_action_target
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.schema import UserType
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.schema import (
    ConnectIntegrationResult,
    CredentialType,
    ExternalAccountType,
    SourceType,
    TemplateType,
    VendorType,
    apply_integration_patch_fields,
    connect_integration_target,
    impl_default_update_fields,
    integration_create_attrs,
    save_provided_fields,
)
from angee.operator.daemon import OperatorDaemon

InferenceProvider = apps.get_model("agents", "InferenceProvider")
InferenceModel = apps.get_model("agents", "InferenceModel")
Skill = apps.get_model("agents", "Skill")
MCPServer = apps.get_model("agents", "MCPServer")
MCPTool = apps.get_model("agents", "MCPTool")
Agent = apps.get_model("agents", "Agent")
Integration = apps.get_model("integrate", "Integration")
Vendor = apps.get_model("integrate", "Vendor")
Credential = apps.get_model("integrate", "Credential")
ExternalAccount = apps.get_model("integrate", "ExternalAccount")
Source = apps.get_model("integrate", "Source")
Template = apps.get_model("integrate", "Template")
User = get_user_model()


@strawberry_django.type(InferenceProvider)
class InferenceProviderType(AngeeNode):
    """Admin projection of an inference provider child model."""

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType
    backend_class: auto
    lifecycle: auto
    runtime_status: auto
    name: auto
    base_url: auto
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
    runtime_class: auto
    workspace_template: TemplateType | None
    service_inputs: JSON
    workspace_inputs: JSON
    service: auto
    workspace: auto
    lifecycle: auto
    runtime_status: auto
    last_error: auto
    can_provision: bool
    can_deprovision: bool
    can_delete: bool
    created_at: auto
    updated_at: auto


@strawberry.type
class AgentChatEndpoint:
    """Browser-reachable chat endpoint for a running agent.

    ``url`` is the agent's routed WebSocket URL (no token); the browser appends
    ``token`` as a query parameter, which the central Caddy forward-auths against
    the operator. ``mcp_servers`` is the agent's rendered ``.mcp.json`` server map,
    so the chat session can advertise the same MCP servers the agent runs with.
    ``model_handle`` is the selected agent model in the service runtime's convention,
    used to select the ACP session model explicitly after session creation.
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
    lifecycle: str | None = strawberry.UNSET
    name: str = ""
    base_url: str = ""
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
    lifecycle: str | None = strawberry.UNSET
    name: str | None = strawberry.UNSET
    base_url: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET


_AGENT_RESOURCE = hasura_model_resource(
    AgentType,
    model=Agent,
    name="agents",
    filterable=["id", "owner", "model", "name", "is_template", "lifecycle", "runtime_status", "updated_at"],
    sortable=["name", "is_template", "lifecycle", "runtime_status", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["is_template", "lifecycle", "runtime_status", "updated_at"],
    insertable=[
        "name",
        "owner",
        "description",
        "is_template",
        "instructions",
        "model",
        "inference_credential",
        "skills",
        "mcp_servers",
        "mcp_tools",
        "runtime_class",
        "workspace_template",
        "service_inputs",
        "workspace_inputs",
        "lifecycle",
    ],
    updatable=[
        "name",
        "description",
        "is_template",
        "instructions",
        "model",
        "inference_credential",
        "skills",
        "mcp_servers",
        "mcp_tools",
        "runtime_class",
        "workspace_template",
        "service_inputs",
        "workspace_inputs",
        "lifecycle",
    ],
    field_id_decode={
        "owner": public_pk_decoder(User),
        "model": public_pk_decoder(InferenceModel),
        "inference_credential": public_pk_decoder(Credential),
        "skills": public_pk_decoder(Skill),
        "mcp_servers": public_pk_decoder(MCPServer),
        "mcp_tools": public_pk_decoder(MCPTool),
        "workspace_template": public_pk_decoder(Template),
    },
    write_backend=AngeeHasuraWriteBackend(
        Agent,
        public_id_fields=(
            "owner",
            "model",
            "inference_credential",
            "skills",
            "mcp_servers",
            "mcp_tools",
            "workspace_template",
        ),
        delete_guard=lambda agent: agent.delete_blocker(),
    ),
)
_SKILL_RESOURCE = hasura_model_resource(
    SkillType,
    model=Skill,
    name="skills",
    filterable=["id", "source", "name", "path", "updated_at"],
    sortable=["source", "name", "path", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["source", "source__path", "updated_at"],
    insert=False,
    update=False,
    delete=True,
    field_id_decode={"source": public_pk_decoder(Source)},
)
_MCP_SERVER_RESOURCE = hasura_model_resource(
    MCPServerType,
    model=MCPServer,
    name="mcp_servers",
    filterable=["id", "name", "placement", "transport", "credential", "updated_at"],
    sortable=["name", "placement", "transport", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["placement", "transport"],
    insertable=["name", "description", "placement", "transport", "url", "credential", "config"],
    updatable=["name", "description", "placement", "transport", "url", "credential", "config"],
    field_id_decode={"credential": public_pk_decoder(Credential)},
    write_backend=AngeeHasuraWriteBackend(MCPServer, public_id_fields=("credential",)),
)
_MCP_TOOL_RESOURCE = hasura_model_resource(
    MCPToolType,
    model=MCPTool,
    name="mcp_tools",
    filterable=["id", "server", "name", "enabled", "updated_at"],
    sortable=["server", "name", "enabled", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["server", "server__name", "enabled", "updated_at"],
    insertable=["server", "name", "description", "input_schema", "enabled"],
    updatable=["name", "description", "input_schema", "enabled"],
    field_id_decode={"server": public_pk_decoder(MCPServer)},
    write_backend=AngeeHasuraWriteBackend(MCPTool, public_id_fields=("server",)),
)
_INFERENCE_PROVIDER_RESOURCE = hasura_model_resource(
    InferenceProviderType,
    model=InferenceProvider,
    name="inference_providers",
    filterable=["id", "vendor", "owner", "backend_class", "lifecycle", "runtime_status", "name", "updated_at"],
    sortable=["vendor", "backend_class", "lifecycle", "runtime_status", "name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["backend_class", "lifecycle", "runtime_status", "vendor", "vendor__display_name"],
    insert=False,
    update=False,
    delete=True,
    field_id_decode={
        "vendor": public_pk_decoder(Vendor),
        "owner": public_pk_decoder(User),
        "credential": public_pk_decoder(Credential),
        "account": public_pk_decoder(ExternalAccount),
    },
)
_INFERENCE_MODEL_RESOURCE = hasura_model_resource(
    InferenceModelType,
    model=InferenceModel,
    name="inference_models",
    filterable=["id", "provider", "publisher", "name", "display_name", "model_use", "is_default", "status"],
    sortable=["provider", "publisher", "name", "display_name", "model_use", "is_default", "status", "updated_at"],
    aggregatable=["id", "context_window", "max_output_tokens"],
    groupable=["provider", "provider__name", "model_use", "status"],
    insertable=[
        "provider",
        "publisher",
        "name",
        "display_name",
        "description",
        "model_use",
        "is_default",
        "status",
        "context_window",
        "max_output_tokens",
        "capabilities",
        "config",
    ],
    updatable=[
        "publisher",
        "name",
        "display_name",
        "description",
        "model_use",
        "is_default",
        "status",
        "context_window",
        "max_output_tokens",
        "capabilities",
        "config",
    ],
    field_id_decode={
        "provider": public_pk_decoder(InferenceProvider),
        "publisher": public_pk_decoder(Vendor),
    },
    write_backend=AngeeHasuraWriteBackend(
        InferenceModel,
        public_id_fields=("provider", "publisher"),
    ),
)


def _provider_oauth_client(provider: Any) -> Any:
    """Return the OAuth client selected by this provider's backend."""

    return provider.backend.connect_oauth_client("Inference provider")


@strawberry.type
class InferenceProviderCreateMutation:
    """Admin create for an inference provider child row."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_inference_provider(self, data: InferenceProviderInput) -> InferenceProviderType:
        """Create an inference provider directly."""

        attrs = {
            **integration_create_attrs(data, reason="agents.graphql.inference_provider.create"),
            "backend_class": InferenceProvider.impl_key_for(
                "backend_class",
                None if data.backend_class is strawberry.UNSET else data.backend_class,
                default="manual",
            ),
        }
        if data.account is strawberry.UNSET and (credential := attrs.get("credential")) is not None:
            attrs["account"] = getattr(credential, "external_account", None)
        if data.name:
            attrs["name"] = data.name
        if data.base_url:
            attrs["base_url"] = data.base_url
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

        backend_changed = False
        with (
            action_target(
                InferenceProvider,
                data.id,
                reason="agents.graphql.inference_provider.update",
            ) as provider,
            transaction.atomic(),
        ):
            provided = apply_integration_patch_fields(
                provider,
                data,
                reason="agents.graphql.inference_provider.update",
                ignore_null_lifecycle=True,
            )
            if data.backend_class is not strawberry.UNSET:
                backend_changed = provider.set_impl_key("backend_class", data.backend_class, default="manual")
                provided.add("backend_class")
            if data.name is not strawberry.UNSET:
                provider.name = data.name or ""
                provided.add("name")
            if data.base_url is not strawberry.UNSET:
                provider.base_url = data.base_url or ""
                provided.add("base_url")
            if data.config is not strawberry.UNSET:
                provider.config = data.config
                provided.add("config")
            if backend_changed:
                provider.materialize_impl_defaults("backend_class", provided=frozenset(provided))
                provided.update(impl_default_update_fields(provider, "backend_class"))
            save_provided_fields(provider, provided)
        return cast(InferenceProviderType, provider)


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
            .select_related("model")
            .order_by("-updated_at")
            .first()
        )


@strawberry.type
class AgentSessionQuery:
    """Authenticated agent session queries for chat surfaces."""

    @strawberry.field
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
            model_handle=str(agent.service_model_handle()) if model is not None else "",
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


@strawberry.type
class AgentActionMutation:
    """GraphQL action bridge for agent runtime operations."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def provision_agent(self, id: PublicID) -> ActionResult:
        """Render the agent into an operator workspace and service."""

        return provisioning.provision_agent(id)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def reprovision_agent(self, id: PublicID) -> ActionResult:
        """Recreate the agent service over its existing workspace."""

        return provisioning.reprovision_agent(id)

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

        agent = resolve_action_target(
            Agent,
            id,
            reason="agents.graphql.agent_chat_endpoint",
            select_related=("model",),
        )
        session = _mint_session(agent)
        return AgentChatEndpoint(
            url=session["url"],
            token=session["token"],
            expires_at=session["expires_at"],
            mcp_servers=session["mcp_servers"],
            model_handle=str(agent.service_model_handle()),
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
        """Tear down the agent's operator workspace and service."""

        return provisioning.deprovision_agent(id)


_CONSOLE_TYPES: list[object] = [
    InferenceProviderType,
    InferenceModelType,
    SkillType,
    MCPServerType,
    MCPToolType,
    AgentType,
    AgentChatEndpoint,
    *_AGENT_RESOURCE.types,
    *_SKILL_RESOURCE.types,
    *_MCP_SERVER_RESOURCE.types,
    *_MCP_TOOL_RESOURCE.types,
    *_INFERENCE_PROVIDER_RESOURCE.types,
    *_INFERENCE_MODEL_RESOURCE.types,
]


schemas = {
    "console": {
        "query": [
            AgentSessionQuery,
            _AGENT_RESOURCE.query,
            _SKILL_RESOURCE.query,
            _MCP_SERVER_RESOURCE.query,
            _MCP_TOOL_RESOURCE.query,
            _INFERENCE_PROVIDER_RESOURCE.query,
            _INFERENCE_MODEL_RESOURCE.query,
        ],
        "mutation": [
            _AGENT_RESOURCE.mutation,
            InferenceProviderCreateMutation,
            InferenceProviderConnectMutation,
            InferenceProviderUpdateMutation,
            _SKILL_RESOURCE.mutation,
            _MCP_SERVER_RESOURCE.mutation,
            _MCP_TOOL_RESOURCE.mutation,
            _INFERENCE_PROVIDER_RESOURCE.mutation,
            _INFERENCE_MODEL_RESOURCE.mutation,
            InferenceActionMutation,
            AgentActionMutation,
        ],
        "subscription": [changes(Agent, field="agentChanged")],
        "types": _CONSOLE_TYPES,
        "type_extensions": [IntegrationInferenceProviderExtension],
    },
}
"""GraphQL contributions installed by the agents addon."""
