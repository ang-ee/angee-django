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
from django.db import models, transaction
from django.utils import timezone
from rebac import system_context
from rebac.managers import RebacManager

from angee.agents.backends import InferenceBackend, InferenceRequest, InferenceResponse
from angee.agents.skills import parse_skill_meta
from angee.base.fields import ImplClassField, StateField
from angee.base.impl import ImplDefaultsMixin
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel
from angee.integrate.credentials import CredentialKind


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

        field = cast(ImplClassField, type(self)._meta.get_field("backend_class"))
        backend_class = cast(type[InferenceBackend], field.resolve_class(self.backend_class))
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


class SkillManager(RebacManager):
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

    objects = RebacManager()

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

    objects = RebacManager()

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


class Agent(SqidMixin, AuditMixin, AngeeModel):
    """An agent definition (or, when ``is_template``, an agent template).

    The operator renders an agent into a workspace from ``workspace_template`` and a
    service from ``service_template`` (both ``integrate.Template`` rows), writing the
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
    service_template = models.ForeignKey(
        "integrate.Template",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
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

    objects = RebacManager()

    class Meta:
        """Django model options for agents."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "agents/agent"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the agent's name."""

        return self.name

    @property
    def can_provision(self) -> bool:
        """Whether the provision action may start from the current lifecycle facts."""

        return str(self.runtime_status) == RuntimeStatus.ERROR.value or str(self.lifecycle) in {
            AgentLifecycle.DRAFT.value,
            AgentLifecycle.DEPROVISIONED.value,
        }

    @property
    def can_deprovision(self) -> bool:
        """Whether the teardown action is meaningful for the current rendered state."""

        return str(self.lifecycle) in {
            AgentLifecycle.PROVISIONING.value,
            AgentLifecycle.READY.value,
            AgentLifecycle.DEPROVISIONING.value,
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

    def mark_provisioning(self) -> None:
        """Enter the provision flow: lifecycle provisioning, run state reset to stopped."""

        self.lifecycle = cast(AgentLifecycle, AgentLifecycle.PROVISIONING)
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.STOPPED)
        self.last_error = ""
        self.save(update_fields=["lifecycle", "runtime_status", "last_error", "updated_at"])

    def mark_workspace_provisioned(self, *, workspace: str) -> None:
        """Record the workspace as soon as the operator creates it."""

        self.workspace = workspace
        self.lifecycle = cast(AgentLifecycle, AgentLifecycle.PROVISIONING)
        self.last_error = ""
        self.save(update_fields=["workspace", "lifecycle", "last_error", "updated_at"])

    def mark_service_provisioned(self, *, service: str) -> None:
        """Record the service as soon as the operator creates it."""

        self.service = service
        self.lifecycle = cast(AgentLifecycle, AgentLifecycle.PROVISIONING)
        self.last_error = ""
        self.save(update_fields=["service", "lifecycle", "last_error", "updated_at"])

    def mark_provisioned(self, *, workspace: str, service: str = "") -> None:
        """Record the operator instance the provision flow rendered for this agent.

        The daemon owns the workspace/service lifecycle; the server-side provision
        flow renders them and calls this to persist the resulting instance names, mark
        the lifecycle ``READY`` and the run state ``RUNNING``. Clears any prior error.
        ``service`` is optional — a workspace-only agent renders no service.
        """

        self.workspace = workspace
        self.service = service
        self.lifecycle = cast(AgentLifecycle, AgentLifecycle.READY)
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.RUNNING)
        self.last_error = ""
        self.save(update_fields=["workspace", "service", "lifecycle", "runtime_status", "last_error", "updated_at"])

    def mark_deprovisioned(self) -> None:
        """Clear the operator instance after teardown: lifecycle deprovisioned, run state stopped."""

        self.workspace = ""
        self.service = ""
        self.lifecycle = cast(AgentLifecycle, AgentLifecycle.DEPROVISIONED)
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.STOPPED)
        self.last_error = ""
        self.save(update_fields=["workspace", "service", "lifecycle", "runtime_status", "last_error", "updated_at"])

    def mark_deprovisioning(self) -> None:
        """Mark the agent as tearing down through the operator teardown flow."""

        self.lifecycle = cast(AgentLifecycle, AgentLifecycle.DEPROVISIONING)
        self.last_error = ""
        self.save(update_fields=["lifecycle", "last_error", "updated_at"])

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
        the persisted names never point at a torn-down instance.
        """

        update_fields = ["lifecycle", "runtime_status", "last_error", "updated_at"]
        if clear_instances:
            self.workspace = ""
            self.service = ""
            update_fields = ["workspace", "service", *update_fields]
        elif clear_service:
            self.service = ""
            update_fields = ["service", *update_fields]
        self.lifecycle = cast(AgentLifecycle, AgentLifecycle.READY if self.workspace else AgentLifecycle.DRAFT)
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.ERROR)
        self.last_error = message[:2000]
        self.save(update_fields=update_fields)

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

    def provision_service_inputs(self) -> dict[str, str]:
        """Resolve the structured service-template inputs from this agent.

        Carries the model handle and the credential-driven auth selection (prefer
        OAuth: an OAuth credential renders the runtime's OAuth env var, a static key
        the API-key env var — the service template owns the name↔kind map). The
        secret *value* never appears here — only ``secret_name``; the operator-held
        value is synced server-side. ``service_inputs`` supplies template-specific
        extras (``permission_mode``, ``provider``) and loses to the structured keys.
        """

        structured: dict[str, str] = {}
        model = getattr(self, "model", None)
        if model is not None:
            structured["model"] = self.service_model_handle()
        # Advertise auth only when there is a usable secret to sync — otherwise the
        # rendered service would reference a ${secret.<name>} the operator never gets
        # (this must agree with the secret sync in the provision flow).
        if self.inference_secret():
            credential = self._inference_credential()
            structured["auth_mode"] = "oauth" if credential.kind == CredentialKind.OAUTH else "api_key"
            structured["secret_name"] = self.inference_secret_name()
        # The MCP bearers ride the container env too: one ``${secret.<name>}`` env line per
        # credentialed server (the operator resolves it), which the service template renders
        # under the service env and ``.mcp.json`` reads via ``${ANGEE_MCP_BEARER_<…>}``.
        # The provision answer channel is string-only — every input is ``str()``-ed at the
        # return below and the daemon takes string answers — so this renders the per-server
        # env lines as text here rather than passing a structured list for the template to
        # loop; the template splices them verbatim with ``| safe`` (their indentation must
        # match its ``env:`` block).
        mcp_env = "\n".join(
            f'      {self.mcp_bearer_env(server)}: "${{secret.{secret_name}}}"'
            for server, secret_name in self._addressable_mcp_servers()
            if secret_name
        )
        if mcp_env:
            structured["mcp_env"] = mcp_env
        merged = {**(self.service_inputs or {}), **structured}
        return {key: str(value) for key, value in merged.items()}

    def service_model_handle(self) -> str:
        """Return the selected model handle in the current service runtime's convention."""

        model = getattr(self, "model", None)
        if model is None:
            return ""
        service_template = getattr(self, "service_template", None)
        if getattr(service_template, "name", "") == "claude-code":
            return str(getattr(model, "provider_model_name", "") or model.name)
        return str(model.name)

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

    def inference_credential_ready(self) -> bool:
        """Whether this agent can be provisioned with working inference auth.

        A model-less agent needs no inference credential, so it is always ready. A
        model-backed agent is ready only when its credential yields a usable secret — a
        missing or placeholder credential (no key) would render a service that can never
        authenticate, so the provision flow refuses it up front rather than bringing up a
        broken agent. Connecting a real credential (e.g. an Anthropic OAuth account) makes
        it ready.
        """

        if self.model_id is None:
            return True
        return bool(self.inference_secret())

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
