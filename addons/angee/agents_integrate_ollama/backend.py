"""Ollama specialization of the OpenAI-compatible inference backend."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from angee.agents_integrate_openai.backend import OpenAIInferenceBackend

logger = logging.getLogger(__name__)


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

    def _discovered_model_use(self) -> str:
        """Leave modality to curated rows because Ollama's list omits it."""

        return ""

    def _model_discovery(self, client: Any, model_id: str) -> dict[str, Any]:
        """Read optional architecture facts from Ollama's native show endpoint."""

        try:
            shown = client.with_options(max_retries=0).post(
                "../api/show",
                cast_to=dict[str, Any],
                body={"model": model_id, "verbose": False},
            )
        except Exception as exc:
            logger.warning("Could not read Ollama metadata for %s: %s", model_id, exc)
            return {}
        if not isinstance(shown, Mapping):
            return {}
        context_window = self._architecture_context_window(shown)
        config: dict[str, Any] = {}
        num_ctx = self._configured_num_ctx(shown.get("parameters"))
        if num_ctx:
            config["ollama_num_ctx"] = num_ctx
        return {"context_window": context_window, "config": config}

    @staticmethod
    def _architecture_context_window(shown: Mapping[str, Any]) -> int:
        """Return Ollama's architecture context length when it is unambiguous."""

        model_info = shown.get("model_info")
        if not isinstance(model_info, Mapping):
            return 0
        details = shown.get("details")
        family = str(details.get("family", "") or "").strip() if isinstance(details, Mapping) else ""
        preferred = model_info.get(f"{family}.context_length") if family else None
        if type(preferred) is int and preferred > 0:
            return preferred
        candidates = [
            value
            for key, value in model_info.items()
            if str(key).endswith(".context_length") and type(value) is int and value > 0
        ]
        return candidates[0] if len(candidates) == 1 else 0

    @staticmethod
    def _configured_num_ctx(parameters: Any) -> int:
        """Return an explicit positive ``num_ctx`` Modelfile parameter."""

        if not isinstance(parameters, str):
            return 0
        for line in parameters.splitlines():
            key, separator, value = line.strip().partition(" ")
            if key != "num_ctx" or not separator:
                continue
            try:
                parsed = int(value.strip())
            except ValueError:
                return 0
            return parsed if parsed > 0 else 0
        return 0

    def _build_model(self, handle: str, client: Any) -> OpenAIChatModel:
        """Declare Ollama's OpenAI-compatible native JSON-schema envelope."""

        return OpenAIChatModel(
            handle,
            provider=OpenAIProvider(openai_client=client),
            profile=OpenAIModelProfile(
                supports_thinking=True,
                supports_json_schema_output=True,
                default_structured_output_mode="native",
                openai_chat_supports_max_completion_tokens=(
                    self._max_tokens_param() == "max_completion_tokens"
                ),
            ),
        )
