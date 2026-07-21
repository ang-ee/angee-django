"""Anthropic SDK implementation of the agents inference backend."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from angee.agents.backends import InferenceModelSpec, InferenceRequest, InferenceResponse, ChatAPI
from angee.agents.runtimes import ANTHROPIC_OAUTH_CLIENT_HEADERS, ANTHROPIC_OAUTH_SYSTEM_PREAMBLE
from angee.agents.sdk_backends import SDKInferenceBackend
from angee.integrate.credentials import CredentialKind


def oauth_system_blocks(system: Any) -> list[dict[str, Any]]:
    """Return ``system`` as the block list an OAuth request must open with.

    The OAuth edge matches the FIRST system block exactly against the Claude
    Code identity line — a concatenated ``"<preamble>\\n\\n<rest>"`` string is
    refused (verified empirically 2026-07-20), so the preamble rides as its
    own leading block and any caller-supplied system follows it.
    """

    blocks: list[dict[str, Any]] = [{"type": "text", "text": ANTHROPIC_OAUTH_SYSTEM_PREAMBLE}]
    if isinstance(system, str):
        rest = system.removeprefix(ANTHROPIC_OAUTH_SYSTEM_PREAMBLE).strip()
        if rest:
            blocks.append({"type": "text", "text": rest})
    elif isinstance(system, list):
        blocks.extend(
            block
            for block in system
            if not (isinstance(block, dict) and block.get("text") == ANTHROPIC_OAUTH_SYSTEM_PREAMBLE)
        )
    return blocks


class _OAuthMessagesTransport(httpx.AsyncHTTPTransport):
    """Rewrite Messages API system prompts into the OAuth block shape.

    Installed only on OAuth-credentialed async clients, so SDK consumers that
    assemble their own system prompt (pydantic-ai joins instructions into one
    string) still satisfy the edge's exact-first-block check without knowing
    the vendor quirk.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/messages"):
            try:
                body = json.loads(request.content.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                body = None
            if isinstance(body, dict):
                body["system"] = oauth_system_blocks(body.get("system"))
                content = json.dumps(body).encode("utf-8")
                request.stream = httpx.ByteStream(content)
                request.headers["content-length"] = str(len(content))
        return await super().handle_async_request(request)

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
    chat_api = ChatAPI.ANTHROPIC_MESSAGES
    oauth_client = "anthropic-personal"
    api_key_env = ("ANTHROPIC_API_KEY",)
    defaults = {
        "vendor": "anthropic",
        "name": "Anthropic",
    }
    default_broker_name = DEFAULT_BROKER_NAME
    default_model_limit = DEFAULT_MODEL_LIMIT
    client_class_path = "anthropic.Anthropic"
    async_client_class_path = "anthropic.AsyncAnthropic"
    sdk_package_name = "anthropic"

    def _client_kwargs(self, *, credential: Any | None = None) -> dict[str, Any]:
        """Return Anthropic SDK kwargs, including the OAuth beta when needed."""

        kwargs = super()._client_kwargs(credential=credential)
        if "auth_token" in kwargs:
            kwargs["default_headers"] = dict(ANTHROPIC_OAUTH_CLIENT_HEADERS)
        return kwargs

    def system_preamble(self, credential: Any | None = None) -> str:
        """Return the Claude Code identity line an OAuth token's requests must open with."""

        resolved = credential if credential is not None else getattr(self.provider, "credential", None)
        if resolved is not None and resolved.kind == CredentialKind.OAUTH:
            return ANTHROPIC_OAUTH_SYSTEM_PREAMBLE
        return ""

    def _async_client_kwargs(self, *, credential: Any | None = None) -> dict[str, Any]:
        """Add the OAuth block-rewrite transport to async OAuth clients."""

        kwargs = super()._async_client_kwargs(credential=credential)
        if "auth_token" in kwargs:
            kwargs["http_client"] = httpx.AsyncClient(transport=_OAuthMessagesTransport())
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
        if self.system_preamble():
            params["system"] = oauth_system_blocks(system)
        elif system:
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
