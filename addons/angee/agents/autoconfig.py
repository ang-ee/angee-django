"""Settings fragments required by the agents addon."""

from __future__ import annotations

SETTINGS = {
    # The ``InferenceProvider.backend_class`` registry: each key a provider row may
    # name → the dotted path of the ``InferenceBackend`` it resolves to. agents ships
    # only the built-in ``manual`` backend (``ImplClassField`` requires a non-empty
    # registry); a vendor backend addon adds its own impl with a yamlconf dotted key
    # (``"ANGEE_INFERENCE_BACKEND_CLASSES.anthropic": "…"``). See ``ImplClassField``.
    "ANGEE_INFERENCE_BACKEND_CLASSES": {"manual": "angee.agents.backends.ManualInferenceBackend"},
}
"""Django settings contributed when the agents addon is installed."""
