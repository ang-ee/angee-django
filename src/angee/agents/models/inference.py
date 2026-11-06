"""Agent inference owners and their native model contracts."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
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
from rebac import system_context

from angee.agents.backends import InferenceBackend
from angee.agents.models.choices import InferenceModelStatus, InferenceModelUse
from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.implementations import ImplDefaultsMixin
from angee.iam.models.base import ProtectedDataModel, ProtectedModel
from angee.integrate.models import IntegrationCreateMode
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.resources.mixins import ResourceLoadMixin


class InferenceDeploymentIdentity(TypedDict):
    """Non-secret exact identity of one callable inference deployment."""

    model: str
    provider: str
    backend: str
    native_model: str
    endpoint: str


class InferenceModelUnavailable(ValueError):
    """A selected catalogue model cannot serve the requested inference role."""


INFERENCE_OUTPUT_TOOL = "inference_output"


InferenceOutputSchema = Mapping[str, Any] | Sequence[ToolDefinition]


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
    """Project native usage into shared token, request and tool-call counters."""

    values = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "tokens": usage.total_tokens,
        "requests": usage.requests,
        "tool_calls": usage.tool_calls if isinstance(usage, RunUsage) else 0,
    }
    return {key: int(value) for key, value in values.items() if value}


class InferenceProvider(ImplDefaultsMixin, ProtectedModel, inherits="integrate.Integration"):
    """An LLM provider account, materialized as an integration child row.

    It draws its API credential from its inherited integration credential and
    resolves its provider-specific :class:`~angee.agents.backends.InferenceBackend`
    from ``backend_class``. Django keeps the catalogue; the backend lists the
    provider's models into :class:`InferenceModel` rows.
    """

    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.FORM

    backend_class = ImplClassField(
        InferenceBackend,
        default="manual",
        create_only=True,
    )
    """Registry key for the inference backend this provider uses."""
    name = models.CharField(max_length=128, blank=True)
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

        backend_class = self.get_backend_class_impl()
        return backend_class(self)

    def test_connection(self) -> str:
        """Exercise the selected backend's connection (the Integration test contract)."""

        return self.backend.test_connection()

    def refresh_models(self) -> int:
        """Re-list this provider's models into :class:`InferenceModel` rows."""

        model = apps.get_model("agents", "InferenceModel")
        return int(model.objects.sync_from_provider(self))

    def chat(
        self,
        *,
        model: str,
        messages: Sequence[ModelMessage],
        model_settings: ModelSettings | None = None,
        model_request_parameters: ModelRequestParameters | None = None,
        credential: Any | None = None,
    ) -> ModelResponse:
        """Send one request using Pydantic AI's native message/settings contract."""

        backend = self.backend
        return backend.chat(
            model,
            messages,
            model_settings=model_settings,
            model_request_parameters=model_request_parameters,
            credential=credential,
        )


class InferenceModelManager[ModelT: InferenceModel](AngeeHistoryManager[ModelT]):
    """Manager owning catalogue sync and selection of authorized deployments."""

    def usable(self, actor: Any, role: str, *, uses: Collection[InferenceModelUse]) -> tuple[ModelT, ...]:
        """Return readable, approved, callable models with catalogue defaults first."""

        models = []
        for model in self.with_actor(actor).select_related("provider").order_by("-is_default", "pk"):
            try:
                model.require_usable(actor, role, uses=uses)
            except PermissionDenied, InferenceModelUnavailable:
                continue
            models.append(model)
        return tuple(models)

    def sync_from_provider(self, provider: Any) -> int:
        """Upsert one row per model the provider advertises (non-destructive).

        Missing handles are left in place, not pruned, so an agent's ``model`` FK is
        never broken by a transient provider response; deprecation is a status edit.
        """

        backend = provider.backend
        specs = list(backend.list_models())
        with system_context(reason="agents.inference_model.sync"), transaction.atomic():
            for spec in specs:
                self.update_or_create(provider_id=provider.pk, name=spec.handle, defaults=spec.upsert_defaults())
        return len(specs)


class InferenceModel(ResourceLoadMixin, AuditMixin, ProtectedDataModel, concrete=True):
    """One model in a provider's catalogue, agents bind to by FK.

    ``publisher`` is the model's maker, reusing the ``integrate.Vendor`` catalogue
    (which need not be the serving provider's vendor — an OpenAI-compatible router can
    serve another maker's model).
    """

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

    def bind(self, *, credential: Any | None = None) -> AbstractAsyncContextManager[Model]:
        """Bind this catalogue model's native adapter and own its client lifetime."""

        provider: Any = self.provider
        backend = provider.backend
        return backend.model(self.provider_model_name, credential=credential)

    def is_transient_error(self, error: Exception) -> bool:
        """Classify a failure through this model's native provider backend."""

        with system_context(reason="agents.inference_model.error"):
            provider: Any = self.provider
            return bool(provider.backend.is_transient_error(error))

    def request_error_types(self) -> tuple[type[BaseException], ...]:
        """Use the selected backend's native request exception types."""
        with system_context(reason="agents.inference_model.error_types"):
            provider: Any = self.provider
            return provider.backend.request_error_types()

    def deployment_identity(self) -> InferenceDeploymentIdentity:
        """Return the non-secret endpoint binding used by role approval policy."""

        provider: Any = self.provider
        backend = provider.backend
        return {
            "model": str(self.sqid),
            "provider": str(provider.sqid),
            "backend": str(provider.backend_class),
            "native_model": str(self.provider_model_name),
            "endpoint": backend.endpoint,
        }

    def require_approved(self, role: str) -> None:
        """Enforce this deployment's exact identity in the configured role allowlist.

        An absent policy leaves the catalogue unrestricted. A configured policy
        fails closed for missing roles, malformed entries and identity changes.
        Denial raises ``PermissionDenied``; malformed policy raises ``ValueError``.
        """

        policy = getattr(settings, "ANGEE_INFERENCE_APPROVED_DEPLOYMENTS", None)
        if policy is None:
            return
        if not isinstance(policy, Mapping):
            raise ValueError("The inference deployment approval policy is invalid.")
        approved = policy.get(role, [])
        if not isinstance(approved, (list, tuple)) or not all(isinstance(item, Mapping) for item in approved):
            raise ValueError(f"The inference {role} deployment approval policy is invalid.")
        identity = self.deployment_identity()
        if not any(dict(item) == identity for item in approved):
            raise PermissionDenied(f"The configured {role} model deployment is not approved.")

    def require_capability(self, uses: Collection[InferenceModelUse]) -> None:
        """Require a callable lifecycle and one of the declared model uses."""

        if self.status in {InferenceModelStatus.DEPRECATED, InferenceModelStatus.RETIRED}:
            raise InferenceModelUnavailable("Select an available inference model.")
        if self.model_use not in uses:
            raise InferenceModelUnavailable(
                f"Inference requires a model with one of these uses: {', '.join(sorted(uses))}."
            )

    def require_usable(
        self,
        actor: Any,
        role: str,
        *,
        uses: Collection[InferenceModelUse],
    ) -> None:
        """Require actor read access, role approval and a declared model capability."""

        if not self.with_actor(actor).has_access("read"):
            raise PermissionDenied("You cannot read the configured inference model.")
        self.require_approved(role)
        self.require_capability(uses)

    def chat(
        self,
        messages: Sequence[ModelMessage],
        *,
        model_settings: ModelSettings | None = None,
        model_request_parameters: ModelRequestParameters | None = None,
        credential: Any | None = None,
    ) -> ModelResponse:
        """Make one native request using this catalogue model's provider handle."""

        provider: Any = self.provider
        return provider.chat(
            model=self.provider_model_name,
            messages=messages,
            model_settings=model_settings,
            model_request_parameters=model_request_parameters,
            credential=credential,
        )

    def infer(
        self,
        messages: Sequence[ModelMessage],
        *,
        output_schema: InferenceOutputSchema | None = None,
        images: Sequence[BinaryContent] = (),
        settings: ModelSettings | None = None,
        credential: Any | None = None,
    ) -> InferenceResult:
        """Make one request and expose native response, usage and structured output.

        Images stay as pydantic-ai :class:`BinaryContent` values. They are appended
        as one user message so provider adapters retain ownership of their wire
        representation. A sequence passed as ``output_schema`` is interpreted as
        native function tools; no tool is executed by this one-shot call.
        """

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
        )
        usage = normalize_inference_usage(response.usage)
        try:
            output = decode_inference_output(response) if isinstance(output_schema, Mapping) else None
        except ValueError as error:
            raise InferenceOutputError(str(error), response=response, usage=usage) from error
        return InferenceResult(response=response, usage=usage, output=output)
