"""Settings fragments required by the Anthropic inference addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.anthropic": "angee.agents_integrate_anthropic.oauth.AnthropicOAuth",
    # Contribute Anthropic into the provider backend registry. An
    # ``InferenceProvider`` row selects it with ``backend_class = "anthropic"``.
    "ANGEE_INFERENCE_BACKEND_CLASSES.anthropic": ("angee.agents_integrate_anthropic.backend.AnthropicInferenceBackend"),
}
"""Django settings contributed when the Anthropic inference addon is installed."""
