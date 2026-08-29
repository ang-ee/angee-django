"""OpenAI SDK implementation of the agents inference backend."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from angee.agents.backends import ChatAPI, InferenceModelSpec, InferenceRequest, InferenceResponse
from angee.agents.sdk_backends import SDKInferenceBackend

DEFAULT_BROKER_NAME = "openai"
_ALLOWED_MESSAGE_ROLES = frozenset({"assistant", "developer", "system", "tool", "user"})
_MAX_TOKEN_FIELDS = frozenset({"max_tokens", "max_completion_tokens"})
_RESERVED_CHAT_OPTIONS = frozenset(
    {
        "messages",
        "model",
        "stream",
        "temperature",
        "tools",
        *_MAX_TOKEN_FIELDS,
    }
)


class OpenAIInferenceBackend(SDKInferenceBackend):
    """Inference backend backed by OpenAI's official Python SDK."""

    key = "openai"
    label = "OpenAI"
    icon = "openai"
    chat_api = ChatAPI.OPENAI_CHAT
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

    def chat(self, request: InferenceRequest) -> InferenceResponse:
        """Send one non-streaming Chat Completions request through OpenAI."""

        params: dict[str, Any] = {
            **self._message_options(request, reserved=_RESERVED_CHAT_OPTIONS, owner=self.label),
            "model": self._provider_model(request.model),
            "messages": self._openai_messages(request),
            self._max_tokens_param(): request.max_tokens,
        }
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if request.tools:
            params["tools"] = list(request.tools)
        completion = self.client().chat.completions.create(**params)
        text = self._completion_text(completion)
        raw = self._json_object(completion)
        return InferenceResponse(
            text=text,
            content=[{"type": "text", "text": text}] if text else [],
            usage=self._json_object(raw.get("usage")),
            raw=raw,
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

    def _openai_messages(self, request: InferenceRequest) -> list[dict[str, Any]]:
        """Return OpenAI Chat Completions ``messages`` from a provider-neutral request."""

        messages: list[dict[str, Any]] = []
        system = request.system.strip()
        if system:
            messages.append({"role": "system", "content": system})
        for item in request.messages:
            message = self._openai_message(item)
            messages.append(message)
        return messages

    def _openai_message(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """Return one OpenAI message, preserving SDK-native fields."""

        role = str(item.get("role") or "").strip()
        if role not in _ALLOWED_MESSAGE_ROLES:
            raise ValueError(f"OpenAI messages support {sorted(_ALLOWED_MESSAGE_ROLES)}, got {role!r}.")
        message = {str(key): value for key, value in item.items()}
        message["role"] = role
        message.setdefault("content", "")
        return message

    def _completion_text(self, completion: Any) -> str:
        """Return text from the first OpenAI chat completion choice."""

        choices = getattr(completion, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "") if message is not None else ""
        return self._string_content(content)
