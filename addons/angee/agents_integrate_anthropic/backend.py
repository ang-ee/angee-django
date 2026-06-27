"""Anthropic SDK implementation of the agents inference backend."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from angee.agents.backends import InferenceModelSpec, InferenceRequest, InferenceResponse
from angee.agents.runtimes import ANTHROPIC_OAUTH_BETA_HEADER
from angee.agents.sdk_backends import SDKInferenceBackend

DEFAULT_MODEL_LIMIT = 1000
DEFAULT_BROKER_NAME = "anthropic"
_RESERVED_MESSAGE_OPTIONS = frozenset(
    {
        "max_tokens",
        "messages",
        "model",
        "stream",
        "system",
        "temperature",
        "tools",
    }
)


class AnthropicInferenceBackend(SDKInferenceBackend):
    """Inference backend backed by Anthropic's official Python SDK."""

    key = "anthropic"
    label = "Anthropic"
    icon = "anthropic"
    oauth_client = "anthropic-personal"
    api_key_env = ("ANTHROPIC_API_KEY",)
    defaults = {
        "vendor": "anthropic",
        "name": "Anthropic",
    }
    default_broker_name = DEFAULT_BROKER_NAME
    default_model_limit = DEFAULT_MODEL_LIMIT
    client_class_path = "anthropic.Anthropic"
    sdk_package_name = "anthropic"

    def _client_kwargs(self) -> dict[str, Any]:
        """Return Anthropic SDK kwargs, including the OAuth beta when needed."""

        kwargs = super()._client_kwargs()
        if "auth_token" in kwargs:
            kwargs["default_headers"] = {"anthropic-beta": ANTHROPIC_OAUTH_BETA_HEADER}
        return kwargs

    def list_models(self) -> Sequence[InferenceModelSpec]:
        """List Anthropic models and their broker-prefixed aliases."""

        specs: list[InferenceModelSpec] = []
        for model in self.client().models.list(limit=self._model_limit()):
            model_id = str(getattr(model, "id", "") or "").strip()
            if not model_id:
                continue
            display_name = str(getattr(model, "display_name", "") or model_id)
            context_window = int(getattr(model, "max_input_tokens", 0) or 0)
            max_tokens = int(getattr(model, "max_tokens", 0) or 0)
            config = {"provider_model": model_id, "source": "anthropic"}
            capabilities = self._json_object(getattr(model, "capabilities", None))
            specs.extend(
                self._model_specs(
                    handle=model_id,
                    display_name=display_name,
                    context_window=context_window,
                    max_output_tokens=max_tokens,
                    capabilities=capabilities,
                    config=config,
                )
            )
        return specs

    def chat(self, request: InferenceRequest) -> InferenceResponse:
        """Send one non-streaming Messages API request through Anthropic."""

        system, messages = self._anthropic_messages(request)
        params: dict[str, Any] = {
            **self._message_options(request, reserved=_RESERVED_MESSAGE_OPTIONS, owner="Anthropic"),
            "model": self._provider_model(request.model),
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if system:
            params["system"] = system
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if request.tools:
            params["tools"] = list(request.tools)
        message = self.client().messages.create(**params)
        raw = self._json_object(message)
        return InferenceResponse(
            text=self._string_content(getattr(message, "content", [])),
            content=self._json_list(raw.get("content")),
            usage=self._json_object(raw.get("usage")),
            raw=raw,
        )

    def _anthropic_messages(self, request: InferenceRequest) -> tuple[str, list[dict[str, Any]]]:
        """Return Anthropic Messages API ``system`` and ``messages`` arguments."""

        system_parts = [request.system.strip()] if request.system.strip() else []
        messages: list[dict[str, Any]] = []
        for item in request.messages:
            role = str(item.get("role") or "").strip()
            content = item.get("content", "")
            if role == "system":
                text = self._string_content(content)
                if text:
                    system_parts.append(text)
                continue
            if role not in {"user", "assistant"}:
                raise ValueError(f"Anthropic messages only support user/assistant roles, got {role!r}.")
            messages.append({"role": role, "content": content})
        return "\n\n".join(part for part in system_parts if part), messages
