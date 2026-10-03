"""GraphQL schema contributions for the agents addon.

Console surface for the agent catalogue: agents (and their templates), the
skills they mount, the MCP servers/tools they reach, and the inference
provider/model catalogue they run on. Catalogue operations retain their declared
permission gates; persisted chat uses the caller's row permissions. Skill
*sources* are managed in the integrate VCS console (a ``kind="skill"`` source);
this addon owns only the discovered :class:`Skill` rows.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rebac import current_actor, system_context
from strawberry import auto
from strawberry.permission import BasePermission
from strawberry.scalars import JSON

from angee.agents import provisioning
from angee.agents.context import render_view_context
from angee.agents.models import RuntimeStatus, SessionStatus
from angee.base.actors import actor_user_id
from angee.base.identity import public_subject_ref
from angee.graphql.actions import ActionResult, action_target, authorized_permission_target, resolve_action_target
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.graphql.writes import write_queryset
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import PlatformAdminPermission, request_from_info
from angee.iam.schema import UserType
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.schema import (
    ConnectIntegrationResult,
    CredentialType,
    ExternalAccountType,
    IntegrationLabelMixin,
    VendorType,
    apply_integration_patch_fields,
    connect_integration_target,
    integration_create_attrs,
    save_provided_fields,
)
from angee.integrate_vcs.schema import SourceType, TemplateType

InferenceProvider = apps.get_model("agents", "InferenceProvider")
InferenceModel = apps.get_model("agents", "InferenceModel")
Skill = apps.get_model("agents", "Skill")
MCPServer = apps.get_model("agents", "MCPServer")
MCPTool = apps.get_model("agents", "MCPTool")
Agent = apps.get_model("agents", "Agent")
AgentRuntimeImpl = Agent._meta.get_field("runtime_class").choices_enum
strawberry.enum(cast(Any, AgentRuntimeImpl))
AgentSessionModel = apps.get_model("agents", "AgentSession")
AgentTurn = apps.get_model("agents", "AgentTurn")
Integration = apps.get_model("integrate", "Integration")
Vendor = apps.get_model("integrate", "Vendor")
Credential = apps.get_model("integrate", "Credential")
ExternalAccount = apps.get_model("integrate", "ExternalAccount")
Source = apps.get_model("integrate_vcs", "Source")
Template = apps.get_model("integrate_vcs", "Template")
User = get_user_model()


@strawberry_django.type(InferenceProvider)
class InferenceProviderType(IntegrationLabelMixin, AngeeNode):
    """Admin projection of an inference provider child model."""

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType | None
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
    requires_approval: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Agent)
class AgentType(AngeeNode):
    """Admin projection of an agent (or, when ``is_template``, an agent template)."""

    owner: UserType
    permissions = permissions_field(("call",))

    @strawberry.field
    def assignment_subject(self) -> str:
        """Return the public service-user subject that acts for this agent."""

        return str(public_subject_ref(cast(Any, self).principal_subject()))

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
    conflict_kind: auto
    conflict_name: auto
    expects_service: bool = strawberry_django.field(only=["runtime_class"])
    runs_in_process: bool = strawberry_django.field(only=["runtime_class"])
    can_chat: bool = strawberry_django.field(only=["runtime_status", "runtime_class", "service"])
    can_provision: bool = strawberry_django.field(
        only=["lifecycle", "runtime_status", "workspace", "conflict_kind", "conflict_name"]
    )
    can_adopt: bool = strawberry_django.field(only=["lifecycle", "conflict_kind"])
    can_replace: bool = strawberry_django.field(only=["lifecycle", "conflict_kind"])
    can_reprovision: bool = strawberry_django.field(
        only=["lifecycle", "workspace", "conflict_kind", "conflict_name", "runtime_class"]
    )
    can_deprovision: bool = strawberry_django.field(only=["lifecycle", "workspace", "service", "conflict_kind"])
    can_delete: bool = strawberry_django.field(
        only=["lifecycle", "workspace", "service", "conflict_kind"],
        annotate={"_has_active_turns": lambda info: Agent.has_active_turns_expression()},
    )
    created_at: auto
    updated_at: auto


@strawberry_django.type(AgentSessionModel)
class AgentSessionType(AngeeNode):
    """Owner-visible projection of one persisted agent conversation."""

    agent: AgentType
    owner: UserType
    title: auto
    context: JSON
    status: auto
    usage: JSON
    last_error: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(AgentTurn)
class AgentTurnType(AngeeNode):
    """Owner-visible projection of one persisted agent turn."""

    session: AgentSessionType
    index: auto
    prompt: auto
    context: JSON
    status: auto
    updates: JSON
    text: auto
    usage: JSON
    error: auto
    created_at: auto
    updated_at: auto


@strawberry.type
class AgentChatEndpoint:
    """Browser-reachable chat endpoint for a running agent.

    In-process chat uses a same-origin WebSocket and the Django session cookie.
    Container chat uses an operator-routed URL with ``token`` in the query string.
    ``mcp_servers`` is the container agent's rendered ``.mcp.json`` server map.
    ``model_handle`` is the selected agent model in the service runtime's convention,
    used to select the ACP session model explicitly after session creation.
    """

    url: str
    token: str
    expires_at: str
    mcp_servers: JSON
    model_handle: str
    protocol_version: int = strawberry.field(
        description="the ACP protocol version the endpoint speaks; the client must know it before it connects",
    )


@strawberry.type
class AgentChatTarget:
    """Read-only view resolution for either persisted or container chat."""

    agent_id: PublicID
    agent_name: str
    status: str
    model_handle: str
    runtime_class: AgentRuntimeImpl  # type: ignore[valid-type]
    runs_in_process: bool
    session_id: PublicID | None = None


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
    config: JSON | None = strawberry.field(
        default=strawberry.UNSET,
        description="Merge supplied config keys with existing config; null removes a key.",
    )


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
    ),
)
_AGENT_SESSION_RESOURCE = hasura_model_resource(
    AgentSessionType,
    model=AgentSessionModel,
    name="agent_sessions",
    filterable=["id", "agent", "owner", "status", "updated_at"],
    sortable=["title", "status", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["agent", "owner", "status"],
    insert=False,
    update=False,
    write_backend=AngeeHasuraWriteBackend(AgentSessionModel),
    field_id_decode={
        "agent": public_pk_decoder(Agent),
        "owner": public_pk_decoder(User),
    },
)
_AGENT_TURN_RESOURCE = hasura_model_resource(
    AgentTurnType,
    model=AgentTurn,
    name="agent_turns",
    filterable=["id", "session", "index", "status", "updated_at"],
    sortable=["session", "index", "status", "created_at", "updated_at"],
    aggregatable=["id", "index"],
    groupable=["session", "status"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"session": public_pk_decoder(AgentSessionModel)},
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
    insertable=["server", "name", "description", "input_schema", "enabled", "requires_approval"],
    updatable=["description", "input_schema", "enabled", "requires_approval"],
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
            attrs["account"] = credential.external_account
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
                queryset=InferenceProvider._default_manager.select_related("vendor"),
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
        """Update a provider, merging supplied config keys."""

        with (
            action_target(
                InferenceProvider,
                data.id,
                reason="agents.graphql.inference_provider.update",
            ) as provider,
            transaction.atomic(),
        ):
            if data.backend_class is not strawberry.UNSET:
                provider.set_impl_key("backend_class", data.backend_class, default="manual")
            provided = apply_integration_patch_fields(
                provider,
                data,
                reason="agents.graphql.inference_provider.update",
                ignore_null_lifecycle=True,
            )
            if data.name is not strawberry.UNSET:
                provider.name = data.name or ""
                provided.add("name")
            if data.base_url is not strawberry.UNSET:
                provider.base_url = data.base_url or ""
                provided.add("base_url")
            if data.config is not strawberry.UNSET:
                provided.update(provider.apply_config_patch(data.config))
            save_provided_fields(provider, provided)
        return cast(InferenceProviderType, provider)


def _agent_for_view(view: dict[str, Any]) -> Any:
    """Return the running agent that serves ``view`` for the current actor, or ``None``.

    v1 routes every view to the **actor's own** running agent with an available chat
    transport (the most recently updated). ``view["type"]`` is the routing seam — a
    later slice dispatches on it to pick a view-specialised agent — so it is read
    here even though v1 ignores it.
    """

    del view  # routing seam: a later slice dispatches on ``view["type"]``; v1 ignores it
    actor = current_actor()
    user_id = actor_user_id(actor) if actor is not None else None
    if user_id is None:
        return None
    with system_context(reason="agents.graphql.agent_for_view"):
        candidates = (
            Agent.objects.filter(owner_id=user_id, is_template=False, runtime_status=RuntimeStatus.RUNNING)
            .select_related("model")
            .order_by("-updated_at")
        )
        return next((agent for agent in candidates if agent.can_chat), None)


@strawberry.type
class AgentSessionQuery:
    """Authenticated agent session queries for chat surfaces."""

    @strawberry.field
    def resolve_session_for_view(self, view: JSON) -> AgentChatTarget | None:
        """Resolve the agent that serves the user's current view, for the side chatter.

        The chatter knows the *view*, not the agent: this picks the actor's running agent
        (``view["type"]`` is the routing seam for a later view-specialised agent) so the
        client can select its chat surface. Returns ``None`` when the
        user has no running agent, so the chatter shows a call-to-action instead of erroring.
        """

        agent = _agent_for_view(dict(view) if isinstance(view, dict) else {})
        if agent is None:
            return None
        model = getattr(agent, "model", None)
        owner_id = actor_user_id(current_actor())
        with system_context(reason="agents.graphql.session_for_view"):
            session = (
                AgentSessionModel.objects.filter(agent=agent, owner_id=owner_id)
                .exclude(status=SessionStatus.CLOSED)
                .order_by("-updated_at")
                .first()
                if agent.runs_in_process
                else None
            )
        return AgentChatTarget(
            agent_id=PublicID(str(agent.sqid)),
            agent_name=str(agent.name),
            status=str(agent.runtime_status),
            model_handle=str(agent.service_model_handle()) if model is not None else "",
            runtime_class=agent.runtime_class,
            runs_in_process=agent.runs_in_process,
            session_id=PublicID(str(session.sqid)) if session is not None else None,
        )


@strawberry.type
class InferenceActionMutation:
    """Operational actions on an inference provider."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def refresh_provider_models(self, id: PublicID) -> ActionResult:
        """Re-list one provider's models into the catalogue now."""

        with action_target(
            InferenceProvider,
            id,
            reason="agents.graphql.refresh_provider_models",
            queryset=InferenceProvider._default_manager.select_related("credential__oauth_client", "vendor"),
        ) as provider:
            try:
                count = provider.refresh_models()
            except Exception as error:  # noqa: BLE001 — backend failure is the result, not a 500
                return ActionResult(ok=False, message=f"Refresh failed: {error}")
        return ActionResult(ok=True, message=f"Synced {count} model(s).")


class AgentChatEndpointPermission(BasePermission):
    """Declare the callable-agent gate and the runtime's additional admin policy."""

    message = PlatformAdminPermission.message
    error_extensions = {"code": "PERMISSION_DENIED"}

    def has_permission(self, source: Any, info: strawberry.Info, **kwargs: Any) -> bool:
        agent = authorized_permission_target(info, Agent, kwargs["id"], "call")
        return not agent.runtime_backend.chat_requires_admin or PlatformAdminPermission().has_permission(source, info)


@strawberry.type
class AgentDeletePreviewMutation:
    """Authored cascade delete preview for agents under the caller's permissions."""

    @strawberry.mutation(name="delete_agent")
    def delete_agent(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm deletion of one agent by public id."""

        return delete_by_public_id(Agent, str(id), confirm=confirm, queryset=write_queryset(Agent))


attach_delete_preview_metadata(
    AgentDeletePreviewMutation,
    model=Agent,
    node=AgentType,
    field="delete_agent",
)


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
    def adopt_agent(self, id: PublicID) -> ActionResult:
        """Record the conflicting instance as the agent's own, keeping its container as it is."""

        return provisioning.adopt_agent(id)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def replace_agent(self, id: PublicID) -> ActionResult:
        """Destroy the conflicting instance, then provision the agent afresh."""

        return provisioning.replace_agent(id)

    @strawberry.mutation(permission_classes=[AgentChatEndpointPermission])
    def agent_chat_endpoint(self, info: strawberry.Info, id: PublicID) -> AgentChatEndpoint:
        """Format the endpoint selected by the agent's runtime."""

        agent = authorized_permission_target(info, Agent, id, "call")
        return AgentChatEndpoint(**agent.runtime_backend.chat_endpoint(agent, request_from_info(info)))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def render_agent_prompt(self, id: PublicID, view: JSON) -> str:
        """Render the ``<system_context>`` block for an agent and the user's open view.

        ``view`` is the view envelope ``{kind, type: "<app>/<model>", sqid?, sqids?,
        params?}``. This preview does not modify a session or a stored prompt.
        In-process chat supplies the envelope on each ACP prompt; its runtime
        renders the retained turn context through the same ``agents.context`` owner.
        Resolving the agent (admin-gated) confirms the caller may drive it.
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
    AgentSessionType,
    AgentTurnType,
    AgentChatEndpoint,
    AgentChatTarget,
    *_AGENT_RESOURCE.types,
    *_AGENT_SESSION_RESOURCE.types,
    *_AGENT_TURN_RESOURCE.types,
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
            _AGENT_SESSION_RESOURCE.query,
            _AGENT_TURN_RESOURCE.query,
            _SKILL_RESOURCE.query,
            _MCP_SERVER_RESOURCE.query,
            _MCP_TOOL_RESOURCE.query,
            _INFERENCE_PROVIDER_RESOURCE.query,
            _INFERENCE_MODEL_RESOURCE.query,
        ],
        "mutation": [
            _AGENT_RESOURCE.mutation,
            AgentDeletePreviewMutation,
            _AGENT_SESSION_RESOURCE.mutation,
            _AGENT_TURN_RESOURCE.mutation,
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
        "subscription": [
            changes(Agent, field="agentChanged"),
            changes(AgentSessionModel, field="agentSessionChanged"),
            changes(AgentTurn, field="agentTurnChanged"),
        ],
        "types": _CONSOLE_TYPES,
        "type_extensions": [IntegrationInferenceProviderExtension],
    },
}
"""GraphQL contributions installed by the agents addon."""
