"""Inference backend protocol and the bundled built-in backend.

A backend is the per-provider strategy an ``InferenceProvider`` row names by a key
in ``ANGEE_INFERENCE_BACKEND_CLASSES`` (mirrors ``storage.Backend`` /
``ANGEE_STORAGE_BACKEND_CLASSES``). A vendor backend (openai, anthropic, …) wraps an
HTTP client and lists the provider's models live; it ships in its own addon and
registers its key, the way a storage backend addon adds ``s3``. The bundled
:class:`ManualInferenceBackend` is built in and uses no client — its catalogue is
curated by hand. This module stays ORM-free; the backend reads its credential and
endpoint from the provider it is bound to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


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


class InferenceBackend:
    """The strategy one inference provider resolves to, bound to its capability row.

    Subclasses read the API credential from ``provider.integration.credential`` and
    the endpoint from ``provider.base_url``, and list the provider's catalogue.
    """

    def __init__(self, provider: Any) -> None:
        """Bind the backend to its ``InferenceProvider`` (for credential and endpoint)."""

        self.provider = provider

    def list_models(self) -> Sequence[InferenceModelSpec]:
        """Return the provider's advertised models for catalogue upsert."""

        raise NotImplementedError("InferenceBackend subclasses must implement list_models().")


class ManualInferenceBackend(InferenceBackend):
    """Built-in backend with no client — its catalogue is curated by hand.

    The default registry entry: a provider on this backend lists no models to sync,
    so its :class:`InferenceModel` rows are entered through the console. A vendor
    backend addon supplies the live-listing alternative.
    """

    def list_models(self) -> Sequence[InferenceModelSpec]:
        """Return no models; the catalogue is maintained by hand on this backend."""

        return ()
