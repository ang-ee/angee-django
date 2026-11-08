"""Ollama specialization of the OpenAI-compatible inference backend."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, cast
from urllib.parse import urlsplit

import httpx2
from pydantic_ai.settings import ModelSettings
from typesafe_sdk import RetryPolicy, TypeSafeClient, TypeSafeError
from typesafe_sdk.constants import DEFAULT_TIMEOUT

from angee.agents.sdk_backends import SDKInferenceBackend
from angee.agents_integrate_openai.backend import OpenAIInferenceBackend

if TYPE_CHECKING:
    from pydantic_ai.models.openai import OpenAIChatModel
    from typesafe_sdk import Content, Question, SystemOneResponse

logger = logging.getLogger(__name__)


class OllamaInferenceBackend(OpenAIInferenceBackend):
    """OpenAI-compatible inference served by a configured Ollama endpoint.

    ``keep_alive`` is provider deployment policy and rides the native request
    body. ``generation_limit``, an exact positive integer, supplies the
    OpenAI-compatible ``max_tokens`` setting when a caller does not specify one.
    Context allocation is deliberately absent: configure ``num_ctx`` in the model's
    Ollama Modelfile so discovery and every runtime agree on the deployed context
    window. Models bind through pydantic-ai's native ``OllamaProvider``, whose
    per-family profile this backend only extends. SDK retries are disabled:
    callers own retries within their execution budget, rather than duplicating
    queued work after a local inference timeout.
    """

    key = "ollama"
    label = "Ollama"
    icon = "ollama"
    defaults = {
        "vendor": "ollama",
        "name": "Ollama",
    }
    default_base_url = "http://localhost:11434/v1"
    default_broker_name = "ollama"
    model_allow_prefixes: ClassVar[tuple[str, ...]] = ()
    model_deny_prefixes: ClassVar[tuple[str, ...]] = ()
    api_key_env: ClassVar[tuple[str, ...]] = ()
    transient_error_paths = (
        *OpenAIInferenceBackend.transient_error_paths,
        "typesafe_sdk.TypeSafeAPIConnectionError", "typesafe_sdk.TypeSafeAPITimeoutError",
        "typesafe_sdk.TypeSafeRateLimitError", "typesafe_sdk.TypeSafeInternalServerError",
    )

    @property
    def requires_credential(self) -> bool:
        """Ollama deployments may run without authentication."""

        return False

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
        self._validate_endpoint_url(str(kwargs.get("base_url") or ""))
        # The SDK is an optional dependency, loaded once a client is bound, not
        # when the backend is catalogued.
        from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

        client_class = DefaultAsyncHttpxClient if asynchronous else DefaultHttpxClient
        kwargs["http_client"] = client_class(trust_env=False, follow_redirects=False)
        kwargs["max_retries"] = 0
        return kwargs

    @staticmethod
    def _validate_endpoint_url(base_url: str) -> None:
        """Require an explicit HTTP endpoint without inline auth or request overrides."""

        try:
            parsed = urlsplit(base_url)
            valid_port = parsed.port is None or parsed.port > 0
        except ValueError:
            valid_port = False
            parsed = urlsplit("")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not valid_port
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama inference requires an HTTP(S) endpoint without credentials, query or fragment.")

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
            generation_limit = self._generation_limit(raw_limit)
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

    @staticmethod
    def _generation_limit(raw: Any) -> int:
        """Return a configured limit that is exactly a positive integer, never a truncation.

        The decimal text form refuses booleans, fractions, infinities and NaN
        alike, while integral spellings such as ``"2048"`` or ``2048.0`` pass.
        """

        try:
            exact = Decimal(str(raw))
            limit = int(exact)
        except (ArithmeticError, ValueError) as error:
            raise ValueError("Ollama generation_limit must be a positive integer.") from error
        if limit <= 0 or exact != limit:
            raise ValueError("Ollama generation_limit must be a positive integer.")
        return limit

    def _model_discovery(self, client: Any, model_id: str) -> dict[str, Any]:
        """Read native capabilities and architecture; absent facts preserve curated rows."""

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
        capabilities = shown.get("capabilities")
        model_use = ""
        if isinstance(capabilities, list) and all(isinstance(value, str) for value in capabilities):
            if "decision" in capabilities:
                model_use = "decision"
            elif "embedding" in capabilities:
                model_use = "embedding"
            elif "completion" in capabilities:
                model_use = "multimodal" if "vision" in capabilities else "chat"
        context_window = self._architecture_context_window(shown)
        config: dict[str, Any] = {}
        num_ctx = self._configured_num_ctx(shown.get("parameters"))
        if num_ctx:
            config["ollama_num_ctx"] = num_ctx
        return {"model_use": model_use, "context_window": context_window, "config": config}

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

    def request_error_types(self) -> tuple[type[BaseException], ...]:
        """Include decision SDK validation and API failures without hiding them."""

        return (*super().request_error_types(), TypeSafeError)

    def decide(
        self,
        handle: str,
        state: Content,
        questions: Mapping[str, Question],
        *,
        timeout: float | None = None,
        credential: Any | None = None,
    ) -> SystemOneResponse:
        """Call Ollama System One with native types, endpoint policy and client lifetime."""

        self.validate_timeout(timeout)
        kwargs = SDKInferenceBackend._client_kwargs(self, credential=credential)
        self._validate_endpoint_url(str(kwargs["base_url"]))
        # The native SDK appends /v1/systemone; the shared provider stores /v1.
        kwargs["base_url"] = str(kwargs["base_url"]).removesuffix("/v1")
        if timeout is not None:
            kwargs["timeout"] = timeout
        # Local Ollama accepts the native uncompressed protocol. Explicit auth,
        # endpoint and transport prevent SDK environment defaults from rerouting it.
        with TypeSafeClient(
            **kwargs,
            compression=False,
            retry=RetryPolicy(max_retries=0),
            http_client=httpx2.Client(
                timeout=kwargs.get("timeout", DEFAULT_TIMEOUT), trust_env=False, follow_redirects=False,
            ),
        ) as client:
            return client.system_one(state=state, questions=questions, model=self._provider_model(handle))

    def _build_model(self, handle: str, client: Any) -> OpenAIChatModel:
        """Bind the native Ollama provider and extend its profile with the schema-in-instructions envelope."""

        # The SDK adapter loads with the model it binds, not with the catalogue.
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.profiles.openai import OpenAIModelProfile
        from pydantic_ai.providers.ollama import OllamaProvider

        return OpenAIChatModel(
            handle,
            provider=OllamaProvider(openai_client=client),
            profile=OpenAIModelProfile(
                supports_thinking=True,
                native_output_requires_schema_in_instructions=True,
                default_structured_output_mode="native",
                openai_chat_supports_max_completion_tokens=(self._max_tokens_param() == "max_completion_tokens"),
            ),
        )
