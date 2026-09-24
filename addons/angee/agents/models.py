"""Source models for the agent catalogue.

An :class:`Agent` is a definition the operator later renders into a workspace and
service. It draws on three catalogues this addon also owns: :class:`Skill` rows
discovered from an ``integrate_vcs.Source``, :class:`MCPServer`/:class:`MCPTool` rows,
and an :class:`InferenceProvider` integration child with its
:class:`InferenceModel` rows. Templates are agents with
``is_template`` set. This addon keeps definitions only; the operator owns lifecycle.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.db.models.signals import class_prepared, post_delete
from django.utils import timezone
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.output import OutputObjectDefinition
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage, RunUsage
from rebac import SubjectRef, system_context, to_subject_ref
from rebac.mixins import RebacModelBase

from angee.agents.backends import InferenceBackend
from angee.agents.deployments import InferenceDeploymentIdentity
from angee.agents.runtimes import AgentRuntime, operator_secret_ref
from angee.agents.skills import parse_skill_meta
from angee.base.db import get_write_alias, refresh_deferred, related_on
from angee.base.fields import StateField
from angee.base.impl import ImplClassField, ImplDefaultsMixin
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel, role_anchor
from angee.base.permissions import require_authorization_database
from angee.base.transitions import StateTransitions, save_state, transition


class InferenceModelUse(models.TextChoices):
    """What an inference model is used for (mirrors the LLM catalogue's model use)."""

    CHAT = "chat", "Chat"
    COMPLETION = "completion", "Completion"
    EMBEDDING = "embedding", "Embedding"
    MULTIMODAL = "multimodal", "Multimodal"
    GENERATION = "generation", "Generation"
    IMAGE = "image", "Image"


class InferenceModelStatus(models.TextChoices):
    """Lifecycle of a model in a provider's catalogue."""

    AVAILABLE = "available", "Available"
    PREVIEW = "preview", "Preview"
    DEPRECATED = "deprecated", "Deprecated"
    RETIRED = "retired", "Retired"


class MCPPlacement(models.TextChoices):
    """Where an MCP server runs relative to the platform."""

    INTERNAL = "internal", "Internal"
    EXTERNAL = "external", "External"


class MCPTransport(models.TextChoices):
    """How an agent reaches an MCP server."""

    STDIO = "stdio", "stdio"
    HTTP = "http", "HTTP"
    SSE = "sse", "SSE"


BUILTIN_MCP_ANGEE = "angee"
"""``MCPServer.config["builtin"]`` value for this process's built-in Angee MCP server."""

INFERENCE_OUTPUT_TOOL = "inference_output"
"""Native output-tool name used when a provider implements JSON output through tools."""

InferenceOutputSchema = Mapping[str, Any] | Sequence[ToolDefinition]
"""One JSON output schema or native function-tool declarations for a direct request."""


@dataclass(frozen=True, slots=True)
class InferenceResult:
    """One provider response, normalized usage and optional decoded schema object."""

    response: ModelResponse
    usage: dict[str, int]
    output: dict[str, Any] | None = None


class InferenceOutputError(ValueError):
    """A paid provider response whose structured output could not be decoded."""

    def __init__(self, message: str, *, response: ModelResponse, usage: dict[str, int]) -> None:
        """Retain response telemetry and usage for caller-owned accounting."""

        super().__init__(message)
        self.response = response
        self.usage = usage


def inference_request_parameters(
    output_schema: InferenceOutputSchema | None,
) -> ModelRequestParameters:
    """Build the one native request-parameter envelope used by direct inference.

    A JSON-schema mapping supplies both native output shapes so the model's
    profile can select JSON-schema or output-tool mode. A sequence retains
    pydantic-ai's native :class:`ToolDefinition` values as callable function tools;
    direct inference returns those calls without executing a tool loop.
    """

    if output_schema is None:
        return ModelRequestParameters()
    if isinstance(output_schema, Mapping):
        output = OutputObjectDefinition(
            json_schema=dict(output_schema),
            name=INFERENCE_OUTPUT_TOOL,
            description="A structured result matching the declared JSON schema.",
        )
        return ModelRequestParameters(
            output_mode="auto",
            output_object=output,
            output_tools=[
                ToolDefinition(
                    name=INFERENCE_OUTPUT_TOOL,
                    parameters_json_schema=output.json_schema,
                    description=output.description,
                    kind="output",
                )
            ],
        )
    function_tools = list(output_schema)
    if not all(isinstance(tool, ToolDefinition) for tool in function_tools):
        raise TypeError("Inference function tools must be native pydantic-ai ToolDefinition values.")
    return ModelRequestParameters(function_tools=function_tools)


def decode_inference_output(response: ModelResponse) -> dict[str, Any]:
    """Decode the declared schema object from a native output tool or JSON text."""

    output_calls = [
        part for part in response.parts if isinstance(part, ToolCallPart) and part.tool_name == INFERENCE_OUTPUT_TOOL
    ]
    if output_calls:
        if len(output_calls) != 1:
            raise ValueError("Structured inference response is missing or ambiguous.")
        try:
            return output_calls[0].args_as_dict(raise_if_invalid=True)
        except AssertionError as error:
            raise ValueError("Structured inference output root must be an object.") from error
    text = response.text
    if text is None:
        raise ValueError("Structured inference response is missing or ambiguous.")
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) < 3 or lines[-1].strip() != "```":
            raise ValueError("Structured inference response has an incomplete code fence.")
        text = "\n".join(lines[1:-1]).strip()
        if text.startswith("json\n"):
            text = text[5:].lstrip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Structured inference output root must be an object.")
    return value


def normalize_inference_usage(usage: RequestUsage | RunUsage) -> dict[str, int]:
    """Project native usage into every numeric workflow budget axis.

    ``requests`` is retained deliberately: ``WorkflowRun.debit_budget`` and the
    engine budget gate accept arbitrary top-level numeric axes, and both agent
    sessions and extraction already account for provider request count.
    """

    values = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "tokens": usage.total_tokens,
        "requests": usage.requests,
        "tool_calls": usage.tool_calls if isinstance(usage, RunUsage) else 0,
    }
    return {key: int(value) for key, value in values.items() if value}


ToolRole = role_anchor("agents/toolrole", name="ToolRole")
"""Table-less REBAC type anchor for hierarchical tool bundles."""


def _update_field_names(update_fields: Any) -> set[str]:
    """Normalize Django's ``update_fields`` save argument to field names."""

    if isinstance(update_fields, str):
        return {update_fields}
    return {str(field) for field in update_fields}


class AgentLifecycle(models.TextChoices):
    """Where an agent sits in the operator provision pipeline.

    The lifecycle axis: a forward journey the render pipeline drives, distinct from
    the agent's observed run state (:class:`RuntimeStatus`). A provision moves it
    ``DRAFT → PROVISIONING → READY``; a teardown moves it ``→ DEPROVISIONING →
    DEPROVISIONED``. Whether the rendered agent is actually up — and whether the last
    operation failed — is the orthogonal :attr:`Agent.runtime_status`, never folded
    in here.
    """

    DRAFT = "draft", "Draft"
    PROVISIONING = "provisioning", "Provisioning"
    READY = "ready", "Ready"
    DEPROVISIONING = "deprovisioning", "Deprovisioning"
    DEPROVISIONED = "deprovisioned", "Deprovisioned"


class RuntimeStatus(models.TextChoices):
    """The observed run state of a provisioned thing — the colored-dot axis.

    Orthogonal to a provision lifecycle (:class:`AgentLifecycle`): stopped/running/
    error/warning is "is it up right now, and is anything wrong", the grey/green/red/
    amber dot the frontend renders through the ``colorDot`` widget. Reused for any
    model that has a run state; the operator daemon reports the same vocabulary for
    its services (see ``docs/frontend/guidelines.md`` for the shared tone mapping).
    """

    STOPPED = "stopped", "Stopped"
    RUNNING = "running", "Running"
    ERROR = "error", "Error"
    WARNING = "warning", "Warning"


class SessionStatus(models.TextChoices):
    """Display projection of one persisted agent session's workflow state."""

    IDLE = "idle", "Idle"
    RUNNING = "running", "Running"
    AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
    CLOSED = "closed", "Closed"
    ERROR = "error", "Error"


class TurnStatus(models.TextChoices):
    """Execution state of one prompt-to-response turn."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class InferenceProvider(ImplDefaultsMixin, metaclass=RebacModelBase):
    """An LLM provider account, materialized as an integration child row.

    It draws its API credential from its inherited integration credential and
    resolves its provider-specific :class:`~angee.agents.backends.InferenceBackend`
    from ``backend_class``. Django keeps the catalogue; the backend lists the
    provider's models into :class:`InferenceModel` rows.
    """

    runtime = True
    extends = "integrate.Integration"
    integration_create_mode = "FORM"
    integration_kind_label = "Inference provider"

    backend_class = ImplClassField(
        base_class=InferenceBackend,
        registry_setting="ANGEE_INFERENCE_BACKEND_CLASSES",
        default="manual",
        create_only=True,
    )
    """Registry key for the inference backend this provider uses."""
    name = models.CharField(max_length=128)
    base_url = models.URLField(blank=True)
    """Base endpoint for OpenAI-compatible providers; blank uses the backend default."""
    config = models.JSONField(default=dict, blank=True)
    """Provider-scoped settings used by inference implementations."""

    class Meta:
        """Django model options for the inference provider child model."""

        abstract = True
        ordering = ("name",)
        rebac_resource_type = "agents/inference_provider"

    def __str__(self) -> str:
        """Return the provider's display label."""

        return self.name or f"provider:{self.public_id}"

    @property
    def backend(self) -> InferenceBackend:
        """Return the backend bound to this provider's credential and endpoint.

        Resolved fresh per access (unlike ``storage.Backend.storage``, which caches):
        the built-in ``manual`` backend holds no client, and a vendor backend reads
        the live credential off this provider each call, so a rotated key takes
        effect at once and the backend owns any client lifetime it needs.
        """

        backend_class = cast(type[InferenceBackend], self.resolve_impl("backend_class"))
        return backend_class(self)

    def refresh_models(self, *, using: str | None = None) -> int:
        """Re-list this provider's models into :class:`InferenceModel` rows."""

        model = apps.get_model("agents", "InferenceModel")
        using = get_write_alias(type(self), using=using, instance=self)
        return int(model.objects.db_manager(using).sync_from_provider(self, using=using))

    def chat(
        self,
        *,
        model: str,
        messages: Sequence[ModelMessage],
        model_settings: ModelSettings | None = None,
        model_request_parameters: ModelRequestParameters | None = None,
        credential: Any | None = None,
        using: str | None = None,
    ) -> ModelResponse:
        """Send one request using Pydantic AI's native message/settings contract."""

        using = get_write_alias(type(self), using=using, instance=self)
        refresh_deferred(self, using=using)
        backend = self.backend
        return backend.chat(
            model,
            messages,
            model_settings=model_settings,
            model_request_parameters=model_request_parameters,
            credential=credential,
            using=using,
        )


class InferenceModelManager(AngeeManager):
    """Manager owning the upsert of model rows from a provider's catalogue."""

    def sync_from_provider(self, provider: Any, *, using: str | None = None) -> int:
        """Upsert one row per model the provider advertises (non-destructive).

        Missing handles are left in place, not pruned, so an agent's ``model`` FK is
        never broken by a transient provider response; deprecation is a status edit.
        """

        using = get_write_alias(self.model, using=using, bound=self, instance=provider)
        refresh_deferred(provider, using=using)
        backend = provider.backend
        specs = list(backend.list_models(using=using))
        with system_context(reason="agents.inference_model.sync"), transaction.atomic(using=using):
            for spec in specs:
                self.db_manager(using).update_or_create(
                    provider_id=provider.pk, name=spec.handle, defaults=spec.upsert_defaults()
                )
        return len(specs)


class InferenceModel(SqidMixin, AuditMixin, AngeeModel):
    """One model in a provider's catalogue, agents bind to by FK.

    ``publisher`` is the model's maker, reusing the ``integrate.Vendor`` catalogue
    (which need not be the serving provider's vendor — an OpenAI-compatible router can
    serve another maker's model).
    """

    runtime = True
    catalogue = True
    catalogue_tier = "demo"
    catalogue_tiers = ("install", "demo")

    sqid_prefix = "imd_"
    provider = models.ForeignKey("agents.InferenceProvider", on_delete=models.CASCADE, related_name="models")
    publisher = models.ForeignKey(
        "integrate.Vendor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_models",
    )
    name = models.CharField(max_length=200)
    """The selectable runtime handle; ``config.provider_model`` carries the native provider id."""
    display_name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    model_use = StateField(choices_enum=InferenceModelUse, default=InferenceModelUse.CHAT)
    is_default = models.BooleanField(default=False)
    status = StateField(choices_enum=InferenceModelStatus, default=InferenceModelStatus.AVAILABLE)
    context_window = models.PositiveIntegerField(default=0)
    max_output_tokens = models.PositiveIntegerField(null=True, blank=True)
    capabilities = models.JSONField(default=dict, blank=True)
    config = models.JSONField(default=dict, blank=True)

    objects = InferenceModelManager()

    class Meta:
        """Django model options for catalogue models."""

        abstract = True
        ordering = ("provider", "name")
        rebac_resource_type = "agents/inference_model"
        constraints = (models.UniqueConstraint(fields=("provider", "name"), name="uniq_agents_inference_model_name"),)

    def __str__(self) -> str:
        """Return the model's display label."""

        return self.display_name or self.name

    @property
    def provider_model_name(self) -> str:
        """Return the provider-native model id for runtimes that talk to the provider directly."""

        config = self.config if isinstance(self.config, Mapping) else {}
        provider_model = str(config.get("provider_model") or "").strip()
        return provider_model or self.name

    @property
    def credential(self) -> Any:
        """Return the API credential for this model, via its provider's integration."""

        return self.provider.credential

    def bind(self, *, credential: Any | None = None, using: str | None = None) -> AbstractAsyncContextManager[Model]:
        """Bind this catalogue model's native adapter and own its client lifetime."""

        using = get_write_alias(type(self), using=using, instance=self)
        refresh_deferred(self, using=using, fields=("config", "name"))
        provider: Any = related_on(self, "provider", using=using)
        backend = provider.backend
        return backend.model(self.provider_model_name, credential=credential, using=using)

    def deployment_identity(self, *, using: str | None = None) -> InferenceDeploymentIdentity:
        """Return the non-secret endpoint binding used by role approval policy."""

        using = get_write_alias(type(self), using=using, instance=self)
        refresh_deferred(self, using=using, fields=("config", "name"))
        provider: Any = related_on(self, "provider", using=using)
        backend = provider.backend
        return {
            "model": str(self.sqid),
            "provider": str(provider.sqid),
            "backend": str(provider.backend_class),
            "native_model": str(self.provider_model_name),
            "endpoint": backend.endpoint,
        }

    def require_approved(self, role: str, *, using: str | None = None) -> None:
        """Enforce this deployment's exact identity in the configured role allowlist.

        An absent policy leaves the catalogue unrestricted. A configured policy
        fails closed for missing roles, malformed entries and identity changes.
        """

        policy = getattr(settings, "ANGEE_INFERENCE_APPROVED_DEPLOYMENTS", None)
        if policy is None:
            return
        if not isinstance(policy, Mapping):
            raise ValueError("The inference deployment approval policy is invalid.")
        approved = policy.get(role)
        if not isinstance(approved, (list, tuple)) or not all(isinstance(item, Mapping) for item in approved):
            raise ValueError(f"The inference {role} deployment approval policy is invalid.")
        identity = self.deployment_identity(using=using)
        if not any(dict(item) == identity for item in approved):
            raise ValueError(f"The configured {role} model deployment is not approved.")

    def require_capability(self, role: str) -> None:
        """Require a callable lifecycle and the modality consumed by this role."""

        if self.status in {InferenceModelStatus.DEPRECATED, InferenceModelStatus.RETIRED}:
            raise ValueError("Select an available inference model.")
        if role == "recognition":
            if self.model_use not in {InferenceModelUse.MULTIMODAL, InferenceModelUse.IMAGE}:
                raise ValueError("Recognition requires an image-capable model.")
        elif self.model_use not in {InferenceModelUse.CHAT, InferenceModelUse.MULTIMODAL}:
            raise ValueError(f"Inference role {role} requires a chat-capable model.")

    def require_usable(self, actor: Any, role: str, *, using: str | None = None) -> None:
        """Require actor read access, deployment approval and role capability.

        REBAC's field-backed checks currently have no database-alias contract,
        so authorization fails closed before reads on non-default databases.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        require_authorization_database(using, operation="Inference model authorization")
        refresh_deferred(self, using=using, fields=("status", "model_use"))
        if not self.with_actor(actor).has_access("read"):
            raise PermissionDenied("You cannot read the configured inference model.")
        self.require_approved(role, using=using)
        self.require_capability(role)

    def chat(
        self,
        messages: Sequence[ModelMessage],
        *,
        model_settings: ModelSettings | None = None,
        model_request_parameters: ModelRequestParameters | None = None,
        credential: Any | None = None,
        using: str | None = None,
    ) -> ModelResponse:
        """Make one native request using this catalogue model's provider handle."""

        using = get_write_alias(type(self), using=using, instance=self)
        refresh_deferred(self, using=using, fields=("config", "name"))
        provider: Any = related_on(self, "provider", using=using)
        return provider.chat(
            model=self.provider_model_name,
            messages=messages,
            model_settings=model_settings,
            model_request_parameters=model_request_parameters,
            credential=credential,
            using=using,
        )

    def infer(
        self,
        messages: Sequence[ModelMessage],
        *,
        output_schema: InferenceOutputSchema | None = None,
        images: Sequence[BinaryContent] = (),
        settings: ModelSettings | None = None,
        credential: Any | None = None,
        using: str | None = None,
    ) -> InferenceResult:
        """Make one request and expose native response, usage and structured output.

        Images stay as pydantic-ai :class:`BinaryContent` values. They are appended
        as one user message so provider adapters retain ownership of their wire
        representation. A sequence passed as ``output_schema`` is interpreted as
        native function tools; no tool is executed by this one-shot call.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        request_messages = list(messages)
        image_parts = list(images)
        if not all(isinstance(image, BinaryContent) for image in image_parts):
            raise TypeError("Inference images must be native pydantic-ai BinaryContent values.")
        if image_parts:
            request_messages.append(ModelRequest(parts=[UserPromptPart(image_parts)]))
        response = self.chat(
            request_messages,
            model_settings=settings,
            model_request_parameters=inference_request_parameters(output_schema),
            credential=credential,
            using=using,
        )
        usage = normalize_inference_usage(response.usage)
        try:
            output = decode_inference_output(response) if isinstance(output_schema, Mapping) else None
        except ValueError as error:
            raise InferenceOutputError(str(error), response=response, usage=usage) from error
        return InferenceResult(response=response, usage=usage, output=output)


class SkillManager(AngeeManager):
    """Manager owning the reconcile of skill rows from a skill source."""

    def sync_from_source(self, source: Any, *, using: str | None = None) -> int:
        """Walk the source for ``SKILL.md`` and upsert/prune :class:`Skill` rows."""

        using = get_write_alias(self.model, using=using, bound=self, instance=source)
        repository: Any = related_on(
            source, "repository", using=using, select_related=("vcs_bridge__credential__oauth_client",)
        )
        source._meta.get_field("repository").set_cached_value(source, repository)
        vcs_bridge = repository.vcs_bridge
        descriptors = vcs_bridge.discover(source, marker="SKILL.md", parse=parse_skill_meta)
        seen: set[Any] = set()
        with system_context(reason="agents.skill.sync"), transaction.atomic(using=using):
            for descriptor in descriptors:
                skill, _created = self.db_manager(using).update_or_create(
                    source_id=source.pk,
                    path=str(descriptor.get("path", "")),
                    defaults={
                        "name": str(descriptor.get("name", "")),
                        "description": str(descriptor.get("description", "")),
                        "metadata": dict(descriptor.get("metadata", {})),
                    },
                )
                seen.add(skill.pk)
            self.db_manager(using).filter(source=source).exclude(pk__in=seen).delete()
            source.last_synced_at = timezone.now()
            source.save(using=using, update_fields=["last_synced_at", "updated_at"])
        return len(descriptors)


class Skill(SqidMixin, AuditMixin, AngeeModel):
    """One skill discovered under an ``integrate_vcs.Source`` (``source_kind="skill"``).

    The operator mounts the skill's directory into an agent's workspace; Django keeps
    the inventory only. Discovery reuses the integrate source walk.
    """

    runtime = True
    source_kind = "skill"
    """Binds the ``skill`` source kind to this output model (see ``integrate_vcs.Source``)."""

    sqid_prefix = "skl_"
    source = models.ForeignKey("integrate_vcs.Source", on_delete=models.CASCADE, related_name="skills")
    name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    path = models.CharField(max_length=1024, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = SkillManager()

    class Meta:
        """Django model options for discovered skills."""

        abstract = True
        ordering = ("name", "path")
        rebac_resource_type = "agents/skill"
        constraints = (models.UniqueConstraint(fields=("source", "path"), name="uniq_agents_skill_path"),)

    def __str__(self) -> str:
        """Return the skill's display label."""

        return self.name or self.path or f"skill:{self.public_id}"


class MCPServer(SqidMixin, AuditMixin, AngeeModel):
    """An MCP server an agent can reach — internal to the platform or external.

    An external server authenticates with an ``integrate.Credential``; the operator renders
    the selected servers and their authorized tools into an agent's MCP config.
    """

    runtime = True
    catalogue = True
    catalogue_tier = "demo"

    sqid_prefix = "mcp_"
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    placement = StateField(choices_enum=MCPPlacement, default=MCPPlacement.EXTERNAL)
    transport = StateField(choices_enum=MCPTransport, default=MCPTransport.HTTP)
    url = models.URLField(blank=True)
    # Expected to be a non-rotating credential (e.g. a static token). For an internal
    # server this credential's secret is the HMAC key from which each agent's per-agent
    # bearer is derived (see ``bearer_for``); the raw secret never leaves the platform.
    # Rotating the credential re-keys the derivation, so every provisioned agent's bearer
    # stops verifying and its MCP calls 401 until reprovisioned. Constrain to static
    # credentials at the catalogue level.
    credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mcp_servers",
    )
    config = models.JSONField(default=dict, blank=True)

    objects = AngeeManager()

    class Meta:
        """Django model options for MCP servers."""

        abstract = True
        ordering = ("name",)
        rebac_resource_type = "agents/mcp_server"

    def __str__(self) -> str:
        """Return the server's name."""

        return self.name

    @property
    def builtin(self) -> str:
        """Return the built-in MCP server key this row targets, if any."""

        config = self.config if isinstance(self.config, Mapping) else {}
        return str(config.get("builtin") or "").strip()

    @property
    def resolved_url(self) -> str:
        """Return the container-reachable URL this MCP server renders for agents.

        Explicit ``url`` wins for external/custom servers. The built-in Angee MCP
        server is modelled as ``config = {"builtin": "angee"}`` with no row-owned
        URL; the stack supplies the concrete container-reachable URL through
        ``ANGEE_BUILTIN_MCP_URL`` so demo/catalogue data never bakes in a dev port.
        """

        if self.url:
            return str(self.url)
        if self.builtin == BUILTIN_MCP_ANGEE:
            return str(getattr(settings, "ANGEE_BUILTIN_MCP_URL", "") or "").strip()
        return ""

    @property
    def is_addressable(self) -> bool:
        """Whether a rendered container can reach this server — i.e. it has a URL.

        A stdio server is a local command with no URL and isn't rendered into an
        agent's ``.mcp.json``. A built-in Angee server is addressable when the stack
        supplied ``ANGEE_BUILTIN_MCP_URL``.
        """

        return bool(self.resolved_url)

    def config_entry(self, bearer_env: str | None) -> dict[str, Any]:
        """Return this server's ``.mcp.json`` entry, given an optional bearer env var.

        A credentialed server carries an ``Authorization: Bearer`` header whose value is
        the agent runtime's ``${<bearer_env>}`` expansion — the bearer rides the *container
        env* (set from the operator secret in the service env, like the inference token),
        never the file or browser. The operator only resolves ``${secret.<name>}`` in a
        service's env, not in file content, so a bearer placed literally in ``.mcp.json``
        would never resolve. Pass ``None`` for an uncredentialed server.
        """

        entry: dict[str, Any] = {"type": str(self.transport), "url": self.resolved_url}
        if bearer_env is not None:
            entry["headers"] = {"Authorization": f"Bearer ${{{bearer_env}}}"}
        return entry

    def bearer_for(self, agent: Agent) -> str:
        """Return the bearer ``agent`` presents to this MCP server.

        For an ``INTERNAL`` (platform-verified) server this is a *per-agent* bearer
        ``"<agent sqid>.<hmac>"`` derived from the server credential (see
        :meth:`_bearer_digest`): each agent gets a distinct value, an agent holding only
        its own bearer cannot forge a peer's (it never sees the server secret), and the
        derivation is deterministic, so a reprovision re-mints the same value while the
        credential is unchanged. For any other placement the server verifies the raw token
        itself, so it receives the credential's ``secret_value()`` unchanged. Callers
        refresh the credential (``ensure_fresh()``) first, as :meth:`Agent.mcp_secrets` does.
        """

        if self.placement == MCPPlacement.INTERNAL:
            return f"{agent.sqid}.{self._bearer_digest(agent)}"
        return str(self.credential.secret_value())

    def accepts_bearer_digest(self, agent: Agent, digest: str) -> bool:
        """Whether ``digest`` is the per-agent bearer digest this server mints for ``agent``.

        Only meaningful for an ``INTERNAL`` server (an external one verifies its own raw
        token, not a derived digest), so any other placement returns ``False``. Compared in
        constant time against the freshly recomputed digest so a mismatch leaks no timing.
        """

        if self.placement != MCPPlacement.INTERNAL:
            return False
        return hmac.compare_digest(self._bearer_digest(agent), digest)

    def _bearer_digest(self, agent: Agent) -> str:
        """Return the HMAC digest binding ``agent`` to this server's credential.

        The single home of the derivation, so minting (:meth:`bearer_for`) and verifying
        (:meth:`accepts_bearer_digest`) cannot drift: the credential secret keys an
        HMAC-SHA256 over the agent's stable public sqid.
        """

        secret = str(self.credential.secret_value())
        return hmac.new(secret.encode(), agent.sqid.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def parse_bearer(bearer: str) -> tuple[str, str] | None:
        """Split a platform bearer into its ``(agent sqid, digest)`` parts, or ``None``.

        The internal-server bearer minted by :meth:`bearer_for` is
        ``"<agent sqid>.<hmac>"``. Agent sqids are a prefix plus base-alphabet characters
        and never contain ``.``, so a single partition recovers the parts. The digest must
        be the 64-character lowercase hexadecimal output of HMAC-SHA256; rejecting every
        other shape here also keeps arbitrary Unicode away from ``hmac.compare_digest``.
        """

        sqid, dot, digest = bearer.partition(".")
        if (
            not dot
            or not sqid
            or len(digest) != hashlib.sha256().digest_size * 2
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return None
        return sqid, digest


class MCPTool(SqidMixin, AuditMixin, AngeeModel):
    """One tool an MCP server exposes; agents select the tools they may call."""

    runtime = True

    sqid_prefix = "mct_"
    server = models.ForeignKey("agents.MCPServer", on_delete=models.CASCADE, related_name="tools")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    input_schema = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=False)
    """Whether invoking this tool must suspend for an owner decision."""

    class Meta:
        """Django model options for MCP tools."""

        abstract = True
        ordering = ("server", "name")
        rebac_resource_type = "agents/tool_grant"
        constraints = (models.UniqueConstraint(fields=("server", "name"), name="uniq_agents_mcp_tool_name"),)

    def __str__(self) -> str:
        """Return the tool's name."""

        return self.name


class AgentManager(AngeeManager):
    """Manager owning service-user lifecycle for agent principals."""

    def service_username(self, agent: Any) -> str:
        """Return the deterministic username for ``agent``'s service user."""

        return f"agent-{agent.sqid}"

    def sync_service_user(self, agent: Any, *, using: str | None = None) -> Any:
        """Create or update ``agent``'s non-login service user.

        The service row is system-owned attribution state, not actor-authored
        profile data, so it is written elevated and keyed only by the agent's
        stable sqid-derived username.
        """

        if agent.pk is None:
            raise ValueError("Agent must be saved before syncing its service user.")
        using = get_write_alias(self.model, using=using, bound=self, instance=agent)
        user_model = get_user_model()
        username = self.service_username(agent)
        defaults = {
            "first_name": agent.name,
            "last_name": "",
            "email": "",
            "kind": "service",
        }
        with system_context(reason="agents.service_user.sync"), transaction.atomic(using=using):
            if agent.user_id:
                user: Any = related_on(agent, "user", using=using)
                changed: set[str] = set()
                for field, value in {"username": username, **defaults}.items():
                    if getattr(user, field) != value:
                        setattr(user, field, value)
                        changed.add(field)
                if changed:
                    user.save(using=using, update_fields=changed)
                agent._meta.get_field("user").set_cached_value(agent, user)
                return user
            user, _created = user_model._base_manager.db_manager(using).update_or_create(
                username=username, defaults=defaults
            )
            agent.user_id = user.pk
            agent._meta.get_field("user").set_cached_value(agent, user)
            type(agent)._base_manager.using(using).filter(pk=agent.pk).update(user_id=user.pk)
            return user

    def deactivate_service_user(self, agent: Any, *, using: str | None = None) -> None:
        """Deactivate ``agent``'s linked service user, leaving attribution FKs intact."""

        if not agent.user_id:
            return
        using = get_write_alias(self.model, using=using, bound=self, instance=agent)
        user_model = get_user_model()
        manager = user_model._base_manager.db_manager(using)
        with system_context(reason="agents.service_user.deactivate"):
            manager.filter(pk=agent.user_id).update(is_active=False)


class Agent(SqidMixin, AuditMixin, AngeeModel):
    """An agent definition (or, when ``is_template``, an agent template).

    The operator renders an agent into a workspace from ``workspace_template`` and a
    service from the template its ``runtime_class`` declares, writing the
    ``instructions`` into AGENTS.md/CLAUDE.md, the selected skills and MCP servers/tools
    into the workspace, and the model's API credential into the service. ``service`` and
    ``workspace`` hold the operator instance names once rendered.
    """

    runtime = True
    rebac_grantable = {"reader": "share", "editor": "share"}

    sqid_prefix = "agt_"
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_template = models.BooleanField(default=False, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agents")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="agent",
    )
    """Non-login user that authenticates, authorizes, and attributes this agent's actions."""
    instructions = models.TextField(blank=True)
    """The agent's system instructions, rendered into AGENTS.md/CLAUDE.md."""
    model = models.ForeignKey(
        "agents.InferenceModel",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="agents",
    )
    inference_credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    """Per-agent inference credential override. When set, the agent authenticates inference
    with this credential (e.g. a connected Anthropic OAuth account) instead of the one its
    model's provider integration carries; unset falls back to that catalogue chain."""
    skills = models.ManyToManyField("agents.Skill", blank=True, related_name="agents")
    mcp_servers = models.ManyToManyField("agents.MCPServer", blank=True, related_name="agents")
    mcp_tools = models.ManyToManyField("agents.MCPTool", blank=True, related_name="agents")
    runtime_class = ImplClassField(
        base_class=AgentRuntime,
        registry_setting="ANGEE_AGENT_RUNTIME_CLASSES",
        default="none",
    )
    """Registry key for the agent runtime — the program this agent renders into. The
    runtime owns its operator service template, how it consumes an inference credential
    as container env, and the model-handle convention; ``none`` renders no service
    (a workspace-only agent)."""
    workspace_template = models.ForeignKey(
        "integrate_vcs.Template",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    service_inputs = models.JSONField(default=dict, blank=True)
    workspace_inputs = models.JSONField(default=dict, blank=True)
    service = models.CharField(max_length=128, blank=True)
    """Operator service instance name, set when the agent is rendered."""
    workspace = models.CharField(max_length=128, blank=True)
    """Operator workspace instance name, set when the agent is rendered."""
    lifecycle = StateField(choices_enum=AgentLifecycle, default=AgentLifecycle.DRAFT)
    """Provision-pipeline position (:class:`AgentLifecycle`), set by the render flow."""
    runtime_status = StateField(choices_enum=RuntimeStatus, default=RuntimeStatus.STOPPED)
    """Observed run state (:class:`RuntimeStatus`) — the colored dot; ``ERROR`` pairs
    with ``last_error``. Set by the render flow; the daemon owns the live truth."""
    last_error = models.TextField(blank=True)
    """The reason ``runtime_status`` is ``ERROR`` — the last failed operation."""

    lifecycle_transitions = StateTransitions(
        lifecycle,
        {
            AgentLifecycle.DRAFT: [
                AgentLifecycle.PROVISIONING,
                AgentLifecycle.DEPROVISIONING,
                AgentLifecycle.DEPROVISIONED,
            ],
            AgentLifecycle.PROVISIONING: [
                AgentLifecycle.PROVISIONING,
                AgentLifecycle.READY,
                AgentLifecycle.DEPROVISIONING,
            ],
            AgentLifecycle.READY: [
                AgentLifecycle.PROVISIONING,
                AgentLifecycle.DEPROVISIONING,
            ],
            AgentLifecycle.DEPROVISIONING: [
                AgentLifecycle.DEPROVISIONING,
                AgentLifecycle.DEPROVISIONED,
            ],
            AgentLifecycle.DEPROVISIONED: [
                AgentLifecycle.PROVISIONING,
                AgentLifecycle.DEPROVISIONING,
                AgentLifecycle.DEPROVISIONED,
            ],
        },
    )

    objects = AgentManager()

    class Meta:
        """Django model options for agents."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "agents/agent"

    def __str__(self) -> str:
        """Return the agent's name."""

        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the agent and sync its service-user label.

        Mirrors ``iam.User.save()``: the row save owns a small derived sync, and
        the manager performs the system-owned dependent write.
        """

        using = get_write_alias(type(self), using=kwargs.get("using"), instance=self)
        kwargs["using"] = using
        creating = self._state.adding
        update_fields = kwargs.get("update_fields")
        should_check_name = creating or update_fields is None or "name" in _update_field_names(update_fields)
        persisted_name = None
        if not creating and should_check_name:
            persisted_name = (
                type(self)._base_manager.using(using).filter(pk=self.pk).values_list("name", flat=True).first()
            )
        with transaction.atomic(using=using):
            super().save(*args, **kwargs)
            if creating or (should_check_name and persisted_name != self.name):
                type(self).objects.db_manager(using).sync_service_user(self, using=using)

    def principal_subject(self, *, using: str | None = None) -> SubjectRef:
        """Return the service user's REBAC subject for actions this agent performs.

        This is distinct from :attr:`owner`: the owner manages the agent definition,
        while the linked non-login user represents the running agent as an actor.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        if self.user_id is None:
            raise ValueError("Agent has no service user and cannot act.")
        user: Any = related_on(self, "user", using=using)
        return to_subject_ref(user)

    @property
    def runtime_backend(self) -> AgentRuntime:
        """Return the :class:`~angee.agents.runtimes.AgentRuntime` this agent renders into.

        Resolved fresh from ``runtime_class`` per access (the runtime is stateless); it
        owns the service template, the credential→env mapping, and the model handle.
        """

        runtime_class = cast(type[AgentRuntime], self.resolve_impl("runtime_class", default="none"))
        return runtime_class()

    @property
    def can_provision(self) -> bool:
        """Whether the provision action may start from the current lifecycle facts."""

        return str(self.runtime_status) == str(RuntimeStatus.ERROR) or str(self.lifecycle) in {
            str(AgentLifecycle.DRAFT),
            str(AgentLifecycle.DEPROVISIONED),
        }

    @property
    def can_deprovision(self) -> bool:
        """Whether the teardown action is meaningful for the current rendered state."""

        return str(self.lifecycle) in {
            str(AgentLifecycle.PROVISIONING),
            str(AgentLifecycle.READY),
            str(AgentLifecycle.DEPROVISIONING),
        } or bool(self.workspace or self.service)

    @property
    def can_delete(self) -> bool:
        """Whether deleting the definition can leave no orphaned operator instance."""

        return not self.can_deprovision

    def delete_blocker(self) -> str | None:
        """Return the delete-blocking reason, or ``None`` when deletion is allowed."""

        if self.can_delete:
            return None
        return "Deprovision this agent before deleting it."

    @transition(
        lifecycle,
        source=[
            AgentLifecycle.DRAFT,
            AgentLifecycle.PROVISIONING,
            AgentLifecycle.READY,
            AgentLifecycle.DEPROVISIONED,
        ],
        target=AgentLifecycle.PROVISIONING,
        on_success=save_state,
    )
    def mark_provisioning(self, *, using: str | None = None) -> None:
        """Enter the provision flow: lifecycle provisioning, run state reset to stopped."""

        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.STOPPED)
        self.last_error = ""
        self._transition_fields = {"runtime_status", "last_error"}

    @transition(
        lifecycle,
        source=AgentLifecycle.PROVISIONING,
        target=AgentLifecycle.PROVISIONING,
        on_success=save_state,
    )
    def mark_workspace_provisioned(self, *, workspace: str, using: str | None = None) -> None:
        """Record the workspace as soon as the operator creates it."""

        self.workspace = workspace
        self.last_error = ""
        self._transition_fields = {"workspace", "last_error"}

    @transition(
        lifecycle,
        source=AgentLifecycle.PROVISIONING,
        target=AgentLifecycle.PROVISIONING,
        on_success=save_state,
    )
    def mark_service_provisioned(self, *, service: str, using: str | None = None) -> None:
        """Record the service as soon as the operator creates it."""

        self.service = service
        self.last_error = ""
        self._transition_fields = {"service", "last_error"}

    @transition(
        lifecycle,
        source=AgentLifecycle.PROVISIONING,
        target=AgentLifecycle.READY,
        on_success=save_state,
    )
    def mark_provisioned(self, *, workspace: str, service: str = "", using: str | None = None) -> None:
        """Record the operator instance the provision flow rendered for this agent.

        The daemon owns the workspace/service lifecycle; the server-side provision
        flow renders them and calls this to persist the resulting instance names, mark
        the lifecycle ``READY`` and the run state ``RUNNING``. Clears any prior error.
        ``service`` is optional — a workspace-only agent renders no service.
        """

        self.workspace = workspace
        self.service = service
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.RUNNING)
        self.last_error = ""
        self._transition_fields = {"workspace", "service", "runtime_status", "last_error"}

    @transition(
        lifecycle,
        source=[AgentLifecycle.DRAFT, AgentLifecycle.DEPROVISIONING, AgentLifecycle.DEPROVISIONED],
        target=AgentLifecycle.DEPROVISIONED,
        on_success=save_state,
    )
    def mark_deprovisioned(self, *, using: str | None = None) -> None:
        """Clear the operator instance after teardown: lifecycle deprovisioned, run state stopped."""

        self.workspace = ""
        self.service = ""
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.STOPPED)
        self.last_error = ""
        self._transition_fields = {"workspace", "service", "runtime_status", "last_error"}

    @transition(
        lifecycle,
        source=[
            AgentLifecycle.DRAFT,
            AgentLifecycle.PROVISIONING,
            AgentLifecycle.READY,
            AgentLifecycle.DEPROVISIONING,
            AgentLifecycle.DEPROVISIONED,
        ],
        target=AgentLifecycle.DEPROVISIONING,
        on_success=save_state,
    )
    def mark_deprovisioning(self, *, using: str | None = None) -> None:
        """Mark the agent as tearing down through the operator teardown flow."""

        self.last_error = ""
        self._transition_fields = {"last_error"}

    def mark_provision_failed(
        self, message: str, *, clear_instances: bool = False, clear_service: bool = False, using: str | None = None
    ) -> None:
        """Record a failed operation: run state ``ERROR`` (the red dot), reason kept.

        The failure lands on the run-state axis: ``last_error`` holds the reason and the
        dot turns red. ``clear_instances`` blanks both instance names (a provision rolled
        the workspace back); ``clear_service`` blanks only the service (a reprovision
        destroyed the old service before the recreate failed — the workspace is preserved).
        The lifecycle then follows the workspace, never stranding mid-flow: an agent left
        holding a workspace is still provisioned (``READY``), one rolled back to nothing is
        a clean ``DRAFT`` retry. The red run-state dot carries the failure either way, and
        the persisted names never point at a torn-down instance. This deliberately bypasses
        the declared lifecycle graph because the target is data-dependent recovery state,
        not a user-visible lifecycle action.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        transition_fields = {"runtime_status", "last_error"}
        if clear_instances:
            self.workspace = ""
            self.service = ""
            transition_fields.update({"workspace", "service"})
        elif clear_service:
            self.service = ""
            transition_fields.add("service")
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.ERROR)
        self.last_error = message[:2000]
        self._transition_fields = transition_fields
        self.lifecycle_transitions.force_state(
            self,
            cast(AgentLifecycle, AgentLifecycle.READY if self.workspace else AgentLifecycle.DRAFT),
            reason="agent provision failure reconciles lifecycle from persisted operator instance names",
            using=using,
        )

    def provision_workspace_inputs(self, *, using: str | None = None) -> dict[str, str]:
        """Resolve the ``agent-default`` workspace template inputs from this agent.

        The structured fields (name, instructions, MCP servers) are the source of
        truth, so they win over any same-named key in ``workspace_inputs`` (which
        carries template-specific extras only). All values are stringified — Copier
        and the daemon take string answers.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        self._state.db = using
        structured = {
            "agent_name": self.name,
            "instructions": self.instructions,
            "mcp_json": json.dumps(self.mcp_config(), separators=(",", ":")),
        }
        merged = {**(self.workspace_inputs or {}), **structured}
        return {key: str(value) for key, value in merged.items()}

    _SERVICE_ENV_INDENT = "      "
    """Indent for spliced ``env:`` lines — must match the service templates' ``env:`` block."""

    def provision_service_inputs(self, *, using: str | None = None) -> dict[str, str]:
        """Resolve the structured service-template inputs from this agent.

        Carries the runtime model handle plus the runtime-owned auth env block. The
        secret *value* never appears here — only operator ``${secret.…}`` placeholders;
        the value is synced server-side. The runtime (not the provider) owns how the
        credential becomes env, because the same token feeds different env vars in
        different runtimes. ``service_inputs`` supplies template-specific extras
        (``permission_mode`` etc.) and loses to the structured keys.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        self._state.db = using
        structured: dict[str, str] = {}
        runtime = self.runtime_backend
        model: Any = related_on(self, "model", using=using, select_related=("provider",))
        if model is not None:
            structured["model"] = runtime.model_handle(model)
        # Advertise auth only when the runtime renders a service and there is a usable secret
        # to sync — otherwise the rendered service would reference a ${secret.<name>} the
        # operator never gets, and a workspace-only runtime has no service container to read
        # it. This must agree with the readiness gate and the secret sync in the provision
        # flow (both also keyed on ``renders_service``).
        if runtime.renders_service and model is not None and self.inference_secret():
            credential = self.inference_credential_for_runtime()
            backend = getattr(getattr(model, "provider", None), "backend", None)
            if credential is None or backend is None:
                raise ValueError("Inference auth requires a model provider backend.")
            structured["auth_env"] = self._service_env_lines(
                runtime.auth_env(backend=backend, credential=credential, secret_name=self.inference_secret_name())
            )
        # The MCP bearers ride the container env too: one ``${secret.<name>}`` line per
        # credentialed server (the operator resolves it), which the service template renders
        # under the service env and ``.mcp.json`` reads via ``${ANGEE_MCP_BEARER_<…>}``.
        mcp_env = {
            self.mcp_bearer_env(server): operator_secret_ref(secret_name)
            for server, secret_name in self._addressable_mcp_servers(using=using)
            if secret_name
        }
        if mcp_env:
            structured["mcp_env"] = self._service_env_lines(mcp_env)
        merged = {**(self.service_inputs or {}), **structured}
        return {key: str(value) for key, value in merged.items()}

    @classmethod
    def _service_env_lines(cls, env: Mapping[str, str]) -> str:
        """Return YAML ``env:`` lines a service template splices verbatim with ``| safe``.

        The provision answer channel is string-only, so the env block is rendered to
        text here (not a structured list the template loops). ``json.dumps`` double-quotes
        each value, and the shared indent must match the template's ``env:`` nesting.
        """

        return "\n".join(f"{cls._SERVICE_ENV_INDENT}{name}: {json.dumps(value)}" for name, value in env.items())

    def service_model_handle(self) -> str:
        """Return the selected model handle in this agent's runtime convention."""

        model = getattr(self, "model", None)
        return self.runtime_backend.model_handle(model) if model is not None else ""

    def mcp_config(self, *, using: str | None = None) -> dict[str, Any]:
        """Return the ``.mcp.json`` document for this agent's reachable MCP servers.

        Each server renders its own entry (:meth:`MCPServer.config_entry`); this supplies
        the bearer env var (:meth:`mcp_bearer_env`) for a credentialed server and skips
        servers that aren't addressable. The header expands that env var, which the service
        env sets from the operator secret (:meth:`provision_service_inputs`) — the value
        rides the container env, never the file or the browser. See :meth:`mcp_secrets`.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        servers = {
            server.name: server.config_entry(self.mcp_bearer_env(server) if secret_name else None)
            for server, secret_name in self._addressable_mcp_servers(using=using)
        }
        return {"mcpServers": servers}

    def _addressable_mcp_servers(self, *, using: str) -> Iterator[tuple[MCPServer, str]]:
        """Yield ``(server, secret_name)`` for each addressable MCP server, in row order.

        The single owner of "which servers this agent exposes, and the operator secret
        name each credentialed one uses" — so the rendered ``.mcp.json`` header
        (:meth:`mcp_config`) and the value synced under it (:meth:`mcp_secrets`) can't
        drift. ``secret_name`` is ``""`` for an uncredentialed server.
        """

        for server in self.mcp_servers.using(using).select_related("credential"):
            if not server.is_addressable:
                continue
            yield server, (self.mcp_secret_name(server) if server.credential_id else "")

    def mcp_secret_name(self, server: MCPServer) -> str:
        """Return the operator secret name holding one MCP server's bearer for this agent.

        Stable and scoped to the agent + server credential: the service env references it
        (``${secret.<name>}`` → the bearer env var) and the provision flow syncs the agent's
        bearer (:meth:`mcp_secrets`) under it (the value never appears in the file or the browser).
        """

        return f"agent-{self.sqid}-mcp-{server.credential.sqid}"

    def mcp_bearer_env(self, server: MCPServer) -> str:
        """Return the container env var carrying one MCP server's bearer for this agent.

        The rendered ``.mcp.json`` header reads it via the agent runtime's ``${VAR}``
        expansion; the service env sets it from the operator secret (:meth:`mcp_secret_name`),
        which the operator resolves into the container env. Keyed by the server credential so
        the env name and the ``.mcp.json`` reference can't drift, and unique per server in the
        agent's container. The sqid segment is upper-cased so the env name is portable across
        container runtimes/shells that reject or fold lowercase env names.
        """

        return f"ANGEE_MCP_BEARER_{server.credential.sqid.upper()}"

    def mcp_secrets(self, *, using: str | None = None) -> dict[str, str]:
        """Return ``{secret_name: bearer_value}`` for every credentialed MCP server.

        The bearer is whatever this agent presents to the server (:meth:`MCPServer.bearer_for`):
        a per-agent derived token for an internal, platform-verified server, or the raw
        credential secret for an external one. Server-side only — the provision flow pushes
        these to the operator secret store so each server's ``${secret.<name>}`` header
        resolves in the container; the raw internal-server secret never reaches a container.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        secrets: dict[str, str] = {}
        for server, secret_name in self._addressable_mcp_servers(using=using):
            if not secret_name:
                continue
            server.credential.ensure_fresh()
            secrets[secret_name] = server.bearer_for(self)
        return secrets

    def inference_secret_name(self) -> str:
        """Return the operator secret name holding this agent's inference token.

        Stable and agent-scoped — the provision inputs reference it and the
        (server-side) secret sync writes the credential value under it.
        """

        return f"agent-{self.sqid}-inference"

    def inference_secret(self, *, using: str | None = None) -> str:
        """Return the inference credential's secret value (API key or OAuth token), or ``""``.

        Server-side only — the value is pushed to the operator secret store under
        ``inference_secret_name()`` and never returned to the browser. An OAuth token
        near expiry is renewed first (:meth:`integrate.Credential.ensure_fresh`) so the value
        frozen into the provisioned service has its full lifetime ahead of it.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        self._state.db = using
        credential = self.inference_credential_for_runtime()
        if credential is None:
            return ""
        credential.ensure_fresh()
        return str(credential.secret_value())

    def provision_inference_secret(self, *, using: str | None = None) -> str:
        """Return the runtime-shaped inference secret payload synced to the operator store.

        The value the provision flow stores under :meth:`inference_secret_name`, that the
        service's ``${secret.<name>}`` auth placeholder resolves to in the container: the
        raw credential secret for most runtimes, or a runtime-built payload (OpenCode's
        base64 ``auth.json`` for an OAuth credential — see
        :meth:`~angee.agents.runtimes.AgentRuntime.auth_secret_value`). ``""`` when there is
        no credential to sync. Readiness still gates on :meth:`inference_secret` (the raw
        token), so an empty credential is refused before this richer payload is built.
        A runtime that renders no service has no container to consume the secret, so nothing
        is synced (kept in step with :meth:`provision_service_inputs`' auth-env block).
        """

        using = get_write_alias(type(self), using=using, instance=self)
        self._state.db = using
        runtime = self.runtime_backend
        if not runtime.renders_service:
            return ""
        credential = self.inference_credential_for_runtime()
        if credential is None:
            return ""
        credential.ensure_fresh()
        return runtime.auth_secret_value(credential)

    def inference_credential_ready(self, *, using: str | None = None) -> bool:
        """Whether this agent can be provisioned with working inference auth.

        A model-less agent needs no inference credential, so it is always ready. A
        credential-less backend is ready only for an in-process runtime, where its default
        endpoint reaches the host directly. Every attached credential must yield a usable
        secret, and a service-rendering runtime must be able to consume its kind, so an
        unworkable pairing is refused here rather than degrading silently at run time.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        self._state.db = using
        if self.model_id is None:
            return True
        credential = self.inference_credential_for_runtime()
        runtime = self.runtime_backend
        if credential is None:
            model: Any = related_on(self, "model", using=using, select_related=("provider",))
            return not model.provider.backend.requires_credential and not runtime.renders_service
        if not self.inference_secret():
            return False
        return not runtime.renders_service or runtime.supports_credential(credential)

    def inference_model(self, *, using: str | None = None) -> AbstractAsyncContextManager[Model]:
        """Bind the native model using this agent's credential override."""

        using = get_write_alias(type(self), using=using, instance=self)
        self._state.db = using
        if self.model_id is None:
            raise ValueError("An in-process agent requires an inference model.")
        model: Any = related_on(self, "model", using=using)
        return model.bind(credential=self.inference_credential_for_runtime(using=using), using=using)

    def inference_credential_for_runtime(self, *, using: str | None = None) -> Any:
        """Return the ``integrate.Credential`` backing this agent's inference, or ``None``.

        A per-agent ``inference_credential`` override wins (e.g. a connected Anthropic OAuth
        account the user pointed this agent at); otherwise the model's catalogue credential
        (the model→provider→credential chain the catalogue owns), asked of the model rather
        than walked here.
        """

        using = get_write_alias(type(self), using=using, instance=self)
        if self.inference_credential_id is not None:
            return related_on(self, "inference_credential", using=using, select_related=("oauth_client",))
        if self.model_id is None:
            return None
        model: Any = related_on(self, "model", using=using, select_related=("provider__credential__oauth_client",))
        return model.credential


class AgentSession(SqidMixin, AuditMixin, AngeeModel):
    """Runtime-neutral persisted conversation backed by one workflow run."""

    runtime = True

    sqid_prefix = "ase_"
    agent = models.ForeignKey("agents.Agent", on_delete=models.PROTECT, related_name="sessions")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_sessions")
    title = models.CharField(max_length=200, blank=True)
    context = models.JSONField(default=dict, blank=True)
    status = StateField(choices_enum=SessionStatus, default=SessionStatus.IDLE)
    replay_state = models.JSONField(default=list, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)

    status_transitions = StateTransitions(
        status,
        {
            SessionStatus.IDLE: [SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.CLOSED, SessionStatus.ERROR],
            SessionStatus.RUNNING: [
                SessionStatus.RUNNING,
                SessionStatus.IDLE,
                SessionStatus.AWAITING_APPROVAL,
                SessionStatus.CLOSED,
                SessionStatus.ERROR,
            ],
            SessionStatus.AWAITING_APPROVAL: [
                SessionStatus.RUNNING,
                SessionStatus.CLOSED,
                SessionStatus.ERROR,
            ],
            SessionStatus.ERROR: [
                SessionStatus.ERROR,
                SessionStatus.IDLE,
                SessionStatus.RUNNING,
                SessionStatus.CLOSED,
            ],
        },
    )

    objects = AngeeManager()

    class Meta:
        """Django model options for persisted agent sessions."""

        abstract = True
        ordering = ("-updated_at", "sqid")
        rebac_resource_type = "agents/session"

    def __str__(self) -> str:
        """Return the session title or its agent name."""

        return self.title or str(self.agent)

    @transition(
        status,
        source=[SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.AWAITING_APPROVAL, SessionStatus.ERROR],
        target=SessionStatus.RUNNING,
        on_success=save_state,
    )
    def mark_running(self, *, using: str | None = None) -> None:
        """Project active turn execution onto the session."""

        self.last_error = ""
        self._transition_fields = {"last_error"}

    @transition(
        status,
        source=[SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.ERROR],
        target=SessionStatus.IDLE,
        on_success=save_state,
    )
    def mark_idle(self, *, using: str | None = None) -> None:
        """Project a parked session awaiting its next user turn."""

        self.last_error = ""
        self._transition_fields = {"last_error"}

    @transition(
        status,
        source=SessionStatus.RUNNING,
        target=SessionStatus.AWAITING_APPROVAL,
        on_success=save_state,
    )
    def mark_awaiting_approval(self, *, using: str | None = None) -> None:
        """Project a suspended tool approval onto the session."""

    @transition(
        status,
        source=[SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.AWAITING_APPROVAL, SessionStatus.ERROR],
        target=SessionStatus.CLOSED,
        on_success=save_state,
    )
    def close(self, *, using: str | None = None) -> None:
        """Close the conversation so its workflow step can finish."""

    @transition(
        status,
        source=[SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.AWAITING_APPROVAL, SessionStatus.ERROR],
        target=SessionStatus.ERROR,
        on_success=save_state,
    )
    def mark_error(self, message: str, *, using: str | None = None) -> None:
        """Project a session-level runtime error."""

        self.last_error = message[:2000]
        self._transition_fields = {"last_error"}


class AgentTurn(SqidMixin, AuditMixin, AngeeModel):
    """One prompt-to-response cycle with append-only ACP updates."""

    runtime = True

    sqid_prefix = "atn_"
    session = models.ForeignKey("agents.AgentSession", on_delete=models.CASCADE, related_name="turns")
    index = models.PositiveIntegerField()
    prompt = models.TextField()
    status = StateField(choices_enum=TurnStatus, default=TurnStatus.PENDING)
    updates = models.JSONField(default=list, blank=True)
    text = models.TextField(blank=True)
    usage = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    status_transitions = StateTransitions(
        status,
        {
            TurnStatus.PENDING: [TurnStatus.RUNNING, TurnStatus.CANCELED],
            TurnStatus.RUNNING: [
                TurnStatus.RUNNING,
                TurnStatus.AWAITING_APPROVAL,
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELED,
            ],
            TurnStatus.AWAITING_APPROVAL: [
                TurnStatus.RUNNING,
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELED,
            ],
        },
    )

    objects = AngeeManager()

    class Meta:
        """Django model options for agent turns."""

        abstract = True
        ordering = ("session", "index")
        rebac_resource_type = "agents/turn"
        constraints = (models.UniqueConstraint(fields=("session", "index"), name="uniq_agents_turn_session_index"),)

    @transition(
        status,
        source=[TurnStatus.PENDING, TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL],
        target=TurnStatus.RUNNING,
        on_success=save_state,
    )
    def mark_running(self, *, using: str | None = None) -> None:
        """Claim or resume this turn for runtime execution."""

        self.error = ""
        self._transition_fields = {"error"}

    @transition(
        status,
        source=TurnStatus.RUNNING,
        target=TurnStatus.AWAITING_APPROVAL,
        on_success=save_state,
    )
    def mark_awaiting_approval(self, *, using: str | None = None) -> None:
        """Suspend this turn for deferred tool approval."""

    @transition(
        status,
        source=[TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL],
        target=TurnStatus.COMPLETED,
        on_success=save_state,
    )
    def mark_completed(self, *, text: str, usage: Mapping[str, int], using: str | None = None) -> None:
        """Persist the final response projection for a completed turn."""

        self.text = text
        self.usage = dict(usage)
        self.error = ""
        self._transition_fields = {"text", "usage", "error"}

    @transition(
        status,
        source=[TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL],
        target=TurnStatus.FAILED,
        on_success=save_state,
    )
    def mark_failed(self, message: str, *, using: str | None = None) -> None:
        """Persist a terminal failure for this turn."""

        self.error = message[:2000]
        self._transition_fields = {"error"}

    @transition(
        status,
        source=[TurnStatus.PENDING, TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL],
        target=TurnStatus.CANCELED,
        on_success=save_state,
    )
    def cancel(self, *, using: str | None = None) -> None:
        """Cancel this turn without deleting its audit trail."""


def _deactivate_agent_service_user(
    sender: type[models.Model],
    instance: models.Model,
    using: str,
    **kwargs: Any,
) -> None:
    """Deactivate an agent service user after every delete path Django supports."""

    del sender, kwargs
    type(instance).objects.deactivate_service_user(instance, using=using)


def _connect_agent_lifecycle(sender: type[models.Model], **kwargs: Any) -> None:
    """Connect concrete Agent lifecycle handlers."""

    del kwargs
    try:
        is_agent = issubclass(sender, Agent)
    except TypeError:
        return
    if not is_agent or sender._meta.abstract:
        return
    post_delete.connect(
        _deactivate_agent_service_user,
        sender=sender,
        dispatch_uid=f"angee.agents.{sender._meta.label_lower}.service_user.deactivate",
    )


class_prepared.connect(
    _connect_agent_lifecycle,
    dispatch_uid="angee.agents.agent_lifecycle.class_prepared",
)
