"""Source models for the agent catalogue.

An :class:`Agent` is a definition the operator later renders into a workspace and
service. It draws on three catalogues this addon also owns: :class:`Skill` rows
discovered from an ``integrate.Source``, :class:`MCPServer`/:class:`MCPTool` rows,
and an :class:`InferenceProvider` (an ``integrate.Capability`` over an
``Integration``) with its :class:`InferenceModel` rows. Templates are agents with
``is_template`` set. This addon keeps definitions only; the operator owns lifecycle.
"""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from rebac import system_context
from rebac.managers import RebacManager

from angee.agents.backends import InferenceBackend
from angee.agents.skills import parse_skill_meta
from angee.base.fields import ImplClassField, SqidField, StateField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.integrate.models import Capability


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


class AgentStatus(models.TextChoices):
    """Provisioning state of an agent (set by the operator render pipeline)."""

    DRAFT = "draft", "Draft"
    PROVISIONING = "provisioning", "Provisioning"
    RUNNING = "running", "Running"
    STOPPED = "stopped", "Stopped"
    ERROR = "error", "Error"
    ARCHIVED = "archived", "Archived"


class InferenceProvider(Capability):
    """The inference capability over an ``Integration`` — an LLM provider account.

    A concrete :class:`~angee.integrate.models.Capability`: it draws its API
    credential from ``self.integration.credential`` and resolves a per-row
    :class:`~angee.agents.backends.InferenceBackend` named by ``backend_class``
    (anthropic/openai/…), the same shape as ``storage.Backend``. Django keeps the
    catalogue; the backend lists the provider's models into :class:`InferenceModel`
    rows.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="ipr", min_length=8)
    name = models.CharField(max_length=128)
    base_url = models.URLField(blank=True)
    """Base endpoint for OpenAI-compatible providers; blank uses the backend default."""
    backend_class = ImplClassField(
        base_class=InferenceBackend,
        registry_setting="ANGEE_INFERENCE_BACKEND_CLASSES",
        default="manual",
    )
    """The backend this provider resolves to — an explicit per-row key into
    ``ANGEE_INFERENCE_BACKEND_CLASSES`` (never derived from the vendor). Defaults to the
    built-in ``manual`` backend whose catalogue is hand-curated."""

    objects = RebacManager()

    class Meta:
        """Django model options for the inference provider capability."""

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
        the built-in ``manual`` backend holds no client, and a vendor backend reads the
        live credential off the provider each call, so a rotated key takes effect at
        once and the backend owns any client lifetime it needs.
        """

        field = cast(ImplClassField, type(self)._meta.get_field("backend_class"))
        return cast(InferenceBackend, field.resolve_class(self.backend_class)(self))

    def refresh_models(self) -> int:
        """Re-list this provider's models into :class:`InferenceModel` rows."""

        model = apps.get_model("agents", "InferenceModel")
        return int(model.objects.sync_from_provider(self))

    def service_environment(self) -> dict[str, str]:
        """Return credential-backed environment variables for rendered services."""

        integration = getattr(self, "integration", None)
        if integration is None:
            return {}
        return integration.credential_env_value()


class InferenceModelManager(RebacManager):
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

    sqid = SqidField(real_field_name="id", prefix="imd", min_length=8)
    provider = models.ForeignKey("agents.InferenceProvider", on_delete=models.CASCADE, related_name="models")
    publisher = models.ForeignKey(
        "integrate.Vendor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_models",
    )
    name = models.CharField(max_length=200)
    """The wire identifier the provider expects (e.g. ``claude-opus-4-8``)."""
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
        constraints = (
            models.UniqueConstraint(fields=("provider", "name"), name="uniq_agents_inference_model_name"),
        )

    def __str__(self) -> str:
        """Return the model's display label."""

        return self.display_name or self.name


class SkillManager(RebacManager):
    """Manager owning the reconcile of skill rows from a skill source."""

    def sync_from_source(self, source: Any) -> int:
        """Walk the source for ``SKILL.md`` and upsert/prune :class:`Skill` rows."""

        vcs_integration = source.repository.vcs_integration
        descriptors = vcs_integration.discover(source, marker="SKILL.md", parse=parse_skill_meta)
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

    sqid = SqidField(real_field_name="id", prefix="skl", min_length=8)
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
        constraints = (
            models.UniqueConstraint(fields=("source", "path"), name="uniq_agents_skill_path"),
        )

    def __str__(self) -> str:
        """Return the skill's display label."""

        return self.name or self.path or f"skill:{self.public_id}"


class MCPServer(SqidMixin, AuditMixin, AngeeModel):
    """An MCP server an agent can reach — internal to the platform or external.

    An external server authenticates with an ``iam.Credential``; the operator renders
    the selected servers and their authorized tools into an agent's MCP config.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="mcp", min_length=8)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    placement = StateField(choices_enum=MCPPlacement, default=MCPPlacement.EXTERNAL)
    transport = StateField(choices_enum=MCPTransport, default=MCPTransport.HTTP)
    url = models.URLField(blank=True)
    credential = models.ForeignKey(
        "iam.Credential",
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


class MCPTool(SqidMixin, AuditMixin, AngeeModel):
    """One tool an MCP server exposes; agents select the tools they may call."""

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="mct", min_length=8)
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
        constraints = (
            models.UniqueConstraint(fields=("server", "name"), name="uniq_agents_mcp_tool_name"),
        )

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

    sqid = SqidField(real_field_name="id", prefix="agt", min_length=8)
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
    status = StateField(choices_enum=AgentStatus, default=AgentStatus.DRAFT)
    last_error = models.TextField(blank=True)

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

    def service_environment(self) -> dict[str, str]:
        """Return model-provider environment variables for rendered services."""

        model = getattr(self, "model", None)
        provider = getattr(model, "provider", None) if model is not None else None
        if provider is None:
            return {}
        return cast(dict[str, str], provider.service_environment())
