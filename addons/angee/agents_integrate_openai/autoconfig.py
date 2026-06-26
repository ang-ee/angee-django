"""Composer defaults for the OpenAI inference integration addon."""

SETTINGS = {
    # The addon contributes the provider implementation; an ``InferenceProvider``
    # row selects it with ``backend_class = "openai"``.
    "ANGEE_INFERENCE_BACKEND_CLASSES.openai": ("angee.agents_integrate_openai.backend.OpenAIInferenceBackend"),
}
"""Django settings contributed when the OpenAI inference addon is installed."""
