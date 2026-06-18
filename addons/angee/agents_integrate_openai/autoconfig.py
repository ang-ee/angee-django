"""Composer defaults for the OpenAI inference integration addon."""

SETTINGS = {
    # The addon contributes the provider implementation; an ``Integration`` row
    # selects it with ``impl_class = "openai"``.
    "ANGEE_INTEGRATION_IMPLS.openai": (
        "angee.agents_integrate_openai.backend.OpenAIInferenceBackend"
    ),
}
"""Django settings contributed when the OpenAI inference addon is installed."""
