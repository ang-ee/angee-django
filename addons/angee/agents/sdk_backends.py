"""Shared helpers for official SDK-backed inference providers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from angee.agents.backends import InferenceBackend, InferenceModelSpec, InferenceRequest

_OAUTH_CREDENTIAL_KIND = "oauth"


class SDKInferenceBackend(InferenceBackend):
    """Base for inference backends that wrap a vendor's official Python SDK."""

    client_class: ClassVar[Any | None] = None
    default_broker_name: ClassVar[str] = ""
    default_model_limit: ClassVar[int] = 1000
    oauth_auth_kwarg: ClassVar[str] = "auth_token"
    sdk_package_name: ClassVar[str] = "provider SDK"

    def client(self) -> Any:
        """Return a vendor SDK client bound to this provider's credential."""

        client_class = self.client_class or self._load_client_class()
        return client_class(**self._client_kwargs())

    def _client_kwargs(self) -> dict[str, Any]:
        """Return common SDK client constructor kwargs."""

        kwargs: dict[str, Any] = self._credential_auth()
        base_url = str(getattr(self.provider, "base_url", "") or "").strip()
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        timeout = self._config_int("timeout_seconds", default=0)
        if timeout:
            kwargs["timeout"] = timeout
        return kwargs

    def _credential_auth(self) -> dict[str, str]:
        """Return SDK auth kwargs for the attached credential kind."""

        credential = getattr(self.provider, "credential", None)
        if credential is None:
            raise ValueError(f"{self.label} inference requires an attached credential.")
        ensure_fresh = getattr(credential, "ensure_fresh", None)
        if callable(ensure_fresh):
            ensure_fresh()
        secret = str(credential.secret_value() or "")
        if not secret:
            raise ValueError(f"{self.label} inference credential has no secret.")
        key = "api_key"
        if self._credential_kind(credential) == _OAUTH_CREDENTIAL_KIND:
            key = self.oauth_auth_kwarg
            if not key:
                raise ValueError(f"{self.label} inference does not support OAuth credentials.")
        return {key: secret}

    def _message_options(
        self,
        request: InferenceRequest,
        *,
        reserved: frozenset[str],
        owner: str,
    ) -> dict[str, Any]:
        """Return provider-specific request kwargs after guarding owned fields."""

        options = dict(request.options)
        collisions = reserved & options.keys()
        if collisions:
            names = ", ".join(sorted(collisions))
            raise ValueError(f"{owner} request option(s) are owned by the provider: {names}.")
        return options

    def _model_limit(self) -> int:
        """Return the configured model-list page size."""

        return self._config_int("model_limit", default=self.default_model_limit)

    def _broker_name(self) -> str:
        """Return the broker prefix to materialize beside native model ids."""

        value = self._config_value("broker_name", default=self.default_broker_name)
        return str(value or "").strip().strip("/")

    def _provider_model(self, model: str) -> str:
        """Strip this provider's broker prefix before calling the vendor SDK."""

        handle = model.strip()
        broker_name = self._broker_name()
        prefix = f"{broker_name}/" if broker_name else ""
        return handle.removeprefix(prefix) if prefix else handle

    def _model_specs(
        self,
        *,
        handle: str,
        display_name: str = "",
        description: str = "",
        model_use: str = "chat",
        context_window: int = 0,
        max_output_tokens: int = 0,
        capabilities: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> list[InferenceModelSpec]:
        """Return native and broker-prefixed model specs for one SDK model."""

        name = display_name or handle
        base_config = dict(config or {})
        base = InferenceModelSpec(
            handle=handle,
            display_name=name,
            description=description,
            model_use=model_use,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            capabilities=dict(capabilities or {}),
            config=base_config,
        )
        broker_name = self._broker_name()
        if not broker_name:
            return [base]
        return [
            base,
            InferenceModelSpec(
                handle=f"{broker_name}/{handle}",
                display_name=f"{name} ({broker_name})",
                description=description,
                model_use=model_use,
                context_window=context_window,
                max_output_tokens=max_output_tokens,
                capabilities=dict(capabilities or {}),
                config={**base_config, "broker_name": broker_name},
            ),
        ]

    def _config_value(self, key: str, *, default: Any = None) -> Any:
        """Return one provider config value."""

        config = getattr(self.provider, "config", None)
        if isinstance(config, Mapping) and key in config:
            return config[key]
        return default

    def _config_int(self, key: str, *, default: int) -> int:
        """Return one integer config value with a defensive fallback."""

        try:
            return int(self._config_value(key, default=default))
        except (TypeError, ValueError):
            return default

    def _config_string_list(self, key: str, *, default: Sequence[str]) -> tuple[str, ...]:
        """Return one config value as a tuple of non-empty strings."""

        value = self._config_value(key)
        if value is None:
            return tuple(default)
        if isinstance(value, str):
            raw_items: Sequence[Any] = value.split(",")
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
            raw_items = value
        else:
            return tuple(default)
        return tuple(str(item).strip() for item in raw_items if str(item).strip())

    def _load_client_class(self) -> Any:
        """Import the concrete SDK client class."""

        raise RuntimeError(f"Install the `{self.sdk_package_name}` package to use the {self.label} inference backend.")

    @staticmethod
    def _credential_kind(credential: Any) -> str:
        """Return a credential kind value without importing the integrate model."""

        kind = getattr(credential, "kind", "")
        return str(getattr(kind, "value", kind))

    @staticmethod
    def _string_content(content: Any) -> str:
        """Return textual content from a string or content-block list."""

        if isinstance(content, str):
            return content.strip()
        if isinstance(content, Sequence) and not isinstance(content, str | bytes):
            parts: list[str] = []
            for block in content:
                if isinstance(block, Mapping):
                    text = block.get("text") if block.get("type") == "text" else ""
                else:
                    text = getattr(block, "text", "") if getattr(block, "type", "") == "text" else ""
                if text:
                    parts.append(str(text))
            return "\n".join(parts).strip()
        return str(content or "").strip()

    @classmethod
    def _content_text(cls, content: Any) -> str:
        """Return concatenated assistant text content."""

        return cls._string_content(content)

    @classmethod
    def _json_list(cls, value: Any) -> list[dict[str, Any]]:
        """Return a JSON-safe list of mapping objects."""

        dumped = cls._json_value(value)
        if isinstance(dumped, list):
            return [item if isinstance(item, dict) else {"value": item} for item in dumped]
        return []

    @classmethod
    def _json_object(cls, value: Any) -> dict[str, Any]:
        """Return a JSON-safe object from SDK models, mappings, or plain objects."""

        dumped = cls._json_value(value)
        return dumped if isinstance(dumped, dict) else {}

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        """Return a value safe to store in JSONField/GraphQL JSON."""

        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, Mapping):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return [cls._json_value(item) for item in value]
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return cls._json_value(model_dump(mode="json"))
            except TypeError:
                return cls._json_value(model_dump())
        attrs = {
            key: item
            for key, item in getattr(value, "__dict__", {}).items()
            if not key.startswith("_")
        }
        return {key: cls._json_value(item) for key, item in attrs.items()}
