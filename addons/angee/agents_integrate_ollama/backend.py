"""Ollama specialization of the OpenAI-compatible inference backend."""

from __future__ import annotations

from typing import ClassVar

from angee.agents_integrate_openai.backend import OpenAIInferenceBackend


class OllamaInferenceBackend(OpenAIInferenceBackend):
    """OpenAI-compatible inference served by a local Ollama endpoint."""

    key = "ollama"
    label = "Ollama"
    icon = "ollama"
    defaults = {
        "vendor": "ollama",
        "name": "Ollama",
    }
    default_base_url = "http://localhost:11434/v1"
    requires_credential = False
    default_broker_name = "ollama"
    model_allow_prefixes: ClassVar[tuple[str, ...]] = ()
    model_deny_prefixes: ClassVar[tuple[str, ...]] = ()
    api_key_env: ClassVar[tuple[str, ...]] = ()
