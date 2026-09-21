"""Inference backend protocol and the bundled built-in backend.

An inference provider row selects one backend via ``backend_class``. Vendor backend
addons (openai, anthropic, …) wrap official SDK clients and list models live; the
built-in manual backend has no client and leaves the catalogue hand-curated.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast

from asgiref.sync import async_to_sync
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from angee.base.db import get_write_alias, related_on
from angee.base.impl import ImplBase
from angee.integrate.connect import enabled_oauth_client_from_hint


@dataclass(frozen=True, slots=True)
class InferenceModelSpec:
    """One model a backend advertises, in the shape ``InferenceModel`` rows carry.

    Empty/zero optional fields let the upsert preserve richer hand-entered or seeded
    metadata instead of overwriting it on a live refresh.
    """

    handle: str
    display_name: str = ""
    description: str = ""
    model_use: str = "chat"
    context_window: int = 0
    max_output_tokens: int = 0
    capabilities: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)

    def upsert_defaults(self) -> dict[str, Any]:
        """Return the ``InferenceModel`` upsert defaults this spec contributes.

        Empty/zero optional fields are omitted so a live refresh preserves richer
        hand-entered or seeded metadata instead of overwriting it.
        """

        defaults: dict[str, Any] = {"display_name": self.display_name or self.handle}
        if self.model_use:
            defaults["model_use"] = self.model_use
        if self.description:
            defaults["description"] = self.description
        if self.context_window:
            defaults["context_window"] = self.context_window
        if self.max_output_tokens:
            defaults["max_output_tokens"] = self.max_output_tokens
        if self.capabilities:
            defaults["capabilities"] = self.capabilities
        if self.config:
            defaults["config"] = self.config
        return defaults


class InferenceBackend(ImplBase):
    """The strategy one inference provider resolves to.

    Subclasses read the API credential, endpoint, and config directly from the
    provider row that selected them.
    """

    category = "inference"
    label = "Inference"
    icon = "sparkles"
    # Vendor-neutral: a base backend never pins a product OAuth client. Provider
    # connect is available only when a vendor backend addon sets this slug.
    oauth_client: ClassVar[str] = ""
    # The vendor SDK's own API-key env var name(s) — a provider-native fact an agent
    # runtime composes into the container env (see ``angee.agents.runtimes``). An
    # empty tuple means the backend declares no static-key runtime env.
    api_key_env: ClassVar[tuple[str, ...]] = ()
    # Whether callers must attach an inference credential. This belongs to the
    # contract because every backend consumer must make the same typed decision.
    requires_credential: ClassVar[bool] = True
    defaults = {
        "name": "Manual",
        "status": "draft",
    }

    def __init__(self, provider: Any) -> None:
        """Bind this backend to its provider row."""

        self.provider = provider
        self.using: str | None = None

    def connect_oauth_client(self, owner_label: str, *, using: str | None = None) -> Any:
        """Return the enabled OAuth client this backend connects its provider through.

        The backend's ``oauth_client`` hint is the only source; an empty hint is not
        connectable. The bound provider's vendor slug feeds the ``{vendor}`` template.
        """

        using = get_write_alias(type(self.provider), using=using, instance=self.provider)
        vendor = related_on(self.provider, "vendor", using=using, required=False)
        vendor_slug = str(getattr(vendor, "slug", "") or "")
        return enabled_oauth_client_from_hint(
            self.oauth_client,
            owner_label=owner_label,
            reason="agents.graphql.connect_inference_provider.oauth_client",
            vendor_slug=vendor_slug,
            using=using,
        )

    def list_models(self) -> Sequence[InferenceModelSpec]:
        """Return the provider's advertised models for catalogue upsert."""

        raise NotImplementedError("InferenceBackend subclasses must implement list_models().")

    def model(self, handle: str, *, credential: Any | None = None) -> AbstractAsyncContextManager[Model]:
        """Bind a native model for one invocation, closing owned clients on exit.

        Resolve credentials synchronously before entering the returned context;
        Django credential refresh must not run inside the model's async loop.
        """

        raise NotImplementedError(f"{self.label} does not support in-process inference.")

    def request_settings(self, model_settings: ModelSettings | None) -> ModelSettings | None:
        """Reject transport overrides and return safe direct-request settings.

        Vendor backends may admit named transport extensions by removing and
        validating them before composing this owner.
        """

        result = dict(model_settings or {})
        forbidden = {"extra_body", "extra_headers", "extra_query"} & result.keys()
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ValueError(f"Inference request settings cannot override provider transport: {names}.")
        return cast(ModelSettings, result)

    def chat(
        self,
        handle: str,
        messages: Sequence[ModelMessage],
        *,
        model_settings: ModelSettings | None = None,
        model_request_parameters: ModelRequestParameters | None = None,
        credential: Any | None = None,
    ) -> ModelResponse:
        """Make one native request; tools are declared but never executed here."""

        request_settings = self.request_settings(model_settings)
        binding = self.model(handle, credential=credential)

        async def request() -> ModelResponse:
            async with binding as model:
                return await model_request(
                    model,
                    messages,
                    model_settings=request_settings,
                    model_request_parameters=model_request_parameters,
                )

        return async_to_sync(request)()


class ManualInferenceBackend(InferenceBackend):
    """Built-in backend with no client — its catalogue is curated by hand.

    The default registry entry: a provider on this backend lists no models to sync,
    so its :class:`InferenceModel` rows are entered through the console. A vendor
    backend addon supplies the live-listing alternative.
    """

    key = "manual"
    label = "Manual inference"

    def list_models(self) -> Sequence[InferenceModelSpec]:
        """Return no models; the catalogue is maintained by hand on this backend."""

        return ()


def is_retryable_provider_error(error: Exception) -> bool:
    """Return whether an SDK/provider exception represents a transient failure."""

    status = getattr(error, "status_code", None)
    if status in {408, 409, 425, 429, 500, 502, 503, 504, 529}:
        return True
    error_type = type(error).__name__.lower()
    message = str(error).lower()
    retryable_terms = (
        "ratelimit",
        "rate_limit",
        "rate limit",
        "overload",
        "overloaded",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "try again",
    )
    return any(term in error_type or term in message for term in retryable_terms)
