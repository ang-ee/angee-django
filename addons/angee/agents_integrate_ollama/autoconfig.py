"""Composer defaults for the Ollama inference integration addon."""

SETTINGS = {
    # The addon contributes the provider implementation; an ``InferenceProvider``
    # row selects it with ``backend_class = "ollama"``.
    "ANGEE_INFERENCE_BACKEND_CLASSES.ollama": (
        "angee.agents_integrate_ollama.backend.OllamaInferenceBackend"
    ),
}
"""Django settings contributed when the Ollama inference addon is installed."""
