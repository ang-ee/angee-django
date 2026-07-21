"""Inference backend protocol and the bundled built-in backend.

An inference provider row selects one backend via ``backend_class``. Vendor backend
addons (openai, anthropic, …) wrap official SDK clients and list models live; the
built-in manual backend has no client and leaves the catalogue hand-curated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from angee.base.impl import ImplBase
from angee.integrate.connect import enabled_oauth_client_from_hint


class ChatAPI(StrEnum):
    """The chat protocol an inference backend speaks to its vendor.

    A vendor-neutral fact each backend *declares*, so a consumer that must pick a
    protocol adapter reads the declaration instead of switching on vendor identity.
    That is what lets an OpenAI-compatible backend (Ollama, a local router, a
    hosted gateway) inherit the right adapter from its base class without the
    consumer growing a branch per vendor.

    It is deliberately coarser than a vendor: many vendors speak ``OPENAI_CHAT``.
    A new protocol adds a member here and one builder in the consuming runtime;
    a new vendor on an existing protocol adds neither.
    """

    ANTHROPIC_MESSAGES = "anthropic_messages"
    OPENAI_CHAT = "openai_chat"


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

        defaults: dict[str, Any] = {"display_name": self.display_name or self.handle, "model_use": self.model_use}
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


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """Provider-neutral request for one non-streaming chat completion."""

    model: str
    messages: Sequence[Mapping[str, Any]]
    system: str = ""
    max_tokens: int = 1024
    temperature: float | None = None
    tools: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    """Provider-neutral response returned by a backend chat call."""

    text: str
    content: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class InferenceBackend(ImplBase):
    """The strategy one inference provider resolves to.

    Subclasses read the API credential, endpoint, and config directly from the
    provider row that selected them.
    """

    category = "inference"
    label = "Inference"
    icon = "sparkles"
    # The chat protocol this backend speaks, for consumers that must bind a protocol
    # adapter to it (the in-process pydantic-ai runtime is the one today). Empty means
    # the backend offers no in-process adapter — the built-in manual backend has no
    # client at all. Subclasses inherit it, so an OpenAI-compatible vendor declares
    # nothing here.
    chat_api: ClassVar[str] = ""
    # Vendor-neutral: a base backend never pins a product OAuth client. Provider
    # connect is available only when a vendor backend addon sets this slug.
    oauth_client: ClassVar[str] = ""
    # The vendor SDK's own API-key env var name(s) — a provider-native fact an agent
    # runtime composes into the container env (see ``angee.agents.runtimes``). An
    # empty tuple means the backend declares no static-key runtime env.
    api_key_env: ClassVar[tuple[str, ...]] = ()
    defaults = {
        "name": "Manual",
        "status": "draft",
    }

    def __init__(self, provider: Any) -> None:
        """Bind this backend to its provider row."""

        self.provider = provider

    def connect_oauth_client(self, owner_label: str) -> Any:
        """Return the enabled OAuth client this backend connects its provider through.

        The backend's ``oauth_client`` hint is the only source; an empty hint is not
        connectable. The bound provider's vendor slug feeds the ``{vendor}`` template.
        """

        vendor_slug = str(getattr(getattr(self.provider, "vendor", None), "slug", "") or "")
        return enabled_oauth_client_from_hint(
            self.oauth_client,
            owner_label=owner_label,
            reason="agents.graphql.connect_inference_provider.oauth_client",
            vendor_slug=vendor_slug,
        )

    def system_preamble(self, credential: Any | None = None) -> str:
        """Return the vendor-required system-prompt opening for ``credential``.

        Most (vendor × credential-kind) pairs need none. Anthropic's OAuth
        (Personal Plans) edge refuses requests whose system prompt does not
        open with the Claude Code identity line; that backend overrides this.
        Callers prepend a non-empty preamble ahead of their own instructions.
        ``credential`` defaults to the bound provider's.
        """

        del credential
        return ""

    def list_models(self) -> Sequence[InferenceModelSpec]:
        """Return the provider's advertised models for catalogue upsert."""

        raise NotImplementedError("InferenceBackend subclasses must implement list_models().")

    def chat(self, request: InferenceRequest) -> InferenceResponse:
        """Send one non-streaming chat request through this provider."""

        del request
        raise NotImplementedError("InferenceBackend subclasses must implement chat().")


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
