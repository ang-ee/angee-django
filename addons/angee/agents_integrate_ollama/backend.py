"""Ollama specialization of the OpenAI-compatible inference backend."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, ClassVar, cast
from urllib.parse import urlsplit

from openai import DefaultAsyncHttpxClient, DefaultHttpxClient
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from angee.agents_integrate_openai.backend import OpenAIInferenceBackend

logger = logging.getLogger(__name__)


class OllamaInferenceBackend(OpenAIInferenceBackend):
    """OpenAI-compatible inference served by a loopback-only Ollama endpoint.

    ``keep_alive`` is provider deployment policy and rides the native request
    body. ``generation_limit`` supplies the OpenAI-compatible ``max_tokens``
    setting when a caller does not specify one. Context allocation is deliberately
    absent: configure ``num_ctx`` in the model's Ollama Modelfile so discovery and
    every runtime agree on the deployed context window.
    """

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

    def _client_kwargs(self, *, credential: Any | None = None) -> dict[str, Any]:
        """Build the sync SDK client with proxy inheritance disabled."""

        return self._ollama_client_kwargs(credential=credential, asynchronous=False)

    def _async_client_kwargs(self, *, credential: Any | None = None) -> dict[str, Any]:
        """Build the async SDK client with the identical endpoint policy."""

        return self._ollama_client_kwargs(credential=credential, asynchronous=True)

    def _ollama_client_kwargs(
        self,
        *,
        credential: Any | None,
        asynchronous: bool,
    ) -> dict[str, Any]:
        kwargs = super()._client_kwargs(credential=credential)
        self._validate_loopback_url(str(kwargs.get("base_url") or ""))
        client_class = DefaultAsyncHttpxClient if asynchronous else DefaultHttpxClient
        kwargs["http_client"] = client_class(trust_env=False, follow_redirects=False)
        return kwargs

    @staticmethod
    def _validate_loopback_url(base_url: str) -> None:
        """Reject credentials, redirects and non-loopback inference endpoints."""

        try:
            parsed = urlsplit(base_url)
            valid_port = parsed.port is None or parsed.port > 0
        except ValueError:
            valid_port = False
            parsed = urlsplit("")
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or not valid_port
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama inference only connects to a loopback endpoint.")

    def request_settings(self, model_settings: ModelSettings | None) -> ModelSettings:
        """Map deployment keepalive and generation limits to the native SDK request."""

        requested = dict(model_settings or {})
        raw_extra_body = requested.pop("extra_body", None)
        if raw_extra_body is None:
            caller_extra_body: dict[str, Any] = {}
        elif isinstance(raw_extra_body, Mapping):
            caller_extra_body = dict(raw_extra_body)
        else:
            raise ValueError("Ollama extra_body must be an object containing only keep_alive.")
        unknown_extra_body = set(caller_extra_body) - {"keep_alive"}
        if unknown_extra_body:
            names = ", ".join(sorted(unknown_extra_body))
            raise ValueError(f"Ollama request settings do not admit extra_body keys: {names}.")

        result = dict(super().request_settings(cast(ModelSettings, requested)) or {})
        raw_limit = self._config_value("generation_limit")
        if raw_limit is not None:
            try:
                generation_limit = int(raw_limit)
            except (TypeError, ValueError) as error:
                raise ValueError("Ollama generation_limit must be a positive integer.") from error
            if isinstance(raw_limit, bool) or generation_limit <= 0:
                raise ValueError("Ollama generation_limit must be a positive integer.")
            if "max_tokens" not in result:
                result["max_tokens"] = generation_limit

        keep_alive = self._config_value("keep_alive", default="5m")
        if (
            not isinstance(keep_alive, str | int)
            or isinstance(keep_alive, bool)
            or (isinstance(keep_alive, str) and not keep_alive.strip())
        ):
            raise ValueError("Ollama keep_alive must be a duration string or integer seconds.")
        if "keep_alive" in caller_extra_body and caller_extra_body["keep_alive"] != keep_alive:
            raise ValueError("Ollama keep_alive is owned by the inference provider deployment.")
        result["extra_body"] = {"keep_alive": keep_alive}
        return cast(ModelSettings, result)

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
                openai_chat_supports_max_completion_tokens=(self._max_tokens_param() == "max_completion_tokens"),
            ),
        )
