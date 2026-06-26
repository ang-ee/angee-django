"""Settings fragments required by the Anthropic inference addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute Anthropic into the provider backend registry. An
    # ``InferenceProvider`` row selects it with ``backend_class = "anthropic"``.
    "ANGEE_INFERENCE_BACKEND_CLASSES.anthropic": ("angee.agents_integrate_anthropic.backend.AnthropicInferenceBackend"),
}
"""Django settings contributed when the Anthropic inference addon is installed."""
