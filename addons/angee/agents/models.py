"""Source models for the agent catalogue.

An :class:`Agent` is a definition the operator later renders into a workspace and
service. It draws on three catalogues this addon also owns: :class:`Skill` rows
discovered from an ``integrate.Source``, :class:`MCPServer`/:class:`MCPTool` rows,
and an :class:`InferenceProvider` integration child with its
:class:`InferenceModel` rows. Templates are agents with
``is_template`` set. This addon keeps definitions only; the operator owns lifecycle.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.db.models.signals import class_prepared, m2m_changed, post_delete
from django.utils import timezone
from rebac import RelationshipTuple, SubjectRef, system_context, to_object_ref
from rebac.relationships import delete_relationships, write_relationships
from rebac.types import RelationshipFilter

from angee.agents.backends import InferenceBackend, InferenceRequest, InferenceResponse
from angee.agents.runtimes import AgentRuntime, operator_secret_ref
from angee.agents.skills import parse_skill_meta
from angee.base.fields import StateField
from angee.base.impl import ImplClassField, ImplDefaultsMixin
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel
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


class InferenceProvider(ImplDefaultsMixin, AngeeModel):
    """An LLM provider account, materialized as an integration child row.

    It draws its API credential from its inherited integration credential and
    resolves its provider-specific :class:`~angee.agents.backends.InferenceBackend`
    from ``backend_class``. Django keeps the catalogue; the backend lists the
    provider's models into :class:`InferenceModel` rows.
    """

    runtime = True
    extends = "integrate.Integration"
    integration_kind_label = "Inference provider"

    backend_class = ImplClassField(
        base_class=InferenceBackend,
        registry_setting="ANGEE_INFERENCE_BACKEND_CLASSES",
        default="manual",
    )
    """Registry key for the inference backend this provider uses."""
    name = models.CharField(max_length=128)
    base_url = models.URLField(blank=True)
    """Base endpoint for OpenAI-compatible providers; blank uses the backend default."""
    config = models.JSONField(default=dict, blank=True)
    """Provider-scoped settings used by inference implementations."""

    objects = AngeeManager()

    class Meta:
        """Django model options for the inference provider child model."""

        abstract = True
        ordering = ("name",)
        rebac_resource_type = "agents/inference_provider"
        rebac_id_attr = "sqid"

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

    def refresh_models(self) -> int:
        """Re-list this provider's models into :class:`InferenceModel` rows."""

        model = apps.get_model("agents", "InferenceModel")
        return int(model.objects.sync_from_provider(self))

    def chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        system: str = "",
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
        options: Mapping[str, Any] | None = None,
    ) -> InferenceResponse:
        """Send one non-streaming chat request through this provider."""

        request = InferenceRequest(
            model=model,
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            options={} if options is None else dict(options),
        )
        return self.backend.chat(request)


class InferenceModelManager(AngeeManager):
    """Manager owning the upsert of model rows from a provider's catalogue."""

    def sync_from_provider(self, provider: Any) -> int:
        """Upsert one row per model the provider advertises (non-destructive).

        Missing handles are left in place, not pruned, so an agent's ``model`` FK is
        never broken by a transient provider response; deprecation is a status edit.
        """

        specs = list(provider.backend.list_models())
        with system_context(reason="agents.inference_model.sync"), transaction.atomic():
            for spec in specs:
                self.update_or_create(provider=provider, name=spec.handle, defaults=spec.upsert_defaults())
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
        rebac_id_attr = "sqid"
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

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
        options: Mapping[str, Any] | None = None,
    ) -> InferenceResponse:
        """Send one non-streaming chat request through this catalogue model."""

        return self.provider.chat(
            model=self.name,
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            options=options,
        )


class SkillManager(AngeeManager):
    """Manager owning the reconcile of skill rows from a skill source."""

    def sync_from_source(self, source: Any) -> int:
        """Walk the source for ``SKILL.md`` and upsert/prune :class:`Skill` rows."""

        vcs_bridge = source.repository.vcs_bridge
        descriptors = vcs_bridge.discover(source, marker="SKILL.md", parse=parse_skill_meta)
        seen: set[Any] = set()
        with system_context(reason="agents.skill.sync"), transaction.atomic():
            for descriptor in descriptors:
                skill, _created = self.update_or_create(
                    source=source,
                    path=str(descriptor.get("path", "")),
                    defaults={
                        "name": str(descriptor.get("name", "")),
                        "description": str(descriptor.get("description", "")),
                        "metadata": dict(descriptor.get("metadata", {})),
                    },
                )
                seen.add(skill.pk)
            self.filter(source=source).exclude(pk__in=seen).delete()
            source.last_synced_at = timezone.now()
            source.save(update_fields=["last_synced_at", "updated_at"])
        return len(descriptors)


class Skill(SqidMixin, AuditMixin, AngeeModel):
    """One skill discovered under an ``integrate.Source`` (``source_kind="skill"``).

    The operator mounts the skill's directory into an agent's workspace; Django keeps
    the inventory only. Discovery reuses the integrate source walk.
    """

    runtime = True
    source_kind = "skill"
    """Binds the ``skill`` source kind to this output model (see ``integrate.Source``)."""

    sqid_prefix = "skl_"
    source = models.ForeignKey("integrate.Source", on_delete=models.CASCADE, related_name="skills")
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
        rebac_id_attr = "sqid"
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
    # Expected to be a non-rotating credential (e.g. a static token). The provisioned
    # bearer is a *frozen* snapshot of ``secret_value()`` (see ``Agent.mcp_secrets``);
    # for a rotating credential (OAuth) a later refresh would drift the live secret from
    # the frozen bearer, so the verifier stops matching and the agent's MCP calls 401
    # until reprovisioned. Constrain to static credentials at the catalogue level.
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
        rebac_id_attr = "sqid"

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


class MCPTool(SqidMixin, AuditMixin, AngeeModel):
    """One tool an MCP server exposes; agents select the tools they may call."""

    runtime = True

    sqid_prefix = "mct_"
    server = models.ForeignKey("agents.MCPServer", on_delete=models.CASCADE, related_name="tools")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    input_schema = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)

    objects = AngeeManager()

    class Meta:
        """Django model options for MCP tools."""

        abstract = True
        ordering = ("server", "name")
        rebac_resource_type = "agents/mcp_tool"
        rebac_id_attr = "sqid"
        constraints = (models.UniqueConstraint(fields=("server", "name"), name="uniq_agents_mcp_tool_name"),)

    def __str__(self) -> str:
        """Return the tool's name."""

        return self.name


class AgentManager(AngeeManager):
    """Manager owning service-user lifecycle for agent principals."""

    def service_username(self, agent: Any) -> str:
        """Return the deterministic username for ``agent``'s service user."""

        return f"agent-{agent.sqid}"

    def sync_service_user(self, agent: Any) -> Any:
        """Create or update ``agent``'s non-login service user.

        The service row is system-owned attribution state, not actor-authored
        profile data, so it is written elevated and keyed only by the agent's
        stable sqid-derived username.
        """

        if agent.pk is None:
            raise ValueError("Agent must be saved before syncing its service user.")
        user_model = get_user_model()
        username = self.service_username(agent)
        defaults = {
            "first_name": agent.name,
            "last_name": "",
            "email": "",
            "kind": "service",
        }
        with system_context(reason="agents.service_user.sync"), transaction.atomic():
            if agent.user_id:
                user = user_model._base_manager.get(pk=agent.user_id)
                changed: set[str] = set()
                for field, value in {"username": username, **defaults}.items():
                    if getattr(user, field) != value:
                        setattr(user, field, value)
                        changed.add(field)
                if changed:
                    user.save(update_fields=changed)
                return user
            user, _created = user_model._base_manager.update_or_create(username=username, defaults=defaults)
            agent.user_id = user.pk
            type(agent)._base_manager.filter(pk=agent.pk).update(user_id=user.pk)
            return user

    def deactivate_service_user(self, agent: Any, *, using: str | None = None) -> None:
        """Deactivate ``agent``'s linked service user, leaving attribution FKs intact."""

        if not agent.user_id:
            return
        user_model = get_user_model()
        manager = user_model._base_manager.db_manager(using) if using else user_model._base_manager
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
    """Non-login service user used for audit/revision attribution by this agent."""
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
        "integrate.Template",
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
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the agent's name."""

        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the agent and sync its service-user label.

        Mirrors ``iam.User.save()``: the row save owns a small derived sync, and
        the manager performs the system-owned dependent write.
        """

        creating = self._state.adding
        update_fields = kwargs.get("update_fields")
        should_check_name = creating or update_fields is None or "name" in _update_field_names(update_fields)
        persisted_name = None
        if not creating and should_check_name:
            persisted_name = type(self)._base_manager.filter(pk=self.pk).values_list("name", flat=True).first()
        with transaction.atomic():
            super().save(*args, **kwargs)
            if creating or (should_check_name and persisted_name != self.name):
                type(self).objects.sync_service_user(self)

    def principal_subject(self) -> SubjectRef:
        """Return this agent's own REBAC subject identity for actions it performs.

        This is distinct from :attr:`owner`: the owner manages the agent definition,
        while the agent subject represents the running agent as an actor.
        """

        ref = to_object_ref(self)
        return SubjectRef.of(ref.resource_type, ref.resource_id)

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
    def mark_provisioning(self) -> None:
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
    def mark_workspace_provisioned(self, *, workspace: str) -> None:
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
    def mark_service_provisioned(self, *, service: str) -> None:
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
    def mark_provisioned(self, *, workspace: str, service: str = "") -> None:
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
    def mark_deprovisioned(self) -> None:
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
    def mark_deprovisioning(self) -> None:
        """Mark the agent as tearing down through the operator teardown flow."""

        self.last_error = ""
        self._transition_fields = {"last_error"}

    def mark_provision_failed(
        self, message: str, *, clear_instances: bool = False, clear_service: bool = False
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
        )

    def provision_workspace_inputs(self) -> dict[str, str]:
        """Resolve the ``agent-default`` workspace template inputs from this agent.

        The structured fields (name, instructions, MCP servers) are the source of
        truth, so they win over any same-named key in ``workspace_inputs`` (which
        carries template-specific extras only). All values are stringified — Copier
        and the daemon take string answers.
        """

        structured = {
            "agent_name": self.name,
            "instructions": self.instructions,
            "mcp_json": json.dumps(self.mcp_config(), separators=(",", ":")),
        }
        merged = {**(self.workspace_inputs or {}), **structured}
        return {key: str(value) for key, value in merged.items()}

    _SERVICE_ENV_INDENT = "      "
    """Indent for spliced ``env:`` lines — must match the service templates' ``env:`` block."""

    def provision_service_inputs(self) -> dict[str, str]:
        """Resolve the structured service-template inputs from this agent.

        Carries the runtime model handle plus the runtime-owned auth env block. The
        secret *value* never appears here — only operator ``${secret.…}`` placeholders;
        the value is synced server-side. The runtime (not the provider) owns how the
        credential becomes env, because the same token feeds different env vars in
        different runtimes. ``service_inputs`` supplies template-specific extras
        (``permission_mode`` etc.) and loses to the structured keys.
        """

        structured: dict[str, str] = {}
        runtime = self.runtime_backend
        model = getattr(self, "model", None)
        if model is not None:
            structured["model"] = runtime.model_handle(model)
        # Advertise auth only when the runtime renders a service and there is a usable secret
        # to sync — otherwise the rendered service would reference a ${secret.<name>} the
        # operator never gets, and a workspace-only runtime has no service container to read
        # it. This must agree with the readiness gate and the secret sync in the provision
        # flow (both also keyed on ``renders_service``).
        if runtime.renders_service and model is not None and self.inference_secret():
            credential = self._inference_credential()
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
            for server, secret_name in self._addressable_mcp_servers()
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

    def mcp_config(self) -> dict[str, Any]:
        """Return the ``.mcp.json`` document for this agent's reachable MCP servers.

        Each server renders its own entry (:meth:`MCPServer.config_entry`); this supplies
        the bearer env var (:meth:`mcp_bearer_env`) for a credentialed server and skips
        servers that aren't addressable. The header expands that env var, which the service
        env sets from the operator secret (:meth:`provision_service_inputs`) — the value
        rides the container env, never the file or the browser. See :meth:`mcp_secrets`.
        """

        servers = {
            server.name: server.config_entry(self.mcp_bearer_env(server) if secret_name else None)
            for server, secret_name in self._addressable_mcp_servers()
        }
        return {"mcpServers": servers}

    def _addressable_mcp_servers(self) -> Iterator[tuple[MCPServer, str]]:
        """Yield ``(server, secret_name)`` for each addressable MCP server, in row order.

        The single owner of "which servers this agent exposes, and the operator secret
        name each credentialed one uses" — so the rendered ``.mcp.json`` header
        (:meth:`mcp_config`) and the value synced under it (:meth:`mcp_secrets`) can't
        drift. ``secret_name`` is ``""`` for an uncredentialed server.
        """

        for server in self.mcp_servers.select_related("credential"):
            if not server.is_addressable:
                continue
            yield server, (self.mcp_secret_name(server) if server.credential_id else "")

    def mcp_secret_name(self, server: MCPServer) -> str:
        """Return the operator secret name holding one MCP server's bearer for this agent.

        Stable and scoped to the agent + server credential: the service env references it
        (``${secret.<name>}`` → the bearer env var) and the provision flow syncs the
        credential value under it (the value never appears in the file or the browser).
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

    def mcp_secrets(self) -> dict[str, str]:
        """Return ``{secret_name: bearer_value}`` for every credentialed MCP server.

        Server-side only — the provision flow pushes these to the operator secret
        store so each server's ``${secret.<name>}`` header resolves in the container.
        """

        secrets: dict[str, str] = {}
        for server, secret_name in self._addressable_mcp_servers():
            if not secret_name:
                continue
            server.credential.ensure_fresh()
            secrets[secret_name] = str(server.credential.secret_value())
        return secrets

    def inference_secret_name(self) -> str:
        """Return the operator secret name holding this agent's inference token.

        Stable and agent-scoped — the provision inputs reference it and the
        (server-side) secret sync writes the credential value under it.
        """

        return f"agent-{self.sqid}-inference"

    def inference_secret(self) -> str:
        """Return the inference credential's secret value (API key or OAuth token), or ``""``.

        Server-side only — the value is pushed to the operator secret store under
        ``inference_secret_name()`` and never returned to the browser. An OAuth token
        near expiry is renewed first (:meth:`integrate.Credential.ensure_fresh`) so the value
        frozen into the provisioned service has its full lifetime ahead of it.
        """

        credential = self._inference_credential()
        if credential is None:
            return ""
        credential.ensure_fresh()
        return str(credential.secret_value())

    def provision_inference_secret(self) -> str:
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

        runtime = self.runtime_backend
        if not runtime.renders_service:
            return ""
        credential = self._inference_credential()
        if credential is None:
            return ""
        credential.ensure_fresh()
        return runtime.auth_secret_value(credential)

    def inference_credential_ready(self) -> bool:
        """Whether this agent can be provisioned with working inference auth.

        A model-less agent needs no inference credential, so it is always ready. A
        model-backed agent is ready only when its credential yields a usable secret — a
        missing or placeholder credential (no key) would render a service that can never
        authenticate, so the provision flow refuses it up front rather than bringing up a
        broken agent. When the runtime renders a service, the runtime must also be able to
        consume that credential kind (e.g. OpenCode cannot use an OAuth token), so an
        unworkable pairing is refused here rather than degrading silently at run time.
        """

        if self.model_id is None:
            return True
        credential = self._inference_credential()
        if credential is None or not self.inference_secret():
            return False
        runtime = self.runtime_backend
        return not runtime.renders_service or runtime.supports_credential(credential)

    def _inference_credential(self) -> Any:
        """Return the ``integrate.Credential`` backing this agent's inference, or ``None``.

        A per-agent ``inference_credential`` override wins (e.g. a connected Anthropic OAuth
        account the user pointed this agent at); otherwise the model's catalogue credential
        (the model→provider→credential chain the catalogue owns), asked of the model rather
        than walked here.
        """

        override = getattr(self, "inference_credential", None)
        if override is not None:
            return override
        model = getattr(self, "model", None)
        return model.credential if model is not None else None


_MCP_AGENT_RELATION = "agent"


def _write_agent_mcp_relation(resource: models.Model, agent: Agent) -> None:
    """Grant one selected agent access to one selected MCP resource."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(resource),
                relation=_MCP_AGENT_RELATION,
                subject=agent.principal_subject(),
            )
        ]
    )


def _delete_agent_mcp_relation(resource: models.Model, agent: Agent) -> None:
    """Revoke one selected agent's access to one selected MCP resource."""

    resource_ref = to_object_ref(resource)
    subject = agent.principal_subject()
    delete_relationships(
        RelationshipFilter(
            resource_type=resource_ref.resource_type,
            resource_id=resource_ref.resource_id,
            relation=_MCP_AGENT_RELATION,
            subject_type=subject.subject_type,
            subject_id=subject.subject_id,
        )
    )


def _sync_agent_mcp_selection(
    *,
    instance: models.Model,
    action: str,
    reverse: bool,
    model: type[models.Model],
    pk_set: set[Any] | None,
    field_name: str,
) -> None:
    """Mirror one Agent↔MCP M2M edit into the non-field REBAC relation."""

    if action not in {"post_add", "post_remove", "pre_clear"}:
        return
    if action == "pre_clear":
        if reverse:
            pairs = [(instance, agent) for agent in getattr(instance, "agents").all()]
        else:
            pairs = [(resource, instance) for resource in getattr(instance, field_name).all()]
    elif reverse:
        pairs = [(instance, agent) for agent in model._base_manager.filter(pk__in=pk_set or ())]
    else:
        pairs = [(resource, instance) for resource in model._base_manager.filter(pk__in=pk_set or ())]

    for resource, agent in pairs:
        if action == "post_add":
            _write_agent_mcp_relation(resource, cast(Agent, agent))
        else:
            _delete_agent_mcp_relation(resource, cast(Agent, agent))


def _sync_agent_mcp_servers(
    sender: type[models.Model],
    instance: models.Model,
    action: str,
    reverse: bool,
    model: type[models.Model],
    pk_set: set[Any] | None,
    **kwargs: Any,
) -> None:
    """Mirror Agent.mcp_servers changes into ``agents/mcp_server#agent`` tuples."""

    del sender, kwargs
    _sync_agent_mcp_selection(
        instance=instance,
        action=action,
        reverse=reverse,
        model=model,
        pk_set=pk_set,
        field_name="mcp_servers",
    )


def _sync_agent_mcp_tools(
    sender: type[models.Model],
    instance: models.Model,
    action: str,
    reverse: bool,
    model: type[models.Model],
    pk_set: set[Any] | None,
    **kwargs: Any,
) -> None:
    """Mirror Agent.mcp_tools changes into ``agents/mcp_tool#agent`` tuples."""

    del sender, kwargs
    _sync_agent_mcp_selection(
        instance=instance,
        action=action,
        reverse=reverse,
        model=model,
        pk_set=pk_set,
        field_name="mcp_tools",
    )


def _deactivate_agent_service_user(
    sender: type[models.Model],
    instance: models.Model,
    using: str,
    **kwargs: Any,
) -> None:
    """Deactivate an agent service user after every delete path Django supports."""

    del sender, kwargs
    type(instance).objects.deactivate_service_user(instance, using=using)


def _connect_agent_mcp_reconcile(sender: type[models.Model], **kwargs: Any) -> None:
    """Connect concrete Agent signal handlers."""

    del kwargs
    try:
        is_agent = issubclass(sender, Agent)
    except TypeError:
        return
    if not is_agent or sender._meta.abstract:
        return
    m2m_changed.connect(
        _sync_agent_mcp_servers,
        sender=getattr(sender, "mcp_servers").through,
        dispatch_uid=f"angee.agents.{sender._meta.label_lower}.mcp_servers.rebac",
    )
    m2m_changed.connect(
        _sync_agent_mcp_tools,
        sender=getattr(sender, "mcp_tools").through,
        dispatch_uid=f"angee.agents.{sender._meta.label_lower}.mcp_tools.rebac",
    )
    post_delete.connect(
        _deactivate_agent_service_user,
        sender=sender,
        dispatch_uid=f"angee.agents.{sender._meta.label_lower}.service_user.deactivate",
    )


class_prepared.connect(
    _connect_agent_mcp_reconcile,
    dispatch_uid="angee.agents.agent_mcp_reconcile.class_prepared",
)
