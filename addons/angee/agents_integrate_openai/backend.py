"""OpenAI SDK implementation of the agents inference backend."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from angee.agents.backends import InferenceModelSpec
from angee.agents.sdk_backends import SDKInferenceBackend

DEFAULT_BROKER_NAME = "openai"
_MAX_TOKEN_FIELDS = frozenset({"max_tokens", "max_completion_tokens"})


class OpenAIInferenceBackend(SDKInferenceBackend):
    """Inference backend backed by OpenAI's official Python SDK."""

    key = "openai"
    label = "OpenAI"
    icon = "openai"
    api_key_env: ClassVar[tuple[str, ...]] = ("OPENAI_API_KEY",)
    defaults = {
        "vendor": "openai",
        "name": "OpenAI",
    }
    default_broker_name = DEFAULT_BROKER_NAME
    client_class_path = "openai.OpenAI"
    async_client_class_path = "openai.AsyncOpenAI"
    # Empty is the explicit allow-all sentinel; deny prefixes are still evaluated first.
    model_allow_prefixes: ClassVar[tuple[str, ...]] = ("gpt-", "chatgpt-", "o1", "o3", "o4")
    model_deny_prefixes: ClassVar[tuple[str, ...]] = (
        "babbage-",
        "codex-",
        "dall-e",
        "davinci-",
        "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe",
        "gpt-image-",
        "omni-moderation",
        "text-embedding-",
        "tts-",
        "whisper-",
    )
    oauth_auth_kwarg = ""
    sdk_package_name = "openai"

    def list_models(self) -> Sequence[InferenceModelSpec]:
        """List OpenAI models and their broker-prefixed aliases."""

        specs: list[InferenceModelSpec] = []
        for model in self.client().models.list():
            model_id = str(getattr(model, "id", "") or "").strip()
            if not model_id:
                continue
            if not self._is_chat_model(model_id):
                continue
            config = {"provider_model": model_id, "source": self.key}
            owned_by = str(getattr(model, "owned_by", "") or "").strip()
            if owned_by:
                config["owned_by"] = owned_by
            specs.extend(
                self._model_specs(
                    handle=model_id,
                    display_name=model_id,
                    config=config,
                )
            )
        return specs

    def _build_model(self, handle: str, client: Any) -> OpenAIChatModel:
        """Use native protocol conversion, including the configured token-limit field."""

        return OpenAIChatModel(
            handle,
            provider=OpenAIProvider(openai_client=client),
            profile=OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=self._max_tokens_param() == "max_completion_tokens"
            ),
        )

    def _is_chat_model(self, model_id: str) -> bool:
        """Return whether a compatible model id should enter the chat catalogue.

        Deny prefixes win; an empty allow-prefix tuple is the explicit allow-all
        sentinel for every remaining id.
        """

        denied = self._config_string_list("model_deny_prefixes", default=self.model_deny_prefixes)
        if any(model_id.startswith(prefix) for prefix in denied):
            return False
        allowed = self._config_string_list("model_allow_prefixes", default=self.model_allow_prefixes)
        return not allowed or any(model_id.startswith(prefix) for prefix in allowed)

    def _max_tokens_param(self) -> str:
        """Return the OpenAI token-limit parameter owned by this backend."""

        value = str(self._config_value("max_tokens_param", default="max_tokens") or "max_tokens")
        if value not in _MAX_TOKEN_FIELDS:
            allowed = ", ".join(sorted(_MAX_TOKEN_FIELDS))
            raise ValueError(f"{self.label} max_tokens_param must be one of: {allowed}.")
        return value
