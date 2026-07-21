"""Bind an Angee inference backend to the pydantic-ai model that speaks its protocol.

The runtime owns pydantic-ai, so the protocol → ``Model`` class table lives here.
The *protocol* is not decided here: each backend declares it as ``chat_api``
(``angee.agents.backends.ChatAPI``), so a backend that inherits an OpenAI-compatible
protocol from its base class needs no entry of its own. Vendor request quirks stay
with the vendor backend — they ride the SDK client it hands over.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from angee.agents.backends import ChatAPI


def _anthropic_messages(handle: str, client: Any) -> Any:
    """Return the pydantic-ai model for Anthropic's Messages API."""

    return AnthropicModel(handle, provider=AnthropicProvider(anthropic_client=client))


def _openai_chat(handle: str, client: Any) -> Any:
    """Return the pydantic-ai model for OpenAI's Chat Completions API."""

    return OpenAIChatModel(handle, provider=OpenAIProvider(openai_client=client))


MODEL_BUILDERS: dict[str, Callable[[str, Any], Any]] = {
    ChatAPI.ANTHROPIC_MESSAGES: _anthropic_messages,
    ChatAPI.OPENAI_CHAT: _openai_chat,
}
"""The pydantic-ai model each declared chat protocol resolves to."""


def model_for_agent(agent: Any) -> Any:
    """Return the pydantic-ai model bound to ``agent``'s live credential."""

    model = getattr(agent, "model", None)
    if model is None:
        raise ValueError("An in-process agent requires an inference model.")
    backend = model.provider.backend
    build = MODEL_BUILDERS.get(backend.chat_api)
    if build is None:
        raise ValueError(
            f"The {backend.key!r} inference backend declares no in-process chat protocol; "
            f"set `chat_api` on it to one of: {', '.join(sorted(MODEL_BUILDERS))}."
        )
    credential = agent.inference_credential_for_runtime()
    return build(str(model.provider_model_name), backend.async_client(credential=credential))
